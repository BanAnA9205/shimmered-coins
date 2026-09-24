import dataclasses
import json

from conftest import TOY
from shimmered_coins.cli import main, parse_coins
from shimmered_coins.data import apply_overrides, diff, load_dataset
from shimmered_coins.data import db as dbmod


def test_json_roundtrip(toy):
    again = type(toy).from_dict(json.loads(json.dumps(toy.to_dict())))
    assert again == toy


def test_sqlite_roundtrip(toy, tmp_path):
    path = tmp_path / "toy" / "db.sqlite"
    dbmod.save(toy, path)
    loaded = load_dataset(path)
    assert loaded.items == toy.items
    assert loaded.recipes == toy.recipes
    assert loaded.groups == toy.groups
    assert loaded.shops == toy.shops
    assert loaded.shimmer == toy.shimmer
    assert loaded.npcs == toy.npcs
    assert load_dataset(path.parent).version == "toy-1"


def test_overrides(toy):
    ring = toy.item_by_name("Ring").id
    kit = toy.item_by_name("Kit").id
    out = apply_overrides(
        toy,
        {
            "items": {str(ring): {"value": 1}},
            "remove_recipes": [0],
            "remove_shimmer": [{"item": kit}],
            "add_shimmer": [{"kind": "transmute", "item": ring, "outputs": [[kit, 1]]}],
        },
    )
    assert out.items[ring].value == 1
    assert all(r.id != 0 for r in out.recipes)
    assert not any(s.item == kit for s in out.shimmer)
    assert any(s.item == ring and s.kind == "transmute" for s in out.shimmer)


def test_diff(toy):
    ring = toy.item_by_name("Ring").id
    items = dict(toy.items)
    items[ring] = dataclasses.replace(items[ring], value=15000)
    newer = dataclasses.replace(
        toy,
        version="toy-2",
        items=items,
        shimmer=[s for s in toy.shimmer if not (s.kind == "transmute" and s.item == 1)],
    )
    d = diff(toy, newer)
    assert d["value_changed"] == [("Ring", 20000, 15000)]
    assert d["shimmer_removed"] == ["transmute: 1x Ore -> 1x Gem [downedBoss]"]
    assert not d["recipes_added"] and not d["recipes_removed"]


def test_parse_coins():
    assert parse_coins("1p 2g 3s 4c") == 1_020_304
    assert parse_coins("250") == 250
    assert parse_coins("5g") == 50_000


def test_cli_search_and_build(tmp_path, capsys):
    assert (
        main(
            [
                "search",
                str(TOY),
                "--all-npcs",
                "1",
                "--flag",
                "downedBoss",
                "--max-steps",
                "5",
                "--top",
                "2",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "gain +700.00%" in out
    db = tmp_path / "db.sqlite"
    assert main(["data", "build", str(TOY), "--out", str(db)]) == 0
    assert (
        main(
            [
                "search",
                str(db),
                "--npc",
                "Smith=0.8",
                "--npc",
                "Merchant",
                "--json",
                "--max-steps",
                "5",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    payload = json.loads(out[out.index("{") :])
    assert payload["results"][0]["moves"][0]["kind"] == "buy"


def test_cli_npc_zones(capsys):
    args = ["search", str(TOY), "--npc", "Smith", "--max-steps", "3", "--top", "1"]
    assert main(args + ["--npc", "Merchant=1@snow,forest"]) == 0
    assert "Frost Charm" in capsys.readouterr().out
    assert main(args + ["--npc", "Merchant@any"]) == 0
    assert "living in snow" in capsys.readouterr().out
    assert main(args + ["--npc", "Merchant", "--travel-stock", "Kit"]) == 0
    capsys.readouterr()
    assert main(["data", "info", str(TOY)]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["zones"] == ["snow"]
    assert [f["name"] for f in info["flags"]["progression"]] == ["downedboss", "hardmode"]
