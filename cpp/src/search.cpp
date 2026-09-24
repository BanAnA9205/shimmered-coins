// Branch-and-bound over move sequences. See python/shimmered_coins/search/engine.py for the
// reference description; this file follows it step for step.
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <map>
#include <mutex>

#include "shimcoins/core.hpp"

#ifdef _OPENMP
#include <omp.h>
#endif

namespace shimcoins {

void Graph::finalize() {
    buy_of.assign(n_items, -1);
    sell_of.assign(n_items, -1);
    consumers.assign(n_items, {});
    for (int i = 0; i < (int)ts.size(); ++i) {
        const auto& t = ts[i];
        if (t.kind == BUY) {
            buy_of[t.out[0].first] = i;
        } else if (t.kind == SELL) {
            sell_of[t.in[0].first] = i;
        } else {
            for (auto& s : t.in) {
                auto& c = consumers[s.first];
                if (c.empty() || c.back() != i) c.push_back(i);
            }
        }
    }
}

Bounds compute_bounds(const Graph& g, int max_steps, const std::vector<double>& leftover) {
    Bounds b;
    std::vector<double> v0(g.n_items, 0.0);
    for (size_t i = 0; i < leftover.size() && i < v0.size(); ++i) v0[i] = leftover[i];
    b.V.push_back(v0);
    b.M.push_back(1.0);
    for (int k = 0; k < max_steps; ++k) {
        const auto& pv = b.V.back();
        const double pm = b.M.back();
        std::vector<double> cur = pv;
        for (const auto& t : g.ts) {
            if (t.kind == BUY) continue;
            if (t.kind == SELL) {
                auto& c = cur[t.in[0].first];
                c = std::max(c, pm * t.revenue);
                continue;
            }
            double out = pm * t.revenue;
            for (auto& s : t.out) out += s.second * pv[s.first];
            for (auto& s : t.in) {
                auto& c = cur[s.first];
                c = std::max(c, out / s.second);
            }
        }
        double m = pm;
        for (int item = 0; item < g.n_items; ++item)
            if (g.buy_of[item] >= 0) m = std::max(m, pv[item] / g.ts[g.buy_of[item]].cost);
        b.V.push_back(std::move(cur));
        b.M.push_back(m);
    }
    return b;
}

namespace {

using Bits = std::vector<uint64_t>;

inline void set_bit(Bits& b, int i) { b[i >> 6] |= uint64_t(1) << (i & 63); }
inline bool get_bit(const Bits& b, int i) { return (b[i >> 6] >> (i & 63)) & 1; }
inline bool intersects(const Bits& a, const Bits& b) {
    for (size_t i = 0; i < a.size(); ++i)
        if (a[i] & b[i]) return true;
    return false;
}

struct Shared {
    const Graph& g;
    const Params& p;
    const std::vector<double>& w;
    Bounds bounds;
    std::vector<Bits> downstream;  // item -> items reachable through craft/shimmer
    std::vector<char> is_must;
    std::mutex mu;
    std::map<std::vector<int>, Found> found;  // key: sorted transform ids
    std::atomic<double> thresh;
    std::atomic<int64_t> nodes{0}, lp_solves{0}, pruned{0};
    std::atomic<bool> stop{false};
    std::chrono::steady_clock::time_point start;

    Shared(const Graph& g_, const Params& p_, const std::vector<double>& w_)
        : g(g_), p(p_), w(w_), thresh(p_.min_gain) {}

    void update_threshold() {  // caller holds mu
        std::vector<double> gains;
        gains.reserve(found.size());
        for (auto& kv : found) gains.push_back(kv.second.gain);
        double t = p.min_gain;
        if ((int)gains.size() >= p.top_n) {
            std::nth_element(gains.begin(), gains.begin() + (p.top_n - 1), gains.end(),
                             std::greater<double>());
            t = std::max(t, gains[p.top_n - 1]);
        }
        thresh.store(t);
    }

