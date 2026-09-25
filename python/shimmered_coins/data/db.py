"""SQLite storage for curated per-version datasets (``data/<version>/db.sqlite``)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .schema import SCHEMA_VERSION, Dataset

_DDL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE items (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, internal_name TEXT NOT NULL,
    value INTEGER NOT NULL, max_stack INTEGER NOT NULL,
    is_coin INTEGER NOT NULL, sellable INTEGER NOT NULL);
CREATE INDEX items_name ON items(name);
CREATE TABLE recipes (
    id INTEGER PRIMARY KEY, result INTEGER NOT NULL, result_stack INTEGER NOT NULL,
    stations TEXT NOT NULL, conditions TEXT NOT NULL);
CREATE INDEX recipes_result ON recipes(result);
CREATE TABLE recipe_ingredients (
    recipe_id INTEGER NOT NULL, pos INTEGER NOT NULL, item INTEGER, grp TEXT,
    count INTEGER NOT NULL, PRIMARY KEY (recipe_id, pos));
CREATE TABLE recipe_groups (name TEXT PRIMARY KEY, items TEXT NOT NULL, icon_item INTEGER NOT NULL);
CREATE TABLE shops (
    npc INTEGER NOT NULL, item INTEGER NOT NULL, price INTEGER, currency TEXT NOT NULL,
    conditions TEXT NOT NULL, limited INTEGER NOT NULL);
CREATE TABLE shimmer (
    kind TEXT NOT NULL, item INTEGER NOT NULL, count INTEGER NOT NULL,
    outputs TEXT NOT NULL, conditions TEXT NOT NULL);
CREATE INDEX shimmer_item ON shimmer(item);
CREATE TABLE npcs (id INTEGER PRIMARY KEY, name TEXT NOT NULL, has_shop INTEGER NOT NULL);
CREATE TABLE npc_drops (
    npc INTEGER NOT NULL, item INTEGER NOT NULL, rate REAL NOT NULL,
    min INTEGER NOT NULL, max INTEGER NOT NULL, conditions TEXT NOT NULL,
    PRIMARY KEY (npc, item, min, max, conditions));
CREATE TABLE conditions (
    name TEXT PRIMARY KEY, scope TEXT NOT NULL, description TEXT NOT NULL, depends TEXT NOT NULL);
"""


