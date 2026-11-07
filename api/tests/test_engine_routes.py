"""Real mode: REST, WS and run history on sim.Engine with a small synthetic SimData."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy import create_engine, text

from api.engine_mode import EngineManager
from api.main import create_app
from api.models import Location
from sim.stations_seed import load_seed_stations
from sim.synthetic import generate
from stations import store

from .conftest import recv, recv_until

POLICIES = ["always_on", "naive_quiet_hours"]


@pytest.fixture(scope="module")
def small_data():
    locs, _ = load_seed_stations()
    return generate(seed=0, train_days=3, test_days=2, locations=locs.head(5))


@pytest.fixture
def ec(small_data, tmp_path):
    mgr = EngineManager(data=small_data, eval_path=tmp_path / "eval_results.json")
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


def test_stations(ec, small_data):
    locs = TypeAdapter(list[Location]).validate_python(ec.get("/api/stations").json())
    assert len(locs) == 5
    assert sum(len(loc.charge_points) for loc in locs) == len(small_data.cps)


def test_run_create_validation(ec):
    assert start(ec)
    assert ec.post("/api/runs", json=body(date="2024-03-20")).status_code == 400
    live = ec.post("/api/runs", json=body(mode="live"))
    assert live.status_code == 400


def test_ws_pass_through(ec, small_data):
    run_id = start(ec)
    with ec.websocket_connect(f"/ws/runs/{run_id}") as ws:
        snap = recv(ws)
        assert snap["type"] == "snapshot" and len(snap["changes"]) == len(small_data.cps)
        ec.post(f"/api/runs/{run_id}/control", json={"action": "speed", "value": 288})
        done, seen = recv_until(ws, lambda m: m["type"] == "done")
    assert [m["slot"] for m in seen if m["type"] == "tick"][-1] == 287
    k = done["kpis"]["naive_quiet_hours"]
    assert k["saved_kwh"] > 0 and k["overhead_ron"] > 0 and "saved_ron" in k


def test_run_history_and_autonomy_in_store(ec, tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'h.db'}", connect_args={"check_same_thread": False})
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE state_events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TIMESTAMP, "
            "cp_id TEXT, from_state TEXT, to_state TEXT, cause TEXT, run_id TEXT)"
        ))  # fmt: skip
        conn.execute(text(
            "CREATE TABLE commands (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TIMESTAMP, "
            "run_id TEXT, cp_id TEXT, policy TEXT, from_state TEXT, target_state TEXT, "
            "reason TEXT, status TEXT, expires_at TIMESTAMP, decided_by TEXT, "
            "decided_at TIMESTAMP)"
        ))  # fmt: skip
        conn.execute(text("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"))
        conn.execute(text("INSERT INTO settings VALUES ('autonomy_mode', '\"ACT\"')"))
    store.set_engine(eng)

    run_id = start(ec, speed=288)
    t_end = time.monotonic() + 15
    while ec.get(f"/api/runs/{run_id}").json()["status"] != "done":
        assert time.monotonic() < t_end
        time.sleep(0.01)
    with eng.connect() as conn:
        n = conn.execute(
            text("SELECT COUNT(*) FROM state_events WHERE run_id = :r"), {"r": run_id}
        ).scalar()
    assert n > 0

    assert ec.put("/api/settings/autonomy", json={"mode": "RECOMMEND"}).status_code == 200
    assert store.get_autonomy() == "RECOMMEND"
    assert ec.get("/api/settings/autonomy").json() == {"mode": "RECOMMEND"}