    void record(const std::vector<int>& seq, const LpOut& r) {
        if (r.gain <= p.min_gain + 1e-9) return;
        double xmax = 1e-12;
        for (double v : r.x) xmax = std::max(xmax, v);
        for (double v : r.x)
            if (v <= 1e-7 * xmax) return;  // an unused move: a shorter sequence covers it
        if (!p.must_use.empty()) {
            std::vector<char> touched(g.n_items, 0);
            for (int t : seq) {
                for (auto& s : g.ts[t].in) touched[s.first] = 1;
                for (auto& s : g.ts[t].out) touched[s.first] = 1;
            }
            for (int m : p.must_use)
                if (!touched[m]) return;
        }
        std::vector<int> key = seq;
        std::sort(key.begin(), key.end());
        std::lock_guard<std::mutex> lock(mu);
        auto it = found.find(key);
        if (it == found.end() || it->second.gain < r.gain) {
            found[key] = Found{seq, r.gain, r.x};
            update_threshold();
        }
    }

    bool out_of_budget() {
        if (stop.load(std::memory_order_relaxed)) return true;
        if (nodes.fetch_add(1) + 1 > p.max_nodes) {
            stop = true;
            return true;
        }
        if (p.time_limit > 0) {
            double el = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
            if (el > p.time_limit) {
                stop = true;
                return true;
            }
        }
        return false;
    }
};

inline int order_rank(const Transform& t) { return t.kind == SELL ? 0 : (t.kind == BUY ? 2 : 1); }

inline bool independent(const Transform& a, const Transform& b) {
    for (auto& o : a.out)
        for (auto& i : b.in)
            if (o.first == i.first) return false;
    for (auto& o : b.out)
        for (auto& i : a.in)
            if (o.first == i.first) return false;
    return true;
}

struct Worker {
    Shared& S;
    std::vector<int> seq;
    std::vector<int> support_count;  // item -> #moves in seq producing it (support = count > 0)

    explicit Worker(Shared& s) : S(s), support_count(s.g.n_items, 0) {}

    double buy_ratio(int t, int k) const {
        if (k < 0) return 0.0;
        const auto& tr = S.g.ts[t];
        return S.bounds.V[k][tr.out[0].first] / tr.cost;
    }

    std::vector<int> children() {
        const auto& g = S.g;
        const auto& p = S.p;
        const int remaining = p.max_steps - (int)seq.size();
        std::vector<int> cands;
        std::vector<char> seen(g.ts.size(), 0);
        auto add = [&](int t) {
            if (!seen[t]) {
                seen[t] = 1;
                cands.push_back(t);
            }
        };
        Bits reach;
        if (!seq.empty() && p.connected) {
            reach.assign(S.downstream.empty() ? 0 : S.downstream[0].size(), 0);
            for (int i = 0; i < g.n_items; ++i)
                if (support_count[i] > 0)
                    for (size_t k = 0; k < reach.size(); ++k) reach[k] |= S.downstream[i][k];
        }
        for (int item = 0; item < g.n_items; ++item) {
            int t = g.buy_of[item];
            if (t < 0 || g.consumers[item].empty()) continue;
            if (seq.empty()) {
                if (p.has_start &&
                    std::find(p.start_items.begin(), p.start_items.end(), item) == p.start_items.end())
                    continue;
            } else if (p.connected && !intersects(S.downstream[item], reach)) {
                continue;
            }
            add(t);
        }
        for (int item = 0; item < g.n_items; ++item) {
            if (support_count[item] <= 0) continue;
            for (int t : g.consumers[item]) {
                bool ok = true;
                for (auto& s : g.ts[t].in)
                    if (support_count[s.first] <= 0) { ok = false; break; }
                if (ok) add(t);
            }
            if (g.sell_of[item] >= 0) add(g.sell_of[item]);
        }
        std::vector<int> out;
        const int last = seq.empty() ? -1 : seq.back();
        for (int t : cands) {
            if (std::find(seq.begin(), seq.end(), t) != seq.end()) continue;
            if (last >= 0 && independent(g.ts[last], g.ts[t])) {
                auto kl = std::make_pair(order_rank(g.ts[last]), last);
                auto kt = std::make_pair(order_rank(g.ts[t]), t);
                if (kt < kl) continue;
            }
            out.push_back(t);
        }
        std::stable_sort(out.begin(), out.end(), [&](int a, int b) {
            double sa = g.ts[a].kind == BUY ? buy_ratio(a, remaining) : 1.0;
            double sb = g.ts[b].kind == BUY ? buy_ratio(b, remaining) : 1.0;
            return sa > sb;
        });
        return out;
    }

