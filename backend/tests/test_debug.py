"""The motor debug API and the per bay calibration, in sim mode."""

import pytest
from fastapi.testclient import TestClient

from cwjh.config import Settings
from cwjh.main import create_app
from cwjh.services.motors import MotorConfig, profile_for

from .test_api import fill, ingredient_id, wait


@pytest.fixture
def client():
    settings = Settings(serial_port="sim", db_url="sqlite://", ack_timeout_s=5, pour_flow_ml_s=2000, invert_direction=False)
    app = create_app(settings)
    with TestClient(app) as client:
        client.app.state.sim.time_scale = 0.01
        wait(lambda: client.get("/api/status").json()["hub_online"])
        yield client


def sim(client):
    return client.app.state.sim


def bay(client, n):
    return client.get("/api/debug/bays").json()["bays"][n - 1]


def config(client, n, **changes):
    return {**bay(client, n)["config"], **changes}


def test_lists_all_bays_with_live_data(client):
    data = client.get("/api/debug/bays").json()
    assert data["online"] and data["fw"] == "sim"
    assert len(data["bays"]) == 12
    first = data["bays"][0]
    assert first["wired"] and first["position"] == 0 and first["switch"] is None
    assert first["profile"] == {"max_speed": 300, "min_speed": 50, "start_ms": 200, "end_ms": 200}


def test_move_by_steps_to_position_and_degrees(client):
    assert client.post("/api/debug/bays/2/move", json={"steps": 40}).json() == {"position": 40}
    assert client.post("/api/debug/bays/2/move", json={"steps": -15}).json() == {"position": 25}
    assert client.post("/api/debug/bays/2/move", json={"position": -30}).json() == {"position": -30}
    assert client.post("/api/debug/bays/2/move", json={"degrees": 10}).json() == {"position": 89}
    assert sim(client).positions[1] == 89
    assert bay(client, 2)["degrees"] == pytest.approx(10, abs=0.01)


def test_move_needs_exactly_one_target(client):
    assert client.post("/api/debug/bays/1/move", json={}).status_code == 422
    assert client.post("/api/debug/bays/1/move", json={"steps": 1, "degrees": 2}).status_code == 422


def test_saved_direction_is_used_for_every_move(client):
    r = client.put("/api/debug/bays/3/config", json=config(client, 3, invert=True))
    assert r.status_code == 200
    client.post("/api/debug/bays/3/move", json={"steps": 100})
    assert sim(client).positions[2] == -100  # the Nano turns the other way
    assert bay(client, 3)["position"] == 100  # in machine terms it is still +100
    # The fill flow uses it too.
    assert client.post("/api/bays/3/fill/start").status_code == 200
    assert sim(client).positions[2] == -round(30 * 8.889)


def test_invalid_config_is_rejected(client):
    r = client.put("/api/debug/bays/1/config", json=config(client, 1, min_speed=400))
    assert r.status_code == 422


def test_profile_follows_the_bottle_weight(client):
    fill(client, 1, "Orange Juice")  # 700 ml at 1.04 g/ml
    item = bay(client, 1)
    assert item["load_g"] == pytest.approx(728)
    # 72.8 % between empty (300 steps/s, 200 ms) and full (150 steps/s, 600 ms).
    assert item["profile"] == {"max_speed": 191, "min_speed": 50, "start_ms": 491, "end_ms": 491}
    client.post("/api/debug/bays/1/move", json={"steps": 10})
    assert sim(client).profiles[1] == (191, 50, 491, 491)
    # A test weight overrides it.
    client.post("/api/debug/bays/1/move", json={"steps": -10, "load_g": 0})
    assert sim(client).profiles[1] == (300, 50, 200, 200)


def test_test_move_goes_out_and_back(client):
    r = client.post("/api/debug/bays/4/test", json={"steps": 60, "repeat": 2})
    assert r.json() == {"position": 0}
    gs = [line.split()[2:] for line in sim(client).received if line.split()[1] == "G"]
    assert gs == [["4", "60"], ["4", "0"], ["4", "60"], ["4", "0"]]


def test_home_without_switch_and_zero_and_hold(client):
    r = client.post("/api/debug/bays/1/home")
    assert r.status_code == 409 and r.json()["detail"] == "hub: no_switch"
    client.post("/api/debug/bays/1/move", json={"steps": 33})
    assert client.post("/api/debug/bays/1/zero").status_code == 200
    assert bay(client, 1)["position"] == 0
    assert client.post("/api/debug/bays/1/hold", json={"on": True}).status_code == 200


def test_raw_requests(client):
    assert client.post("/api/debug/raw", json={"line": "Q"}).json()["reply"].startswith("STATE 0")
    assert client.post("/api/debug/raw", json={"line": "X 1"}).json()["reply"] == "ERR invalid"
    assert client.post("/api/debug/raw", json={"line": "G 13 1"}).json()["reply"] == "ERR invalid"


def test_no_debug_moves_while_a_cocktail_runs(client):
    fill(client, 1, "Orange Juice")
    fill(client, 2, "Grenadine")
    recipe = next(r for r in client.get("/api/recipes").json() if r["name"] == "Virgin Sunrise")
    client.post("/api/cocktail/start", json={"recipe_id": recipe["id"]})
    r = client.post("/api/debug/bays/1/move", json={"steps": 5})
    assert r.status_code == 409 and r.json()["detail"] == "a cocktail is running"
    assert client.post("/api/debug/raw", json={"line": "Q"}).status_code == 200  # read only is fine
    assert client.post("/api/debug/stop").status_code == 200


def test_profile_blend_is_clamped():
    c = MotorConfig.defaults(Settings())
    assert profile_for(c, -50) == profile_for(c, 0)
    assert profile_for(c, 5000) == profile_for(c, c.full_load_g)
    assert profile_for(c, c.full_load_g).max_speed == c.speed_full


def test_unknown_bay_and_solid_check(client):
    assert client.post("/api/debug/bays/13/move", json={"steps": 1}).status_code == 400
    assert ingredient_id(client, "Ice Cubes")


def test_camera_snapshots_are_kept_outside_the_repo(client, tmp_path):
    client.app.state.settings = client.app.state.settings.__class__(
        **{**client.app.state.settings.__dict__, "camera_dir": str(tmp_path)}
    )
    jpeg = b"\xff\xd8\xff\xe0" + b"0" * 100
    r = client.post("/api/debug/camera?label=bay1-Move", content=jpeg, headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 200 and r.json()["saved"].endswith("-bay1-Move.jpg")
    assert (tmp_path / "latest.jpg").read_bytes() == jpeg
    assert client.post("/api/debug/camera", content=b"not a jpeg").status_code == 400


def test_moves_and_requests_ask_the_page_for_a_snapshot(client):
    events = []
    client.app.state.broadcaster.publish = lambda kind, data: events.append((kind, data))
    client.post("/api/debug/bays/1/move", json={"steps": 20})
    client.post("/api/debug/camera/request?label=check 45")
    cameras = [d for k, d in events if k == "camera"]
    assert cameras == [{"label": "bay1-at20", "reason": "move"}, {"label": "check45", "reason": "request"}]
