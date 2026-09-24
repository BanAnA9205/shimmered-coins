"""Quantity optimisation for a fixed, ordered sequence of moves.

Given moves t_1..t_n applied x_1..x_n times (continuous, x >= 0):

* inventory never goes negative:   for every step k and item i consumed at step k,
                                   Σ_{j<=k} (out_ij - in_ij) x_j >= 0
* net spend after step k:          S_k = Σ_{j<=k} (cost_j - revenue_j) x_j
* capital (the "initial cost"):    C = max_k S_k   (money you must have up front)
* result:                          R = -S_n + Σ_i w_i inv_n(i)  (w = value of leftovers)
* gain = R / C

The ratio is scale-invariant, so for gain > 0 we fix C <= P (P = largest buy price in the
sequence, keeping x around O(1)) and maximise R: a plain LP. For gain <= 0 we instead fix the
net loss to 1 and maximise the peak spend, one LP per buy step.

`bound=(V, M)` swaps in optimistic terminal values: leftovers worth V_i per unit and coins
worth M each, which turns the same LP into an upper bound for any extension of the sequence.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import linprog

from ..model.graph import Transform

EPS = 1e-9


@dataclass
class LpResult:
    gain: float
    x: np.ndarray  # batches per move (continuous)
    capital: float  # peak spend in copper for this x


class SequenceLp:
    """Matrices for one sequence, reusable for evaluation and bounding."""

    def __init__(self, seq: list[Transform]):
        self.seq = seq
        n = len(seq)
        items = sorted({i for t in seq for i, _ in t.inputs + t.outputs})
        self.items = items
        col = {i: k for k, i in enumerate(items)}
        # net[i, j]: items of `i` produced (+) / consumed (-) by one batch of move j
        net = np.zeros((len(items), n))
        for j, t in enumerate(seq):
            for i, c in t.outputs:
                net[col[i], j] += c
            for i, c in t.inputs:
                net[col[i], j] -= c
        self.net = net
        self.spend = np.array([t.cost - t.revenue for t in seq], dtype=float)
        self.P = float(max((t.cost for t in seq), default=0))

        rows = []
        # inventory rows:  -Σ_{j<=k} net[i, j] x_j <= 0
        for k, t in enumerate(seq):
            for i, _ in t.inputs:
                r = np.zeros(n)
                r[: k + 1] = -net[col[i], : k + 1]
                rows.append(r)
        self.A_inv = np.array(rows).reshape(-1, n)
        cap = []
        for k, t in enumerate(seq):
            if t.cost > 0:
                r = np.zeros(n)
                r[: k + 1] = self.spend[: k + 1]
                cap.append(r)
        self.A_cap = np.array(cap).reshape(-1, n)

    def _terminal(self, weights: dict[int, float]) -> np.ndarray:
        w = np.array([weights.get(i, 0.0) for i in self.items])
        return w @ self.net if len(self.items) else np.zeros(len(self.seq))

    def maximize(self, weights: dict[int, float], coin_mult: float = 1.0) -> LpResult | None:
        """max  M (P - S_n) + Σ w inv_n   s.t.  S_k <= P, inventory >= 0.  gain = obj/P - 1."""
        n = len(self.seq)
        if self.P <= 0:
            return None
        obj = -coin_mult * self.spend + self._terminal(weights)
        A = np.vstack([self.A_inv, self.A_cap])
        b = np.concatenate([np.zeros(len(self.A_inv)), np.full(len(self.A_cap), self.P)])
        res = linprog(-obj, A_ub=A, b_ub=b, bounds=[(0, None)] * n, method="highs")
        if res.status != 0:
            return None
        x = np.maximum(res.x, 0.0)
        value = coin_mult * self.P + obj @ x
        return LpResult(gain=value / self.P - 1.0, x=x, capital=self._capital(x))

    def minimize_loss(self, weights: dict[int, float]) -> LpResult | None:
        """Best (least negative) gain when no positive gain exists."""
        n = len(self.seq)
        result_row = -self.spend + self._terminal(weights)  # R = result_row @ x
        best: LpResult | None = None
        for r in self.A_cap:  # candidate peak step
            res = linprog(
                -r,
                A_ub=self.A_inv if len(self.A_inv) else None,
                b_ub=np.zeros(len(self.A_inv)) if len(self.A_inv) else None,
                A_eq=result_row[None, :],
                b_eq=[-1.0],
                bounds=[(0, None)] * n,
                method="highs",
            )
            if res.status == 3:  # unbounded peak -> loss ratio tends to 0
                return LpResult(gain=0.0, x=np.zeros(n), capital=0.0)
            if res.status != 0:
                continue
            x = np.maximum(res.x, 0.0)
            peak = self._capital(x)
            if peak > EPS:
                cand = LpResult(gain=-1.0 / peak, x=x, capital=peak)
                if best is None or cand.gain > best.gain:
                    best = cand
        return best

    def evaluate(self, weights: dict[int, float]) -> LpResult | None:
        pos = self.maximize(weights)
        if pos is not None and pos.gain > 1e-7:
            return pos
        return self.minimize_loss(weights)

    def _capital(self, x: np.ndarray) -> float:
        return float(np.max(np.cumsum(self.spend * x), initial=0.0))
