"""Real mode (T23): REST and WS on sim.Engine with a small synthetic SimData."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from starlette.websockets import WebSocketDisconnect

from api.engine_mode import EngineManager
from api.main import create_app
from api.models import Location, PolicyInfo, Recommendation, RunStatus, Timeline
from sim.stations_seed import load_seed_stations
from sim.synthetic import generate

from .conftest import recv, recv_until

POLICIES = ["always_on", "naive_quiet_hours"]


@pytest.fixture(scope="module")
def small_data():
    locs, _ = load_seed_stations()
    return generate(seed=0, train_days=3, test_days=2, locations=locs.head(5))


@pytest.fixture
def mgr(small_data, tmp_path):
    return EngineManager(data=small_data, eval_path=tmp_path / "eval_results.json")


@pytest.fixture
def ec(mgr):
    with TestClient(create_app(fixtures=False, manager=mgr)) as c:
        yield c


def body(**kw) -> dict:
    b = {"mode": "replay", "date": None, "policies": POLICIES,
         "focus_policy": "naive_quiet_hours", "lambda": 0.1, "speed": 1}  # fmt: skip
    return {**b, **kw}


def start(ec, **kw) -> str:
    r = ec.post("/api/runs", json=body(**kw))
    assert r.status_code == 201, r.text
    return r.json()["run_id"]


def control(ec, run_id, action, value=None) -> dict:
    r = ec.post(f"/api/runs/{run_id}/control", json={"action": action, "value": value})
    assert r.status_code == 200, r.text
    return RunStatus.model_validate(r.json()).model_dump(mode="json")


def wait_for(ec, run_id, pred, timeout: float = 15) -> dict:
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end:
        st = ec.get(f"/api/runs/{run_id}").json()
        if pred(st):
            return st
        time.sleep(0.01)
    raise AssertionError(f"run never reached the expected state: {st}")


def test_health_and_stations(ec, small_data):
    assert ec.get("/api/health").json() == {"ok": True}
    locs = TypeAdapter(list[Location]).validate_python(ec.get("/api/stations").json())
    assert len(locs) == 5
    assert sum(len(loc.charge_points) for loc in locs) == len(small_data.cps)
    raw = ec.get("/api/stations").json()
    assert all("state" not in cp for loc in raw for cp in loc["charge_points"])
    assert all(isinstance(cp["connectors"], list) for loc in raw for cp in loc["charge_points"])
    first = small_data.locations.iloc[0]
    county = str(first["county_code"])
    same = set(small_data.locations.loc[small_data.locations.county_code == county, "location_id"])
    by_county = ec.get("/api/stations", params={"county": county.lower()}).json()
    assert {s["location_id"] for s in by_county} == same
    lon, lat = float(first["lon"]), float(first["lat"])
    bbox = f"{lon - 1e-4},{lat - 1e-4},{lon + 1e-4},{lat + 1e-4}"
    inside = ec.get("/api/stations", params={"bbox": bbox}).json()
    assert first["location_id"] in {s["location_id"] for s in inside}
    assert all(abs(s["lon"] - lon) <= 1e-4 and abs(s["lat"] - lat) <= 1e-4 for s in inside)
    assert ec.get("/api/stations", params={"bbox": "1,2,3"}).status_code == 400
    assert ec.get("/api/stations", params={"bbox": "a,b,c,d"}).status_code == 400
    loc_id = first["location_id"]
    assert ec.get(f"/api/stations/{loc_id}").json()["location_id"] == loc_id
    assert ec.get("/api/stations/nope").status_code == 404


def test_station_state_after_run(ec, small_data):
    start(ec)
    loc_id = small_data.locations.location_id.iloc[0]
    loc = Location.model_validate(ec.get(f"/api/stations/{loc_id}").json())
    assert all(cp.state is not None for cp in loc.charge_points)


def test_policies_and_unavailable(ec, mgr):
    infos = TypeAdapter(list[PolicyInfo]).validate_python(ec.get("/api/policies").json())
    assert [p.name for p in infos] == ["always_on", "naive_quiet_hours", "cost_rule", "rl_ppo"]
    assert infos[0].available and infos[1].available
    assert infos[2].lambdas == [0.01, 0.03, 0.1, 0.3, 1.0]
    mgr.unavailable["rl_ppo"] = "not installed (test)"
    rl = next(p for p in ec.get("/api/policies").json() if p["name"] == "rl_ppo")
    assert rl["available"] is False
    r = ec.post("/api/runs", json=body(policies=[*POLICIES, "rl_ppo"], focus_policy=None))
    assert r.status_code == 400 and "rl_ppo" in r.json()["detail"]


def test_autonomy(ec):
    assert ec.get("/api/settings/autonomy").json() == {"mode": "ACT"}
    run_id = start(ec)
    assert ec.put("/api/settings/autonomy", json={"mode": "RECOMMEND"}).json() == {
        "mode": "RECOMMEND"
    }
    assert ec.get("/api/settings/autonomy").json() == {"mode": "RECOMMEND"}
    assert ec.app.state.manager.runs[run_id].engine.autonomy == "RECOMMEND"
    assert ec.put("/api/settings/autonomy", json={"mode": "NOPE"}).status_code == 400


def test_run_create_validation(ec, small_data):
    run_id = start(ec)
    st = ec.get(f"/api/runs/{run_id}").json()
    RunStatus.model_validate(st)
    assert st["sim_time"].startswith(small_data.days("test")[0].isoformat())
    assert set(st["kpis"]) == set(POLICIES) and st["focus_policy"] == "naive_quiet_hours"
    assert start(ec, date="2024-03-12")
    assert ec.post("/api/runs", json=body(date="2024-03-20")).status_code == 400
    assert ec.post("/api/runs", json=body(date="2023-10-02")).status_code == 400  # a train day
    live = ec.post("/api/runs", json=body(mode="live"))
    assert live.status_code == 400 and "T12" in live.json()["detail"]
    assert ec.post("/api/runs", json=body(policies=["nope"])).status_code == 400
    assert ec.post("/api/runs", json=body(focus_policy="cost_rule")).status_code == 400
    assert ec.post("/api/runs", json=body(speed=0)).status_code == 400
    default_focus = start(ec, focus_policy=None)
    assert ec.get(f"/api/runs/{default_focus}").json()["focus_policy"] == "naive_quiet_hours"
    assert ec.get("/api/runs/nope").status_code == 404


def test_controls(ec):
    run_id = start(ec, speed=50)
    st = control(ec, run_id, "pause")
    assert st["status"] == "paused" and st["paused_reason"] == "user"
    for bad in (0, 289, 1.5, "x", None):
        r = ec.post(f"/api/runs/{run_id}/control", json={"action": "speed", "value": bad})
        assert r.status_code == 400
    assert control(ec, run_id, "speed", 200)["speed"] == 200
    for bad in (-1, 288, 2.5, "x", None):
        r = ec.post(f"/api/runs/{run_id}/control", json={"action": "seek", "value": bad})
        assert r.status_code == 400
    at30 = control(ec, run_id, "seek", 30)
    assert at30["slot"] == 30 and at30["sim_time"].endswith("02:30:00+02:00")
    at10 = control(ec, run_id, "seek", 10)
    assert at10["slot"] == 10 and at10["status"] == "paused"
    k10, k30 = at10["kpis"]["always_on"], at30["kpis"]["always_on"]
    assert k10["overhead_kwh"] < k30["overhead_kwh"]
    assert control(ec, run_id, "seek", 30)["kpis"] == at30["kpis"]  # seek back is deterministic
    r = ec.post(f"/api/runs/{run_id}/control", json={"action": "focus", "value": "rl_ppo"})
    assert r.status_code == 400
    assert control(ec, run_id, "focus", "always_on")["focus_policy"] == "always_on"
    assert control(ec, run_id, "play")["status"] == "running"
    wait_for(ec, run_id, lambda s: s["slot"] > 30)


def test_timeline_and_summary(ec, small_data):
    run_id = start(ec, speed=288)
    wait_for(ec, run_id, lambda s: s["slot"] >= 40)
    cp_id = small_data.cps["cp_id"].iloc[0]
    tl = Timeline.model_validate(ec.get(f"/api/runs/{run_id}/cp/{cp_id}/timeline").json())
    assert tl.cp_id == cp_id and tl.policy == "naive_quiet_hours" and tl.states
    assert tl.forecast and tl.commands
    assert ec.get(f"/api/runs/{run_id}/cp/nope/timeline").status_code == 404
    assert ec.get(f"/api/runs/nope/cp/{cp_id}/timeline").status_code == 404
    loc_id = small_data.locations.location_id.iloc[0]
    s = ec.get(f"/api/runs/{run_id}/locations/{loc_id}/summary")
    assert s.json() == {"location_id": loc_id, "current_kw": None, "expected_wait_min": None,
                        "anomaly_score": None}  # fmt: skip
    assert ec.get(f"/api/runs/{run_id}/locations/nope/summary").status_code == 404
    assert ec.get(f"/api/runs/nope/locations/{loc_id}/summary").status_code == 404


def test_ws_unknown_run(ec):
    with pytest.raises(WebSocketDisconnect) as exc:
        with ec.websocket_connect("/ws/runs/nope") as ws:
            ws.receive_json()
    assert exc.value.code == 4404


def test_ws_snapshot_ticks_done(ec, small_data):
    run_id = start(ec, speed=1)
    with ec.websocket_connect(f"/ws/runs/{run_id}") as ws:
        snap = recv(ws)
        assert snap["type"] == "snapshot" and len(snap["changes"]) == len(small_data.cps)
        control(ec, run_id, "speed", 288)
        done, seen = recv_until(ws, lambda m: m["type"] == "done")
    ticks = [m for m in seen if m["type"] == "tick"]
    assert ticks[-1]["slot"] == 287 and ticks[-1]["sim_time"].endswith("23:55:00+02:00")
    assert [t["slot"] for t in ticks] == list(range(ticks[0]["slot"], 288))
    assert any(e["type"] == "arrival" for t in ticks for e in t["events"])
    applied = [r for t in ticks for r in t["recommendations"]]
    assert applied and all(r["status"] == "applied" for r in applied)
    assert all(r["policy"] == "naive_quiet_hours" for r in applied)
    assert len({r["id"] for r in applied}) == len(applied)
    st = ec.get(f"/api/runs/{run_id}").json()
    assert st["status"] == "done" and st["kpis"] == done["kpis"] == ticks[-1]["kpis"]
    assert done["kpis"]["always_on"]["saved_kwh"] == 0
    assert done["kpis"]["naive_quiet_hours"]["saved_kwh"] > 0


def test_recommend_flow(ec):
    ec.put("/api/settings/autonomy", json={"mode": "RECOMMEND"})
    run_id = start(ec, speed=288)
    st = wait_for(ec, run_id, lambda s: s["paused_reason"] == "recommendations")
    assert st["status"] == "paused"
    with ec.websocket_connect(f"/ws/runs/{run_id}") as ws:
        snap = recv(ws)
        assert snap["slot"] == st["slot"] and snap["recommendations"]
        pending = ec.get("/api/recommendations", params={"run_id": run_id, "status": "pending"})
        recs = TypeAdapter(list[Recommendation]).validate_python(pending.json())
        assert recs and all(r.policy == "naive_quiet_hours" for r in recs)
        assert {r.id for r in recs} == {r["id"] for r in snap["recommendations"]}
        rec = recs[0]
        ok = ec.post(f"/api/recommendations/{rec.id}", json={"decision": "approve"})
        assert ok.status_code == 200 and ok.json()["status"] == "approved"
        again = ec.post(f"/api/recommendations/{rec.id}", json={"decision": "reject"})
        assert again.status_code == 409
        if len(recs) > 1:
            rej = ec.post(f"/api/recommendations/{recs[1].id}", json={"decision": "reject"})
            assert rej.json()["status"] == "rejected"
        missing = ec.post("/api/recommendations/999999", json={"decision": "approve"})
        assert missing.status_code == 404
        control(ec, run_id, "play")
        tick, _ = recv_until(ws, lambda m: m["type"] == "tick")
        assert tick["slot"] == st["slot"] + 1
        assert {r["id"]: r["status"] for r in tick["recommendations"]}.get(rec.id) == "approved"
        busy = {e["cp_id"] for e in tick["events"]}
        if rec.cp_id not in busy:
            assert {"cp_id": rec.cp_id, "state": rec.target_state} in tick["changes"]


def test_seek_expires_pending_and_ids_stay_unique(ec):
    ec.put("/api/settings/autonomy", json={"mode": "RECOMMEND"})
    run_id = start(ec, speed=288)
    wait_for(ec, run_id, lambda s: s["paused_reason"] == "recommendations")
    with ec.websocket_connect(f"/ws/runs/{run_id}") as ws:
        recv(ws)
        pending = ec.get("/api/recommendations", params={"run_id": run_id, "status": "pending"})
        ids = {r["id"] for r in pending.json()}
        control(ec, run_id, "seek", 5)
        snap, seen = recv_until(ws, lambda m: m["type"] == "snapshot")
        assert not [m for m in seen if m["type"] == "tick"]
        assert ids <= {r["id"] for r in snap["recommendations"] if r["status"] == "expired"}
    r = ec.post(f"/api/recommendations/{min(ids)}", json={"decision": "approve"})
    assert r.status_code == 409
    control(ec, run_id, "play")
    wait_for(ec, run_id, lambda s: s["status"] == "paused" and s["slot"] >= 5)
    all_ids = [r["id"] for r in ec.get("/api/recommendations").json()]
    assert len(all_ids) == len(set(all_ids)) and max(all_ids) > max(ids)


def test_eval(ec, mgr):
    assert ec.get("/api/eval").status_code == 404
    payload = {"generated_at": "2026-11-07T12:00:00Z", "test_days": [], "results": []}
    mgr.eval_path.write_text(json.dumps(payload))
    assert ec.get("/api/eval").json() == payload
