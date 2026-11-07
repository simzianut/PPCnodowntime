"""WebSocket stream: snapshot, ticks, control messages, RECOMMEND flow, done."""

from __future__ import annotations

import time

import pytest
from starlette.websockets import WebSocketDisconnect

from .conftest import SHORT_SLOTS, recv, recv_until, run_body


def _control(client, run_id, action, value=None):
    r = client.post(f"/api/runs/{run_id}/control", json={"action": action, "value": value})
    assert r.status_code == 200, r.text
    return r.json()


def test_unknown_run_closes_4404(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/runs/nope") as ws:
            ws.receive_json()
    assert exc.value.code == 4404


def test_snapshot_then_ticks(client, short_day):
    run_id = client.post("/api/runs", json=run_body(speed=20)).json()["run_id"]
    with client.websocket_connect(f"/ws/runs/{run_id}") as ws:
        snap = recv(ws)
        assert snap["type"] == "snapshot" and snap["run_id"] == run_id
        assert len(snap["changes"]) == len(short_day.cp_ids)
        tick, _ = recv_until(ws, lambda m: m["type"] == "tick")
        assert tick["slot"] >= snap["slot"]
        assert set(tick["kpis"]) == {"always_on", "naive_quiet_hours", "cost_rule", "rl_ppo"}


def test_two_clients(client):
    run_id = client.post("/api/runs", json=run_body(speed=50)).json()["run_id"]
    with client.websocket_connect(f"/ws/runs/{run_id}") as a:
        with client.websocket_connect(f"/ws/runs/{run_id}") as b:
            assert recv(a)["type"] == "snapshot" and recv(b)["type"] == "snapshot"
            ta, _ = recv_until(a, lambda m: m["type"] == "tick")
            tb, _ = recv_until(b, lambda m: m["type"] == "tick" and m["slot"] >= ta["slot"])
        recv_until(a, lambda m: m["type"] == "tick" and m["slot"] > ta["slot"])


def test_controls_send_documented_messages(client, short_day):
    run_id = client.post("/api/runs", json=run_body(speed=5)).json()["run_id"]
    with client.websocket_connect(f"/ws/runs/{run_id}") as ws:
        recv(ws)
        st = _control(client, run_id, "pause")
        assert st["status"] == "paused" and st["paused_reason"] == "user"
        msg, _ = recv_until(ws, lambda m: m["type"] == "status")
        assert msg["status"] == "paused" and msg["paused_reason"] == "user"

        _control(client, run_id, "speed", 100)
        msg, _ = recv_until(ws, lambda m: m["type"] == "status")
        assert msg["speed"] == 100 and msg["status"] == "paused"

        _control(client, run_id, "seek", 30)
        snap, seen = recv_until(ws, lambda m: m["type"] == "snapshot")
        assert snap["slot"] == 30 and snap["sim_time"].endswith("02:30:00+02:00")
        assert len(snap["changes"]) == len(short_day.cp_ids)
        assert not [m for m in seen if m["type"] == "tick"]

        _control(client, run_id, "focus", "naive_quiet_hours")
        status, _ = recv_until(ws, lambda m: m["type"] == "status")
        assert status["focus_policy"] == "naive_quiet_hours"
        snap = recv(ws)
        assert snap["type"] == "snapshot" and snap["focus_policy"] == "naive_quiet_hours"
        # slot 30 is inside the naive 01:00-04:00 window: no CP is IDLE unless charging or woken
        assert any(c["state"] in ("STANDBY", "DEEP_SLEEP") for c in snap["changes"])

        _control(client, run_id, "play")
        msg, _ = recv_until(ws, lambda m: m["type"] == "status")
        assert msg["status"] == "running" and msg["paused_reason"] is None
        tick, _ = recv_until(ws, lambda m: m["type"] == "tick")
        assert tick["slot"] == 30 and tick["focus_policy"] == "naive_quiet_hours"


def test_done_at_speed_288(client):
    run_id = client.post("/api/runs", json=run_body(speed=1)).json()["run_id"]
    with client.websocket_connect(f"/ws/runs/{run_id}") as ws:
        recv(ws)
        _control(client, run_id, "speed", 288)
        done, seen = recv_until(ws, lambda m: m["type"] == "done")
    ticks = [m for m in seen if m["type"] == "tick"]
    assert ticks[-1]["slot"] == SHORT_SLOTS - 1
    st = client.get(f"/api/runs/{run_id}").json()
    assert st["status"] == "done" and st["kpis"] == done["kpis"] == ticks[-1]["kpis"]
    assert done["kpis"]["always_on"]["saved_kwh"] == 0


def _wait_paused_for_recs(client, run_id) -> dict:
    for _ in range(500):
        st = client.get(f"/api/runs/{run_id}").json()
        if st["paused_reason"] == "recommendations":
            return st
        time.sleep(0.01)
    raise AssertionError("run did not pause for recommendations")


def test_recommend_pauses_and_approve_changes_state(client, short_day):
    """Auto-pause, approve -> the CP shows the target on the next tick (decision 0025)."""
    client.put("/api/settings/autonomy", json={"mode": "RECOMMEND"})
    run_id = client.post("/api/runs", json=run_body(speed=288)).json()["run_id"]
    st = _wait_paused_for_recs(client, run_id)
    assert st["status"] == "paused"
    with client.websocket_connect(f"/ws/runs/{run_id}") as ws:
        snap = recv(ws)
        assert snap["recommendations"] and snap["slot"] == st["slot"]
        pending = client.get(
            "/api/recommendations", params={"run_id": run_id, "status": "pending"}
        ).json()
        assert pending and all(r["policy"] == "rl_ppo" for r in pending)
        slot = st["slot"]
        busy = {short_day.cp_ids[a.cp] for a in short_day.arrivals["rl_ppo"][slot + 1]} | {
            short_day.cp_ids[c] for c, _ in short_day.departures["rl_ppo"][slot + 1]
        }
        rec = next(r for r in pending if r["cp_id"] not in busy)
        ok = client.post(f"/api/recommendations/{rec['id']}", json={"decision": "approve"})
        assert ok.status_code == 200 and ok.json()["status"] == "approved"
        again = client.post(f"/api/recommendations/{rec['id']}", json={"decision": "reject"})
        assert again.status_code == 409
        others = [r for r in pending if r["id"] != rec["id"]]
        if others:
            rej = client.post(
                f"/api/recommendations/{others[0]['id']}", json={"decision": "reject"}
            )
            assert rej.json()["status"] == "rejected"

        _control(client, run_id, "play")
        tick, _ = recv_until(ws, lambda m: m["type"] == "tick")
        assert tick["slot"] == slot + 1
        assert {"cp_id": rec["cp_id"], "state": rec["target_state"]} in tick["changes"]
        decided = {r["id"]: r["status"] for r in tick["recommendations"]}
        assert decided.get(rec["id"]) == "approved"


def test_recommendations_expire(client):
    client.put("/api/settings/autonomy", json={"mode": "RECOMMEND"})
    run_id = client.post("/api/runs", json=run_body(speed=288)).json()["run_id"]
    _wait_paused_for_recs(client, run_id)
    with client.websocket_connect(f"/ws/runs/{run_id}") as ws:
        recv(ws)
        pending = client.get(
            "/api/recommendations", params={"run_id": run_id, "status": "pending"}
        ).json()
        _control(client, run_id, "seek", 10)
        snap, _ = recv_until(ws, lambda m: m["type"] == "snapshot")
        expired = {r["id"] for r in snap["recommendations"] if r["status"] == "expired"}
        assert {r["id"] for r in pending} <= expired
    r = client.post(f"/api/recommendations/{pending[0]['id']}", json={"decision": "approve"})
    assert r.status_code == 409


def test_full_fixture_day_under_10s(full_client):
    t0 = time.perf_counter()
    run_id = full_client.post("/api/runs", json=run_body(speed=1)).json()["run_id"]
    with full_client.websocket_connect(f"/ws/runs/{run_id}") as ws:
        recv(ws)
        _control(full_client, run_id, "speed", 288)
        recv_until(ws, lambda m: m["type"] == "done", limit=1000)
        assert full_client.get("/api/health").status_code == 200
    assert time.perf_counter() - t0 < 10
