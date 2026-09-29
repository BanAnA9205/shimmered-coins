"""Phase 1 (docs/ALGORITHM.md 3-4): recipes as LP solutions over the table.

A recipe is a column vector ``v >= 0`` with ``N v >= 0`` and ``cost . v > 0``; its gain is
``p'(N v) / (cost . v) - 1``. Fixing ``cost . v = 1`` makes "maximize revenue" a plain LP whose
optimal vertices are the *atomic* recipes. Top-K uses Lawler/Murty ranking: the support of the
best recipe is split into disjoint sub-problems ("column t banned; t_1..t_{i-1} forced in"), each
one LP, all in a priority queue by optimum.
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
    banned: frozenset[int]  # sub-problem identity: excluded columns ...
    forced: tuple[int, ...]  # ... and required ones (Lawler/Murty)

    def support(self) -> tuple[int, ...]:
        v = self.v
        return tuple(np.flatnonzero(v > max(_SUPPORT_TOL, _SUPPORT_TOL * v.max())).tolist())


class Solver:
    """One HiGHS model over the table, re-solved with different column bounds (warm-started)."""

    def __init__(self, table: Table, must_use: frozenset[int] = frozenset()):
        self.t = table
        self.rev = table.revenue()
        self.n = len(table.cols)
        self.solves = 0
        self._banned: set[int] = set()
        inf = highspy.kHighsInf
        self.base_ub = np.array([1.0 if c.limited else inf for c in table.cols], dtype=float)
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
        r = self._solve_once(self._banned | set(banned), tuple(forced))
        if r is None:
            return None
        # Forced columns sitting at their EPS lower bound are artifacts of the "column is
        # used" constraint: the recipe does not really want them (docs/ALGORITHM.md 4.3's
        # epsilon). Re-solve without them to get the region's true best ray -- otherwise the
        # same recipe comes back with epsilon pollution and crowds out the next distinct one.
        artifact = [j for j in r.forced if abs(r.v[j] - EPS) <= max(1e-12, 1e-6 * EPS)]
        if not artifact:
            return r
        forced2 = tuple(j for j in r.forced if j not in artifact)
        clean = self._solve_once(r.banned | set(artifact), forced2)
        if clean is None:
            return r
        return clean

    def _solve_once(self, banned: frozenset[int], forced: tuple[int, ...]) -> Recipe | None:
        lb = np.zeros(self.n)
        ub = self.base_ub.copy()
        if banned:
            ub[sorted(banned)] = 0.0
        if forced:
            lb[sorted(forced)] = EPS
        self.h.changeColsBounds(self.n, list(range(self.n)), lb.tolist(), ub.tolist())
        self.h.run()
        self.solves += 1
        if self.h.modelStatusToString(self.h.getModelStatus()).lower() != "optimal":
            return None
        v = np.maximum(np.array(self.h.getSolution().col_value, dtype=float), 0.0)
        return Recipe(v=v, gain=float(self.rev @ v - 1.0), banned=banned, forced=forced)


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
    """4.2-4.3: the k best recipes by gain, Lawler/Murty ranked, deduplicated by support."""
    if k <= 0 or solver.n == 0:
        return []
    first = solver.solve()
    if first is None or not first.support():
        return []
    counter = itertools.count()
    heap: list[tuple[float, int, Recipe]] = [(-first.gain, next(counter), first)]
    out: list[Recipe] = []
    seen: set[tuple[int, ...]] = set()
    while heap and len(out) < k:
        if deadline is not None and time.monotonic() > deadline:
            break
        _, _, r = heapq.heappop(heap)
        supp = r.support()
        if supp in seen:
            continue
        seen.add(supp)
        out.append(r)
        for i, t in enumerate(supp):
            if deadline is not None and time.monotonic() > deadline:
                break
            child = solver.solve(banned=r.banned | {t}, forced=r.forced + tuple(supp[:i]))
            if child is None or not child.support():
                continue
            heapq.heappush(heap, (-child.gain, next(counter), child))
    return out


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
