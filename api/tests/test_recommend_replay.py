"""RECOMMEND replays (decision 0025): every recommendation listed as pending can be decided."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from api.engine_mode import EngineManager
from api.main import create_app
from sim.stations_seed import load_seed_stations
from sim.synthetic import generate


@pytest.fixture(scope="module")
def small_data():
    locs, _ = load_seed_stations()
    return generate(seed=0, train_days=3, test_days=2, locations=locs.head(5))


@pytest.fixture
def rc(small_data, tmp_path):
    mgr = EngineManager(data=small_data, eval_path=tmp_path / "eval_results.json")
    with TestClient(create_app(fixtures=False, manager=mgr)) as c:
        assert c.put("/api/settings/autonomy", json={"mode": "RECOMMEND"}).status_code == 200
        try:
            yield c
        finally:
            c.put("/api/settings/autonomy", json={"mode": "ACT"})


def test_listed_pending_recommendations_never_409(rc):
    """The UI approves what GET /api/recommendations lists; expiry used to arrive a step late."""
    body = {"mode": "replay", "date": None, "policies": ["always_on", "naive_quiet_hours"],
            "focus_policy": "naive_quiet_hours", "lambda": 0.1, "speed": 300}  # fmt: skip
    rid = rc.post("/api/runs", json=body).json()["run_id"]
    codes: list[int] = []
    pauses, n = 0, 0
    end = time.monotonic() + 30
    while pauses < 12 and time.monotonic() < end:
        st = rc.get(f"/api/runs/{rid}").json()
        if st["status"] == "done":
            break
        if st["paused_reason"] == "recommendations":
            pauses += 1
            q = {"run_id": rid, "status": "pending"}
            pending = rc.get("/api/recommendations", params=q).json()
            if pending:  # decide only the oldest, so the others age into their last slot
                n += 1
                oldest = min(pending, key=lambda r: r["id"])
                decision = "approve" if n % 2 else "reject"
                url = f"/api/recommendations/{oldest['id']}"
                codes.append(rc.post(url, json={"decision": decision}).status_code)
            rc.post(f"/api/runs/{rid}/control", json={"action": "play", "value": None})
        time.sleep(0.005)
    assert pauses >= 3 and len(codes) >= 3
    assert set(codes) == {200}
