"""Phase 1 (docs/ALGORITHM.md 3-4): recipes as LP solutions over the table.

A recipe is a column vector ``v >= 0`` with ``N v >= 0`` and ``cost . v > 0``; its gain is
``p'(N v) / (cost . v) - 1``. Fixing ``cost . v = 1`` makes "maximize revenue" a plain LP whose
optimal vertices are the *atomic* recipes. Top-K uses Lawler/Murty ranking: the support of the
best recipe is split into disjoint sub-problems ("element e banned; e_1..e_{i-1} forced in"), each
one LP, all in a priority queue by optimum.

A recipe's identity (its partition *key*) is its moves **and** the items it ends up holding
(``N v > 0``, the residue that is sold). Moves alone are not enough: "buy Ore, shimmer to Gem,
sell Gem" uses a subset of the moves of "buy Ore, shimmer, craft Bar, craft Ring, sell Ring",
so partitioning on moves alone throws the Ring recipe away. With the residue in the key the two
differ (Gem vs Ring is sold), as in elementary flux modes with explicit outflows.
"""

from __future__ import annotations

import heapq
import itertools
import time
from dataclasses import dataclass

import highspy
import numpy as np
import scipy.sparse as sp

from .table import Table

EPS = 1e-6  # "this column is used" lower bound in Lawler sub-problems
_SUPPORT_TOL = 1e-8  # v_t above this counts as used


@dataclass
class Recipe:
    v: np.ndarray  # (n_cols,) fractional batches, cost-normalized
    gain: float
    banned: frozenset[int]  # sub-problem identity: excluded key elements ...
    forced: tuple[int, ...]  # ... and required ones (Lawler/Murty)
    held: tuple[int, ...] = ()  # item rows left over at the end (N v > 0), sold or kept
    split: tuple[int, ...] | None = None  # key of the region optimum, when v was cleaned up

    def support(self) -> tuple[int, ...]:
        v = self.v
        return tuple(np.flatnonzero(v > max(_SUPPORT_TOL, _SUPPORT_TOL * v.max())).tolist())

    def key(self) -> tuple[int, ...]:
        """Partition elements: columns ``j`` and residue rows as ``n_cols + row``."""
        n = len(self.v)
        return self.support() + tuple(n + i for i in self.held)


def residue_rows(N: sp.csc_matrix, v: np.ndarray, price: np.ndarray) -> tuple[int, ...]:
    """Rows with a real leftover, judged by value so cheap and expensive items compare."""
    r = np.asarray(N @ v).ravel()
    scale = max(_SUPPORT_TOL, _SUPPORT_TOL * float(v.max())) if len(v) else _SUPPORT_TOL
    return tuple(np.flatnonzero(r * np.maximum(price, 1.0) > scale).tolist())


def apply_bounds(
    h: highspy.Highs,
    base_ub: np.ndarray,
    price: np.ndarray,
    banned: frozenset[int] | set[int],
    forced: tuple[int, ...],
) -> None:
    """Column and residue-row bounds for one Lawler region (rows 0..m-1 are ``-N v <= 0``)."""
    n, m = len(base_ub), len(price)
    lb = np.zeros(n)
    ub = base_ub.copy()
    lo = np.full(m, -highspy.kHighsInf)
    hi = np.zeros(m)
    for e in banned:
        if e < n:
            ub[e] = 0.0
        else:
            lo[e - n] = 0.0  # nothing of this item may be left over
    for e in forced:
        if e < n:
            lb[e] = EPS
        else:
            # some of it must be left over: EPS copper's worth, so the bound means the same
            # for a 1-copper and a 1-platinum item
            hi[e - n] = -EPS / max(float(price[e - n]), 1.0)
    h.changeColsBounds(n, list(range(n)), lb.tolist(), ub.tolist())
    h.changeRowsBounds(m, list(range(m)), lo.tolist(), hi.tolist())


