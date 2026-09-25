"""End-to-end: compile the extractor and a mock game assembly, extract, build, load.

Needs Mono (`mcs` + `mono`); skipped otherwise.
"""

import shutil
import subprocess

import pytest
from conftest import ROOT
from shimmered_coins.cli import main
from shimmered_coins.data import load_dataset
from shimmered_coins.model import SHIMMER, NpcSetup, WorldConfig, build_graph, check_pricing

pytestmark = pytest.mark.skipif(
    not (shutil.which("mcs") and shutil.which("mono")), reason="mono not installed"
)


def run(*cmd):
    subprocess.run([str(c) for c in cmd], check=True, capture_output=True)


@pytest.fixture(scope="module")
def dump(tmp_path_factory):
    d = tmp_path_factory.mktemp("mock")
    ex = ROOT / "extractor"
    exe, game = d / "TerrariaExtractor.exe", d / "TerrariaServer.exe"
    run(
        "mcs",
        "-nologo",
        "-langversion:5",
        f"-out:{exe}",
        ex / "TerrariaExtractor.cs",
        ex / "Probing.cs",
    )
    run("mcs", "-nologo", "-target:library", f"-out:{game}", ex / "mock" / "MockTerraria.cs")
    run("mono", exe, game, "--out", d / "dump.json")
    run("mono", exe, game, "--out", d / "legacy.json", "--legacy-shops")
    return d / "dump.json"


def test_extracted_dataset(dump):
    ds = load_dataset(dump)
    assert ds.version == "9.9.9.9"
    assert ds.item_by_name("Iron Bar").value == 750
    assert ds.items[7].is_coin and not ds.items[7].sellable
    assert ds.groups["Wood"].icon_item == 4
    chest = next(r for r in ds.recipes if r.result == 6)
    assert [(i.item, i.group, i.count) for i in chest.ingredients] == [
        (None, "Wood", 8),
        (2, None, 2),
    ]
    assert next(r for r in ds.recipes if r.id == 3).conditions == ("crimson",)
    rules = {(s.kind, s.item, s.conditions) for s in ds.shimmer}
    assert rules == {
        ("decraft", 2, ()),
        ("decraft", 3, ("crimson",)),
        ("decraft", 3, ("!crimson",)),
        ("transmute", 5, ("downedmoonlord",)),
        ("decraft", 6, ("downedboss3",)),
    }
    probe = ds.meta["pricing_probe"]
    assert len(probe) == 50  # 5 adjustments x 5 values, without and with the discount
    check = check_pricing(probe)
    assert check["discount_checked"] == 25
    assert check["discount_match_rate"] is not None


def test_modern_shop_conditions(dump):
    ds = load_dataset(dump)
    shops = {(s.item, s.conditions, s.price) for s in ds.shops}
    assert shops == {
        (1, (), None),
        (4, ("!hardmode",), None),
        (2, ("hardmode", "!bloodmoon"), 2000),
        (8, ("zone:snow",), None),
        (9, ("moon:0",), None),
        (13, ("moon:2",), None),  # "quarter moon" = phase 2 OR 6 -> two entries
        (13, ("moon:6",), None),
        (10, ("zone:snow", "happy<=0.9"), None),  # pylon
        (11, ("npc:22",), None),
        (12, ("player:golf-score-over-500",), None),
        (14, ("!daytime",), None),
        (15, ("downedmechboss1", "downedmechboss2", "downedmechboss3"), None),
    }
    info = ds.condition_info
    assert (info("zone:snow").scope, info("zone:snow").description) == ("zone", "In Snow")
    assert info("happy<=0.9").scope == "happiness"
    assert info("npc:22").scope == "npc_present"
    assert info("player:golf-score-over-500").scope == "player"
    assert info("hardmode").description == "In Hardmode"


def test_legacy_shops(dump):
    ds = load_dataset(dump.parent / "legacy.json")
    shops = {(s.item, s.conditions) for s in ds.shops}
    assert shops == {
        (1, ()),
        (2, ("hardmode",)),
        (8, ("zone:snow",)),
        (9, ("zone:desert",)),
        (14, ("!daytime",)),
        (13, ("!daytime", "zone:desert")),  # only visible when two things change at once
    }


def test_extracted_zones_are_per_npc(dump):
    ds = load_dataset(dump)

    def items(**kw):
        g = build_graph(ds, WorldConfig(flags=frozenset({"hardmode"})), NpcSetup(**kw))
        return {g.name(i) for i in g.buy_of}

    snow = {17: frozenset({"snow"})}
    assert "Snow Globe" not in items(happiness={17: 1.0})
    assert "Snow Globe" in items(happiness={17: 1.0}, zones=snow)
    assert "Snow Pylon" not in items(happiness={17: 1.0}, zones=snow)
    assert "Snow Pylon" in items(happiness={17: 0.9}, zones=snow)
    assert "Guide Book" not in items(happiness={17: 1.0})
    assert "Guide Book" in items(happiness={17: 1.0, 22: 1.0})


def test_extracted_conditions_drive_the_graph(dump):
    ds = load_dataset(dump)
    npcs = NpcSetup(happiness={17: 1.0})

    def shimmer_rules(flags):
        g = build_graph(ds, WorldConfig(flags=frozenset(flags)), npcs)
        return {(t.inputs, t.outputs) for t in g.transforms if t.kind == SHIMMER}

    base = shimmer_rules([])
    # Chain decrafts into Iron Bar outside crimson worlds; the Moon Lord transmute is locked.
    assert (((3, 10),), ((2, 1),)) in base
    assert not any(ins == ((5, 1),) for ins, _ in base)
    crimson = shimmer_rules(["crimson", "DownedMoonLord"])  # flags are case-insensitive
    assert (((3, 10),), ((5, 1),)) in crimson


def test_cli_pipeline(dump, tmp_path, capsys):
    db = tmp_path / "db.sqlite"
    assert main(["data", "build", str(dump), "--out", str(db)]) == 0
    assert load_dataset(db).condition_info("zone:snow").description == "In Snow"
    assert main(["search", str(db), "--npc", "Merchant"]) == 0
    assert "9.9.9.9" in capsys.readouterr().out
