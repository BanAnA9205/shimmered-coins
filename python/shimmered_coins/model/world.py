"""World state and NPC setup that decide which moves exist and what they cost."""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class WorldConfig:
    """Everything about the world that gates shops, recipes and shimmer outcomes.

    ``flags`` is a free-form set of condition names emitted by the extractor, e.g.
    ``{"hardmode", "downedmoonlord", "crimson", "bloodmoon"}``. Matching is case-insensitive.
    ``stations`` restricts crafting to recipes whose stations are all available; ``None`` means
    every station is assumed available.
    """

    flags: frozenset[str] = frozenset()
    shimmer: bool = True
    stations: frozenset[int] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "flags", frozenset(f.lower() for f in self.flags))

    def holds(self, conditions: tuple[str, ...] | list[str]) -> bool:
        for c in conditions:
            c = c.lower()
            if c.startswith("!"):
                if c[1:] in self.flags:
                    return False
            elif c not in self.flags:
                return False
        return True


ANY_ZONE = "any"  # "what if this NPC lived anywhere": every zone condition passes


@dataclass(frozen=True)
class NpcSetup:
    """Present NPCs, their happiness price adjustment and where they live.

    ``happiness`` maps npc id -> price adjustment as used by the game: 1.0 is neutral, lower is
    happier (0.75 best). The game clamps it to roughly [0.75, 1.5].

    ``zones`` maps npc id -> the biome zones where you talk to it (e.g. ``{"snow"}``), or
    ``ANY_ZONE`` to consider every biome. Shop conditions like ``zone:snow`` are checked against
    the selling NPC's own zones. NPCs without an entry stand in no special biome.

    ``travel_stock`` is today's Travelling Merchant stock (random in game, so entered by hand).
    """

    happiness: dict[int, float] = field(default_factory=dict)
    discount: bool = False  # Discount Card or Greedy Ring (-20% when buying; they don't stack)
    # Whether happiness also changes what NPCs pay you. TODO(extractor): confirm against
    # Player.GetItemExpectedPrice for every supported version.
    happiness_affects_selling: bool = True
    zones: dict[int, frozenset[str] | str] = field(default_factory=dict)
    travel_stock: frozenset[int] = frozenset()

    def present(self, npc: int) -> bool:
        return npc in self.happiness

    def zones_of(self, npc: int) -> frozenset[str] | str:
        z = self.zones.get(npc, frozenset())
        return z if z == ANY_ZONE else frozenset(x.lower() for x in z)

    def best_sell_adjustment(self) -> float:
        """Selling: any shop NPC buys any item, so only the happiest one matters."""
        if not self.happiness or not self.happiness_affects_selling:
            return 1.0
        return min(self.happiness.values())


def condition_holds(
    cond: str, world: WorldConfig, npc: int | None = None, npcs: NpcSetup | None = None
) -> bool:
    """Evaluate one typed condition string (see docs/DATA_FORMAT.md, "Conditions").

    ``zone:x`` and ``happy<=v`` are about the selling NPC; ``npc:id`` asks whether that NPC
    lives in town; ``need:x`` (crafting next to water, in snow, ...) is assumed reachable;
    everything else is a world flag.
    """
    c = cond.lower()
    if c.startswith("!"):
        inner = c[1:]
        if inner.startswith("zone:") and npcs is not None and npc is not None:
            if npcs.zones_of(npc) == ANY_ZONE:
                return True  # what-if mode: the NPC can also live elsewhere
        return not condition_holds(inner, world, npc, npcs)
    if c.startswith("zone:"):
        if npcs is None or npc is None:
            return True
        z = npcs.zones_of(npc)
        return z == ANY_ZONE or c[5:] in z
    if c.startswith("happy<="):
        if npcs is None or npc is None:
            return True
        return npcs.happiness.get(npc, 99.0) <= float(c[7:]) + 1e-9
    if c.startswith("npc:") and npcs is not None:
        try:
            return npcs.present(int(c[4:]))
        except ValueError:
            return c in world.flags
    if c.startswith("need:"):
        return True
    return c in world.flags


def conditions_hold(
    conds: tuple[str, ...] | list[str],
    world: WorldConfig,
    npc: int | None = None,
    npcs: NpcSetup | None = None,
) -> bool:
    return all(condition_holds(c, world, npc, npcs) for c in conds)


def buy_price(base_price: int, adjustment: float, discount: bool) -> int:
    """Copper paid for one unit bought from an NPC."""
    price = base_price
    if discount:
        price = int(price * 0.8)
    return max(1, int(math.floor(price * adjustment + 0.5)))


def sell_price(value: int, adjustment: float) -> int:
    """Copper received for one unit sold to an NPC (value/5, better when the NPC is happy)."""
    return int(math.floor(value / adjustment + 0.5)) // 5


def check_pricing(probe: list[dict]) -> dict:
    """Compare our price formulas with samples of the game's own GetItemExpectedPrice.

    The extractor records ``{"adjustment", "value", "discount", "buy", "sell"}`` samples (older
    dumps have no ``discount`` key). The game's sell figure may or may not already include the /5,
    so several readings are tried.
    """
    if not probe:
        return {"checked": 0}
    buy_bad = [
        p
        for p in probe
        if p["buy"] is not None
        and buy_price(p["value"], p["adjustment"], p.get("discount", False)) != p["buy"]
    ]
    discounted = [p for p in probe if p.get("discount") and p["buy"] is not None]
    discount_bad = [p for p in buy_bad if p.get("discount")]

    def before_div5(p: dict) -> int:
        return int(math.floor(p["value"] / p["adjustment"] + 0.5))

    readings = {
        "value/adj/5": lambda p: sell_price(p["value"], p["adjustment"]),
        "value/adj (the /5 happens when selling)": before_div5,
        "value/5 (happiness ignored)": lambda p: p["value"] // 5,
        "value (happiness ignored)": lambda p: p["value"],
    }
    rates = {
        name: sum(1 for p in probe if p["sell"] is not None and f(p) == p["sell"]) / len(probe)
        for name, f in readings.items()
    }
    best = max(rates, key=rates.get)
    exact = rates[best] == 1.0
    return {
        "checked": len(probe),
        "buy_match_rate": 1 - len(buy_bad) / len(probe),
        "buy_mismatches": buy_bad[:5],
        "discount_checked": len(discounted),
        "discount_match_rate": 1 - len(discount_bad) / len(discounted) if discounted else None,
        "sell_reading": best if exact else f"closest: {best} ({rates[best]:.0%} of samples)",
        "sell_match_rates": rates,
        "happiness_affects_selling": "ignored" not in best,
    }