    void push(int t) {
        seq.push_back(t);
        for (auto& s : S.g.ts[t].out) ++support_count[s.first];
    }
    void pop() {
        for (auto& s : S.g.ts[seq.back()].out) --support_count[s.first];
        seq.pop_back();
    }

    // Returns false when the node is cut (its children must not be explored).
    bool visit() {
        const auto& p = S.p;
        const auto& last = S.g.ts[seq.back()];
        if (last.kind == SELL || (last.kind != BUY && p.leftover_counts)) {
            ++S.lp_solves;
            LpOut r = maximize(S.g, seq, S.w, 1.0);
            if (r.ok && r.gain > 1e-7) S.record(seq, r);
        }
        if ((int)seq.size() >= p.max_steps) return false;
        if (!p.exhaustive) {
            const int rem = p.max_steps - (int)seq.size();
            ++S.lp_solves;
            LpOut ub = maximize(S.g, seq, S.bounds.V[rem], S.bounds.M[rem]);
            if (!ub.ok || ub.gain <= S.thresh.load() + 1e-9) {
                ++S.pruned;
                return false;
            }
        }
        return true;
    }

    void dfs() {
        if (S.out_of_budget()) return;
        if (!visit()) return;
        for (int t : children()) {
            if (S.stop.load(std::memory_order_relaxed)) return;
            push(t);
            dfs();
            pop();
        }
    }
};

}  // namespace

Report search(const Graph& g, const Params& p, const std::vector<double>& leftover) {
    Shared S(g, p, leftover);
    S.start = std::chrono::steady_clock::now();
    S.bounds = compute_bounds(g, p.max_steps, leftover);

    // downstream closures
    const size_t words = (g.n_items + 63) / 64;
    S.downstream.assign(g.n_items, Bits(words, 0));
#pragma omp parallel for schedule(dynamic, 64)
    for (int item = 0; item < g.n_items; ++item) {
        Bits& d = S.downstream[item];
        std::vector<int> todo{item};
        set_bit(d, item);
        while (!todo.empty()) {
            int cur = todo.back();
            todo.pop_back();
            for (int t : g.consumers[cur])
                for (auto& o : g.ts[t].out)
                    if (!get_bit(d, o.first)) {
                        set_bit(d, o.first);
                        todo.push_back(o.first);
                    }
        }
    }

    // Root: the candidate first moves, each explored as an independent subtree.
    S.nodes = 1;  // the root itself, as in the Python engine
    Worker root(S);
    std::vector<int> firsts;
    for (int t : root.children()) {
        if (!p.exhaustive && root.buy_ratio(t, p.max_steps - 1) - 1.0 <= S.thresh.load() + 1e-9) {
            ++S.pruned;
            continue;
        }
        firsts.push_back(t);
    }

#ifdef _OPENMP
    if (p.threads > 0) omp_set_num_threads(p.threads);
#endif
#pragma omp parallel for schedule(dynamic, 1)
    for (int i = 0; i < (int)firsts.size(); ++i) {
        if (S.stop.load()) continue;
        // re-check the root cut with the threshold found so far by other subtrees
        if (!p.exhaustive &&
            root.buy_ratio(firsts[i], p.max_steps - 1) - 1.0 <= S.thresh.load() + 1e-9) {
            ++S.pruned;
            continue;
        }
        Worker w(S);
        w.push(firsts[i]);
        w.dfs();
    }

    Report rep;
    rep.stats.nodes = S.nodes.load();
    rep.stats.lp_solves = S.lp_solves.load();
    rep.stats.pruned = S.pruned.load();
    rep.stats.complete = !S.stop.load();
    rep.stats.elapsed =
        std::chrono::duration<double>(std::chrono::steady_clock::now() - S.start).count();
    for (auto& kv : S.found) rep.results.push_back(kv.second);
    std::sort(rep.results.begin(), rep.results.end(),
              [](const Found& a, const Found& b) { return a.gain > b.gain; });
    if ((int)rep.results.size() > p.top_n) rep.results.resize(p.top_n);
    return rep;
}

}  // namespace shimcoins
