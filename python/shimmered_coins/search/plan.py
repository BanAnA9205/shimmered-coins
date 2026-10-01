"""The "plan" search: what to do with what you have (the pivot search).

Sources become extra table columns around the money engine's move columns:

- **Stock** (items already held, e.g. everything in the world's chests): one zero-cost
  column per item, bounded by how many are held. Which items to include is the user's
  choice (the UI lists them per world; excluded items are dropped from the mix).
- **Buy**: the shop columns of the money engine, budgeted by ``wallet`` copper.
- **Farm**: one expected-value column per farmable NPC (per-kill EV from ``npc_drops``),
  bounded by ``kills`` total expected effort. Drop entries are gated to the chosen
  difficulty; unknown/event conditions are skipped conservatively.

Objective: **maximize profit** — sellable residue value minus purchases — with the wallet as
a budget row. Not a ratio: owned and farmed items have no coin cost to normalize over.
Free-money loops among craft/shimmer columns (stock/farm excluded: their value is bounded,
not infinite) are detected and banned as in the money engine.

Because owned items can reach crafts the shop economy never could ("use your Chlorophyte
bars"), the *graph* must not prune those columns away: seed ``build_graph(known_items=...)``
with ``plan_known_items`` before calling :func:`plan`.

Plans whose whole support is stock columns (just sell owned things) are hidden by default;
the combined "sell everything" figure is reported once as the ``liquidation`` floor.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from math import ceil

import highspy
import numpy as np
import scipy.sparse as sp

from ..model.graph import BUY, CRAFT, SHIMMER, Graph, Transform, format_coins
from .engine import integer_batches
from .quantize import simulate
from .recipes import (
    EPS,
    Recipe,
    apply_bounds,
    find_free_loops,
    lawler,
    polish,
    residue_rows,
)
from .schedule import order_pool_moves, tour_places
from .table import Table

FARM, STOCK = "farm", "stock"
_DEFAULT_MAX_DEN = 360360
_BATCH_CHARGE = 1e-6  # copper per batch in the LP objective only (see PlanSolver)


@dataclass
class PlanParams:
    top: int = 10
    wallet: int = 0  # copper you are willing to spend on purchases
    kills: int = 0  # expected farm effort budget; 0 = the "with farming" box is off
    expert: bool = False  # gates IsExpert / NotExpert drop entries
    exclude_items: frozenset[int] = frozenset()  # stock items the user unchecked
    must_use: frozenset[int] = frozenset()
    min_profit: float = 0.0  # copper: hide plans that make less than this
    show_liquidation: bool = False  # also list "sell your X" plans
    time_limit: float | None = 30.0


@dataclass
class PlanResult:
    moves: list[tuple[Transform, int]]
    profit: int  # residue value minus purchases, copper
    capital: int  # coins actually spent
    kills: int  # total expected farm effort (summed per npc, rounded up)
    approx: bool  # farm EVs are expected values: exact drop counts vary
    support: tuple[int, ...]
    places: list[str]
    residue: dict[int, float]  # item -> amount left (sold where sellable)

    def summary(self, g: Graph) -> str:
        bits = f"profit {format_coins(self.profit)} (spend {format_coins(self.capital)}"
        if self.kills:
            bits += f", ~{self.kills} kills"
        if self.approx:
            bits += "; farmed amounts are expected values"
        bits += ")"
        lines = [bits]
        lines += [f"  {k + 1}. {g.describe(t, n)}" for k, (t, n) in enumerate(self.moves)]
        if self.residue:
            left = ", ".join(
                f"{int(c) if float(c).is_integer() else format(c, '.3g')} {g.name(i)}"
                for i, c in sorted(self.residue.items())
            )
            lines.append(f"  residue (sold where sellable): {left}")
        return "\n".join(lines)


@dataclass
class PlanReport:
    results: list[PlanResult]
    liquidation: int  # copper from selling everything not excluded; the do-nothing floor
    stock: dict[int, int]  # item -> how many are held (after exclusions)
    loops: list[Recipe] = field(default_factory=list)
    solves: int = 0
    complete: bool = True
    elapsed: float = 0.0
    # every result at once, sharing the same stock, wallet and kill budget (when that beats
    # the best single result): "do all of this"
    combined: PlanResult | None = None


# --------------------------------------------------------------------------- sources --------


def stock_from_world(world, exclude: frozenset[int] = frozenset()) -> dict[int, int]:
    """Every item lying in any chest of a parsed world, counted (prefixes ignored)."""
    out: dict[int, int] = {}
    for chest in world.chests:
        for iid, _prefix, stack in chest.items:
            if iid in exclude:
                continue
            out[iid] = out.get(iid, 0) + stack
    return out


def farm_sources(g: Graph, expert: bool) -> list[tuple[int, list[tuple[int, float]]]]:
    """(npc, [(item, per-kill EV)]) for every farmable NPC, gated by difficulty.

    ``npc <= 0`` entries (fishing crates, oysters, presents) are skipped: those are
    item-open contexts, not farmable enemies.
    """
    out: list[tuple[int, list[tuple[int, float]]]] = []
    for npc, lst in g.dataset.drops:
        if npc <= 0:
            continue
        ev: dict[int, float] = {}
        used = False
        for d in lst:
            if not _drop_holds(d.conditions, expert):
                continue
            used = True
            ev[d.item] = ev.get(d.item, 0.0) + d.rate * (d.min + d.max) / 2
        if used and ev:
            out.append((npc, sorted(ev.items())))
    return out


def _drop_holds(conds: tuple[str, ...], expert: bool) -> bool:
    """Difficulty gating; unknown conditions (moon gating, events, seeds, ...) are skipped."""
    for c in conds:
        if c == "IsExpert":
            if not expert:
                return False
        elif c == "NotExpert":
            if expert:
                return False
        elif c in ("NotFromStatue", "LegacyHack_IsBossAndNotExpert"):
            continue  # statue spawns and boss bookkeeping don't change natural drops
        else:
            return False
    return True


def plan_known_items(
    stock: dict[int, int], farms: list[tuple[int, list[tuple[int, float]]]]
) -> frozenset[int]:
    """Items that seed the graph's forward reachability so crafts only stock or farm
    can feed survive pruning (no shop ever sells them)."""
    items = set(stock)
    for _, ev in farms:
        items.update(i for i, _ in ev)
    return frozenset(items)


# --------------------------------------------------------------------------- table ----------


def _matrix(cols: list[Transform], items: list[int]) -> sp.csc_matrix:
    index = {i: k for k, i in enumerate(items)}
    rows, cols_idx, data = [], [], []
    for c, t in enumerate(cols):
        acc: dict[int, float] = {}
        for i, k in t.inputs:
            acc[index[i]] = acc.get(index[i], 0) - float(k)
        for i, k in t.outputs:
            acc[index[i]] = acc.get(index[i], 0) + float(k)
        for r, v in acc.items():
            if v:
                rows.append(r)
                cols_idx.append(c)
                data.append(v)
    return sp.csc_matrix((data, (rows, cols_idx)), shape=(len(items), len(cols)))


def build_plan_table(
    g: Graph,
    stock: dict[int, int],
    farms: list[tuple[int, list[tuple[int, float]]]],
    kills: int,
) -> Table:
    """Money move columns plus stock and farm source columns; no pruning (the graph did it)."""
    cols: list[Transform] = [t for t in g.transforms if t.kind in (BUY, CRAFT, SHIMMER)]
    for item in sorted(stock):
        cols.append(Transform(0, STOCK, (), ((item, 1),), ref=f"stock:{item}"))
    for npc, ev in farms:
        cols.append(Transform(0, FARM, (), tuple(ev), ref=f"farm:{npc}", npc=npc))

    items = sorted({i for t in cols for i, _n in t.inputs + t.outputs})
    price = np.zeros(len(items))
    row_of = {i: k for k, i in enumerate(items)}
    for item, t in g.sell_of.items():
        if item in row_of:
            price[row_of[item]] = t.revenue
    # normalization vector: what one batch ties up. Buys tie up their price; consuming a
    # held item ties up its sell value (an opportunity cost -- the quantity the money
    # engine normalizes by, keeping LP vertices atomic); farm batches tie up nothing, so
    # pure "farm and liquidate" supports cannot satisfy the row (their profit IS the wage).
    used = np.array([_tied_value(t, row_of, price) for t in cols], dtype=float)
    return Table(
        N=_matrix(cols, items),
        cost=np.array([float(t.cost) for t in cols]),
        price=price,
        cols=cols,  # type: ignore[arg-type]  # stock/farm are plan-only kinds
        item_index=row_of,
        plan_used=used,
    )


def _tied_value(t: Transform, row_of: dict[int, int], price: np.ndarray) -> float:
    """Value one batch ties up, plus one unit of effort. Buys tie up their price, using a
    held item ties up its sell value (opportunity cost), farming ties up its effort. The +1
    per batch keeps every column normalizable (chains rooted in unsellable junk price their
    inputs at 0) and matches the plan mode's effort-in-action-units doc; it only orders
    candidates inside the partition - the reported metric stays profit."""
    if t.kind == STOCK:
        return 1.0 + float(price[row_of[int(t.ref.split(":", 1)[1])]])
    if t.kind == FARM:
        return 1.0
    tied = 1.0 + float(t.cost)
    for i, k in t.inputs:
        tied += float(k) * float(price[row_of[i]])
    return tied


class _LoopContext:
    """The solver surface ``find_free_loops`` needs, over a stock/farm-free table."""

    def __init__(self, table: Table):
        self.t = table
        self.rev = table.revenue()
        self.solves = 0
        self.banned: set[int] = set()

    def ban(self, cols) -> None:
        self.banned.update(cols)


def _loop_table(g: Graph) -> Table:
    """Craft/shimmer columns only: loop detection must not see stock or farm columns --
    their value is bounded (by what you hold / the kill budget), so it is not free money."""
    cols = [t for t in g.transforms if t.kind in (CRAFT, SHIMMER)]
    items = sorted({i for t in cols for i, _n in t.inputs + t.outputs})
    price = np.zeros(len(items))
    row_of = {i: k for k, i in enumerate(items)}
    for item, t in g.sell_of.items():
        if item in row_of:
            price[row_of[item]] = t.revenue
    return Table(
        N=_matrix(cols, items),
        cost=np.zeros(len(cols)),
        price=price,
        cols=cols,  # type: ignore[arg-type]
        item_index=row_of,
    )


# --------------------------------------------------------------------------- solver ---------


class PlanSolver:
    """max-profit LP with the wallet as a budget row, re-solved for Lawler partitions."""

    def __init__(
        self,
        table: Table,
        ubs: np.ndarray,
        wallet: int,
        must_use: frozenset[int] = frozenset(),
    ):
        self.t = table
        self.n = len(table.cols)
        self.profit = table.revenue() - table.cost
        self.solves = 0
        inf = highspy.kHighsInf
        h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        self.h = h
        self.base_ub = ubs.astype(float)
        # Normalize by the value each batch ties up (opportunity cost of consumed items
        # plus one unit of effort): with ``used . v = 1`` LP optima sit at vertices, which
        # are the *atomic* recipes -- without it, max-profit packs hundreds of unrelated
        # columns into one giant sum (observed: a 692-column optimum). The ranking value is
        # profit per tied value; the user-facing metric remains raw profit (finalized).
        self.used = np.asarray(table.plan_used, dtype=float)
        h.addVars(self.n, [0.0] * self.n, self.base_ub.tolist())
        # A tiny per-batch charge: balanced zero-profit cycles (2 Wood -> 3 Stick -> 2 Wood)
        # tie up value at no profit, so without it the LP pads plans with them for free.
        h.changeColsCost(self.n, list(range(self.n)), (-self.profit + _BATCH_CHARGE).tolist())
        inv = (-table.N).tocsr()
        h.addRows(
            inv.shape[0],
            [-inf] * inv.shape[0],
            [0.0] * inv.shape[0],
            inv.nnz,
            inv.indptr.tolist(),
            inv.indices.tolist(),
            inv.data.tolist(),
        )
        wallet_row = sp.csr_matrix(table.cost.reshape(1, -1))
        h.addRows(
            1,
            [-inf],
            [float(max(wallet, 0))],
            wallet_row.nnz,
            wallet_row.indptr.tolist(),
            wallet_row.indices.tolist(),
            wallet_row.data.tolist(),
        )
        used_row = sp.csr_matrix(self.used.reshape(1, -1))
        h.addRows(
            1,
            [1.0],
            [1.0],
            used_row.nnz,
            used_row.indptr.tolist(),
            used_row.indices.tolist(),
            used_row.data.tolist(),
        )
        self._banned: set[int] = set()
        # must-use rows: at least one column touching the item must be used
        # (stock columns touch their own item, so "must use X" accepts selling held X)
        self.must_rows: dict[int, list[int]] = {}
        for item in must_use & set(table.item_index):
            r = table.item_index[item]
            cols = [j for j in range(self.n) if table.N[r, j] != 0]
            if cols:
                self.must_rows[r] = cols
                h.addRows(1, [EPS], [inf], len(cols), [0], cols, [1.0] * len(cols))

    def ban(self, cols) -> None:
        self._banned.update(cols)

    def solve(self, banned=(), forced=()) -> Recipe | None:
        all_banned = frozenset(self._banned | set(banned))
        for cols in self.must_rows.values():
            if all(j in all_banned or self.base_ub[j] <= 0 for j in cols):
                return None  # this partition can never touch a must-use item
        apply_bounds(self.h, self.base_ub, self.t.price, all_banned, tuple(forced))
        self.h.run()
        self.solves += 1
        if self.h.modelStatusToString(self.h.getModelStatus()).lower() != "optimal":
            return None
        v = np.maximum(np.array(self.h.getSolution().col_value, dtype=float), 0.0)
        tied = float(self.used @ v)
        profit = float(self.profit @ v)
        # record the raw profit as the ranking gain *per tied value* -- close to the money
        # engine's convention: unity means break-even (liquidation), 1.1 means +10%
        return Recipe(
            v=v,
            gain=profit / max(tied, 1e-12) - 1.0,
            banned=all_banned,
            forced=tuple(forced),
            held=residue_rows(self.t.N, v, self.t.price),
        )

    def top_k(self, k: int, deadline: float | None) -> list[Recipe]:
        """Lawler partition (shared with the money engine): the k best distinct plans.

        Stops at the first region whose best plan makes no profit: regions come off the heap
        best-first, so nothing after it can either."""
        return lawler(self.solve, lambda r: polish(self, r), k, deadline, stop_gain=-1.0 + 1e-9)


def _plan_ubs(table: Table, stock: dict[int, int], kills: int) -> np.ndarray:
    inf = highspy.kHighsInf
    out = []
    for t in table.cols:
        if t.kind == STOCK:
            out.append(float(stock[int(t.ref.split(":", 1)[1])]))
        elif t.kind == FARM:
            out.append(float(max(kills, 0)))
        else:
            out.append(inf)
    return np.array(out, dtype=float)


# --------------------------------------------------------------------------- finalize -------


def _order_plan(
    g: Graph, moves_in: list[tuple[Transform, int]]
) -> list[tuple[Transform, int]] | None:
    """Sources first (stock, buys grouped by NPC, farm), then pool moves in dependency
    order; cycle moves needed by the plan are seeded from the sources."""
    stock = [(t, n) for t, n in moves_in if t.kind == STOCK]
    buys = sorted(
        ((t, n) for t, n in moves_in if t.kind == BUY),
        key=lambda tn: (tn[0].npc is None, tn[0].npc or 0, tn[0].id),
    )
    farms = [(t, n) for t, n in moves_in if t.kind == FARM]
    pool = [(t, n) for t, n in moves_in if t.kind in (CRAFT, SHIMMER)]
    inv: dict[int, float] = {}
    for t, n in stock + buys + farms:
        for i, c in t.outputs:
            inv[i] = inv.get(i, 0.0) + c * n
    ordered = order_pool_moves(g, pool, inv)  # type: ignore[arg-type]
    if ordered is None:
        return None
    pool_moves, seeds = ordered
    return stock + buys + seeds + farms + pool_moves


def _size_limit(
    moves: list[tuple[Transform, int]], capital: int, stock: dict[int, int], params: PlanParams
) -> int:
    """How many copies of the smallest whole plan your stock, wallet and kill budget allow.

    The LP is normalized (``used . v = 1``), so its absolute bounds -- held counts, the
    wallet row, the kill budget -- never bind; quantities are enforced here instead."""
    f: float = float("inf")
    taken: dict[int, int] = {}
    kills = 0
    for t, n in moves:
        if t.kind == STOCK:
            item = int(t.ref.split(":", 1)[1])
            taken[item] = taken.get(item, 0) + n
        elif t.kind == FARM:
            kills += n
    for item, n in taken.items():
        f = min(f, stock.get(item, 0) // n)
    if capital > 0:
        f = min(f, max(params.wallet, 0) // capital)
    if kills > 0:
        f = min(f, max(params.kills, 0) // kills)
    return 1 if f == float("inf") else int(f)


def _finalize_plan(
    g: Graph,
    table: Table,
    r: Recipe,
    seller: int | None,
    stock: dict[int, int] | None = None,
    params: PlanParams | None = None,
) -> PlanResult | None:
    """Whole-batch replay of a plan; farm kills are rounded up (expected values).

    With ``stock``/``params`` the smallest whole plan is checked against what you hold, the
    wallet and the kill budget, and then scaled up to as many copies as they allow."""
    supp_idx = r.support()
    supp = [table.cols[j] for j in supp_idx]
    v = r.v[list(supp_idx)]
    farm_mask = [t.kind == FARM for t in supp]
    price = {item: table.price[k] for item, k in table.item_index.items()}

    for attempt in range(6):
        scale = 1 << attempt
        nonfarm_x = np.array([x for x, m in zip(v, farm_mask) if not m])
        ints = integer_batches(nonfarm_x, _DEFAULT_MAX_DEN) if len(nonfarm_x) else []
        # how much the integerization scaled the recipe: farm kills must scale with it
        ratios = [
            int_i / max(float(v_j), 1e-12)
            for int_i, v_j in zip(ints, [x for x, m in zip(v, farm_mask) if not m])
            if v_j > 1e-9
        ]
        k = max(ratios) if ratios else 1.0
        moves_in: list[tuple[Transform, int]] = []
        nf = iter(ints)
        kills_total = 0
        for t, x, m in zip(supp, v, farm_mask):
            if m:
                kills = max(ceil(x * k * scale), 1)
                kills_total += kills
                moves_in.append((t, kills))
            else:
                n = next(nf) * scale
                if n <= 0:
                    n = scale
                moves_in.append((t, n))
        moves = _order_plan(g, moves_in)
        if moves is None:
            return None  # an unseedable cycle: scaling won't help
        seq = [t for t, _ in moves]
        counts = [n for _, n in moves]
        plan = simulate(seq, counts, price)
        if plan is None:
            continue
        profit = int(round(plan.profit + plan.leftover_value))
        tied = 0.0
        for t, n in moves:
            tied += _tied_value(t, table.item_index, table.price) * n
        exact_gain = profit / max(tied, 1e-12) - 1.0
        if exact_gain < r.gain - max(0.01, 0.05 * abs(r.gain)):
            continue  # the rung's approximation drifted: try the next one
        if params is not None:
            f = _size_limit(moves, int(round(plan.capital)), stock or {}, params)
            if f < 1:
                return None  # even one copy needs more than you hold or can spend
            if f > 1:
                moves = [(t, n * f) for t, n in moves]
                kills_total *= f
                plan = simulate([t for t, _ in moves], [n for _, n in moves], price)
                if plan is None:  # pragma: no cover - scaling cannot break a plan
                    return None
                profit = int(round(plan.profit + plan.leftover_value))
        return PlanResult(
            moves=moves,
            profit=profit,
            capital=int(round(plan.capital)),
            kills=kills_total,
            approx=any(farm_mask),
            support=tuple(supp_idx),
            places=tour_places(g, supp, seller),
            residue={i: c for i, c in plan.leftover.items() if c > 0.5 or c < -0.5},
        )
    return None


# --------------------------------------------------------------------------- combine --------


def combine(
    g: Graph,
    table: Table,
    supports: list[tuple[int, ...]],
    stock: dict[int, int],
    params: PlanParams,
    seller: int | None,
    time_limit: float = 5.0,
) -> PlanResult | None:
    """One plan that runs the found plans side by side, sharing stock, wallet and kills.

    The ranking LP is normalized, so each result is a single recipe scaled up on its own:
    two results drawing on the same Bars both assume they get all of them. Here the moves of
    every result go into one integer program with the real limits (held counts, the wallet,
    the kill budget) and whole batches, so the answer is directly playable. It stays small:
    only the moves the results already use, not the whole table.
    """
    cols = sorted({j for supp in supports for j in supp})
    if not cols:
        return None
    sub = table.N[:, cols].tocsr()
    rows = np.flatnonzero(np.diff(sub.indptr))  # items these moves touch
    sub = sub[rows]
    n = len(cols)
    inf = highspy.kHighsInf
    ts = [table.cols[j] for j in cols]
    ub = []
    for t in ts:
        if t.kind == STOCK:
            ub.append(float(stock.get(int(t.ref.split(":", 1)[1]), 0)))
        elif t.kind == FARM:
            ub.append(float(max(params.kills, 0)))
        else:
            ub.append(inf)
    profit = (table.revenue() - table.cost)[cols]

    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.setOptionValue("time_limit", float(max(time_limit, 0.1)))
    h.addVars(n, [0.0] * n, ub)
    h.changeColsCost(n, list(range(n)), (-profit + _BATCH_CHARGE).tolist())
    h.changeColsIntegrality(n, list(range(n)), [highspy.HighsVarType.kInteger] * n)
    inv = (-sub).tocsr()  # N v >= 0
    h.addRows(
        inv.shape[0],
        [-inf] * inv.shape[0],
        [0.0] * inv.shape[0],
        inv.nnz,
        inv.indptr.tolist(),
        inv.indices.tolist(),
        inv.data.tolist(),
    )
    paid = [k for k in range(n) if table.cost[cols[k]] > 0]
    if paid:
        h.addRows(
            1,
            [-inf],
            [float(max(params.wallet, 0))],
            len(paid),
            [0],
            paid,
            [float(table.cost[cols[k]]) for k in paid],
        )
    farmed = [k for k in range(n) if ts[k].kind == FARM]
    if farmed:
        h.addRows(
            1, [-inf], [float(max(params.kills, 0))], len(farmed), [0], farmed, [1.0] * len(farmed)
        )
    h.run()
    if h.getInfo().primal_solution_status != 2:  # no feasible point found
        return None
    x = h.getSolution().col_value
    moves_in = [(t, int(round(v))) for t, v in zip(ts, x) if round(v) >= 1]
    if not moves_in:
        return None
    moves = _order_plan(g, moves_in)
    if moves is None:
        return None
    price = {item: table.price[k] for item, k in table.item_index.items()}
    plan_ = simulate([t for t, _ in moves], [c for _, c in moves], price)
    if plan_ is None or plan_.capital > max(params.wallet, 0):
        return None  # seeding a cycle would need more than the wallet
    used = [t for t, _ in moves_in]
    return PlanResult(
        moves=moves,
        profit=int(round(plan_.profit + plan_.leftover_value)),
        capital=int(round(plan_.capital)),
        kills=sum(c for t, c in moves if t.kind == FARM),
        approx=any(t.kind == FARM for t in used),
        support=tuple(cols[k] for k in range(n) if round(x[k]) >= 1),
        places=tour_places(g, used, seller),
        residue={i: c for i, c in plan_.leftover.items() if c > 0.5 or c < -0.5},
    )


# --------------------------------------------------------------------------- entry ----------


def plan(g: Graph, stock: dict[int, int], params: PlanParams = PlanParams()) -> PlanReport:
    """The pivot search: best uses of owned items (+ optional buys and farming EVs)."""
    start = time.monotonic()
    deadline = start + params.time_limit if params.time_limit is not None else None

    liquidation = int(
        sum(count * g.sell_unit(i) for i, count in stock.items() if g.sell_unit(i) > 0)
    )

    farms = farm_sources(g, params.expert) if params.kills > 0 else []
    table = build_plan_table(g, stock, farms, params.kills)

    # free-money loops live in craft/shimmer space; stock/farm are bounded, never loops
    loop_ctx = _LoopContext(_loop_table(g))
    loops = find_free_loops(loop_ctx, deadline)
    col_index = {t: j for j, t in enumerate(table.cols)}
    solver = PlanSolver(
        table, _plan_ubs(table, stock, params.kills), params.wallet, params.must_use
    )
    for lp in loops:
        for j in lp.support():
            plan_col = col_index.get(loop_ctx.t.cols[j])
            if plan_col is not None:
                solver.ban({plan_col})

    filters = params.min_profit > 0 or params.must_use or not params.show_liquidation
    recipes = solver.top_k(params.top * 3 if filters else params.top, deadline)
    seller = g.npcs.happiest_npc() if g.npcs is not None else None

    results: list[PlanResult] = []
    for r in recipes:
        kinds = {table.cols[j].kind for j in r.support()}
        if kinds == {STOCK} and not params.show_liquidation:
            continue  # "sell your X": the floor is reported once, not as spam
        if params.must_use:
            touched = set()
            for j in r.support():
                touched.update(i for i, _ in table.cols[j].inputs + table.cols[j].outputs)
            if not params.must_use <= touched:
                continue
        res = _finalize_plan(g, table, r, seller, stock, params)
        if res is None:
            continue
        if res.profit < params.min_profit:
            continue
        results.append(res)

    # the partition enumerates candidates by gain; the page shows the most profitable
    results.sort(key=lambda r: r.profit, reverse=True)
    results = results[: params.top]
    combined = None
    if len(results) > 1:
        left = deadline - time.monotonic() if deadline is not None else 5.0
        if left > 0:
            combined = combine(
                g, table, [r.support for r in results], stock, params, seller, min(left, 5.0)
            )
        if combined is not None and combined.profit <= results[0].profit:
            combined = None  # nothing to gain over doing the best plan alone
    elapsed = time.monotonic() - start
    return PlanReport(
        results=results,
        combined=combined,
        liquidation=liquidation,
        stock=stock,
        loops=loops,
        solves=solver.solves + loop_ctx.solves,
        complete=deadline is None or time.monotonic() <= deadline,
        elapsed=elapsed,
    )


__all__ = [
    "PlanParams",
    "PlanReport",
    "PlanResult",
    "PlanSolver",
    "build_plan_table",
    "farm_sources",
    "plan",
    "plan_known_items",
    "stock_from_world",
    "format_coins",
]
