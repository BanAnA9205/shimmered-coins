"""Loading, manual curation (overrides) and version diffing of datasets."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from . import db
from .schema import Dataset, ShimmerRule


def load_dataset(path: str | Path) -> Dataset:
    """Load a dataset from an extractor JSON dump or a curated SQLite file."""
    path = Path(path)
    if path.is_dir():
        path = path / "db.sqlite"
    if path.suffix in (".sqlite", ".db"):
        return db.load(path)
    with open(path, encoding="utf-8") as f:
        return Dataset.from_dict(json.load(f))


def apply_overrides(ds: Dataset, overrides: dict[str, Any]) -> Dataset:
    """Apply manual curation patches.

    Supported keys::

        {
          "items": {"<id>": {"value": 123, "sellable": false}},
          "remove_recipes": [recipe_id, ...],
          "remove_shimmer": [{"item": id, "kind": "decraft"}, ...],
          "add_shimmer": [<ShimmerRule dict>, ...],
          "remove_shops": [{"npc": id, "item": id}, ...]
        }
    """
    items = dict(ds.items)
    for sid, patch in overrides.get("items", {}).items():
        iid = int(sid)
        items[iid] = dataclasses.replace(items[iid], **patch)

    drop_recipes = set(overrides.get("remove_recipes", []))
    recipes = [r for r in ds.recipes if r.id not in drop_recipes]

    def matches(rule: ShimmerRule, pat: dict[str, Any]) -> bool:
        return rule.item == pat["item"] and pat.get("kind", rule.kind) == rule.kind

    shimmer = [
        s for s in ds.shimmer if not any(matches(s, p) for p in overrides.get("remove_shimmer", []))
    ]
    extra = Dataset.from_dict(
        {"version": ds.version, "items": [], "shimmer": overrides.get("add_shimmer", [])}
    ).shimmer
    shimmer.extend(extra)

    drop_shops = {(p["npc"], p["item"]) for p in overrides.get("remove_shops", [])}
    shops = [s for s in ds.shops if (s.npc, s.item) not in drop_shops]

    meta = {**ds.meta, "overrides_applied": True}
    return Dataset(
        ds.version,
        items,
        recipes,
        dict(ds.groups),
        shops,
        shimmer,
        dict(ds.npcs),
        meta,
        dict(ds.conditions),
    )


def diff(a: Dataset, b: Dataset) -> dict[str, Any]:
    """Structured summary of what changed from dataset `a` to dataset `b`."""

    def name(ds: Dataset, iid: int) -> str:
        it = ds.items.get(iid)
        return it.name if it else f"#{iid}"

    out: dict[str, Any] = {"from": a.version, "to": b.version}
    out["items_added"] = sorted(name(b, i) for i in b.items.keys() - a.items.keys())
    out["items_removed"] = sorted(name(a, i) for i in a.items.keys() - b.items.keys())
    out["value_changed"] = sorted(
        (name(b, i), a.items[i].value, b.items[i].value)
        for i in a.items.keys() & b.items.keys()
        if a.items[i].value != b.items[i].value
    )

    def recipe_sigs(ds: Dataset) -> set[str]:
        sigs = set()
        for r in ds.recipes:
            ings = ", ".join(
                f"{g.count}x {name(ds, g.item) if g.item is not None else g.group}"
                for g in r.ingredients
            )
            sigs.add(f"{r.result_stack}x {name(ds, r.result)} <- {ings}")
        return sigs

    ra, rb = recipe_sigs(a), recipe_sigs(b)
    out["recipes_added"], out["recipes_removed"] = sorted(rb - ra), sorted(ra - rb)

    def shimmer_sigs(ds: Dataset) -> set[str]:
        return {
            f"{s.kind}: {s.count}x {name(ds, s.item)} -> "
            + ", ".join(f"{c}x {name(ds, o)}" for o, c in s.outputs)
            + (f" [{' & '.join(s.conditions)}]" if s.conditions else "")
            for s in ds.shimmer
        }

    sa, sb = shimmer_sigs(a), shimmer_sigs(b)
    out["shimmer_added"], out["shimmer_removed"] = sorted(sb - sa), sorted(sa - sb)
    return out
