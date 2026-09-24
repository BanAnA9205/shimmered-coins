"""Compile a dataset + world + NPC setup into a set of linear transforms (the possible moves).

Every move is a transform applied `x` times ("batches"):
  consumes  Σ inputs * x  items, produces  Σ outputs * x  items,
  spends    cost * x      copper and earns  revenue * x  copper.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from dataclasses import dataclass, field

from ..data.schema import Dataset, Recipe
from .world import NpcSetup, WorldConfig, buy_price, sell_price

BUY, SELL, CRAFT, SHIMMER = "buy", "sell", "craft", "shimmer"
KINDS = (BUY, SELL, CRAFT, SHIMMER)

MAX_GROUP_VARIANTS = 16  # cap on "any wood" style expansions per recipe


@dataclass(frozen=True)
class Transform:
    id: int
    kind: str
    inputs: tuple[tuple[int, int], ...]  # (item, count) per batch
    outputs: tuple[tuple[int, int], ...]
    cost: int = 0  # copper spent per batch
    revenue: int = 0  # copper earned per batch
    npc: int | None = None
    limited: bool = False  # limited shop stock; a loop using it is not repeatable
    ref: str = ""  # provenance, e.g. "recipe:123" or "shimmer:decraft"

    @property
    def input_items(self) -> frozenset[int]:
        return frozenset(i for i, _ in self.inputs)

    @property
    def output_items(self) -> frozenset[int]:
        return frozenset(i for i, _ in self.outputs)


@dataclass(frozen=True)
class Constraints:
    kinds: frozenset[str] = frozenset(KINDS)
    include_items: frozenset[int] | None = None  # whitelist: every touched item must be in it
    exclude_items: frozenset[int] = frozenset()


@dataclass
class Graph:
    dataset: Dataset
    transforms: list[Transform]
    buy_of: dict[int, Transform] = field(default_factory=dict)  # item -> cheapest buy
    sell_of: dict[int, Transform] = field(default_factory=dict)
    consumers: dict[int, list[Transform]] = field(default_factory=dict)  # item -> non-sell users

    def name(self, item: int) -> str:
        it = self.dataset.items.get(item)
        return it.name if it else f"#{item}"

    def sell_unit(self, item: int) -> int:
        t = self.sell_of.get(item)
        return t.revenue if t else 0

    def describe(self, t: Transform, batches: int | float = 1) -> str:
        def fmt(pairs: tuple[tuple[int, int], ...]) -> str:
            return " + ".join(f"{_num(c * batches)} {self.name(i)}" for i, c in pairs)

        if t.kind == BUY:
            npc = self.dataset.npcs.get(t.npc)
            who = f" from {npc.name}" if npc else ""
            return f"buy {fmt(t.outputs)}{who} for {format_coins(t.cost * batches)}"
        if t.kind == SELL:
            return f"sell {fmt(t.inputs)} for {format_coins(t.revenue * batches)}"
        verb = "craft" if t.kind == CRAFT else "shimmer"
        return f"{verb} {fmt(t.inputs)} -> {fmt(t.outputs)}"


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:.3g}"


def format_coins(copper: float) -> str:
    """1234567 -> '1p 23g 45s 67c' (fractional amounts shown with decimals on copper)."""
    neg = copper < 0
    c = abs(copper)
    whole = int(c)
    frac = c - whole
    parts = []
    for unit, sym in ((1_000_000, "p"), (10_000, "g"), (100, "s")):
        if whole >= unit:
            parts.append(f"{whole // unit}{sym}")
            whole %= unit
    if whole or frac or not parts:
        parts.append(f"{whole + frac:.4g}c" if frac else f"{whole}c")
    return ("-" if neg else "") + " ".join(parts)


def _expand_ingredients(ds: Dataset, recipe: Recipe) -> list[tuple[tuple[int, int], ...]]:
    """All concrete ingredient lists for a recipe (recipe groups expanded, capped)."""
    options: list[list[tuple[int, int]]] = []
    for ing in recipe.ingredients:
        if ing.item is not None:
            options.append([(ing.item, ing.count)])
        else:
            grp = ds.groups.get(ing.group or "")
            members = list(grp.items) if grp else []
            if grp and grp.icon_item in members:  # icon item first so the cap keeps it
                members.remove(grp.icon_item)
                members.insert(0, grp.icon_item)
            options.append([(m, ing.count) for m in members] or [])
    variants = []
    for combo in itertools.islice(itertools.product(*options), MAX_GROUP_VARIANTS):
        merged: dict[int, int] = defaultdict(int)
        for item, count in combo:
            merged[item] += count
        variants.append(tuple(sorted(merged.items())))
    return variants


def build_graph(
    ds: Dataset,
    world: WorldConfig,
    npcs: NpcSetup,
    constraints: Constraints = Constraints(),
) -> Graph:
    items = ds.items
    coins = {i for i, it in items.items() if it.is_coin}
    raw: list[Transform] = []

    def allowed(*item_lists: tuple[tuple[int, int], ...]) -> bool:
        for lst in item_lists:
            for i, _ in lst:
                if i in coins or i in constraints.exclude_items or i not in items:
                    return False
                if constraints.include_items is not None and i not in constraints.include_items:
                    return False
        return True

    # Buy: cheapest offer per item among present NPCs.
    if BUY in constraints.kinds:
        best: dict[int, Transform] = {}
        for s in ds.shops:
            if s.currency != "coins" or not npcs.present(s.npc) or not world.holds(s.conditions):
                continue
            outs = ((s.item, 1),)
            if not allowed(outs):
                continue
            base = s.price if s.price is not None else items[s.item].value
            if base <= 0:
                continue
            price = buy_price(base, npcs.happiness[s.npc], npcs.discount)
            t = Transform(
                0, BUY, (), outs, cost=price, npc=s.npc, limited=s.limited, ref=f"shop:{s.npc}"
            )
            cur = best.get(s.item)
            if cur is None or (price, t.limited) < (cur.cost, cur.limited):
                best[s.item] = t
        raw.extend(best.values())

    # Sell: any shop NPC buys anything at value/5 adjusted by the happiest NPC.
    if SELL in constraints.kinds and any(
        ds.npcs.get(n) is None or ds.npcs[n].has_shop for n in npcs.happiness
    ):
        adj = npcs.best_sell_adjustment()
        for it in items.values():
            if it.is_coin or not it.sellable or it.value <= 0:
                continue
            ins = ((it.id, 1),)
            if allowed(ins):
                rev = sell_price(it.value, adj)
                if rev > 0:
                    raw.append(Transform(0, SELL, ins, (), revenue=rev))

    if CRAFT in constraints.kinds:
        for r in ds.recipes:
            if not world.holds(r.conditions):
                continue
            if world.stations is not None and not set(r.stations) <= world.stations:
                continue
            outs = ((r.result, r.result_stack),)
            for ins in _expand_ingredients(ds, r):
                if ins and allowed(ins, outs):
                    raw.append(Transform(0, CRAFT, ins, outs, ref=f"recipe:{r.id}"))

    if SHIMMER in constraints.kinds and world.shimmer:
        for s in ds.shimmer:
            if not world.holds(s.conditions):
                continue
            ins = ((s.item, s.count),)
            outs = tuple((i, c) for i, c in s.outputs if i not in coins)
            if outs and allowed(ins, outs):
                raw.append(Transform(0, SHIMMER, ins, outs, ref=f"shimmer:{s.kind}"))

    return _finalize(ds, _prune(raw))


def _prune(ts: list[Transform]) -> list[Transform]:
    """Drop transforms that can never be part of a useful sequence.

    Forward: inputs must be obtainable starting from coins (buyable, or produced from such).
    Backward: at least one output must be convertible back into coins.
    """
    reach: set[int] = set()
    changed = True
    while changed:
        changed = False
        for t in ts:
            if t.input_items <= reach and not t.output_items <= reach:
                reach |= t.output_items
                changed = True
    ts = [t for t in ts if t.input_items <= reach]

    liquid: set[int] = {i for t in ts if t.kind == SELL for i in t.input_items}
    changed = True
    while changed:
        changed = False
        for t in ts:
            if t.kind != SELL and t.output_items & liquid and not t.input_items <= liquid:
                liquid |= t.input_items
                changed = True
    return [t for t in ts if t.kind == SELL or t.output_items & liquid]


def _finalize(ds: Dataset, ts: list[Transform]) -> Graph:
    order = {BUY: 0, CRAFT: 1, SHIMMER: 2, SELL: 3}
    ts.sort(key=lambda t: (order[t.kind], t.inputs, t.outputs, t.ref))
    final = [Transform(i, *_fields(t)) for i, t in enumerate(ts)]
    g = Graph(ds, final)
    consumers: dict[int, list[Transform]] = defaultdict(list)
    for t in final:
        if t.kind == BUY:
            g.buy_of[t.outputs[0][0]] = t
        elif t.kind == SELL:
            g.sell_of[t.inputs[0][0]] = t
        else:
            for i in t.input_items:
                consumers[i].append(t)
    g.consumers = dict(consumers)
    return g


def _fields(t: Transform) -> tuple:
    return (t.kind, t.inputs, t.outputs, t.cost, t.revenue, t.npc, t.limited, t.ref)
