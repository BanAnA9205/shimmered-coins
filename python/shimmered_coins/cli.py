"""Command line interface: ``shimcoins data ...`` and ``shimcoins search ...``."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .data import apply_overrides, diff, load_dataset
from .data import db as dbmod
from .data.schema import Dataset
from .model import KINDS, Constraints, NpcSetup, WorldConfig, build_graph, check_pricing
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


def _npcs(ds: Dataset, specs: list[str] | None, all_at: float | None) -> dict[int, float]:
    happy: dict[int, float] = {}
    if all_at is not None:
        happy = {n.id: all_at for n in ds.npcs.values() if n.has_shop}
    for spec in specs or []:
        name, _, adj = spec.partition("=")
        npc = int(name) if name.isdigit() else ds.npc_by_name(name).id
        happy[npc] = float(adj) if adj else 1.0
    return happy


def cmd_data_build(a: argparse.Namespace) -> int:
    ds = load_dataset(a.source)
    if a.overrides:
        with open(a.overrides, encoding="utf-8") as f:
            ds = apply_overrides(ds, json.load(f))
    out = Path(a.out) if a.out else Path("data") / ds.version / "db.sqlite"
    dbmod.save(ds, out)
    print(
        f"wrote {out}: {len(ds.items)} items, {len(ds.recipes)} recipes, "
        f"{len(ds.shops)} shop entries, {len(ds.shimmer)} shimmer rules"
    )
    return 0


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
    happiness = _npcs(ds, a.npc, a.all_npcs)
    if not happiness:
        print("no NPCs given: use --npc NAME[=ADJ] or --all-npcs ADJ", file=sys.stderr)
        return 2
    world = WorldConfig(flags=frozenset(a.flag or ()), shimmer=not a.no_shimmer)
    npcs = NpcSetup(happiness=happiness, discount=a.discount)
    kinds = frozenset(a.kinds.split(",")) if a.kinds else frozenset(KINDS)
    cons = Constraints(
        kinds=kinds,
        include_items=_items(ds, a.include) if a.include else None,
        exclude_items=_items(ds, a.exclude),
    )
    g = build_graph(ds, world, npcs, cons)
    params = SearchParams(
        max_steps=a.max_steps,
        top_n=a.top,
        leftover=a.leftover,
        min_gain=a.min_gain,
        allow_loss=a.allow_loss,
        must_use=_items(ds, a.must_use),
        start_items=_items(ds, a.start) if a.start else None,
        exhaustive=a.exhaustive,
        budget=a.budget,
        max_nodes=a.max_nodes,
        time_limit=a.time_limit,
    )
    rep = search(g, params)
    if a.json:
        print(
            json.dumps(
                {
                    "version": ds.version,
                    "stats": rep.stats.__dict__,
                    "results": [
                        {
                            "gain": r.gain,
                            "lp_gain": r.lp_gain,
                            "capital": r.capital,
                            "profit": r.profit,
                            "repeatable": r.repeatable,
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
        f"{ds.version}: {len(g.transforms)} moves available; searched {s.nodes} nodes "
        f"({s.pruned} pruned, {s.lp_solves} LPs) in {s.elapsed:.2f}s"
        + ("" if s.complete else "  [stopped early: limit reached]")
    )
    if not rep.results:
        print(
            "no sequence found" + ("" if a.allow_loss else " with positive gain (try --allow-loss)")
        )
    for k, r in enumerate(rep.results, 1):
        print(f"\n#{k} {r.summary(g)}")
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
        metavar="NAME[=ADJ]",
        help="present NPC and its happiness price adjustment (0.75 happiest..1.5)",
    )
    s.add_argument("--all-npcs", type=float, metavar="ADJ", help="all shop NPCs present at ADJ")
    s.add_argument("--discount", action="store_true", help="Discount Card equipped")
    s.add_argument("--flag", action="append", help="world flag, e.g. hardmode, downedMoonLord")
    s.add_argument("--no-shimmer", action="store_true")
    s.add_argument("--max-steps", type=int, default=4)
    s.add_argument("--top", type=int, default=10)
    s.add_argument("--leftover", choices=["discard", "sell"], default="discard")
    s.add_argument("--min-gain", type=float, default=0.0)
    s.add_argument("--allow-loss", action="store_true")
    s.add_argument("--kinds", help=f"comma list from {','.join(KINDS)}")
    s.add_argument("--include", action="append", help="only use these items (repeatable)")
    s.add_argument("--exclude", action="append", help="never use these items")
    s.add_argument("--must-use", action="append", help="sequence must involve these items")
    s.add_argument("--start", action="append", help="first buy must be one of these items")
    s.add_argument("--budget", type=parse_coins, help="scale plans up to this, e.g. '5p 20g'")
    s.add_argument("--exhaustive", action="store_true", help="no pruning (slow; for checking)")
    s.add_argument("--max-nodes", type=int, default=500_000)
    s.add_argument("--time-limit", type=float, default=60.0)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_search)

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
