// shimmered-coins C++ search core.
//
// Mirrors python/shimmered_coins/search (engine.py, lp.py, bounds.py). The Python engine is the
// reference: any behaviour change goes there first, and tests/test_native.py checks both agree.
#pragma once

#include <cstdint>
#include <utility>
#include <vector>

namespace shimcoins {

enum Kind : int { BUY = 0, SELL = 1, CRAFT = 2, SHIMMER = 3 };

using Stack = std::pair<int, int>;  // (dense item index, count)

struct Transform {
    int kind = CRAFT;
    std::vector<Stack> in, out;
    double cost = 0, revenue = 0;
};

struct Graph {
    int n_items = 0;
    std::vector<Transform> ts;
    // derived by finalize()
    std::vector<int> buy_of, sell_of;               // item -> transform index or -1
    std::vector<std::vector<int>> consumers;        // item -> craft/shimmer transforms using it
    void finalize();
};

struct Params {
    int max_steps = 4;
    int top_n = 10;
    bool leftover_counts = false;       // leftover policy "sell" (weights given separately)
    double min_gain = 0.0;
    std::vector<int> must_use;          // dense item indices
    bool has_start = false;
    std::vector<int> start_items;
    bool connected = true;
    bool exhaustive = false;
    int64_t max_nodes = 500000;
    double time_limit = 60.0;           // seconds; <= 0 means none
    int threads = 0;                    // 0 = OpenMP default
};

struct Found {
    std::vector<int> seq;               // transform indices in order
    double gain = 0;
    std::vector<double> x;              // batches per move (continuous)
};

struct Stats {
    int64_t nodes = 0, lp_solves = 0, pruned = 0;
    double elapsed = 0;
    bool complete = true;
};

struct Report {
    std::vector<Found> results;
    Stats stats;
};

// ---- LP for one sequence -----------------------------------------------------------------

struct LpOut {
    bool ok = false;
    double gain = 0;
    std::vector<double> x;
};

// Solve  max  M (P - S_n) + Σ w_i inv_n(i)   s.t. S_k <= P (buy steps), inventory >= 0, x >= 0.
// `w` is indexed by dense item id (may be empty = all zero).
LpOut maximize(const Graph& g, const std::vector<int>& seq, const std::vector<double>& w,
               double coin_mult);

// ---- bounds ------------------------------------------------------------------------------

struct Bounds {
    std::vector<std::vector<double>> V;  // V[k][item]
    std::vector<double> M;
};

Bounds compute_bounds(const Graph& g, int max_steps, const std::vector<double>& leftover);

// ---- search ------------------------------------------------------------------------------

Report search(const Graph& g, const Params& p, const std::vector<double>& leftover);

}  // namespace shimcoins