def lawler(solve, polish, k: int, deadline: float | None, stop_gain: float | None = None):
    """Best-first Lawler/Murty ranking over region optima, deduplicated after ``polish``.

    ``solve(banned, forced)`` returns a region's optimum (or None); ``polish(r)`` the recipe to
    report for it. Regions are always split on the optimum's own key, even when its polished
    form duplicates an earlier result -- skipping the split would lose the whole region.
    """
    first = solve(frozenset(), ())
    if first is None or not first.support():
        return []
    counter = itertools.count()
    heap: list[tuple[float, int, Recipe]] = [(-first.gain, next(counter), first)]
    out: list[Recipe] = []
    seen: set[tuple[int, ...]] = set()
    pops = 0
    while heap and len(out) < k and pops < 50 * k + 100:
        if deadline is not None and time.monotonic() > deadline:
            break
        _, _, r = heapq.heappop(heap)
        pops += 1
        if stop_gain is not None and r.gain <= stop_gain:
            break  # best-first: nothing left anywhere beats the floor
        shown = polish(r)
        if shown.key() not in seen and shown.support():
            seen.add(shown.key())
            out.append(shown)
        key = r.key()
        for i, e in enumerate(key):
            if deadline is not None and time.monotonic() > deadline:
                break
            child = solve(r.banned | {e}, r.forced + key[:i])
            if child is not None and child.support():
                heapq.heappush(heap, (-child.gain, next(counter), child))
    return out


class Solver:
    """One HiGHS model over the table, re-solved with different column bounds (warm-started)."""

    def __init__(self, table: Table, must_use: frozenset[int] = frozenset()):
        self.t = table
        self.rev = table.revenue()
        self.n = len(table.cols)
        self.solves = 0
        self._banned: set[int] = set()
        inf = highspy.kHighsInf
        self.base_ub = np.full(self.n, inf, dtype=float)
        h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        h.addVars(self.n, [0.0] * self.n, self.base_ub.tolist())
        h.changeColsCost(self.n, list(range(self.n)), (-self.rev).tolist())
        inv = (-table.N).tocsr()  # N v >= 0  ->  -N v <= 0
        h.addRows(
            inv.shape[0],
            [-inf] * inv.shape[0],
            [0.0] * inv.shape[0],
            inv.nnz,
            inv.indptr.tolist(),
            inv.indices.tolist(),
            inv.data.tolist(),
        )
        cost = sp.csr_matrix(table.cost.reshape(1, -1))
        h.addRows(
            1,
            [1.0],
            [1.0],
            cost.nnz,
            cost.indptr.tolist(),
            cost.indices.tolist(),
            cost.data.tolist(),
        )
        # must-use rows (4.4): at least one column touching the item must be used
        self.missing_must = frozenset(m for m in must_use if m not in table.item_index)
        for item in must_use & table.item_index.keys():
            cols = [j for j in range(self.n) if table.N[table.item_index[item], j] != 0]
            h.addRows(1, [EPS], [highspy.kHighsInf], len(cols), [0], cols, [1.0] * len(cols))
        self.h = h

    def ban(self, cols) -> None:
        """Permanently forbid columns (free-money loop supports, 4.1)."""
        self._banned.update(cols)

    def solve(self, banned=(), forced=()) -> Recipe | None:
        """The optimum of one Lawler region (``banned``/``forced`` are key elements)."""
        banned = frozenset(self._banned | set(banned))
        apply_bounds(self.h, self.base_ub, self.t.price, banned, tuple(forced))
        self.h.run()
        self.solves += 1
        if self.h.modelStatusToString(self.h.getModelStatus()).lower() != "optimal":
            return None
        v = np.maximum(np.array(self.h.getSolution().col_value, dtype=float), 0.0)
        return Recipe(
            v=v,
            gain=float(self.rev @ v - 1.0),
            banned=banned,
            forced=tuple(forced),
            held=residue_rows(self.t.N, v, self.t.price),
        )

    def polish(self, r: Recipe) -> Recipe:
        """Drop the EPS "is used" artifacts of a region optimum (docs/ALGORITHM.md 4.3).

        Forced columns sitting at their EPS lower bound are there only because the region
        demands them. The recipe actually meant is the best one inside the optimum's own
        moves without them; it is what gets reported, while the region is still split on the
        optimum itself."""
        return polish(self, r)


def _at_eps(N: sp.csc_matrix, price: np.ndarray, r: Recipe) -> tuple[set[int], bool]:
    """Forced columns at their EPS bound, and whether any forced residue sits at its bound."""
    n = len(r.v)
    cols = {j for j in r.forced if j < n and abs(r.v[j] - EPS) <= max(1e-12, 1e-6 * EPS)}
    res = np.asarray(N @ r.v).ravel()
    at_row = False
    for e in r.forced:
        if e >= n:
            bound = EPS / max(float(price[e - n]), 1.0)
            at_row |= abs(res[e - n] - bound) <= max(1e-15, 1e-6 * bound)
    return cols, at_row


