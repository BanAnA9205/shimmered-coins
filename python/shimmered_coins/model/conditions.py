"""Grouping of a dataset's conditions for display (UI, `data info`)."""

from __future__ import annotations

from ..data.schema import Dataset

GROUPS = ("progression", "time", "events", "seeds", "player", "other")

_SEEDS = (
    "expert",
    "master",
    "normalmode",
    "worthy",
    "drunk",
    "dontstarve",
    "notthebees",
    "remix",
    "zenith",
    "getfixedboi",
    "noTraps".lower(),
    "seed",
)
_EVENTS = (
    "bloodmoon",
    "eclipse",
    "party",
    "rain",
    "wind",
    "halloween",
    "christmas",
    "lantern",
    "invasion",
    "pumpkinmoon",
    "frostmoon",
    "sandstorm",
    "starfall",
)


def flag_group(name: str, scope: str) -> str:
    if scope == "player":
        return "player"
    n = name.lower()
    if n.startswith("moon:") or n in ("daytime", "nighttime", "day", "night") or "time" in n:
        return "time"
    if n == "hardmode" or n.startswith("downed") or n in ("crimson", "corruption"):
        return "progression"
    if any(k in n for k in _EVENTS):
        return "events"
    if any(k in n for k in _SEEDS):
        return "seeds"
    return "other"


def flag_groups(ds: Dataset) -> dict[str, list[dict]]:
    """User-settable flags (world and player scope), grouped. Zone, happiness, NPC-presence and
    crafting-location conditions are not flags: they come from the NPC setup."""
    out: dict[str, list[dict]] = {g: [] for g in GROUPS}
    for name in sorted(ds.condition_names()):
        info = ds.condition_info(name)
        if info.scope not in ("world", "player"):
            continue
        out[flag_group(name, info.scope)].append(
            {"name": name, "description": info.description or name}
        )
    return {g: v for g, v in out.items() if v}


def zones(ds: Dataset) -> list[str]:
    """Biome zones that some shop entry depends on."""
    return sorted(
        n[5:]
        for n in ds.condition_names()
        if ds.condition_info(n).scope == "zone" and n.startswith("zone:")
    )
