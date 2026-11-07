from __future__ import annotations

import json

import anyio
import pytest
from fastapi.testclient import TestClient

from api.fixture_mode import FixtureManager
from api.fixture_store import RecordedDay
from api.fixtures_gen import build_run_day
from api.main import create_app
from api.models import DoneMessage, SnapshotMessage, StatusMessage, TickMessage
from sim.stations_seed import load_seed_stations

SHORT_SLOTS = 48
MODELS = {
    "tick": TickMessage,
    "snapshot": SnapshotMessage,
    "status": StatusMessage,
    "done": DoneMessage,
}


@pytest.fixture(scope="session")
def short_day() -> RecordedDay:
    locs, cps = load_seed_stations()
    cps = cps[cps.location_id.isin(locs.location_id[:10])].reset_index(drop=True)
    return RecordedDay(build_run_day(cps, n_slots=SHORT_SLOTS))


@pytest.fixture
def client(short_day):
    with TestClient(create_app(fixtures=True, manager=FixtureManager(day=short_day))) as c:
        yield c


@pytest.fixture(scope="session")
def full_client():
    with TestClient(create_app(fixtures=True)) as c:
        yield c


def run_body(**kw) -> dict:
    body = {
        "mode": "replay",
        "date": "2024-03-12",
        "policies": ["always_on", "naive_quiet_hours", "cost_rule", "rl_ppo"],
        "focus_policy": "rl_ppo",
        "lambda": 0.1,
        "speed": 1,
    }
    return {**body, **kw}


def recv(ws, timeout: float = 10) -> dict:
    """Receive one WS message (fails instead of hanging) and validate it against its model."""

    async def _get():
        with anyio.fail_after(timeout):
            return await ws._send_rx.receive()

    message = ws.portal.call(_get)
    ws._raise_on_close(message)
    msg = json.loads(message["text"])
    MODELS[msg["type"]].model_validate(msg)
    return msg


def recv_until(ws, pred, limit: int = 2000) -> tuple[dict, list[dict]]:
    seen = []
    for _ in range(limit):
        msg = recv(ws)
        seen.append(msg)
        if pred(msg):
            return msg, seen
    raise AssertionError("expected message not received")
