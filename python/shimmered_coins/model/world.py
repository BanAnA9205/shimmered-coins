"""World state and NPC setup that decide which moves exist and what they cost."""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class WorldConfig:
    """Everything about the world that gates shops, recipes and shimmer outcomes.

    ``flags`` is a free-form set of condition names emitted by the extractor, e.g.
    ``{"hardmode", "downedMoonLord", "crimson", "night", "moon:full"}``.
    ``stations`` restricts crafting to recipes whose stations are all available; ``None`` means
    every station is assumed available.
    """

    flags: frozenset[str] = frozenset()
    shimmer: bool = True
    stations: frozenset[int] | None = None

    def holds(self, conditions: tuple[str, ...] | list[str]) -> bool:
        for c in conditions:
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
