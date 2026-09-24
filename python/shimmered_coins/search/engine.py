"""Reference tree search over move sequences (pure Python; the C++ core will mirror it).

Tree: the root is an empty inventory, each edge appends one move (a transform). Quantities are
not part of the tree: each node's sequence is scored by the LP in `lp.py`, which picks how many
batches every move uses (so a move may use only part of what is held).

Pruning (disabled with ``exhaustive=True``):
* branch-and-bound with `bounds.py` optimistic values: a subtree is cut when even the most
  optimistic continuation cannot beat the current N-th best gain;
* partial-order reduction: two adjacent moves that don't feed each other are only explored in
  one canonical order (sells first, buys last: selling before an independent buy is never worse
  for capital, and delaying a buy past an independent craft changes nothing);
* each transform appears at most once per sequence (repeating a whole loop is reported via the
  `repeatable` flag instead);
* after the first move, a buy must be able to meet what is already held: something downstream
  of the bought item must also be downstream of the held items (``connected=True``), so
  independent loops aren't glued together.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from ..model.graph import BUY, SELL, Graph, Transform, format_coins
from . import native
from .bounds import Bounds, compute_bounds
from .lp import SequenceLp
from .native import native_search
from .quantize import IntegerPlan, quantize

LEFTOVER_POLICIES = ("discard", "sell")


@dataclass
class SearchParams:
    max_steps: int = 4
    top_n: int = 10
    leftover: str = "discard"  # value of items left at the end: "discard" (0) or "sell"
    min_gain: float = 0.0  # only report / explore sequences that can beat this
    allow_loss: bool = False  # also report the least-bad sequences when nothing is profitable
    must_use: frozenset[int] = frozenset()  # every one of these items must be touched
    start_items: frozenset[int] | None = None  # restrict the first buy to these items
    connected: bool = True
    exhaustive: bool = False
    budget: int | None = None  # copper; scale the integer plan up to this
    max_nodes: int = 500_000
    time_limit: float | None = 60.0
    backend: str = "auto"  # "python", "native" (C++ core) or "auto" (native when installed)
    threads: int = 0  # native backend: OpenMP threads (0 = all cores)


@dataclass
class Move:
    transform: Transform
    batches: int
    text: str


@dataclass
class Result:
    moves: list[Move]
    gain: float  # exact gain of the integer plan (LP gain if no integer plan was found)
    lp_gain: float
    capital: int
    profit: int
    leftover: dict[int, int]
    repeatable: bool

    def summary(self, g: Graph) -> str:
        lines = [
            f"gain {self.gain:+.2%}  (capital {format_coins(self.capital)}, "
            f"profit {format_coins(self.profit)}{', repeatable loop' if self.repeatable else ''})"
        ]
        lines += [f"  {k + 1}. {m.text}" for k, m in enumerate(self.moves)]
        if self.leftover:
            left = ", ".join(f"{c} {g.name(i)}" for i, c in sorted(self.leftover.items()))
            lines.append(f"  leftover: {left}")
        return "\n".join(lines)


@dataclass
class SearchStats:
    nodes: int = 0
    lp_solves: int = 0
    pruned: int = 0
    elapsed: float = 0.0
    complete: bool = True  # False if a node/time limit stopped the search early


@dataclass
class SearchReport:
    results: list[Result]
    stats: SearchStats = field(default_factory=SearchStats)


class _Stop(Exception):
    pass


def leftover_weights(g: Graph, policy: str) -> dict[int, float]:
    if policy not in LEFTOVER_POLICIES:
        raise ValueError(f"leftover policy must be one of {LEFTOVER_POLICIES}")
    if policy == "discard":
        return {}
    return {i: float(t.revenue) for i, t in g.sell_of.items()}


def _independent(a: Transform, b: Transform) -> bool:
    return not (a.output_items & b.input_items) and not (b.output_items & a.input_items)


def _order_key(t: Transform) -> tuple[int, int]:
    return (0 if t.kind == SELL else 2 if t.kind == BUY else 1, t.id)


class _Downstream:
    """Items reachable from an item through craft/shimmer moves (including itself)."""

    def __init__(self, g: Graph):
        self.g = g
        self.memo: dict[int, frozenset[int]] = {}

    def __call__(self, item: int) -> frozenset[int]:
        got = self.memo.get(item)
        if got is None:
            seen = {item}
            todo = [item]
            while todo:
                for t in self.g.consumers.get(todo.pop(), []):
                    for o in t.output_items - seen:
                        seen.add(o)
                        todo.append(o)
            got = self.memo[item] = frozenset(seen)
        return got


def search(g: Graph, params: SearchParams = SearchParams()) -> SearchReport:
    w = leftover_weights(g, params.leftover)
    if params.backend not in ("auto", "python", "native"):
        raise ValueError(f"unknown backend {params.backend!r}")
    use_native = params.backend == "native" or (
        params.backend == "auto" and native.available() and not params.allow_loss
    )
    if use_native:
        if params.allow_loss:
            raise ValueError("allow_loss is only supported by the python backend")
        ranked, stats = native_search(g, params, w)
        results = [_finalize(g, seq, gain, x, w, params.budget) for gain, seq, x in ranked]
        results.sort(key=lambda r: r.gain, reverse=True)
        return SearchReport(results, stats)
    bounds = compute_bounds(g, params.max_steps, w)
    stats = SearchStats()
    downstream = _Downstream(g)
    start = time.monotonic()
    found: dict[tuple[int, ...], tuple[float, list[Transform], np.ndarray]] = {}

    def threshold() -> float:
        gains = sorted((v[0] for v in found.values()), reverse=True)
        base = params.min_gain if not params.allow_loss else -np.inf
        if len(gains) >= params.top_n:
            return max(base, gains[params.top_n - 1])
        return base

    def record(seq: list[Transform], gain: float, x: np.ndarray) -> None:
        if not params.allow_loss and gain <= params.min_gain + 1e-9:
            return
        if (x <= 1e-7 * max(x.max(), 1e-12)).any():
            return  # some move is unused: the shorter sequence covers it
        touched = {i for t in seq for i, _ in t.inputs + t.outputs}
        if not params.must_use <= touched:
            return
        key = tuple(sorted(t.id for t in seq))
        if key not in found or found[key][0] < gain:
            found[key] = (gain, list(seq), x.copy())

    def children(seq: list[Transform], support: frozenset[int]) -> list[Transform]:
        last = seq[-1] if seq else None
        reach: set[int] = set()
        if seq and params.connected:
            for i in support:
                reach |= downstream(i)
        used = {t.id for t in seq}
        remaining = params.max_steps - len(seq)
        cands: dict[int, Transform] = {}
        for item, t in g.buy_of.items():
            users = g.consumers.get(item, [])
            if not users:
                continue
            if not seq:
                if params.start_items is not None and item not in params.start_items:
                    continue
            elif params.connected and not downstream(item) & reach:
                continue
            cands[t.id] = t
        for item in support:
            for t in g.consumers.get(item, []):
                if t.input_items <= support:
                    cands[t.id] = t
            if item in g.sell_of:
                t = g.sell_of[item]
                cands[t.id] = t
        out = []
        for t in cands.values():
            if t.id in used:
                continue
            if last is not None and _independent(last, t) and _order_key(t) < _order_key(last):
                continue
            out.append(t)

        # Explore the most promising moves first so good incumbents appear early.
        def score(t: Transform) -> float:
            if t.kind == BUY:
                return bounds.buy_ratio(t, remaining)
            return 1.0

        out.sort(key=score, reverse=True)
        return out

    def dfs(seq: list[Transform], support: frozenset[int]) -> None:
        stats.nodes += 1
        if stats.nodes > params.max_nodes or (
            params.time_limit is not None and time.monotonic() - start > params.time_limit
        ):
            raise _Stop
        if seq:
            lp = SequenceLp(seq)
            # A trailing move whose result is worthless gets x = 0 in the LP, so the sequence
            # could never be recorded: skip scoring it (a trailing buy, or when leftovers are
            # discarded, any trailing move except a sell).
            last = seq[-1]
            if last.kind == SELL or (last.kind != BUY and params.leftover != "discard"):
                stats.lp_solves += 1
                res = lp.maximize(w)
                if res is not None and res.gain > 1e-7:
                    record(seq, res.gain, res.x)
                elif params.allow_loss:
                    neg = lp.minimize_loss(w)
                    stats.lp_solves += len(lp.A_cap)
                    if neg is not None:
                        record(seq, neg.gain, neg.x)
            if len(seq) >= params.max_steps:
                return
            if not params.exhaustive:
                r = params.max_steps - len(seq)
                stats.lp_solves += 1
                ub = lp.maximize(bounds.V[r], bounds.M[r])
                if ub is None or ub.gain <= threshold() + 1e-9:
                    stats.pruned += 1
                    return
        for t in children(seq, support):
            if not seq and not params.exhaustive:
                if bounds.buy_ratio(t, params.max_steps - 1) - 1.0 <= threshold() + 1e-9:
                    stats.pruned += 1
                    continue
            dfs(seq + [t], support | t.output_items)

    try:
        dfs([], frozenset())
    except _Stop:
        stats.complete = False
    stats.elapsed = time.monotonic() - start

    ranked = sorted(found.values(), key=lambda v: v[0], reverse=True)[: params.top_n]
    results = [_finalize(g, seq, gain, x, w, params.budget) for gain, seq, x in ranked]
    results.sort(key=lambda r: r.gain, reverse=True)
    return SearchReport(results, stats)


def _finalize(
    g: Graph,
    seq: list[Transform],
    lp_gain: float,
    x: np.ndarray,
    w: dict[int, float],
    budget: int | None,
) -> Result:
    plan: IntegerPlan | None = quantize(seq, x, w, budget)
    if plan is None:  # could not find an exact integer plan; report the continuous one
        moves = [Move(t, 0, g.describe(t, float(n))) for t, n in zip(seq, x)]
        cap = int(
            round(float(np.max(np.cumsum([(t.cost - t.revenue) * n for t, n in zip(seq, x)]))))
        )
        return Result(moves, lp_gain, lp_gain, cap, int(round(lp_gain * cap)), {}, False)
    moves = [Move(t, n, g.describe(t, n)) for t, n in zip(seq, plan.batches)]
    repeatable = plan.gain > 0 and not any(t.limited for t in seq)
    return Result(moves, plan.gain, lp_gain, plan.capital, plan.profit, plan.leftover, repeatable)


__all__ = ["Bounds", "Move", "Result", "SearchParams", "SearchReport", "SearchStats", "search"]
