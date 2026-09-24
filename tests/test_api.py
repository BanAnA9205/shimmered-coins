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


def test_search(client):
    body = {
        "dataset": "demo",
        "npcs": {"1": 1.0, "2": 1.0},
        "flags": ["downedBoss"],
        "max_steps": 5,
        "top_n": 2,
    }
    res = client.post("/api/search", json=body).json()
    best = res["results"][0]
    assert best["gain"] == pytest.approx(7.0)
    assert [m["kind"] for m in best["moves"]] == ["buy", "craft", "shimmer", "craft", "sell"]
    assert best["moves"][0]["outputs"] == [{"id": 1, "name": "Ore", "count": 5}]
    assert best["moves"][-1]["revenue"] == 4000
    assert client.post("/api/search", json={**body, "npcs": {}}).status_code == 400


def test_search_with_npc_zones(client):
    body = {
        "dataset": "demo",
        "npcs": {"1": 1.0, "2": {"adjustment": 1.0, "zones": ["snow"]}},
        "max_steps": 3,
        "top_n": 1,
    }
    best = client.post("/api/search", json=body).json()["results"][0]
    # Frost Charm (snow-only, 15s) shimmers into a Ring that sells for 40s
    assert best["gain"] == pytest.approx(4000 / 1500 - 1)
    assert best["moves"][0]["outputs"][0]["name"] == "Frost Charm"
    body["npcs"]["2"] = {"adjustment": 1.0, "zones": "any"}
    best = client.post("/api/search", json=body).json()["results"][0]
    assert "living in snow" in best["moves"][0]["text"]
    body["npcs"]["2"] = 1.0
    res = client.post("/api/search", json=body).json()["results"]
    assert all(r["moves"][0]["outputs"][0]["name"] != "Frost Charm" for r in res)
