// Minimal C++-side check (the thorough parity tests live in tests/test_native.py).
#include <cmath>
#include <cstdio>

#include "shimcoins/core.hpp"

using namespace shimcoins;

int main() {
    // items: 0 ore, 1 bar, 2 gem, 3 ring
    Graph g;
    g.n_items = 4;
    auto T = [](int kind, std::vector<Stack> in, std::vector<Stack> out, double cost, double rev) {
        Transform t;
        t.kind = kind; t.in = std::move(in); t.out = std::move(out); t.cost = cost; t.revenue = rev;
        return t;
    };
    g.ts = {
        T(BUY, {}, {{0, 1}}, 100, 0),
        T(CRAFT, {{0, 3}}, {{1, 1}}, 0, 0),
        T(SHIMMER, {{0, 1}}, {{2, 1}}, 0, 0),
        T(CRAFT, {{1, 1}, {2, 2}}, {{3, 1}}, 0, 0),
        T(SELL, {{3, 1}}, {}, 0, 4000),
    };
    g.finalize();
    LpOut lp = maximize(g, {0, 1, 2, 3, 4}, {}, 1.0);
    if (!lp.ok || std::fabs(lp.gain - 7.0) > 1e-9) {
        std::printf("LP gain %f, expected 7\n", lp.gain);
        return 1;
    }
    Params p;
    p.max_steps = 5;
    Report r = search(g, p, {});
    if (r.results.empty() || std::fabs(r.results[0].gain - 7.0) > 1e-9) {
        std::printf("search failed\n");
        return 1;
    }
    std::printf("ok: gain %.3f, %lld nodes\n", r.results[0].gain, (long long)r.stats.nodes);
    return 0;
}
