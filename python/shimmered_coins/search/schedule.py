"""Phase 2 (docs/ALGORITHM.md 5-6): ordering a recipe and measuring its trip length.

Only travel costs anything. The tour is: buy (grouped by NPC) -> shimmer pool (crafts and
shimmers interleave there in dependency order) -> sell everything to the happiest NPC. A cycle
inside the recipe is seeded with one extra buy of a cycle item (or the recipe is unschedulable).
The score is ``ln(1 + gain) / (L + c)``: compounding growth per trip, with ``c`` the fixed
per-loop overhead.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..model.graph import Graph, Transform

POOL = "shimmer pool"
TRAVEL = "Travelling Merchant"


def tour_places(g: Graph, supp: list[Transform], seller_id: int | None) -> list[str]:
    """The distinct places the loop visits, in tour order (ALGORITHM.md 5)."""
    npc_ids = sorted({t.npc for t in supp if t.kind == "buy" and t.npc is not None})
    places = [g.dataset.npcs[n].name for n in npc_ids if n in g.dataset.npcs]
    if any(t.kind == "buy" and t.npc is None for t in supp):
        places.append(TRAVEL)
    if any(t.kind == "shimmer" for t in supp):
        places.append(POOL)
    if seller_id is not None:
        name = g.dataset.npcs.get(seller_id)
        seller = name.name if name else f"npc {seller_id}"
        if seller not in places:
            places.append(seller)
    return places


def score(gain: float, places: int, overhead: float) -> float:
    """ln(1 + gain) / (L + c): compounding growth per trip (6.1)."""
    if gain <= -1.0:
        return -math.inf
    return math.log(max(1.0 + gain, 1e-12)) / (places + overhead)


def order_pool_moves(
    g: Graph, moves: list[tuple[Transform, int]], inv: dict[int, int]
) -> tuple[list[tuple[Transform, int]], list[tuple[Transform, int]]] | None:
    """Greedy dependency-ordered execution of the pool moves over ``inv`` (already bought).

    Returns (execution order, seed buys), or None when a cycle cannot be seeded: when nothing
    can progress, one batch's worth of the cheapest blocking cycle item is bought (once per item)
    and the schedule continues from there.
    """
    pending: dict[Transform, int] = dict(moves)
    out: list[tuple[Transform, int]] = []
    seeds: list[tuple[Transform, int]] = []
    seeded: set[int] = set()

    while pending:
        progressed = False
        for t in sorted(pending, key=lambda x: x.id):
            can = pending[t]
            for i, c in t.inputs:
                can = min(can, inv.get(i, 0) // c)
            if can <= 0:
                continue
            for i, c in t.inputs:
                inv[i] -= c * can
            for i, c in t.outputs:
                inv[i] = inv.get(i, 0) + c * can
            out.append((t, can))
            if can == pending[t]:
                del pending[t]
            else:
                pending[t] -= can
            progressed = True
        if progressed:
            continue
        seed = None
        for t in sorted(pending, key=lambda x: x.id):
            for i, c in t.inputs:
                if inv.get(i, 0) < c and i not in seeded and i in g.buy_of:
                    seed = (i, t)
                    break
            if seed:
                break
        if seed is None:
            return None
        item, blocker = seed
        need = max(c for t2 in pending for i2, c in t2.inputs if i2 == item)
        buy = g.buy_of[item]
        inv[item] = inv.get(item, 0) + need
        seeds.append((buy, need))
        seeded.add(item)
    return out, seeds


def schedule(
    g: Graph, supp: list[Transform], batches: dict[int, int]
) -> list[tuple[Transform, int]] | None:
    """Full ordered plan: buys grouped by NPC first, then pool moves in execution order."""
    buys = sorted(
        ((t, batches[t.id]) for t in supp if t.kind == "buy"),
        key=lambda tb: (tb[0].npc is None, tb[0].npc or 0, tb[0].id),
    )
    inv: dict[int, int] = {}
    for t, n in buys:
        for i, c in t.outputs:
            inv[i] = inv.get(i, 0) + c * n
    pool_moves = [(t, batches[t.id]) for t in supp if t.kind != "buy"]
    ordered = order_pool_moves(g, pool_moves, inv)
    if ordered is None:
        return None
    pool, seeds = ordered
    return buys + seeds + pool
