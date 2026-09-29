"""The search entry point: table -> free loops -> top-K recipes -> scheduled integer plans.

Phase 1 ranks atomic recipes by gain (``recipes.py``); each recipe is then integerized, ordered
into a tour (``schedule.py``), replayed exactly with ``quantize.simulate`` and scored with
``ln(1 + gain) / (L + c)``. Results are sorted by score.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from fractions import Fraction
from math import lcm

import numpy as np

from ..model.graph import SELL, Graph, Transform, format_coins
from .quantize import simulate
from .recipes import Recipe, Solver, decompose, find_free_loops, top_k
from .schedule import schedule, score, tour_places
from .table import build_table


@dataclass
class SearchParams:
    top: int = 10
    overhead: float = 1.0  # c in the score: fixed effort of running a loop at all
    min_gain: float = 0.0
    allow_loss: bool = False  # also report the least-bad recipes when nothing is profitable
    must_use: frozenset[int] = frozenset()
    start_items: frozenset[int] | None = None
    wallet: int | None = None  # copper: recipes you can't afford are dropped, the rest scaled to it
    max_places: int | None = None  # drop recipes whose tour visits more places
    time_limit: float | None = 30.0


@dataclass
class Move:
    transform: Transform
    batches: int
    text: str


@dataclass
class Result:
    moves: list[Move]
    gain: float  # exact gain of the integer plan (equals lp_gain up to rounding)
    lp_gain: float
    capital: int
    profit: int  # excludes leftovers; gain counts them at sell value
    leftover: dict[int, int]
    places: list[str]
    length: int
    score: float
    per_trip: float  # exp(score): "x m per trip"
    one_shot: bool

    def summary(self, g: Graph) -> str:
        lines = [
            f"gain {self.gain:+.2%}  ×{self.per_trip:.2f} per trip  (L={self.length}: "
            f"{', '.join(self.places)}; capital {format_coins(self.capital)}, "
            f"profit {format_coins(self.profit)}" + (", one-shot" if self.one_shot else "") + ")"
        ]
        lines += [f"  {k + 1}. {m.text}" for k, m in enumerate(self.moves)]
        if self.leftover:
            left = ", ".join(f"{c} {g.name(i)}" for i, c in sorted(self.leftover.items()))
            lines.append(f"  leftover: {left}")
        return "\n".join(lines)


@dataclass
class SearchStats:
    solves: int = 0
    loops: int = 0
    unschedulable: int = 0
    elapsed: float = 0.0
    complete: bool = True


@dataclass
class SearchReport:
    results: list[Result]
    loops: list[list[Move]] = field(default_factory=list)
    stats: SearchStats = field(default_factory=SearchStats)


# Denominator ladder for integerization: the LP vertex has rational ratios whose denominators
# come from recipe batch counts (e.g. 9999 arrows), so 360360 alone can miss them; each rung is
# verified by replaying the plan, so a wrong approximation just falls through to the next rung.
_LADDER = (1, 2, 6, 12, 60, 360, 2520, 27720, 99990, 360360)


def integer_batches(v: np.ndarray, max_den: int = 360360) -> list[int]:
    """Smallest whole-batch counts proportional to the fractional solution."""
    pos = [Fraction(float(x)).limit_denominator(max_den) for x in v if x > 1e-9]
    k = 1
    for f in pos:
        k = lcm(k, f.denominator)
    out = []
    it = iter(pos)
    for x in v:
        out.append(int(next(it) * k) if x > 1e-9 else 0)
    return out


def _finalize(
    g: Graph, table, r: Recipe, params: SearchParams, seller: int | None
) -> Result | None:
    supp_idx = r.support()
    v = r.v[list(supp_idx)]
    price = {item: table.price[k] for item, k in table.item_index.items()}

    for max_den in _LADDER:
        batches0 = integer_batches(v, max_den)
        supp = [table.cols[j] for j, n in zip(supp_idx, batches0) if n > 0]  # drop 0-batch noise
        if not supp:
            continue
        counts0 = [n for n in batches0 if n > 0]
        for scale in (1, 2, 3):  # rounding may still leave consumers short: scale up
            batches = {t.id: n * scale for t, n in zip(supp, counts0)}
            plan_moves = schedule(g, supp, batches)
            if plan_moves is None:
                break  # structural failure (unseedable cycle): scaling won't help
            seq = [t for t, _ in plan_moves]
            counts = [n for _, n in plan_moves]
            plan = simulate(seq, counts, price)
            if plan is not None and plan.capital > 0:
                # The plan must stay faithful to the LP ray: scaling preserves gain, so a big
                # drop means this rung's approximation is feasible garbage -- try the next one.
                drift = r.gain - plan.gain
                if drift > max(0.02, 0.05 * abs(r.gain)):
                    continue
                one_shot = any(t.limited for t, _ in plan_moves)
                if params.wallet is not None and not one_shot:
                    # present repeatable plans at the size of your wallet
                    f = params.wallet // plan.capital
                    if f > 1:
                        plan_moves = [(t, n * f) for t, n in plan_moves]
                        seq = [t for t, _ in plan_moves]
                        counts = [n for _, n in plan_moves]
                        plan = simulate(seq, counts, price)
                        if plan is None:  # pragma: no cover - scaling cannot break a plan
                            return None
                return _result_from(g, supp, plan_moves, plan, r, params, seller)
    return None


def _result_from(g, supp, plan_moves, plan, r, params, seller) -> Result:
    all_places = tour_places(g, supp + [t for t, _ in plan_moves if t not in supp], seller)
    length = len(all_places)
    sellable = {i: c for i, c in plan.leftover.items() if g.sell_unit(i) > 0}
    residue = tuple(sorted(sellable.items()))
    revenue = sum(g.sell_unit(i) * c for i, c in residue)
    moves = [Move(t, n, g.describe(t, n)) for t, n in plan_moves]
    if residue:
        sell = Transform(-1, SELL, residue, (), revenue=revenue)
        moves.append(Move(sell, 1, g.describe(sell, 1)))
    sc = score(plan.gain, length, params.overhead)
    return Result(
        moves=moves,
        gain=plan.gain,
        lp_gain=r.gain,
        capital=plan.capital,
        profit=plan.profit + plan.leftover_value,
        leftover={i: c for i, c in plan.leftover.items() if g.sell_unit(i) <= 0},
        places=all_places,
        length=length,
        score=sc,
        per_trip=math.exp(sc) if math.isfinite(sc) else 0.0,
        one_shot=any(t.limited for t, _ in plan_moves),
    )


def _loop_moves(g: Graph, table, r: Recipe) -> list[Move]:
    supp_idx = r.support()
    supp = [table.cols[j] for j in supp_idx]
    batches = integer_batches(r.v[list(supp_idx)])
    return [Move(t, n, g.describe(t, n)) for t, n in zip(supp, batches) if n > 0]


def search(g: Graph, params: SearchParams = SearchParams()) -> SearchReport:
    start = time.monotonic()
    deadline = start + params.time_limit if params.time_limit is not None else None
    table = build_table(g, params.start_items)
    solver = Solver(table, must_use=params.must_use)
    loops = find_free_loops(solver, deadline)
    # Post-filters (max_places, wallet, min_gain) drop recipes, so candidate batches must be
    # bigger than what is displayed -- otherwise a filter can leave fewer results than exist.
    k = params.top
    if params.max_places is not None or params.wallet is not None or params.min_gain > 0:
        k = min(5 * params.top, 200)
    recipes = top_k(solver, k, deadline)
    if params.must_use:
        # Must-use rows can force a sum of atomic recipes; split it into its parts (4.3.4).
        # The parts are solved without the must rows: the sum already satisfies them.
        plain = Solver(table)
        plain.ban(solver._banned)
        parts: list[Recipe] = []
        for r in recipes:
            parts.extend(decompose(plain, r))
        seen: set[tuple[int, ...]] = set()
        recipes = []
        for r in sorted(parts, key=lambda x: -x.gain):
            s = r.support()
            if s not in seen:
                seen.add(s)
                recipes.append(r)
        recipes = recipes[: params.top]
        solver.solves += plain.solves
    if solver.missing_must:
        recipes = []  # a required item that no column touches can never be satisfied

    seller = g.npcs.happiest_npc() if g.npcs is not None else None
    stats = SearchStats(solves=solver.solves, loops=len(loops))
    results: list[Result] = []
    for r in recipes:
        if not params.allow_loss and r.gain <= params.min_gain + 1e-12:
            continue
        res = _finalize(g, table, r, params, seller)
        if res is None:
            stats.unschedulable += 1
            continue
        if params.max_places is not None and res.length > params.max_places:
            continue
        if params.wallet is not None and res.capital > params.wallet:
            continue  # can't afford the entry ticket
        results.append(res)
    results.sort(key=lambda x: x.score, reverse=True)
    # shape dedup: same tour, same bought and sold goods is the same idea (e.g. the same chain
    # via a different intermediate) -- keep the best-scoring representative
    seen: set[tuple] = set()
    distinct: list[Result] = []
    for r in results:
        sig = _signature(r)
        if sig in seen:
            continue
        seen.add(sig)
        distinct.append(r)
    stats.elapsed = time.monotonic() - start
    stats.complete = deadline is None or stats.elapsed <= deadline + 5.0
    return SearchReport(
        results=distinct[: params.top],
        loops=[_loop_moves(g, table, r) for r in loops],
        stats=stats,
    )


def _signature(r: Result) -> tuple:
    bought = frozenset(
        i for m in r.moves if m.transform.kind == "buy" for i, _ in m.transform.outputs
    )
    sold = frozenset(
        i for m in r.moves if m.transform.kind == "sell" for i, _ in m.transform.inputs
    )
    return (frozenset(r.places), bought, sold)


__all__ = [
    "Move",
    "Result",
    "SearchParams",
    "SearchReport",
    "SearchStats",
    "search",
]
