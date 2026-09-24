"""HTTP API for the web UI (``shimcoins serve``).

Datasets are discovered under ``$SHIMCOINS_DATA`` (default ``./data``): every
``<version>/db.sqlite`` and every ``*.json`` dump. The bundled toy world is always available as
``demo``.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..data import Dataset, load_dataset
from ..model import KINDS, Constraints, NpcSetup, WorldConfig, build_graph
from ..model.graph import Graph, Transform, format_coins
from ..search import SearchParams, search

DEMO = Path(__file__).resolve().parent.parent / "demo" / "toy_world.json"
WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"
MAX_TIME_LIMIT = 300.0


def data_dir() -> Path:
    return Path(os.environ.get("SHIMCOINS_DATA", "data"))


def discover() -> dict[str, Path]:
    found = {"demo": DEMO}
    root = data_dir()
    if root.is_dir():
        for db in sorted(root.glob("*/db.sqlite")):
            found[db.parent.name] = db
        for js in sorted(root.glob("*.json")):
            found.setdefault(js.stem, js)
    return found


@lru_cache(maxsize=8)
def _load(path: str, mtime: float) -> Dataset:
    return load_dataset(path)


def get_dataset(ds_id: str) -> Dataset:
    path = discover().get(ds_id)
    if path is None:
        raise HTTPException(404, f"unknown dataset {ds_id!r}")
    return _load(str(path), path.stat().st_mtime)


def dataset_flags(ds: Dataset) -> list[str]:
    conds: set[str] = set()
    for r in ds.recipes:
        conds.update(r.conditions)
    for s in ds.shops:
        conds.update(s.conditions)
    for s in ds.shimmer:
        conds.update(s.conditions)
    return sorted({c.lstrip("!").lower() for c in conds})


class SearchRequest(BaseModel):
    dataset: str = "demo"
    flags: list[str] = []
    shimmer: bool = True
    discount: bool = False
    npcs: dict[int, float] = Field(default_factory=dict, description="npc id -> price adjustment")
    kinds: list[str] = list(KINDS)
    include: list[int] | None = None
    exclude: list[int] = []
    must_use: list[int] = []
    start: list[int] | None = None
    max_steps: int = Field(4, ge=1, le=12)
    top_n: int = Field(10, ge=1, le=100)
    leftover: str = "discard"
    min_gain: float = 0.0
    allow_loss: bool = False
    budget: int | None = None
    time_limit: float = Field(30.0, gt=0, le=MAX_TIME_LIMIT)
    max_nodes: int = Field(500_000, ge=1)


def _stack(g: Graph, pairs: tuple[tuple[int, int], ...], batches: int) -> list[dict]:
    return [{"id": i, "name": g.name(i), "count": c * batches} for i, c in pairs]


def _move_json(g: Graph, t: Transform, batches: int, text: str) -> dict:
    npc = g.dataset.npcs.get(t.npc) if t.npc is not None else None
    return {
        "kind": t.kind,
        "batches": batches,
        "text": text,
        "inputs": _stack(g, t.inputs, batches),
        "outputs": _stack(g, t.outputs, batches),
        "cost": t.cost * batches,
        "revenue": t.revenue * batches,
        "npc": npc.name if npc else None,
        "limited": t.limited,
    }


def create_app() -> FastAPI:
    app = FastAPI(title="shimmered-coins")

    @app.get("/api/datasets")
    def list_datasets() -> list[dict]:
        out = []
        for ds_id, path in discover().items():
            try:
                ds = _load(str(path), path.stat().st_mtime)
                out.append({"id": ds_id, "version": ds.version, "items": len(ds.items)})
            except Exception as e:  # a broken file shouldn't hide the others
                out.append({"id": ds_id, "error": str(e)})
        return out

    @app.get("/api/datasets/{ds_id}")
    def dataset_info(ds_id: str) -> dict:
        ds = get_dataset(ds_id)
        return {
            "id": ds_id,
            "version": ds.version,
            "items": len(ds.items),
            "recipes": len(ds.recipes),
            "shimmer_rules": len(ds.shimmer),
            "npcs": [{"id": n.id, "name": n.name} for n in ds.npcs.values() if n.has_shop],
            "flags": dataset_flags(ds),
        }

    @app.get("/api/datasets/{ds_id}/items")
    def items(ds_id: str, q: str = "", limit: int = 20) -> list[dict]:
        ds = get_dataset(ds_id)
        key = q.strip().lower()
        hits = [it for it in ds.items.values() if key in it.name.lower()] if key else []
        hits.sort(key=lambda it: (not it.name.lower().startswith(key), len(it.name), it.name))
        return [{"id": it.id, "name": it.name, "value": it.value} for it in hits[:limit]]

    @app.post("/api/search")
    def run_search(req: SearchRequest) -> dict:
        ds = get_dataset(req.dataset)
        if not req.npcs:
            raise HTTPException(400, "select at least one NPC")
        bad = set(req.kinds) - set(KINDS)
        if bad or req.leftover not in ("discard", "sell"):
            raise HTTPException(400, "invalid kinds or leftover policy")
        g = build_graph(
            ds,
            WorldConfig(flags=frozenset(req.flags), shimmer=req.shimmer),
            NpcSetup(happiness=dict(req.npcs), discount=req.discount),
            Constraints(
                kinds=frozenset(req.kinds),
                include_items=frozenset(req.include) if req.include is not None else None,
                exclude_items=frozenset(req.exclude),
            ),
        )
        rep = search(
            g,
            SearchParams(
                max_steps=req.max_steps,
                top_n=req.top_n,
                leftover=req.leftover,
                min_gain=req.min_gain,
                allow_loss=req.allow_loss,
                must_use=frozenset(req.must_use),
                start_items=frozenset(req.start) if req.start is not None else None,
                budget=req.budget,
                max_nodes=req.max_nodes,
                time_limit=req.time_limit,
            ),
        )
        return {
            "version": ds.version,
            "moves_available": len(g.transforms),
            "stats": vars(rep.stats),
            "results": [
                {
                    "gain": r.gain,
                    "lp_gain": float(r.lp_gain),
                    "capital": r.capital,
                    "capital_text": format_coins(r.capital),
                    "profit": r.profit,
                    "profit_text": format_coins(r.profit),
                    "repeatable": r.repeatable,
                    "moves": [_move_json(g, m.transform, m.batches, m.text) for m in r.moves],
                    "leftover": [
                        {"id": i, "name": g.name(i), "count": c} for i, c in r.leftover.items()
                    ],
                }
                for r in rep.results
            ],
        }

    if WEB_DIST.is_dir():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        public = WEB_DIST.parent / "public"  # fonts/ and sprites/ you add locally

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            for root in (WEB_DIST, public):
                f = (root / path).resolve()
                if path and f.is_file() and root.resolve() in f.parents:
                    return FileResponse(f)
            if Path(path).suffix:  # a missing asset, not a page
                raise HTTPException(404)
            return FileResponse(WEB_DIST / "index.html")

    return app


app = create_app()
