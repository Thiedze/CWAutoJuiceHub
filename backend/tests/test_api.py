"""HTTP and WebSocket layer, running the app in sim mode."""

import time

import pytest
from fastapi.testclient import TestClient

from cwjh.config import Settings
from cwjh.main import create_app


@pytest.fixture
def client():
    app = create_app(Settings(mqtt_host="sim", db_url="sqlite://", ack_timeout_s=5))
    with TestClient(app) as client:
        client.app.state.sim.time_scale = 0.01
        yield client


def wait(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.01)


def phase(client):
    return client.get("/api/status").json()["cocktail"]["phase"]


def ingredient_id(client, name):
    return next(i["id"] for i in client.get("/api/ingredients").json() if i["name"] == name)


def fill(client, bay, name):
    assert client.post(f"/api/bays/{bay}/fill/start").status_code == 200
    r = client.post(f"/api/bays/{bay}/fill/confirm", json={"ingredient_id": ingredient_id(client, name), "volume_ml": 700})
    assert r.status_code == 200, r.text
    return r.json()


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "CWAutoJuiceHub" in r.text


def test_status_reports_online_sim(client):
    wait(lambda: client.get("/api/status").json()["hub_online"])


def test_bays_start_empty(client):
    bays = client.get("/api/bays").json()
    assert len(bays) == 12
    assert not any(b["filled"] for b in bays)


def test_fill_rejects_solids(client):
    r = client.post("/api/bays/1/fill/confirm", json={"ingredient_id": ingredient_id(client, "Ice Cubes"), "volume_ml": 100})
    assert r.status_code == 400


def test_recipe_filter(client):
    names = {r["name"] for r in client.get("/api/recipes?alcoholic=false").json()}
    assert "Virgin Sunrise" in names
    assert "Screwdriver" not in names


def test_cocktail_over_http(client):
    wait(lambda: client.get("/api/status").json()["hub_online"])
    fill(client, 1, "Orange Juice")
    fill(client, 2, "Grenadine")
    recipe = next(r for r in client.get("/api/recipes").json() if r["name"] == "Virgin Sunrise")
    assert recipe["available"]

    with client.websocket_connect("/api/ws") as ws:
        kinds = {ws.receive_json()["type"] for _ in range(2)}
        assert {"online", "cocktail"} <= kinds

    assert client.post("/api/cocktail/start", json={"recipe_id": recipe["id"]}).status_code == 200
    assert client.post("/api/cocktail/next").status_code == 200  # glass
    wait(lambda: phase(client) == "await_manual")
    client.post("/api/cocktail/next")  # ice
    wait(lambda: phase(client) == "done")

    bays = client.get("/api/bays").json()
    assert bays[0]["volume_ml"] == pytest.approx(550, abs=0.2)
    assert bays[1]["volume_ml"] == pytest.approx(685, abs=0.2)


def test_next_without_cocktail_is_conflict(client):
    client.post("/api/cocktail/next")  # idle -> stays idle
    assert phase(client) == "idle"
    wait(lambda: client.get("/api/status").json()["hub_online"])
    assert client.post("/api/cocktail/start", json={"recipe_id": 9999}).status_code == 400
