"""Version-independent dataset schema.

This is the contract between the extractor (which reads a specific Terraria build) and
everything downstream. The extractor emits a JSON document with this shape; `Dataset.from_dict`
parses it and `db.py` stores it in SQLite.

Conditions are plain strings evaluated against a world configuration: a condition ``"hardmode"``
holds when that flag is set, ``"!hardmode"`` holds when it is not. A list of conditions means
all of them must hold.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Item:
    id: int
    name: str
    internal_name: str = ""
    value: int = 0  # base value in copper; NPCs pay value/5 when buying from the player
    max_stack: int = 1
    is_coin: bool = False  # coins cannot be sold and are consumed by shimmer
    sellable: bool = True


@dataclass(frozen=True)
class Ingredient:
    count: int
    item: int | None = None  # concrete item id, or ...
    group: str | None = None  # ... a recipe group name ("any wood")


@dataclass(frozen=True)
class Recipe:
    id: int  # index in the game's recipe table; order matters for shimmer decrafting
    result: int
    result_stack: int
    ingredients: tuple[Ingredient, ...]
    stations: tuple[int, ...] = ()  # tile ids
    conditions: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecipeGroup:
    name: str
    items: tuple[int, ...]
    icon_item: int  # the item shown in the recipe; also what shimmer decrafting returns


@dataclass(frozen=True)
class ShopEntry:
    npc: int
    item: int
    price: int | None = None  # custom shop price in copper (None -> item value)
    currency: str = "coins"  # anything else (e.g. defender medals) is ignored by the engine
    conditions: tuple[str, ...] = ()
    limited: bool = False  # limited stock (travelling merchant, buy-once): loops are not repeatable


@dataclass(frozen=True)
class ShimmerRule:
    """One shimmer outcome. ``kind`` is "transmute" or "decraft".

    Decraft rules are emitted explicitly by the extractor (it applies the version's own logic
    for picking the recipe, returning group icon items, world-evil variants, ...), so the
    engine does not need to re-derive them.
    """

    kind: str
    item: int
    count: int  # how many of `item` are consumed per shimmer batch
    outputs: tuple[tuple[int, int], ...]  # (item, count) per batch
    conditions: tuple[str, ...] = ()


@dataclass(frozen=True)
class NpcDrop:
    """One entry of an NPC's loot table: expected-value farming data.

    ``rate`` is the probability per kill of one roll dropping ``min..max`` of ``item``;
    ``conditions`` are the game's drop-condition class names (``"IsExpert"``,
    ``"NotExpert"`` for the other difficulty, ...).
    """

    item: int
    rate: float
    min: int = 1
    max: int = 1
    conditions: tuple[str, ...] = ()


CONDITION_SCOPES = ("world", "zone", "npc_present", "happiness", "craft_location", "player")


@dataclass(frozen=True)
class ConditionInfo:
    """Catalog entry describing a condition string used by recipes, shops or shimmer rules.

    ``scope`` says what the condition depends on (see CONDITION_SCOPES), ``depends`` lists the
    game state the extractor found it to depend on, ``description`` is the game's own text.
    """

    name: str
    scope: str = "world"
    description: str = ""
    depends: tuple[str, ...] = ()


def infer_scope(name: str) -> str:
    """Scope of a condition name when the dataset has no catalog entry for it."""
    n = name.lower().lstrip("!")
    if n.startswith("zone:"):
        return "zone"
    if n.startswith("npc:"):
        return "npc_present"
    if n.startswith("happy<="):
        return "happiness"
    if n.startswith("need:"):
        return "craft_location"
    if n.startswith("player:"):
        return "player"
    return "world"


@dataclass(frozen=True)
class Npc:
    id: int
    name: str
    has_shop: bool = True


@dataclass
class Dataset:
    version: str
    items: dict[int, Item]
    recipes: list[Recipe] = field(default_factory=list)
    groups: dict[str, RecipeGroup] = field(default_factory=dict)
    shops: list[ShopEntry] = field(default_factory=list)
    shimmer: list[ShimmerRule] = field(default_factory=list)
    npcs: dict[int, Npc] = field(default_factory=dict)
    drops: list[tuple[int, tuple[NpcDrop, ...]]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    conditions: dict[str, ConditionInfo] = field(default_factory=dict)

    def condition_names(self) -> set[str]:
        """Every condition used anywhere, without negation, lowercased."""
        names: set[str] = set()
        for group in (self.recipes, self.shops, self.shimmer):
            for x in group:
                names.update(c.lower().lstrip("!") for c in x.conditions)
        return names

    def condition_info(self, name: str) -> ConditionInfo:
        key = name.lower().lstrip("!")
        return self.conditions.get(key) or ConditionInfo(key, infer_scope(key))

    # ---- lookup helpers -------------------------------------------------------------------
    def item_by_name(self, name: str) -> Item:
        key = name.strip().lower()
        for it in self.items.values():
            if it.name.lower() == key or it.internal_name.lower() == key:
                return it
        raise KeyError(f"unknown item {name!r}")

    def npc_by_name(self, name: str) -> Npc:
        key = name.strip().lower()
        for n in self.npcs.values():
            if n.name.lower() == key:
                return n
        raise KeyError(f"unknown npc {name!r}")

    # ---- (de)serialization ----------------------------------------------------------------
    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Dataset:
        if d.get("schema", SCHEMA_VERSION) != SCHEMA_VERSION:
            raise ValueError(f"unsupported dataset schema {d.get('schema')}")
        items = {int(i["id"]): Item(**i) for i in d["items"]}
        recipes = [
            Recipe(
                id=int(r["id"]),
                result=int(r["result"]),
                result_stack=int(r.get("result_stack", 1)),
                ingredients=tuple(Ingredient(**ing) for ing in r["ingredients"]),
                stations=tuple(r.get("stations", ())),
                conditions=tuple(r.get("conditions", ())),
            )
            for r in d.get("recipes", [])
        ]
        groups = {
            g["name"]: RecipeGroup(g["name"], tuple(g["items"]), int(g["icon_item"]))
            for g in d.get("groups", [])
        }
        shops = [
            ShopEntry(**{**s, "conditions": tuple(s.get("conditions", ()))})
            for s in d.get("shops", [])
        ]
        shimmer = [
            ShimmerRule(
                kind=s["kind"],
                item=int(s["item"]),
                count=int(s.get("count", 1)),
                outputs=tuple((int(o[0]), int(o[1])) for o in s["outputs"]),
                conditions=tuple(s.get("conditions", ())),
            )
            for s in d.get("shimmer", [])
        ]
        npcs = {int(n["id"]): Npc(**n) for n in d.get("npcs", [])}
        for n in d.get("drop_npcs", []):
            # droppers (monsters mostly) join the NPC catalog for display; has_shop stays False
            npcs.setdefault(int(n["id"]), Npc(int(n["id"]), n.get("name", f"npc {n['id']}"), False))
        drops = [
            (
                int(e["npc"]),
                tuple(
                    NpcDrop(
                        item=int(x["item"]),
                        rate=float(x["rate"]),
                        min=int(x.get("min", 1)),
                        max=int(x.get("max", 1)),
                        conditions=tuple(x.get("conditions", ())),
                    )
                    for x in e["drops"]
                ),
            )
            for e in d.get("npc_drops", [])
        ]
        conditions = {
            c["name"].lower(): ConditionInfo(
                name=c["name"].lower(),
                scope=c.get("scope", infer_scope(c["name"])),
                description=c.get("description", ""),
                depends=tuple(c.get("depends", ())),
            )
            for c in d.get("conditions", [])
        }
        return cls(
            d["version"],
            items,
            recipes,
            groups,
            shops,
            shimmer,
            npcs,
            drops,
            d.get("meta", {}),
            conditions,
        )

    def to_dict(self) -> dict[str, Any]:
        def clean(obj: Any) -> Any:
            if isinstance(obj, dict):
                return {k: clean(v) for k, v in obj.items() if v is not None}
            if isinstance(obj, (list, tuple)):
                return [clean(v) for v in obj]
            return obj

        return {
            "schema": SCHEMA_VERSION,
            "version": self.version,
            "meta": self.meta,
            "items": [clean(asdict(i)) for i in self.items.values()],
            "recipes": [clean(asdict(r)) for r in self.recipes],
            "groups": [clean(asdict(g)) for g in self.groups.values()],
            "shops": [clean(asdict(s)) for s in self.shops],
            "shimmer": [clean(asdict(s)) for s in self.shimmer],
            "npcs": [clean(asdict(n)) for n in self.npcs.values()],
            "npc_drops": [
                {
                    "npc": npc,
                    "drops": [clean(asdict(x)) for x in lst],
                }
                for npc, lst in self.drops
            ],
            "conditions": [clean(asdict(c)) for c in self.conditions.values()],
        }
