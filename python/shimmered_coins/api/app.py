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
from ..search.plan import farm_sources

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


class _WorldSetup(BaseModel):
    """Fields shared by /api/search and /api/plan."""

    npcs: dict[int, float | NpcIn] = Field(
        default_factory=dict, description="npc id -> price adjustment, or {adjustment, zones}"
    )
    flags: list[str] = []
    shimmer: bool = True
    discount: bool = False
    travel_stock: list[int] = []
    kinds: list[str] = []
    time_limit: float = Field(30.0, gt=0, le=MAX_TIME_LIMIT)


class SearchRequest(_WorldSetup):
    dataset: str = "demo"
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


class PlanRequest(_WorldSetup):
    """A plan run: which world's stock to use, plus the same world/NPC setup as search."""

    dataset: str = "demo"
    world: str | None = Field(None, description="a .wld file path to take stock from")
    stock: dict[int, int] | None = Field(
        None, description="item id -> count held (from the chosen world's chests; editable)"
    )
    include: list[int] | None = None
    exclude: list[int] = []
    must_use: list[int] = []
    top_n: int = Field(10, ge=1, le=100)
    wallet: int = Field(0, ge=0, description="copper you are willing to spend on buys")
    kills: int = Field(0, ge=0, description="with farming: > 0 turns farming columns on")
    expert: bool = False
    min_profit: int = Field(0, description="hide plans earning less (copper)")
    show_liquidation: bool = False


def _plan_graph(ds: Dataset, req: PlanRequest, stock: dict[int, int]) -> Graph:
    """Build the move graph with the plan's known items (held + farm-EV) seeding reach."""
    probe = build_graph(
        ds,
        WorldConfig(flags=frozenset(req.flags), shimmer=req.shimmer),
        npc_setup(req),  # type: ignore[arg-type]
        Constraints(
            kinds=frozenset(list(req.kinds) or list(KINDS)) | {SELL},
            include_items=frozenset(req.include) if req.include is not None else None,
            exclude_items=frozenset(req.exclude),
        ),
    )
    farms = farm_sources(probe, False) if req.kills else []
    known = frozenset(stock) | {i for _, ev in farms for i, _ in ev}
    return build_graph(
        ds,
        WorldConfig(flags=frozenset(req.flags), shimmer=req.shimmer),
        npc_setup(req),  # type: ignore[arg-type]
        Constraints(
            kinds=frozenset(list(req.kinds) or list(KINDS)) | {SELL},
            include_items=frozenset(req.include) if req.include is not None else None,
            exclude_items=frozenset(req.exclude),
        ),
        known_items=known,
    )


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


def sprites_dir() -> Path:
    return WEB_DIST.parent / "public" / "sprites"


def sprite_count() -> int:
    d = sprites_dir()
    return sum(1 for _ in d.glob("Item_*.png")) if d.is_dir() else 0


_sprite_job: dict = {"running": False, "done": 0, "total": 0, "error": None, "when": None}


