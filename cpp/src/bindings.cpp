// Python bindings: shimmered_coins_core._core
#include <nanobind/nanobind.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/tuple.h>
#include <nanobind/stl/vector.h>

#include "shimcoins/core.hpp"

namespace nb = nanobind;
using namespace shimcoins;

namespace {

Graph make_graph(int n_items, const std::vector<int>& kinds,
                 const std::vector<std::vector<Stack>>& ins,
                 const std::vector<std::vector<Stack>>& outs, const std::vector<double>& cost,
                 const std::vector<double>& revenue) {
    const size_t n = kinds.size();
    if (ins.size() != n || outs.size() != n || cost.size() != n || revenue.size() != n)
        throw std::invalid_argument("transform arrays must have equal length");
    Graph g;
    g.n_items = n_items;
    g.ts.resize(n);
    for (size_t i = 0; i < n; ++i) {
        g.ts[i].kind = kinds[i];
        g.ts[i].in = ins[i];
        g.ts[i].out = outs[i];
        g.ts[i].cost = cost[i];
        g.ts[i].revenue = revenue[i];
        for (auto* v : {&g.ts[i].in, &g.ts[i].out})
            for (auto& s : *v)
                if (s.first < 0 || s.first >= n_items) throw std::out_of_range("item index");
    }
    g.finalize();
    return g;
}

}  // namespace

NB_MODULE(_core, m) {
    m.doc() = "shimmered-coins C++ search core";

    m.def(
        "search",
        [](int n_items, const std::vector<int>& kinds, const std::vector<std::vector<Stack>>& ins,
           const std::vector<std::vector<Stack>>& outs, const std::vector<double>& cost,
           const std::vector<double>& revenue, const std::vector<double>& leftover, int max_steps,
           int top_n, bool leftover_counts, double min_gain, const std::vector<int>& must_use,
           bool has_start, const std::vector<int>& start_items, bool connected, bool exhaustive,
           int64_t max_nodes, double time_limit, int threads) {
            Graph g = make_graph(n_items, kinds, ins, outs, cost, revenue);
            Params p;
            p.max_steps = max_steps;
            p.top_n = top_n;
            p.leftover_counts = leftover_counts;
            p.min_gain = min_gain;
            p.must_use = must_use;
            p.has_start = has_start;
            p.start_items = start_items;
            p.connected = connected;
            p.exhaustive = exhaustive;
            p.max_nodes = max_nodes;
            p.time_limit = time_limit;
            p.threads = threads;
            Report r;
            {
                nb::gil_scoped_release release;
                r = search(g, p, leftover);
            }
            std::vector<std::tuple<std::vector<int>, double, std::vector<double>>> results;
            for (auto& f : r.results) results.emplace_back(f.seq, f.gain, f.x);
            auto stats = std::make_tuple(r.stats.nodes, r.stats.lp_solves, r.stats.pruned,
                                         r.stats.elapsed, r.stats.complete);
            return std::make_pair(results, stats);
        },
        nb::arg("n_items"), nb::arg("kinds"), nb::arg("inputs"), nb::arg("outputs"),
        nb::arg("cost"), nb::arg("revenue"), nb::arg("leftover"), nb::arg("max_steps"),
        nb::arg("top_n"), nb::arg("leftover_counts"), nb::arg("min_gain"), nb::arg("must_use"),
        nb::arg("has_start"), nb::arg("start_items"), nb::arg("connected"), nb::arg("exhaustive"),
        nb::arg("max_nodes"), nb::arg("time_limit"), nb::arg("threads"));

    m.def(
        "maximize",
        [](int n_items, const std::vector<int>& kinds, const std::vector<std::vector<Stack>>& ins,
           const std::vector<std::vector<Stack>>& outs, const std::vector<double>& cost,
           const std::vector<double>& revenue, const std::vector<int>& seq,
           const std::vector<double>& w, double coin_mult) {
            Graph g = make_graph(n_items, kinds, ins, outs, cost, revenue);
            LpOut r = maximize(g, seq, w, coin_mult);
            return std::make_tuple(r.ok, r.gain, r.x);
        },
        "Solve the LP for one sequence (testing hook).");
}
