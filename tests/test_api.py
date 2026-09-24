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
    assert info["flags"] == ["downedboss", "hardmode"]
    items = client.get("/api/datasets/demo/items", params={"q": "b"}).json()
    assert [i["name"] for i in items] == ["Bar", "Bundle"]
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
