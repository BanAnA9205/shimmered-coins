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