def create_app() -> FastAPI:
    app = FastAPI(title="shimmered-coins")

    @app.get("/api/sprites")
    def sprites() -> dict:
        from ..data.sprites import find_game_installs

        return {"installed": sprite_count(), "sources": find_game_installs()}

    @app.get("/api/sprites/preview")
    def sprites_preview(dataset: str = "demo", limit: int = 60) -> list[dict]:
        d = sprites_dir()
        if not d.is_dir():
            return []
        ds = get_dataset(dataset)
        out = []
        for p in sorted(d.glob("Item_*.png")):
            item = int(p.stem.split("_")[1])
            it = ds.items.get(item)
            if it is None:
                continue
            out.append({"id": item, "name": it.name, "url": f"/sprites/{p.name}"})
            if len(out) >= limit:
                break
        return out

    @app.post("/api/sprites/import")
    def sprites_import(body: dict) -> dict:
        import threading

        from ..data.sprites import find_game_installs, import_sprites

        if _sprite_job["running"]:
            raise HTTPException(409, "an import is already running")
        path = body.get("path")
        sources = {s["path"]: s for s in find_game_installs()}
        if path:
            images = Path(path)
            if not images.is_dir():
                raise HTTPException(400, f"not a directory: {path}")
        elif sources:
            images = Path(next(iter(sources.values()))["path"])
        else:
            raise HTTPException(404, "no Terraria/tModLoader install found; pass a path")
        _sprite_job.update(running=True, done=0, total=0, error=None, when=None)

        def run() -> None:
            try:
                import_sprites(
                    images,
                    sprites_dir(),
                    progress=lambda done, total: _sprite_job.update(done=done, total=total),
                )
            except Exception as e:  # noqa: BLE001 - reported to the UI
                _sprite_job["error"] = str(e)
            finally:
                _sprite_job["running"] = False

        threading.Thread(target=run, daemon=True).start()
        return {"started": True, "path": str(images)}

    @app.get("/api/sprites/import")
    def sprites_import_status() -> dict:
        return {
            "running": _sprite_job["running"],
            "done": _sprite_job["done"],
            "total": _sprite_job["total"],
            "error": _sprite_job["error"],
            "installed": sprite_count(),
        }

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

    @app.get("/api/worlds")
    def list_worlds() -> list[dict]:
        """The .wld saves in the player's Worlds folder (never any content preview)."""
        from .worlds import discover_worlds

        return discover_worlds()

    @app.post("/api/plan")
    def run_plan(req: PlanRequest) -> dict:
        from ..search.plan import PlanParams, plan, stock_from_world

        ds = get_dataset(req.dataset)
        # The UI sends the world *and* the items left checked: the checked list wins, so
        # unchecking an item really keeps it out of the plan. The world is read only when
        # no stock list came with it.
        stock: dict[int, int] = {int(k): int(v) for k, v in (req.stock or {}).items() if v > 0}
        if req.world and req.stock is None:
            from ..data.world import parse_world

            if not req.world.endswith(".wld"):
                raise HTTPException(400, "not a .wld file")
            stock = stock_from_world(parse_world(req.world))
        if not stock and req.kills == 0:
            raise HTTPException(
                400, "nothing to plan from: pick a world (or enable farming in Settings)"
            )
        g = _plan_graph(ds, req, stock)
        rep = plan(
            g,
            stock,
            PlanParams(
                top=req.top_n,
                wallet=req.wallet,
                kills=req.kills,
                expert=req.expert,
                must_use=frozenset(req.must_use),
                min_profit=req.min_profit,
                show_liquidation=req.show_liquidation,
                time_limit=req.time_limit,
            ),
        )
        return {
            "version": ds.version,
            "liquidation": rep.liquidation,
            "stats": {
                "solves": rep.solves,
                "loops": len(rep.loops),
                "elapsed": rep.elapsed,
                "complete": rep.complete,
            },
            "results": [
                {
                    "profit": r.profit,
                    "profit_text": format_coins(r.profit),
                    "capital": r.capital,
                    "capital_text": format_coins(r.capital),
                    "kills": r.kills,
                    "approx": r.approx,
                    "places": r.places,
                    "moves": [_move_json(g, m, n, g.describe(m, n)) for m, n in r.moves],
                    "residue": [
                        {"id": i, "name": g.name(i), "count": c}
                        for i, c in sorted(r.residue.items())
                    ],
                }
                for r in rep.results
            ],
        }

    @app.post("/api/worlds/detail")
    def world_detail(body: dict) -> dict:
        """Per-item stock of one world (every chest summed) so the plan UI can uncheck items."""
        from pathlib import Path as _P

        from ..data.world import parse_world

        path = body.get("path")
        if not path:
            raise HTTPException(400, "path is required")
        if not _P(path).is_file():
            raise HTTPException(404, f"no world file {path}")
        if not path.endswith(".wld"):
            raise HTTPException(400, "not a .wld file")
        ds_id = body.get("dataset", "demo")
        ds = get_dataset(ds_id)
        w = parse_world(path)
        held: dict[int, int] = {}
        for chest in w.chests:
            for iid, _prefix, stack in chest.items:
                held[iid] = held.get(iid, 0) + stack
        stock = [
            {
                "id": iid,
                "name": ds.items[iid].name if iid in ds.items else f"item {iid}",
                "count": count,
            }
            for iid, count in sorted(held.items(), key=lambda kv: -kv[1])
        ]
        return {
            "name": w.name,
            "seed": w.seed,
            "version": w.version,
            "chests": [
                {"x": c.x, "y": c.y, "name": c.name, "items": len(c.items)} for c in w.chests
            ],
            "npcs": [{"type": n.type, "name": n.name, "homeless": n.homeless} for n in w.npcs],
            "stock": stock,
        }

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
