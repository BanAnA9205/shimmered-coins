import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
from shimmered_coins.api.app import create_app  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIMCOINS_DATA", str(tmp_path))
    return TestClient(create_app())


def test_datasets_and_info(client):
    ids = [d["id"] for d in client.get("/api/datasets").json()]
    assert ids == ["demo"]
    info = client.get("/api/datasets/demo").json()
    assert {n["name"] for n in info["npcs"]} == {"Smith", "Merchant"}  # Guide has no shop
    assert info["flag_groups"] == {
        "progression": [
            {"name": "downedboss", "description": "The boss has been defeated"},
            {"name": "hardmode", "description": "Hardmode"},
        ]
    }
    assert info["zones"] == ["snow"]
    items = client.get("/api/datasets/demo/items", params={"q": "bu"}).json()
    assert [i["name"] for i in items] == ["Bundle"]
    assert client.get("/api/datasets/nope").status_code == 404


def test_search_returns_recipes(client):
    body = {"dataset": "demo", "npcs": {"1": 1.0, "2": 1.0}, "flags": ["downedboss"]}
    res = client.post("/api/search", json=body)
    assert res.status_code == 200
    d = res.json()
    assert d["stats"]["solves"] > 0 and d["loops"] == []
    top = d["results"][0]
    assert top["gain"] == pytest.approx(7.0)
    assert top["length"] == 2 and top["places"] == ["Smith", "shimmer pool"]
    assert top["moves"][-1]["kind"] == "sell"
    assert client.post("/api/search", json={**body, "npcs": {}}).status_code == 400


def test_npc_setup_parses_zones():
    from shimmered_coins.api.app import SearchRequest, npc_setup

    req = SearchRequest(
        npcs={1: 1.0, 2: {"adjustment": 0.9, "zones": ["snow"]}, 3: {"zones": "any"}},
        travel_stock=[5],
    )
    setup = npc_setup(req)
    assert setup.happiness == {1: 1.0, 2: 0.9, 3: 1.0}
    assert setup.zones_of(2) == frozenset({"snow"})
    assert setup.zones_of(3) == "any"
    assert setup.travel_stock == frozenset({5})


def test_plan_endpoint_runs(client):
    """plans craft from the posted stock; empty input is rejected rather than planned."""
    body = {
        "dataset": "demo",
        "stock": {"1": 6, "2": 1, "3": 2},  # Ore, Bar, Gem -> craft the Ring
        "npcs": {"1": 1.0, "2": 1.0},
        "flags": ["downedboss"],
        "top_n": 3,
    }
    res = client.post("/api/plan", json=body)
    assert res.status_code == 200
    d = res.json()
    assert d["stats"]["solves"] > 0
    assert d["results"] and d["results"][0]["profit"] >= 0

    r_bad = client.post("/api/plan", json={"dataset": "demo"})
    assert r_bad.status_code == 400
    assert client.post("/api/worlds/detail", json={"path": "?"}).status_code == 404


def test_worlds_endpoint_lists_saves_or_empty(client):
    worlds = client.get("/api/worlds").json()
    assert isinstance(worlds, list)
    for w in worlds:
        assert "path" in w and ("error" in w or "chests" in w)


def test_plan_uses_only_the_checked_stock(client, tmp_path):
    """The UI posts the world *and* the items left checked; unchecked items stay out."""
    from test_world import build_wld

    data, _ = build_wld(chests=(((1, 2), "", [(1, 0, 6), (2, 0, 1), (3, 0, 2)]),))
    world = tmp_path / "w.wld"
    world.write_bytes(data)
    body = {
        "dataset": "demo",
        "world": str(world),
        "npcs": {"1": 1.0, "2": 1.0},
        "flags": ["downedboss"],
        "top_n": 5,
        "show_liquidation": True,
    }
    everything = client.post("/api/plan", json=body).json()
    assert any("Gem" in m["text"] for r in everything["results"] for m in r["moves"])
    only_ore = client.post("/api/plan", json={**body, "stock": {"1": 6}}).json()
    for r in only_ore["results"]:
        for m in r["moves"]:
            assert not (m["text"].startswith("take") and ("Gem" in m["text"] or "Bar" in m["text"]))
