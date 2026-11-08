"""Replay of collected days (decision 0090): synthetic status files, replay clock at full speed."""

from __future__ import annotations

import time
from datetime import date

import pytest
from fastapi.testclient import TestClient

from api.collected_replay import CollectedRun, collected_windows
from api.engine_mode import EngineManager
from api.main import create_app
from model.stubs import ConstantForecaster
from sim.stations_seed import load_seed_stations
from sim.synthetic import generate
from sim.tests.test_live import refs_for, row, ts, write_rows

DAY = date(2026, 11, 8)


@pytest.fixture(scope="module")
def small_data():
    locs, _ = load_seed_stations()
    return generate(seed=0, train_days=3, test_days=2, locations=locs.head(5))


def collected_rows(cps: list[str]) -> list[dict]:
    snap, mid, last = (ts(f"2026-11-08 {t}") for t in ("09:50:03", "10:05:03", "10:20:03"))
    return ([row(snap, cp, "AVAILABLE") for cp in cps]
            + [row(snap, cps[1], "CHARGING", sua=snap - 30),
               row(mid, cps[0], "CHARGING", sua=ts("2026-11-08 10:01:00")),  # cps[0] is asleep
               row(mid, cps[1], "AVAILABLE", sua=ts("2026-11-08 10:02:00")),
               row(last, cps[2], "AVAILABLE")])  # fmt: skip


@pytest.fixture
def replay(small_data, tmp_path):
    cps = small_data.cps["cp_id"].tolist()
    (tmp_path / "eval.json").write_text('{"test_days": [], "results": []}')
    mgr = EngineManager(data=small_data, eval_path=tmp_path / "eval.json")
    mgr.collected_dir = tmp_path / "live"
    mgr.source_refs = refs_for(cps)
    mgr.live_forecaster_factory = lambda: (ConstantForecaster(0, 0, 0, 0), "constant_stub")
    mgr._live_traffic = {}
    write_rows(mgr.collected_dir, collected_rows(cps))
    with TestClient(create_app(fixtures=False, manager=mgr)) as c:
        assert c.put("/api/settings/autonomy", json={"mode": "ACT"}).status_code == 200
        yield c, mgr, cps


def body(**kw) -> dict:
    return {"mode": "replay", "date": None, "policies": ["always_on", "cost_rule"],
            "focus_policy": "cost_rule", "lambda": 0.1, "speed": 288, **kw}  # fmt: skip


def wait_done(c, rid: str) -> dict:
    end = time.monotonic() + 10
    while (st := c.get(f"/api/runs/{rid}").json())["status"] != "done":
        assert time.monotonic() < end, st
        time.sleep(0.005)
    return st


def test_windows_span_utc_files_and_skip_half_lines(tmp_path):
    write_rows(tmp_path, [row(ts("2026-11-09 01:50:03"), "L1-1", "AVAILABLE"),  # 23:50 UTC
                          row(ts("2026-11-09 02:20:03"), "L1-1", "CHARGING")])  # fmt: skip
    assert sorted(p.name for p in tmp_path.iterdir()) == ["statuses-20261108.jsonl",
                                                          "statuses-20261109.jsonl"]  # fmt: skip
    with (tmp_path / "statuses-20261109.jsonl").open("a") as f:
        f.write('{"ts": "2026-11-09T05:5')  # the collector is mid-write
    assert collected_windows(tmp_path, 300) == {date(2026, 11, 9): (23, 28)}
    assert collected_windows(tmp_path / "missing", 300) == {}


def test_collected_day_is_offered_first_and_replays_to_window_end(replay):
    c, mgr, cps = replay
    days = c.get("/api/eval").json()["test_days"]
    assert days[0] == DAY.isoformat() and len(days) > 1  # real (non-synthetic) test days follow
    mgr.synthetic = True
    assert c.get("/api/eval").json()["test_days"] == [DAY.isoformat()]

    r = c.post("/api/runs", json=body())
    assert r.status_code == 201, r.text
    run = mgr.runs[r.json()["run_id"]]
    assert isinstance(run, CollectedRun) and run.mode == "replay" and run.day == DAY
    assert (run.start_slot, run.end_slot) == (119, 124)
    assert run.info["source"] == "collected" and run.info["cps_in_run"] == len(cps)
    st = wait_done(c, run.run_id)
    assert st["slot"] == 123 and run.engine.slot == 124
    k = st["kpis"]
    assert k["cost_rule"]["waited_sessions"] == 1 and k["always_on"]["wait_min_total"] == 0
    assert k["cost_rule"]["wait_min_total"] == pytest.approx(3.0)  # deep sleep wake 180 s
    assert k["cost_rule"]["saved_kwh"] > 0

    url = f"/api/runs/{run.run_id}/control"
    st = c.post(url, json={"action": "seek", "value": 0}).json()  # clamped to the window start
    assert st["status"] == "paused" and st["slot"] == 119 and run.gen == 1
    assert c.post(url, json={"action": "speed", "value": 120}).json()["speed"] == 120
    c.post(url, json={"action": "play"})
    assert wait_done(c, run.run_id)["kpis"] == k  # a rebuilt replay gives the same result


def test_collected_replay_errors(replay):
    c, mgr, _ = replay
    r = c.post("/api/runs", json=body(policies=["always_on", "oracle"], focus_policy=None))
    assert r.status_code == 400 and "oracle" in r.json()["detail"]
    r = c.post("/api/runs", json=body(date="2026-11-10"))
    assert r.status_code == 400 and "2026-11-08" in r.json()["detail"]
    mgr.source_refs = refs_for(["X1-1"])  # no collected key matches a charge point
    r = c.post("/api/runs", json=body(date=DAY.isoformat()))
    assert r.status_code == 400 and "no collected status matched" in r.json()["detail"]
