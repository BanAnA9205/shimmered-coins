"""Optimistic per-unit values used for branch-and-bound.

V[k][i]: an upper bound on the copper obtainable from one unit of item i using at most k more
moves. M[k]: an upper bound on how many coins one coin can become in k moves.

  V[0][i] = leftover value of i (0 when leftovers are discarded)
  M[0]    = 1
  V[k][i] = max( V[k-1][i],
                 M[k-1] * sell(i),
                 max over moves t consuming c_i of i:
                     (Σ_out c_o V[k-1][o] + M[k-1] * revenue_t) / c_i )
  M[k]    = max( M[k-1], max over buyable i: V[k-1][i] / price(i) )

The per-move term charges the whole move's output to each input separately (co-ingredients are
treated as free), so it can only overestimate. That keeps the bound valid for multi-input
crafts at the cost of some tightness.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..model.graph import BUY, SELL, Graph, Transform


@dataclass
class Bounds:
    V: list[dict[int, float]]
    M: list[float]

    def buy_ratio(self, t: Transform, k: int) -> float:
        """Upper bound on coins returned per coin spent buying via `t` with k moves after it."""
        if k < 0:
            return 0.0
        item = t.outputs[0][0]
        return self.V[k].get(item, 0.0) / t.cost


def compute_bounds(g: Graph, max_steps: int, leftover: dict[int, float]) -> Bounds:
    V: list[dict[int, float]] = [dict(leftover)]
    M: list[float] = [1.0]
    for _ in range(max_steps):
        pv, pm = V[-1], M[-1]
        cur = dict(pv)

        def bump(item: int, val: float, cur: dict[int, float] = cur) -> None:
            if val > cur.get(item, 0.0):
                cur[item] = val

        for t in g.transforms:
            if t.kind == BUY:
                continue
            if t.kind == SELL:
                bump(t.inputs[0][0], pm * t.revenue)
                continue
            out = sum(c * pv.get(o, 0.0) for o, c in t.outputs) + pm * t.revenue
            for i, c in t.inputs:
                bump(i, out / c)
        m = pm
        for item, t in g.buy_of.items():
            m = max(m, pv.get(item, 0.0) / t.cost)
        V.append(cur)
        M.append(m)
    return Bounds(V, M)
