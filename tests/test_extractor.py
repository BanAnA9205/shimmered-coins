"""End-to-end: compile the extractor and a mock game assembly, extract, build, search.

Needs Mono (`mcs` + `mono`); skipped otherwise.
"""

import shutil
import subprocess

import pytest
from conftest import ROOT
from shimmered_coins.cli import main
from shimmered_coins.data import load_dataset
from shimmered_coins.model import SHIMMER, NpcSetup, WorldConfig, build_graph

pytestmark = pytest.mark.skipif(
    not (shutil.which("mcs") and shutil.which("mono")), reason="mono not installed"
)


@pytest.fixture(scope="module")
def dump(tmp_path_factory):
    d = tmp_path_factory.mktemp("mock")
    ex = ROOT / "extractor"
    subprocess.run(
        [
            "mcs",
            "-nologo",
            "-langversion:5",
            f"-out:{d / 'TerrariaExtractor.exe'}",
            str(ex / "TerrariaExtractor.cs"),
        ],
        check=True,
    )
    subprocess.run(
        [
            "mcs",
            "-nologo",
            "-target:library",
            f"-out:{d / 'TerrariaServer.exe'}",
            str(ex / "mock" / "MockTerraria.cs"),
        ],
        check=True,
    )
    out = d / "dump.json"
    subprocess.run(
        [
            "mono",
            str(d / "TerrariaExtractor.exe"),
            str(d / "TerrariaServer.exe"),
            "--out",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


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
    shops = {(s.item, s.conditions, s.price) for s in ds.shops}
    assert shops == {
        (1, (), None),
        (4, ("!hardmode",), None),
        (2, ("hardmode", "!bloodmoon"), 2000),
    }
    rules = {(s.kind, s.item, s.conditions) for s in ds.shimmer}
    assert rules == {
        ("decraft", 2, ()),
        ("decraft", 3, ("crimson",)),
        ("decraft", 3, ("!crimson",)),
        ("transmute", 5, ("downedmoonlord",)),
        ("decraft", 6, ("downedboss3",)),
    }
    assert len(ds.meta["pricing_probe"]) == 25


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
    assert main(["search", str(db), "--npc", "Merchant", "--allow-loss", "--max-steps", "3"]) == 0
    assert "9.9.9.9" in capsys.readouterr().out
