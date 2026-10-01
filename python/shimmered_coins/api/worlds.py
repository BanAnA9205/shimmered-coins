"""Discover the player's saved worlds (.wld files) for the plan UI. Local-only, of course."""

from __future__ import annotations

import os
from pathlib import Path

from ..data.world import parse_world


def _save_roots() -> list[Path]:
    env = os.environ.get("SHIMCOINS_WORLDS")
    home = Path.home()
    roots = [Path(env)] if env else []
    roots += [
        home / ".local/share/Terraria/Worlds",  # native Linux / Proton saves
        Path.home().parent / "banana9205" / ".local/share/Terraria/Worlds",
        # Steam play on Windows-style layouts is unlikely here; add common ones anyway
        home / "Documents/My Games/Terraria/Worlds",
        home / "AppData/Roaming/Terraria/Worlds",
    ]
    return [p for p in roots if p.is_dir()]


def discover_worlds() -> list[dict]:
    """[{name, seed, version, size, chests, items, path}] for every savable world.

    There is no "chest provenance" in .wld (who placed a chest is not recorded); the plan UI
    lets the user uncheck stock items instead, by design.
    """
    out = []
    for root in dict.fromkeys(_save_roots()):  # dedupe, keep order
        for f in sorted(root.glob("*.wld"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                w = parse_world(f)
            except Exception as e:
                out.append({"name": f.stem, "path": str(f), "error": str(e)})
                continue
            items = sum(len(c.items) for c in w.chests)
            out.append(
                {
                    "name": w.name,
                    "seed": w.seed,
                    "version": w.version,
                    "path": str(f),
                    "chests": len(w.chests),
                    "npcs": len(w.npcs),
                    "items": items,
                }
            )
    return out
