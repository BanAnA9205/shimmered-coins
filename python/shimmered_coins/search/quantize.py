"""Turn continuous LP batch counts into whole batches with exact copper accounting."""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

import numpy as np

from ..model.graph import Transform


@dataclass
class IntegerPlan:
    batches: list[int]
    capital: int  # copper needed up front (peak spend)
    profit: int  # copper at the end minus copper at the start (leftovers excluded)
    leftover: dict[int, int]  # item -> count left in inventory
    leftover_value: int  # copper value credited for leftovers (per leftover policy)

    @property
    def gain(self) -> float:
        return (self.profit + self.leftover_value) / self.capital if self.capital else 0.0


def simulate(
    seq: list[Transform], batches: list[int], weights: dict[int, float]
) -> IntegerPlan | None:
    """Exact integer replay; None if the inventory would ever go negative."""
    inv: dict[int, int] = {}
    spent = peak = 0
    for t, n in zip(seq, batches):
        for i, c in t.inputs:
            inv[i] = inv.get(i, 0) - c * n
            if inv[i] < 0:
                return None
        for i, c in t.outputs:
            inv[i] = inv.get(i, 0) + c * n
        spent += (t.cost - t.revenue) * n
        peak = max(peak, spent)
    left = {i: c for i, c in inv.items() if c > 0}
    lv = int(sum(weights.get(i, 0) * c for i, c in left.items()))
    return IntegerPlan(list(batches), peak, -spent, left, lv)


def quantize(
    seq: list[Transform],
    x: np.ndarray,
    weights: dict[int, float],
    budget: int | None = None,
) -> IntegerPlan | None:
    """Smallest whole-batch plan proportional to `x` (then scaled up to `budget` if given)."""
    pos = x[x > 1e-9]
    if not len(pos):
        return None
    ratios = x / pos.min()
    best: IntegerPlan | None = None
    for max_den in (1, 2, 6, 12, 60, 360, 2520, 27720):
        fr = [
            Fraction(float(r)).limit_denominator(max_den) if r > 1e-9 else Fraction(0)
            for r in ratios
        ]
        k = math.lcm(*(f.denominator for f in fr))
        batches = [int(f * k) for f in fr]
        plan = simulate(seq, batches, weights)
        if plan is None or plan.capital <= 0:
            continue
        if best is None or plan.gain > best.gain + 1e-12:
            best = plan
        if max(abs(r - float(f)) for r, f in zip(ratios, fr)) < 1e-6:
            break  # exact representation found
    if best is not None and budget:
        f = budget // best.capital
        if f > 1:
            best = simulate(seq, [b * f for b in best.batches], weights)
    return best
