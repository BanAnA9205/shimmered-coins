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


@dataclass(frozen=True)
class NpcSetup:
    """Present NPCs and their happiness price adjustment.

    ``happiness`` maps npc id -> price adjustment as used by the game: 1.0 is neutral, lower is
    happier (0.75 best). The game clamps it to roughly [0.75, 1.5].
    """

    happiness: dict[int, float] = field(default_factory=dict)
    discount: bool = False  # Discount Card / Greedy Ring / Shiny Stone... (-20% when buying)
    # Whether happiness also changes what NPCs pay you. TODO(extractor): confirm against
    # Player.GetItemExpectedPrice for every supported version.
    happiness_affects_selling: bool = True

    def present(self, npc: int) -> bool:
        return npc in self.happiness

    def best_sell_adjustment(self) -> float:
        """Selling: any shop NPC buys any item, so only the happiest one matters."""
        if not self.happiness or not self.happiness_affects_selling:
            return 1.0
        return min(self.happiness.values())


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

    The extractor records ``{"adjustment", "value", "buy", "sell"}`` samples. The game's sell
    figure may or may not already include the /5, so several readings are tried.
    """
    if not probe:
        return {"checked": 0}
    buy_bad = [
        p
        for p in probe
        if p["buy"] is not None and buy_price(p["value"], p["adjustment"], False) != p["buy"]
    ]

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
        "sell_reading": best if exact else f"closest: {best} ({rates[best]:.0%} of samples)",
        "sell_match_rates": rates,
        "happiness_affects_selling": "ignored" not in best,
    }