def polish(solver, r: Recipe) -> Recipe:
    """Shared by both solvers: re-solve inside the optimum's own moves without the artifacts."""
    artifact, row_artifact = _at_eps(solver.t.N, solver.t.price, r)
    if not artifact and not row_artifact:
        return r
    outside = set(range(solver.n)) - (set(r.support()) - artifact)
    clean = solver.solve(banned=outside)
    if clean is None or not clean.support():
        return r
    return Recipe(
        v=clean.v,
        gain=clean.gain,
        banned=r.banned,
        forced=r.forced,
        held=clean.held,
        split=r.key(),
    )


def _loop_model(t: Table, banned: set[int], rev: np.ndarray) -> highspy.Highs:
    """max rev.v  s.t.  N v >= 0, cost.v = 0, sum v <= 1, banned columns fixed to 0."""
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    inf = highspy.kHighsInf
    n = len(t.cols)
    ub = [0.0 if j in banned else inf for j in range(n)]
    h.addVars(n, [0.0] * n, ub)
    h.changeColsCost(n, list(range(n)), (-rev).tolist())
    inv = (-t.N).tocsr()
    h.addRows(
        inv.shape[0],
        [-inf] * inv.shape[0],
        [0.0] * inv.shape[0],
        inv.nnz,
        inv.indptr.tolist(),
        inv.indices.tolist(),
        inv.data.tolist(),
    )
    cost0 = sp.csr_matrix(t.cost.reshape(1, -1))
    h.addRows(
        1,
        [0.0],
        [0.0],
        cost0.nnz,
        cost0.indptr.tolist(),
        cost0.indices.tolist(),
        cost0.data.tolist(),
    )
    h.addRows(1, [-inf], [1.0], n, [0], list(range(n)), [1.0] * n)
    return h


def find_free_loops(solver: Solver, deadline: float | None = None) -> list[Recipe]:
    """4.1: zero-cost item-growing cycles (gain would be infinite). Bans each loop's support."""
    t = solver.t
    rev = solver.rev
    loops: list[Recipe] = []
    banned: set[int] = set()
    for _ in range(50):
        if deadline is not None and time.monotonic() > deadline:
            break
        h = _loop_model(t, banned, rev)
        h.run()
        solver.solves += 1
        if h.modelStatusToString(h.getModelStatus()).lower() != "optimal":
            break
        v = np.maximum(np.array(h.getSolution().col_value, dtype=float), 0.0)
        if float(rev @ v) <= 1e-6:
            break
        r = Recipe(v=v, gain=float("inf"), banned=frozenset(banned), forced=())
        supp = r.support()
        if not supp:  # pragma: no cover - defensive
            break
        loops.append(r)
        banned.update(supp)
    solver.ban(banned)
    return loops


def top_k(solver: Solver, k: int, deadline: float | None = None) -> list[Recipe]:
    """4.2-4.3: the k best recipes by gain, Lawler/Murty ranked, deduplicated by key."""
    if k <= 0 or solver.n == 0:
        return []
    return lawler(solver.solve, solver.polish, k, deadline)


def decompose(solver: Solver, w: Recipe) -> list[Recipe]:
    """4.3.4: split a (possibly non-atomic) recipe into its atomic parts.

    Must-use constraints can force a sum of atomic recipes; the LP then returns a solution whose
    support contains smaller recipes. Each round solves the LP restricted to the remaining
    support (a vertex = one atomic part) and subtracts as much of it as fits.
    """
    parts: list[Recipe] = []
    remaining = w.v.copy()
    all_cols = set(range(solver.n))
    for _ in range(solver.n + 1):  # each round removes >= 1 column from the support
        supp = np.flatnonzero(remaining > _SUPPORT_TOL)
        if not len(supp):
            break
        part = solver.solve(banned=all_cols - set(supp.tolist()))
        if part is None or not part.support():
            break
        alpha = min(remaining[j] / part.v[j] for j in part.support())
        parts.append(Recipe(v=part.v * alpha, gain=part.gain, banned=w.banned, forced=w.forced))
        remaining = remaining - part.v * alpha
        remaining[remaining < 0] = 0.0
    return parts
