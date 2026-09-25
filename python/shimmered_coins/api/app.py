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
from ..model import ANY_ZONE, KINDS, SELL, Constraints, NpcSetup, WorldConfig, build_graph
from ..model.conditions import flag_groups, zones
from ..model.graph import Graph, Transform, format_coins
from ..search import SearchParams, search

DEMO = Path(__file__).resolve().parent.parent / "demo" / "toy_world.json"
WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"
MAX_TIME_LIMIT = 300.0


def data_dir() -> Path:
    env = os.environ.get("SHIMCOINS_DATA")
    if env:
        return Path(env)
    root = Path("data")
    if root.is_dir():
        return root
    checkout = Path(__file__).resolve().parents[3] / "data"  # serve from anywhere in the repo
    return checkout if checkout.is_dir() else root


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


class NpcIn(BaseModel):
    adjustment: float = 1.0
    zones: list[str] | str = []  # biome zones, or "any"


class SearchRequest(BaseModel):
    dataset: str = "demo"
    flags: list[str] = []
    shimmer: bool = True
    discount: bool = False
    npcs: dict[int, float | NpcIn] = Field(
        default_factory=dict, description="npc id -> price adjustment, or {adjustment, zones}"
    )
    travel_stock: list[int] = []
    kinds: list[str] = list(KINDS)
    include: list[int] | None = None
    exclude: list[int] = []
    must_use: list[int] = []
    start: list[int] | None = None
    top_n: int = Field(10, ge=1, le=100)
    overhead: float = Field(1.0, ge=0, description="fixed per-loop effort c in the score")
    max_places: int | None = Field(None, ge=1)
    min_gain: float = 0.0
    allow_loss: bool = False
    wallet: int | None = Field(
        None, description="copper: drop unaffordable recipes, scale the rest"
    )
    time_limit: float = Field(30.0, gt=0, le=MAX_TIME_LIMIT)


def npc_setup(req: SearchRequest) -> NpcSetup:
    happiness: dict[int, float] = {}
    npc_zones: dict[int, frozenset[str] | str] = {}
    for npc, v in req.npcs.items():
        if isinstance(v, NpcIn):
            happiness[npc] = v.adjustment
            npc_zones[npc] = ANY_ZONE if v.zones == ANY_ZONE else frozenset(v.zones)
        else:
            happiness[npc] = v
    return NpcSetup(
        happiness=happiness,
        discount=req.discount,
        zones=npc_zones,
        travel_stock=frozenset(req.travel_stock),
    )


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
            "flag_groups": flag_groups(ds),
            "zones": zones(ds),
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
        if bad:
            raise HTTPException(400, f"invalid kinds: {sorted(bad)}")
        g = build_graph(
            ds,
            WorldConfig(flags=frozenset(req.flags), shimmer=req.shimmer),
            npc_setup(req),
            Constraints(
                kinds=frozenset(req.kinds) | {SELL},
                include_items=frozenset(req.include) if req.include is not None else None,
                exclude_items=frozenset(req.exclude),
            ),
        )
        rep = search(
            g,
            SearchParams(
                top=req.top_n,
                overhead=req.overhead,
                min_gain=req.min_gain,
                allow_loss=req.allow_loss,
                must_use=frozenset(req.must_use),
                start_items=frozenset(req.start) if req.start is not None else None,
                wallet=req.wallet,
                max_places=req.max_places,
                time_limit=req.time_limit,
            ),
        )
        return {
            "version": ds.version,
            "moves_available": len(g.transforms),
            "stats": vars(rep.stats),
            "loops": [
                {"moves": [_move_json(g, m.transform, m.batches, m.text) for m in moves]}
                for moves in rep.loops
            ],
            "results": [
                {
                    "gain": r.gain,
                    "lp_gain": float(r.lp_gain),
                    "capital": r.capital,
                    "capital_text": format_coins(r.capital),
                    "profit": r.profit,
                    "profit_text": format_coins(r.profit),
                    "length": r.length,
                    "places": r.places,
                    "score": r.score,
                    "per_trip": r.per_trip,
                    "one_shot": r.one_shot,
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
