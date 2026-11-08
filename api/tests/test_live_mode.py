"""Live mode API (decision 0070): synthetic statuses and a fake clock, no sleeping on wall time."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api import live_mode
from api.engine_mode import EngineManager
from api.main import create_app
from api.models import Recommendation, RunStatus
from model.stubs import ConstantForecaster
from sim.stations_seed import load_seed_stations
from sim.synthetic import generate
from sim.tests.test_live import refs_for, row, ts, write_rows

from .conftest import recv, recv_until

T0 = ts("2026-11-08 10:02:30")  # start slot 119 (09:55) is the last complete one


class FakeClock:
    speed = 1.0

    def __init__(self, now: float) -> None:
        self.t = now

    def now(self) -> float:
        return self.t


@pytest.fixture(scope="module")
def small_data():
    locs, _ = load_seed_stations()
    return generate(seed=0, train_days=3, test_days=2, locations=locs.head(5))


@pytest.fixture
def live(small_data, tmp_path):
    cps = small_data.cps["cp_id"].tolist()
    clock = FakeClock(T0)
    mgr = EngineManager(data=small_data, eval_path=tmp_path / "eval.json")
    mgr.live_dir = tmp_path / "live"
    mgr.source_refs = refs_for(cps)
    mgr.live_clock = lambda: clock
    mgr.live_poll_s = 0.005
    mgr.live_forecaster_factory = lambda: (ConstantForecaster(0, 0, 0, 0), "constant_stub")
    mgr._live_traffic = {}
    snap = ts("2026-11-08 09:50:03")
    write_rows(mgr.live_dir, [row(snap, cp, "AVAILABLE") for cp in cps]
               + [row(snap, cps[1], "CHARGING", sua=snap - 30)])  # fmt: skip
    with TestClient(create_app(fixtures=False, manager=mgr)) as c:
        yield c, mgr, clock, cps


def body(**kw) -> dict:
    return {"mode": "live", "policies": ["always_on", "cost_rule"], "focus_policy": "cost_rule",
            "lambda": 0.1, **kw}  # fmt: skip


def wait_slot(c, run_id: str, slot: int) -> dict:
    end = time.monotonic() + 10
    while (st := c.get(f"/api/runs/{run_id}").json())["slot"] < slot:
        assert time.monotonic() < end, st
        time.sleep(0.005)
    return st


def test_live_run_steps_on_clock_with_arrival_wait(live):
    c, mgr, clock, cps = live
    r = c.post("/api/runs", json=body())
    assert r.status_code == 201, r.text
    rid = r.json()["run_id"]
    run = mgr.runs[rid]
    assert run.info["forecaster"] == "constant_stub" and run.info["cps_in_run"] == len(cps)
    end = time.monotonic() + 10
    while run.engine.slot < 120:  # slot 119 is due at once: cost_rule sleeps idle CPs (ACT)
        assert time.monotonic() < end
        time.sleep(0.005)
    applied = c.get("/api/recommendations", params={"run_id": rid}).json()
    assert applied and all(x["status"] == "applied" for x in applied)
    with c.websocket_connect(f"/ws/runs/{rid}") as ws:
        snap = recv(ws)
        assert snap["type"] == "snapshot" and snap["slot"] == 119
        states = {ch["cp_id"]: ch["state"] for ch in snap["changes"]}
        assert states[cps[1]] == "CHARGING" and states[cps[0]] == "DEEP_SLEEP"
        # 10:01 a car plugs into sleeping cps[0]; the car on cps[1] leaves
        write_rows(mgr.live_dir, [row(ts("2026-11-08 10:05:03"), cps[0], "CHARGING",
                                      sua=ts("2026-11-08 10:01:00")),
                                  row(ts("2026-11-08 10:05:03"), cps[1], "AVAILABLE",
                                      sua=ts("2026-11-08 10:02:00"))])  # fmt: skip
        clock.t = ts("2026-11-08 10:06:01")
        tick = recv(ws)
        assert tick["slot"] == 120
        kinds = {(e["type"], e["cp_id"]) for e in tick["events"]}
        assert ("arrival", cps[0]) in kinds and ("departure", cps[1]) in kinds
        arr = next(e for e in tick["events"] if e["type"] == "arrival")
        assert arr["wait_min"] == pytest.approx(3.0)  # deep sleep wake 180 s
        k = tick["kpis"]
        assert k["cost_rule"]["waited_sessions"] == 1 and k["always_on"]["wait_min_total"] == 0
        assert k["cost_rule"]["saved_kwh"] > 0
        clock.t = ts("2026-11-08 10:21:01")  # three slots due: caught up one tick each
        _, seen = recv_until(ws, lambda m: m.get("slot") == 123)
        assert [m["slot"] for m in seen if m["type"] == "tick"] == [121, 122, 123]
    st = RunStatus.model_validate(c.get(f"/api/runs/{rid}").json())
    assert st.mode == "live" and st.status == "running" and st.slot == 123
    states = {cp["cp_id"]: cp.get("state") for loc in c.get("/api/stations").json()
              for cp in loc["charge_points"]}  # fmt: skip
    assert states[cps[0]] == "CHARGING"
    tl = c.get(f"/api/runs/{rid}/cp/{cps[0]}/timeline").json()
    assert tl["arrivals"][0]["wait_min"] == pytest.approx(3.0)


def test_live_recommend_creates_recommendations(live):
    c, mgr, clock, cps = live
    assert c.put("/api/settings/autonomy", json={"mode": "RECOMMEND"}).status_code == 200
    rid = c.post("/api/runs", json=body()).json()["run_id"]
    q = {"run_id": rid, "status": "pending"}
    end = time.monotonic() + 10
    while not (recs := c.get("/api/recommendations", params=q).json()):
        assert time.monotonic() < end
        time.sleep(0.005)
    st = c.get(f"/api/runs/{rid}").json()
    assert st["status"] == "running"  # live runs never pause for recommendations
    assert all(Recommendation.model_validate(x).policy == "cost_rule" for x in recs)
    ok = c.post(f"/api/recommendations/{recs[0]['id']}", json={"decision": "approve"})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    clock.t = ts("2026-11-08 10:21:01")  # TTL 3 slots: the other pending ones expire
    wait_slot(c, rid, 123)
    left = c.get("/api/recommendations", params={"run_id": rid, "status": "pending"}).json()
    assert not {x["id"] for x in left} & {x["id"] for x in recs}


def test_live_controls_and_errors(live, tmp_path):
    c, mgr, clock, cps = live
    rid = c.post("/api/runs", json=body()).json()["run_id"]
    for action, value in (("speed", 10), ("seek", 3)):
        r = c.post(f"/api/runs/{rid}/control", json={"action": action, "value": value})
        assert r.status_code == 400 and "replay-only" in r.json()["detail"]
    assert c.post(f"/api/runs/{rid}/control", json={"action": "pause"}).json()["status"] == "paused"
    r = c.post("/api/runs", json=body(policies=["always_on", "oracle"], focus_policy=None))
    assert r.status_code == 400 and "oracle" in r.json()["detail"]
    clock.t = ts("2026-11-08 11:30:00")  # newest status 09:50: stale
    r = c.post("/api/runs", json=body())
    assert r.status_code == 400 and "stale" in r.json()["detail"]
    mgr.live_dir = tmp_path / "nothing"
    r = c.post("/api/runs", json=body())
    assert r.status_code == 400 and "no live statuses" in r.json()["detail"]


def test_env_clock_starts_each_live_run_at_start(live, monkeypatch):
    c, mgr, _, _ = live
    mono = [1000.0]  # only live_mode's monotonic is fake; asyncio keeps the real one
    fake = SimpleNamespace(time=time.time, monotonic=lambda: mono[0])
    monkeypatch.setattr(live_mode, "time", fake)
    monkeypatch.setenv("LIVE_CLOCK_START", "2026-11-08T10:02:30")
    monkeypatch.setenv("LIVE_CLOCK_SPEED", "30")
    mgr.live_clock = EngineManager().live_clock  # the real default, not the fixture's fake
    r1 = c.post("/api/runs", json=body())
    assert r1.status_code == 201, r1.text
    c.post(f"/api/runs/{r1.json()['run_id']}/control", json={"action": "pause"})
    mono[0] += 8 * 60  # run 1's clock is now 4 h past START; run 2 must still start at START
    r2 = c.post("/api/runs", json=body())
    assert r2.status_code == 201, r2.text
    run2 = mgr.runs[r2.json()["run_id"]]
    assert run2.clock.now() == pytest.approx(T0)
    assert run2.info["clock"] == "demo from 2026-11-08T10:02:30+02:00 at 30x"
    monkeypatch.setenv("LIVE_CLOCK_START", "2026-11-08T11:30:00")
    r3 = c.post("/api/runs", json=body())
    detail = r3.json()["detail"]
    assert r3.status_code == 400 and "clock: demo from 2026-11-08T11:30:00" in detail
