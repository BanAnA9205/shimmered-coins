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


def moves_available(capsys) -> int:
    err = capsys.readouterr().err
    return int(err.split(" moves available")[0].rsplit(" ", 1)[1])


def test_cli_build_and_search_stub(tmp_path, capsys):
    db = tmp_path / "db.sqlite"
    assert main(["data", "build", str(TOY), "--out", str(db)]) == 0
    capsys.readouterr()
    # the tree search is gone; search reports the move graph and exits with 3
    assert main(["search", str(db), "--npc", "Smith=0.8", "--npc", "Merchant"]) == 3
    assert moves_available(capsys) > 0


def test_cli_npc_zones(capsys):
    args = ["search", str(TOY), "--npc", "Smith"]
    assert main(args + ["--npc", "Merchant"]) == 3
    plain = moves_available(capsys)
    assert main(args + ["--npc", "Merchant=1@snow,forest"]) == 3
    assert moves_available(capsys) > plain  # the snow-only Frost Charm becomes buyable
    assert main(args + ["--npc", "Merchant", "--travel-stock", "Kit"]) == 3
    assert moves_available(capsys) > plain
    assert main(["data", "info", str(TOY)]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["zones"] == ["snow"]
    assert [f["name"] for f in info["flags"]["progression"]] == ["downedboss", "hardmode"]