def save(ds: Dataset, path: str | Path) -> None:
    """Write `ds` to a fresh SQLite file (overwrites)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    d = ds.to_dict()
    con = sqlite3.connect(path)
    try:
        con.executescript(_DDL)
        meta = {"schema": SCHEMA_VERSION, "version": ds.version, **ds.meta}
        con.executemany(
            "INSERT INTO meta VALUES (?, ?)", [(k, json.dumps(v)) for k, v in meta.items()]
        )
        con.executemany(
            "INSERT INTO items VALUES (?,?,?,?,?,?,?)",
            [
                (
                    i.id,
                    i.name,
                    i.internal_name,
                    i.value,
                    i.max_stack,
                    int(i.is_coin),
                    int(i.sellable),
                )
                for i in ds.items.values()
            ],
        )
        for r in ds.recipes:
            con.execute(
                "INSERT INTO recipes VALUES (?,?,?,?,?)",
                (r.id, r.result, r.result_stack, json.dumps(r.stations), json.dumps(r.conditions)),
            )
            con.executemany(
                "INSERT INTO recipe_ingredients VALUES (?,?,?,?,?)",
                [
                    (r.id, pos, ing.item, ing.group, ing.count)
                    for pos, ing in enumerate(r.ingredients)
                ],
            )
        con.executemany(
            "INSERT INTO recipe_groups VALUES (?,?,?)",
            [(g["name"], json.dumps(g["items"]), g["icon_item"]) for g in d["groups"]],
        )
        con.executemany(
            "INSERT INTO shops VALUES (?,?,?,?,?,?)",
            [
                (s.npc, s.item, s.price, s.currency, json.dumps(s.conditions), int(s.limited))
                for s in ds.shops
            ],
        )
        con.executemany(
            "INSERT INTO shimmer VALUES (?,?,?,?,?)",
            [
                (s.kind, s.item, s.count, json.dumps(s.outputs), json.dumps(s.conditions))
                for s in ds.shimmer
            ],
        )
        con.executemany(
            "INSERT INTO npcs VALUES (?,?,?)",
            [(n.id, n.name, int(n.has_shop)) for n in ds.npcs.values()],
        )
        con.executemany(
            "INSERT INTO npc_drops VALUES (?,?,?,?,?,?)",
            [
                (npc, x.item, x.rate, x.min, x.max, json.dumps(list(x.conditions)))
                for npc, lst in ds.drops
                for x in lst
            ],
        )
        con.executemany(
            "INSERT INTO conditions VALUES (?,?,?,?)",
            [
                (c.name, c.scope, c.description, json.dumps(c.depends))
                for c in ds.conditions.values()
            ],
        )
        con.commit()
    finally:
        con.close()


def load(path: str | Path) -> Dataset:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        meta = {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM meta")}
        if meta.pop("schema") != SCHEMA_VERSION:
            raise ValueError(f"{path}: unsupported schema")
        version = meta.pop("version")
        ings: dict[int, list[dict]] = {}
        for rid, item, grp, count in con.execute(
            "SELECT recipe_id, item, grp, count FROM recipe_ingredients ORDER BY recipe_id, pos"
        ):
            ings.setdefault(rid, []).append({"item": item, "group": grp, "count": count})
        d = {
            "schema": SCHEMA_VERSION,
            "version": version,
            "meta": meta,
            "items": [
                {
                    "id": r[0],
                    "name": r[1],
                    "internal_name": r[2],
                    "value": r[3],
                    "max_stack": r[4],
                    "is_coin": bool(r[5]),
                    "sellable": bool(r[6]),
                }
                for r in con.execute("SELECT * FROM items ORDER BY id")
            ],
            "recipes": [
                {
                    "id": r[0],
                    "result": r[1],
                    "result_stack": r[2],
                    "stations": json.loads(r[3]),
                    "conditions": json.loads(r[4]),
                    "ingredients": ings.get(r[0], []),
                }
                for r in con.execute("SELECT * FROM recipes ORDER BY id")
            ],
            "groups": [
                {"name": r[0], "items": json.loads(r[1]), "icon_item": r[2]}
                for r in con.execute("SELECT * FROM recipe_groups")
            ],
            "shops": [
                {
                    "npc": r[0],
                    "item": r[1],
                    "price": r[2],
                    "currency": r[3],
                    "conditions": json.loads(r[4]),
                    "limited": bool(r[5]),
                }
                for r in con.execute("SELECT * FROM shops")
            ],
            "shimmer": [
                {
                    "kind": r[0],
                    "item": r[1],
                    "count": r[2],
                    "outputs": json.loads(r[3]),
                    "conditions": json.loads(r[4]),
                }
                for r in con.execute("SELECT * FROM shimmer")
            ],
            "npcs": [
                {"id": r[0], "name": r[1], "has_shop": bool(r[2])}
                for r in con.execute("SELECT * FROM npcs ORDER BY id")
            ],
            "npc_drops": [
                {
                    "npc": npc,
                    "drops": [
                        {
                            "item": item,
                            "rate": rate,
                            "min": mn,
                            "max": mx,
                            "conditions": json.loads(conds),
                        }
                        for item, rate, mn, mx, conds in rows
                    ],
                }
                for npc, rows in _drops_by_npc(con)
            ],
            "conditions": _conditions(con),
        }
    finally:
        con.close()
    return Dataset.from_dict(d)


def _drops_by_npc(con: sqlite3.Connection):
    current, rows = None, []
    for npc, item, rate, mn, mx, conds in con.execute(
        "SELECT npc, item, rate, min, max, conditions FROM npc_drops ORDER BY npc, item"
    ):
        if npc != current:
            if current is not None:
                yield current, rows
            current, rows = npc, []
        rows.append((item, rate, mn, mx, conds))
    if current is not None:
        yield current, rows


def _conditions(con: sqlite3.Connection) -> list[dict]:
    try:
        rows = con.execute("SELECT name, scope, description, depends FROM conditions").fetchall()
    except sqlite3.OperationalError:  # databases built before the catalog existed
        return []
    return [
        {"name": n, "scope": sc, "description": de, "depends": json.loads(dp)}
        for n, sc, de, dp in rows
    ]
