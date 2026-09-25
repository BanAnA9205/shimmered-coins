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
from .world import ANY_ZONE, NpcSetup, WorldConfig, buy_price, conditions_hold, sell_price

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
    npcs: NpcSetup | None = None  # the setup this graph was built for (phase 2 needs the seller)
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
            npc = self.dataset.npcs.get(t.npc) if t.npc is not None else None
            who = f" from {npc.name}" if npc else ""
            if t.ref == "travel":
                who = " from the Travelling Merchant"
            elif "@" in t.ref:
                who += f" (living in {t.ref.split('@', 1)[1].replace('+', ' & ')})"
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

    # Buy: one column per (item, NPC) offer, so phase 2 can prefer an NPC already on the tour
    # over the cheapest offer (docs/ALGORITHM.md 5).
    if BUY in constraints.kinds:
        offers: dict[tuple[int | None, int], Transform] = {}
        for s in ds.shops:
            if s.currency != "coins" or not npcs.present(s.npc):
                continue
            if not conditions_hold(s.conditions, world, s.npc, npcs):
                continue
            outs = ((s.item, 1),)
            if not allowed(outs):
                continue
            base = s.price if s.price is not None else items[s.item].value
            if base <= 0:
                continue
            price = buy_price(base, npcs.happiness[s.npc], npcs.discount)
            if price <= 0:
                continue  # free (value-1, discounted) items: useless to the LP normalization
            ref = f"shop:{s.npc}"
            if npcs.zones_of(s.npc) == ANY_ZONE:  # say which biome the NPC must live in
                zones = [c[5:] for c in s.conditions if c.lower().startswith("zone:")]
                if zones:
                    ref += "@" + "+".join(zones)
            t = Transform(0, BUY, (), outs, cost=price, npc=s.npc, limited=s.limited, ref=ref)
            cur = offers.get((s.npc, s.item))
            if cur is None or (price, t.limited) < (cur.cost, cur.limited):
                offers[(s.npc, s.item)] = t
        for item in npcs.travel_stock:
            outs = ((item, 1),)
            it = items.get(item)
            if it is None or it.value <= 0 or not allowed(outs):
                continue
            price = buy_price(it.value, 1.0, npcs.discount)
            if price <= 0:
                continue
            cur = offers.get((None, item))
            if cur is None or price < cur.cost:
                offers[(None, item)] = Transform(
                    0, BUY, (), outs, cost=price, limited=True, ref="travel"
                )
        raw.extend(offers.values())

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
            if not conditions_hold(r.conditions, world):
                continue
            if world.stations is not None and not set(r.stations) <= world.stations:
                continue
            outs = ((r.result, r.result_stack),)
            for ins in _expand_ingredients(ds, r):
                if ins and allowed(ins, outs):
                    raw.append(Transform(0, CRAFT, ins, outs, ref=f"recipe:{r.id}"))

    if SHIMMER in constraints.kinds and world.shimmer:
        for s in ds.shimmer:
            if not conditions_hold(s.conditions, world):
                continue
            ins = ((s.item, s.count),)
            outs = tuple((i, c) for i, c in s.outputs if i not in coins)
            if outs and allowed(ins, outs):
                raw.append(Transform(0, SHIMMER, ins, outs, ref=f"shimmer:{s.kind}"))

    return _finalize(ds, _prune(raw), npcs)


def _prune(ts: list[Transform]) -> list[Transform]:
    """Drop transforms that can never be part of a useful sequence.

    Forward: inputs must be obtainable starting from buys. Backward: at least one output must be
    convertible back into coins. Both passes are the sparse reachability in ``search/table.py``
    (docs/ALGORITHM.md 4.0); this is a thin wrapper.
    """
    from ..search.table import column_matrix, prune_columns

    item_index: dict[int, int] = {}
    for t in ts:
        for i, _ in t.inputs + t.outputs:
            item_index.setdefault(i, len(item_index))
    N, is_sell, liquid = column_matrix(ts, item_index)
    keep = prune_columns(N, liquid, is_sell)
    return [t for t, k in zip(ts, keep) if k]


def _finalize(ds: Dataset, ts: list[Transform], npcs: NpcSetup | None = None) -> Graph:
    order = {BUY: 0, CRAFT: 1, SHIMMER: 2, SELL: 3}
    ts.sort(key=lambda t: (order[t.kind], t.inputs, t.outputs, t.ref))
    final = [Transform(i, *_fields(t)) for i, t in enumerate(ts)]
    g = Graph(ds, final, npcs)
    consumers: dict[int, list[Transform]] = defaultdict(list)
    for t in final:
        if t.kind == BUY:
            item = t.outputs[0][0]
            cur = g.buy_of.get(item)  # helpers keep the cheapest offer per item
            if cur is None or (t.cost, t.limited) < (cur.cost, cur.limited):
                g.buy_of[item] = t
        elif t.kind == SELL:
            g.sell_of[t.inputs[0][0]] = t
        else:
            for i in t.input_items:
                consumers[i].append(t)
    g.consumers = dict(consumers)
    return g


def _fields(t: Transform) -> tuple:
    return (t.kind, t.inputs, t.outputs, t.cost, t.revenue, t.npc, t.limited, t.ref)
