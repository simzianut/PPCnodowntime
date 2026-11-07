"""REST routes in fixture mode and the real-mode stub."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from api.main import NOT_IMPLEMENTED, create_app
from api.models import (
    EvalResults,
    Location,
    PolicyInfo,
    Recommendation,
    RunStatus,
    Timeline,
)

from .conftest import run_body


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True}


def test_stations_filters(client):
    all_locs = TypeAdapter(list[Location]).validate_python(client.get("/api/stations").json())
    assert len(all_locs) == 116
    bucharest = client.get("/api/stations", params={"county": "B"}).json()
    assert bucharest and all(s["county_code"] == "B" for s in bucharest)
    bbox = "25.9,44.3,26.3,44.6"
    inside = client.get("/api/stations", params={"bbox": bbox}).json()
    assert inside and all(25.9 <= s["lon"] <= 26.3 and 44.3 <= s["lat"] <= 44.6 for s in inside)
    assert client.get("/api/stations", params={"bbox": "1,2,3"}).status_code == 400
    assert "state" not in all_locs[0].charge_points[0].model_dump(exclude_unset=True)


def test_station_state_follows_latest_run(client):
    client.post("/api/runs", json=run_body())
    loc = client.get("/api/stations/L10001").json()
    Location.model_validate(loc)
    assert all("state" in cp for cp in loc["charge_points"])


def test_station_404(client):
    assert client.get("/api/stations/L19999").status_code == 404


def test_policies(client):
    pols = TypeAdapter(list[PolicyInfo]).validate_python(client.get("/api/policies").json())
    assert len(pols) == 4


def test_autonomy(client):
    assert client.get("/api/settings/autonomy").json() == {"mode": "ACT"}
    assert client.put("/api/settings/autonomy", json={"mode": "RECOMMEND"}).json() == {
        "mode": "RECOMMEND"
    }
    assert client.get("/api/settings/autonomy").json() == {"mode": "RECOMMEND"}
    assert client.put("/api/settings/autonomy", json={"mode": "MAYBE"}).status_code == 400


def test_create_run_and_status(client):
    r = client.post("/api/runs", json=run_body())
    assert r.status_code == 201
    run_id = r.json()["run_id"]
    st = RunStatus.model_validate(client.get(f"/api/runs/{run_id}").json())
    assert st.mode == "replay" and st.focus_policy.value == "rl_ppo"
    assert set(st.kpis) == {"always_on", "naive_quiet_hours", "cost_rule", "rl_ppo"}
    assert st.status in ("running", "paused", "done")


def test_create_run_errors(client):
    bad = [
        run_body(policies=["always_on", "magic"]),
        run_body(date="2024-01-01"),
        run_body(date=None),
        run_body(policies=["always_on"], focus_policy="rl_ppo"),
        run_body(speed=0),
        run_body(mode="rewind"),
    ]
    for body in bad:
        assert client.post("/api/runs", json=body).status_code == 400, body


def test_other_test_day_and_live(client):
    r = client.post("/api/runs", json=run_body(date="2024-03-13"))
    assert r.status_code == 201
    st = client.get(f"/api/runs/{r.json()['run_id']}").json()
    assert st["sim_time"].startswith("2024-03-13")
    live = client.post("/api/runs", json=run_body(mode="live", date=None))
    assert live.status_code == 201
    run_id = live.json()["run_id"]
    assert client.get(f"/api/runs/{run_id}").json()["mode"] == "live"
    for action, value in (("seek", 3), ("speed", 10)):
        r = client.post(f"/api/runs/{run_id}/control", json={"action": action, "value": value})
        assert r.status_code == 400


def test_run_404s(client):
    assert client.get("/api/runs/nope").status_code == 404
    assert client.post("/api/runs/nope/control", json={"action": "play"}).status_code == 404
    assert client.get("/api/runs/nope/cp/L10001-1/timeline").status_code == 404


def test_control_validation(client):
    run_id = client.post("/api/runs", json=run_body()).json()["run_id"]
    url = f"/api/runs/{run_id}/control"
    for body in (
        {"action": "speed", "value": 0},
        {"action": "speed", "value": 289},
        {"action": "speed", "value": "fast"},
        {"action": "seek", "value": 10_000},
        {"action": "focus", "value": "magic"},
        {"action": "jump"},
    ):
        assert client.post(url, json=body).status_code == 400, body
    r = client.post(url, json={"action": "pause"})
    st = RunStatus.model_validate(r.json())
    assert st.status == "paused" and st.paused_reason == "user"
    r = client.post(url, json={"action": "speed", "value": 120}).json()
    assert r["speed"] == 120


def test_timeline(client, short_day):
    run_id = client.post("/api/runs", json=run_body(speed=288)).json()["run_id"]
    client.post(f"/api/runs/{run_id}/control", json={"action": "pause"})
    client.post(f"/api/runs/{run_id}/control", json={"action": "seek", "value": 40})
    cp = short_day.cp_ids[0]
    tl = Timeline.model_validate(client.get(f"/api/runs/{run_id}/cp/{cp}/timeline").json())
    assert tl.cp_id == cp and tl.policy.value == "rl_ppo"
    assert tl.states and tl.states[0].from_.hour == 0
    assert all(a.to <= b.from_ for a, b in zip(tl.states, tl.states[1:], strict=False))
    assert len(tl.forecast) == 40
    assert all(c.status.value == "applied" for c in tl.commands)
    assert client.get(f"/api/runs/{run_id}/cp/X-1/timeline").status_code == 404


def test_recommendations_routes(client):
    assert client.get("/api/recommendations").json() == []
    assert client.post("/api/recommendations/999", json={"decision": "approve"}).status_code == 404
    assert client.get("/api/recommendations", params={"status": "nope"}).status_code == 400
    run_id = client.post("/api/runs", json=run_body(speed=288)).json()["run_id"]
    client.post(f"/api/runs/{run_id}/control", json={"action": "seek", "value": 20})
    recs = client.get("/api/recommendations", params={"run_id": run_id}).json()
    TypeAdapter(list[Recommendation]).validate_python(recs)
    applied = client.get("/api/recommendations", params={"status": "applied"}).json()
    assert len(applied) == len([r for r in recs if r["status"] == "applied"])
    if applied:
        r = client.post(f"/api/recommendations/{applied[0]['id']}", json={"decision": "reject"})
        assert r.status_code == 409


def test_eval_route(client):
    body = client.get("/api/eval").json()
    EvalResults.model_validate(body)
    assert body["fixture"] is True


def test_real_mode_stub():
    with TestClient(create_app(fixtures=False)) as c:
        assert c.get("/api/health").json() == {"ok": True}
        for method, url in (
            ("get", "/api/stations"),
            ("get", "/api/eval"),
            ("post", "/api/runs"),
            ("put", "/api/settings/autonomy"),
        ):
            r = getattr(c, method)(url)
            assert r.status_code == 503
            assert r.json() == {"detail": NOT_IMPLEMENTED}


def test_cors(client):
    r = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
