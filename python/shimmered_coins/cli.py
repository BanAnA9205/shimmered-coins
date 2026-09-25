"""Command line interface: ``shimcoins data ...`` and ``shimcoins search ...``."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .data import build_db, diff, load_dataset
from .data import fetch as fetch_mod
from .data.fetch import FetchError
from .data.schema import Dataset
from .model import (
    ANY_ZONE,
    KINDS,
    SELL,
    Constraints,
    NpcSetup,
    WorldConfig,
    build_graph,
    check_pricing,
)
from .model.conditions import flag_groups, zones
from .search import SearchParams, search

_COIN_RE = re.compile(r"(\d+)\s*([pgsc])", re.I)
_COIN_UNITS = {"p": 1_000_000, "g": 10_000, "s": 100, "c": 1}


def parse_coins(text: str) -> int:
    """'1p 2g 50s' -> copper. A bare number is copper."""
    text = text.strip()
    if text.isdigit():
        return int(text)
    parts = _COIN_RE.findall(text)
    if not parts:
        raise argparse.ArgumentTypeError(f"bad coin amount {text!r}")
    return sum(int(n) * _COIN_UNITS[u.lower()] for n, u in parts)


def _items(ds: Dataset, names: list[str] | None) -> frozenset[int]:
    out = set()
    for n in names or []:
        out.add(int(n) if n.isdigit() else ds.item_by_name(n).id)
    return frozenset(out)


def _npcs(
    ds: Dataset, specs: list[str] | None, all_at: str | None
) -> tuple[dict[int, float], dict[int, frozenset[str] | str]]:
    """Parse ``NAME[=ADJ][@ZONE,ZONE|@any]`` specs into happiness and zone maps.

    ``all_at`` (from ``--all-npcs``) is ``ADJ`` or ``ADJ@any``, applied to every shop NPC.
    """
    happy: dict[int, float] = {}
    npc_zones: dict[int, frozenset[str] | str] = {}

    def set_zones(npc: int, where: str) -> None:
        where = where.strip().lower()
        npc_zones[npc] = (
            ANY_ZONE if where == ANY_ZONE else frozenset(z.strip() for z in where.split(","))
        )

    if all_at is not None:
        head, _, where = all_at.partition("@")
        happy = {n.id: float(head) for n in ds.npcs.values() if n.has_shop}
        if where:
            for npc in happy:
                set_zones(npc, where)
    for spec in specs or []:
        head, _, where = spec.partition("@")
        name, _, adj = head.partition("=")
        npc = int(name) if name.isdigit() else ds.npc_by_name(name).id
        happy[npc] = float(adj) if adj else 1.0
        if where:
            set_zones(npc, where)
    return happy, npc_zones


def cmd_data_build(a: argparse.Namespace) -> int:
    overrides = None
    if a.overrides:
        with open(a.overrides, encoding="utf-8") as f:
            overrides = json.load(f)
    out, ds = build_db(a.source, a.out, overrides)
    print(
        f"wrote {out}: {len(ds.items)} items, {len(ds.recipes)} recipes, "
        f"{len(ds.shops)} shop entries, {len(ds.shimmer)} shimmer rules"
    )
    return 0


def cmd_data_fetch(a: argparse.Namespace) -> int:
    if a.list:
        return fetch_mod.list_servers()
    version = a.version
    if not version:
        if not sys.stdin.isatty():
            print("pass a version (or --yes with one) when not interactive", file=sys.stderr)
            return 2
        version = fetch_mod.choose_version()
    try:
        db = fetch_mod.fetch(
            version,
            fetch_mod.data_root(),
            confirm=None if a.yes else _confirm,
            keep=a.keep,
            progress=fetch_mod._default_progress,
        )
    except FetchError as e:
        print(f"fetch failed: {e}", file=sys.stderr)
        return 2
    print(f"\nnext: shimcoins search {db} --all-npcs 1 --max-steps 5")
    return 0


def _confirm(question: str) -> bool:
    try:
        return input(question).strip().lower() not in ("n", "no")
    except EOFError:
        return False


def cmd_data_info(a: argparse.Namespace) -> int:
    ds = load_dataset(a.source)
    print(
        json.dumps(
            {
                "version": ds.version,
                "meta": {k: v for k, v in ds.meta.items() if k != "pricing_probe"},
                "items": len(ds.items),
                "recipes": len(ds.recipes),
                "groups": len(ds.groups),
                "shops": len(ds.shops),
                "shimmer": len(ds.shimmer),
                "npcs": [n.name for n in ds.npcs.values()],
                "pricing_check": check_pricing(ds.meta.get("pricing_probe", [])),
                "flags": flag_groups(ds),
                "zones": zones(ds),
            },
            indent=2,
        )
    )
    return 0


def cmd_data_diff(a: argparse.Namespace) -> int:
    d = diff(load_dataset(a.a), load_dataset(a.b))
    if a.json:
        print(json.dumps(d, indent=2))
        return 0
    print(f"{d['from']} -> {d['to']}")
    for key, val in d.items():
        if key in ("from", "to") or not val:
            continue
        print(f"\n{key} ({len(val)}):")
        for v in val:
            print(f"  {v}")
    return 0


def cmd_search(a: argparse.Namespace) -> int:
    ds = load_dataset(a.dataset)
    happiness, npc_zones = _npcs(ds, a.npc, a.all_npcs)
    if not happiness:
        print("no NPCs given: use --npc NAME[=ADJ] or --all-npcs ADJ", file=sys.stderr)
        return 2
    world = WorldConfig(flags=frozenset(a.flag or ()), shimmer=not a.no_shimmer)
    npcs = NpcSetup(
        happiness=happiness,
        discount=a.discount,
        zones=npc_zones,
        travel_stock=_items(ds, a.travel_stock),
    )
    kinds = frozenset(a.kinds.split(",")) if a.kinds else frozenset(KINDS)
    kinds |= {SELL}  # selling is implicit: the residue is sold at the end
    cons = Constraints(
        kinds=kinds,
        include_items=_items(ds, a.include) if a.include else None,
        exclude_items=_items(ds, a.exclude),
    )
    g = build_graph(ds, world, npcs, cons)
    params = SearchParams(
        top=a.top,
        overhead=a.overhead,
        min_gain=a.min_gain,
        allow_loss=a.allow_loss,
        must_use=_items(ds, a.must_use),
        start_items=_items(ds, a.start) if a.start else None,
        wallet=a.wallet,
        max_places=a.max_places,
        time_limit=a.time_limit,
    )
    rep = search(g, params)
    if a.json:
        print(
            json.dumps(
                {
                    "version": ds.version,
                    "stats": rep.stats.__dict__,
                    "loops": [
                        {"moves": [{"kind": m.transform.kind, "text": m.text} for m in moves]}
                        for moves in rep.loops
                    ],
                    "results": [
                        {
                            "gain": r.gain,
                            "lp_gain": r.lp_gain,
                            "capital": r.capital,
                            "profit": r.profit,
                            "length": r.length,
                            "places": r.places,
                            "score": r.score,
                            "per_trip": r.per_trip,
                            "one_shot": r.one_shot,
                            "moves": [
                                {"kind": m.transform.kind, "batches": m.batches, "text": m.text}
                                for m in r.moves
                            ],
                            "leftover": {g.name(i): c for i, c in r.leftover.items()},
                        }
                        for r in rep.results
                    ],
                },
                indent=2,
            )
        )
        return 0
    s = rep.stats
    print(
        f"{ds.version}: {len(g.transforms)} moves available; {s.solves} LP solves "
        f"({s.loops} free loops) in {s.elapsed:.2f}s"
        + ("" if s.complete else "  [stopped early: limit reached]")
    )
    for moves in rep.loops:
        print("\ninfinite money loop (no purchase needed, repeatable forever):")
        for m in moves:
            print(f"  {m.text}")
    if not rep.results:
        print("no profitable recipe" + ("" if a.allow_loss else " (try --allow-loss)"))
    for k, r in enumerate(rep.results, 1):
        print(f"\n#{k} {r.summary(g)}")
    return 0


def cmd_sprites(a: argparse.Namespace) -> int:
    from .data.sprites import find_game_installs, import_sprites

    out = Path(a.out) if a.out else fetch_mod.repo_root() / "web" / "public" / "sprites"
    if a.dir:
        if not Path(a.dir).is_dir():
            print(f"not a directory: {a.dir}", file=sys.stderr)
            return 2
        sources = [{"app": a.dir, "path": a.dir, "items": -1}]
    else:
        sources = find_game_installs()
        if not sources:
            print(
                "no Terraria or tModLoader install found; pass the Content/Images dir",
                file=sys.stderr,
            )
            return 2
    print("found game installs:")
    for k, src in enumerate(sources, 1):
        label = f"{k}. {src['app']}: {src['path']}" + (
            f" ({src['items']} item sprites)" if src["items"] >= 0 else ""
        )
        print(f"  {label}")
    pick = sources[0]
    if len(sources) > 1 and not a.yes:
        choice = input(f"import from [1-{len(sources)}] (default 1): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(sources):
            pick = sources[int(choice) - 1]
    done, skipped = import_sprites(Path(pick["path"]), out, bgra=a.bgra, force=a.force)
    msg = f"wrote {done} sprite(s) to {out}"
    if skipped:
        msg += f" ({skipped} already present, use --force to redo)"
    print(msg)
    print("hard-refresh the page (or restart 'shimcoins serve') to see them")
    return 0


def cmd_serve(a: argparse.Namespace) -> int:
    import os

    try:
        import uvicorn
    except ImportError:
        print("the web UI needs extra packages: pip install -e '.[api]'", file=sys.stderr)
        return 2
    if a.data:
        os.environ["SHIMCOINS_DATA"] = a.data
    print(f"shimmered-coins UI on http://{a.host}:{a.port}")
    uvicorn.run("shimmered_coins.api.app:app", host=a.host, port=a.port, log_level="warning")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="shimcoins", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("data", help="dataset tools").add_subparsers(dest="data_cmd", required=True)
    f = d.add_parser(
        "fetch",
        help="download a game server from terraria.org, extract its data, build the db",
    )
    f.add_argument("version", nargs="?", help="e.g. 1.4.5.8 (or zip code 1458); menu if omitted")
    f.add_argument("--yes", action="store_true", help="don't ask before downloading")
    f.add_argument("--keep", action="store_true", help="keep the downloaded server files")
    f.add_argument(
        "--list", action="store_true", help="probe terraria.org for available versions and exit"
    )
    f.set_defaults(func=cmd_data_fetch)
    b = d.add_parser("build", help="extractor JSON -> curated SQLite")
    b.add_argument("source")
    b.add_argument("--overrides", help="JSON curation patch")
    b.add_argument("--out", help="default: data/<version>/db.sqlite")
    b.set_defaults(func=cmd_data_build)
    i = d.add_parser("info", help="summarize a dataset")
    i.add_argument("source")
    i.set_defaults(func=cmd_data_info)
    df = d.add_parser("diff", help="what changed between two versions")
    df.add_argument("a")
    df.add_argument("b")
    df.add_argument("--json", action="store_true")
    df.set_defaults(func=cmd_data_diff)

    s = sub.add_parser("search", help="find profitable move sequences")
    s.add_argument("dataset", help="JSON dump, db.sqlite or data/<version>/")
    s.add_argument(
        "--npc",
        action="append",
        metavar="NAME[=ADJ][@ZONES]",
        help="present NPC, its happiness price adjustment (0.75 happiest..1.5) and the biome "
        "zones it lives in, e.g. Merchant=0.9@snow or Merchant@any (try every biome)",
    )
    s.add_argument(
        "--travel-stock", action="append", help="item the Travelling Merchant sells today"
    )
    s.add_argument(
        "--all-npcs",
        metavar="ADJ[@ZONES]",
        help="all shop NPCs present at ADJ, optionally living somewhere (e.g. 0.75@any)",
    )
    s.add_argument("--discount", action="store_true", help="Discount Card equipped")
    s.add_argument("--flag", action="append", help="world flag, e.g. hardmode, downedMoonLord")
    s.add_argument("--no-shimmer", action="store_true")
    s.add_argument("--top", type=int, default=10, help="number of recipes to report")
    s.add_argument(
        "--overhead",
        type=float,
        default=1.0,
        metavar="C",
        help="fixed per-loop effort c in the score ln(1+gain)/(L+c); 0 = pure compounding",
    )
    s.add_argument(
        "--max-places", type=int, help="drop recipes whose tour visits more than this many places"
    )
    s.add_argument("--min-gain", type=float, default=0.0)
    s.add_argument("--allow-loss", action="store_true", help="also report the least-bad recipes")
    s.add_argument("--kinds", help="comma list from buy,craft,shimmer (selling is always implicit)")
    s.add_argument("--include", action="append", help="only use these items (repeatable)")
    s.add_argument("--exclude", action="append", help="never use these items")
    s.add_argument("--must-use", action="append", help="recipe must involve these items")
    s.add_argument("--start", action="append", help="only buy these items")
    s.add_argument(
        "--wallet",
        type=parse_coins,
        metavar="COINS",
        help="your wallet, e.g. '50g': recipes needing more are dropped, the rest are shown "
        "scaled to it (one-shot recipes stay minimal)",
    )
    s.add_argument("--time-limit", type=float, default=30.0)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_search)

    sp = sub.add_parser(
        "sprites", help="import item art from your Terraria install (auto-detected)"
    )
    sp.add_argument("dir", nargs="?", help="explicit Content/Images dir (scanned if omitted)")
    sp.add_argument("--out", help="default: <repo>/web/public/sprites")
    sp.add_argument("--yes", action="store_true", help="don't ask which install to use")
    sp.add_argument("--force", action="store_true", help="overwrite existing PNGs")
    sp.add_argument(
        "--bgra", action="store_true", help="swap R and B channels if the colors look wrong"
    )
    sp.set_defaults(func=cmd_sprites)

    v = sub.add_parser("serve", help="run the web UI + API")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8765)
    v.add_argument("--data", help="dataset directory (default ./data or $SHIMCOINS_DATA)")
    v.set_defaults(func=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
