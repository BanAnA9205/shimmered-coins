// Dense primal simplex for the tiny per-sequence LPs.
//
// Every constraint has the form  a·x <= b  with b >= 0 (inventory rows have b = 0, capital rows
// b = P), so x = 0 is a feasible basis and no phase 1 is needed. Many rows are degenerate
// (b = 0), so Bland's rule is used to guarantee termination.
#include <algorithm>
#include <cmath>
#include <limits>
#include <unordered_map>

#include "shimcoins/core.hpp"

namespace shimcoins {

namespace {

constexpr double EPS = 1e-9;

// max c·x  s.t.  A x <= b, x >= 0, with b >= 0.  Returns false if unbounded / iteration cap.
bool simplex(std::vector<std::vector<double>> A, std::vector<double> b, const std::vector<double>& c,
             std::vector<double>& x) {
    const size_t m = A.size(), n = c.size();
    // tableau: m rows of [A | I | b], objective row z (reduced costs) = -c
    const size_t cols = n + m;
    std::vector<std::vector<double>> T(m, std::vector<double>(cols + 1, 0.0));
    for (size_t i = 0; i < m; ++i) {
        for (size_t j = 0; j < n; ++j) T[i][j] = A[i][j];
        T[i][n + i] = 1.0;
        T[i][cols] = b[i];
    }
    std::vector<double> z(cols + 1, 0.0);
    for (size_t j = 0; j < n; ++j) z[j] = -c[j];
    std::vector<size_t> basis(m);
    for (size_t i = 0; i < m; ++i) basis[i] = n + i;

    for (int iter = 0; iter < 10000; ++iter) {
        size_t enter = cols;
        for (size_t j = 0; j < cols; ++j)
            if (z[j] < -EPS) { enter = j; break; }  // Bland: lowest index
        if (enter == cols) break;                    // optimal
        size_t leave = m;
        double best = std::numeric_limits<double>::infinity();
        for (size_t i = 0; i < m; ++i) {
            if (T[i][enter] > EPS) {
                double r = T[i][cols] / T[i][enter];
                if (r < best - EPS || (r < best + EPS && leave < m && basis[i] < basis[leave])) {
                    best = r;
                    leave = i;
                }
            }
        }
        if (leave == m) return false;  // unbounded
        const double piv = T[leave][enter];
        for (auto& v : T[leave]) v /= piv;
        for (size_t i = 0; i < m; ++i) {
            if (i == leave) continue;
            const double f = T[i][enter];
            if (f != 0.0)
                for (size_t j = 0; j <= cols; ++j) T[i][j] -= f * T[leave][j];
        }
        const double f = z[enter];
        for (size_t j = 0; j <= cols; ++j) z[j] -= f * T[leave][j];
        basis[leave] = enter;
        if (iter == 9999) return false;
    }
    x.assign(n, 0.0);
    for (size_t i = 0; i < m; ++i)
        if (basis[i] < n) x[basis[i]] = std::max(0.0, T[i][cols]);
    return true;
}

}  // namespace

LpOut maximize(const Graph& g, const std::vector<int>& seq, const std::vector<double>& w,
               double coin_mult) {
    LpOut out;
    const size_t n = seq.size();
    double P = 0;
    for (int t : seq) P = std::max(P, g.ts[t].cost);
    if (P <= 0 || n == 0) return out;

    // local item index
    std::unordered_map<int, int> local;
    for (int t : seq) {
        for (auto& s : g.ts[t].in) local.emplace(s.first, (int)local.size());
        for (auto& s : g.ts[t].out) local.emplace(s.first, (int)local.size());
    }
    std::vector<std::vector<double>> net(local.size(), std::vector<double>(n, 0.0));
    std::vector<double> spend(n);
    for (size_t j = 0; j < n; ++j) {
        const auto& t = g.ts[seq[j]];
        for (auto& s : t.out) net[local[s.first]][j] += s.second;
        for (auto& s : t.in) net[local[s.first]][j] -= s.second;
        spend[j] = (t.cost - t.revenue) / P;  // normalised so capital rows read  S_k/P <= 1
    }

    std::vector<std::vector<double>> A;
    std::vector<double> b;
    for (size_t k = 0; k < n; ++k) {
        const auto& t = g.ts[seq[k]];
        for (auto& s : t.in) {
            std::vector<double> row(n, 0.0);
            const auto& nr = net[local[s.first]];
            for (size_t j = 0; j <= k; ++j) row[j] = -nr[j];
            A.push_back(std::move(row));
            b.push_back(0.0);
        }
        if (t.cost > 0) {
            std::vector<double> row(n, 0.0);
            for (size_t j = 0; j <= k; ++j) row[j] = spend[j];
            A.push_back(std::move(row));
            b.push_back(1.0);
        }
    }

    // objective (in units of P):  -M * spend + Σ_i w_i net_i / P
    std::vector<double> c(n);
    for (size_t j = 0; j < n; ++j) c[j] = -coin_mult * spend[j];
    if (!w.empty())
        for (auto& [item, li] : local) {
            const double wi = item < (int)w.size() ? w[item] : 0.0;
            if (wi != 0.0)
                for (size_t j = 0; j < n; ++j) c[j] += wi * net[li][j] / P;
        }

    std::vector<double> x;
    if (!simplex(std::move(A), std::move(b), c, x)) return out;
    double value = coin_mult;
    for (size_t j = 0; j < n; ++j) value += c[j] * x[j];
    out.ok = true;
    out.gain = value - 1.0;
    out.x = std::move(x);  // batches per move, for capital P (copper)
    return out;
}

}  // namespace shimcoins
