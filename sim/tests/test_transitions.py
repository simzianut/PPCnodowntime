import numpy as np
import pytest

from sim import transitions as tr
from sim.types import CHARGING, DEEP_SLEEP, IDLE, OFFLINE, STANDBY, WAKING, TargetState

NOW = 100.0
DC = (True, 10.0, 180.0)  # has_deep_sleep, standby_wake_s, deep_sleep_wake_s
AC = (False, 10.0, np.nan)


def one(state: int, target: int, profile=DC) -> tuple[int, int, int, float, int]:
    has_deep, sb, ds = profile
    r = tr.apply_commands(
        np.array([state]), np.array([target]), NOW, np.array([has_deep]), np.array([sb]),
        np.array([ds]),
    )  # fmt: skip
    return (
        int(r.code[0]),
        int(r.target[0]),
        int(r.new_state[0]),
        float(r.wake_end[0]),
        int(r.wake_target[0]),
    )


INF = np.inf
CASES = [
    # state, target, profile, code, effective target, new state, wake end, wake target
    (IDLE, IDLE, DC, tr.DROP_SAME, IDLE, IDLE, INF, -1),
    (IDLE, STANDBY, DC, tr.APPLIED, STANDBY, STANDBY, INF, -1),
    (IDLE, DEEP_SLEEP, DC, tr.APPLIED, DEEP_SLEEP, DEEP_SLEEP, INF, -1),
    (STANDBY, IDLE, DC, tr.APPLIED, IDLE, WAKING, NOW + 10, IDLE),
    (STANDBY, STANDBY, DC, tr.DROP_SAME, STANDBY, STANDBY, INF, -1),
    (STANDBY, DEEP_SLEEP, DC, tr.APPLIED, DEEP_SLEEP, DEEP_SLEEP, INF, -1),
    (DEEP_SLEEP, IDLE, DC, tr.APPLIED, IDLE, WAKING, NOW + 180, IDLE),
    (DEEP_SLEEP, STANDBY, DC, tr.APPLIED, STANDBY, WAKING, NOW + 180, STANDBY),
    (DEEP_SLEEP, DEEP_SLEEP, DC, tr.DROP_SAME, DEEP_SLEEP, DEEP_SLEEP, INF, -1),
    (WAKING, IDLE, DC, tr.DROP_WAKING, IDLE, WAKING, INF, -1),
    (WAKING, STANDBY, DC, tr.DROP_WAKING, STANDBY, WAKING, INF, -1),
    (WAKING, DEEP_SLEEP, DC, tr.DROP_WAKING, DEEP_SLEEP, WAKING, INF, -1),
    (CHARGING, IDLE, DC, tr.DROP_BUSY, IDLE, CHARGING, INF, -1),
    (CHARGING, STANDBY, DC, tr.DROP_BUSY, STANDBY, CHARGING, INF, -1),
    (CHARGING, DEEP_SLEEP, DC, tr.DROP_BUSY, DEEP_SLEEP, CHARGING, INF, -1),
    (OFFLINE, IDLE, DC, tr.DROP_OFFLINE, IDLE, OFFLINE, INF, -1),
    (OFFLINE, STANDBY, DC, tr.DROP_OFFLINE, STANDBY, OFFLINE, INF, -1),
    (OFFLINE, DEEP_SLEEP, DC, tr.DROP_OFFLINE, DEEP_SLEEP, OFFLINE, INF, -1),
    # AC: no deep sleep, DEEP_SLEEP is applied as STANDBY
    (IDLE, DEEP_SLEEP, AC, tr.APPLIED, STANDBY, STANDBY, INF, -1),
    (IDLE, STANDBY, AC, tr.APPLIED, STANDBY, STANDBY, INF, -1),
    (STANDBY, DEEP_SLEEP, AC, tr.DROP_SAME, STANDBY, STANDBY, INF, -1),
    (STANDBY, IDLE, AC, tr.APPLIED, IDLE, WAKING, NOW + 10, IDLE),
    (CHARGING, DEEP_SLEEP, AC, tr.DROP_BUSY, STANDBY, CHARGING, INF, -1),
]


@pytest.mark.parametrize(
    "case", CASES, ids=lambda c: f"{c[0]}->{c[1]}-{'DC' if c[2] is DC else 'AC'}"
)
def test_single_transition(case):
    state, target, profile, *expected = case
    assert one(state, target, profile) == tuple(expected)


def test_vectorised_batch_matches_single_and_is_pure():
    state = np.array([c[0] for c in CASES], dtype=np.int8)
    target = np.array([c[1] for c in CASES], dtype=np.int8)
    has_deep = np.array([c[2][0] for c in CASES])
    sb = np.array([c[2][1] for c in CASES])
    ds = np.array([c[2][2] for c in CASES])
    before = state.copy()
    r = tr.apply_commands(state, target, NOW, has_deep, sb, ds)
    assert np.array_equal(state, before)
    for i, c in enumerate(CASES):
        got = (r.code[i], r.target[i], r.new_state[i], r.wake_end[i], r.wake_target[i])
        assert tuple(float(x) for x in got) == tuple(float(x) for x in c[3:])
    assert np.array_equal(r.applied, r.code == tr.APPLIED)
    assert np.array_equal(r.started_waking, r.applied & (r.new_state == WAKING))


def test_drop_reasons():
    r = tr.apply_commands(
        np.array([CHARGING, IDLE, WAKING, OFFLINE, IDLE]),
        np.array([STANDBY, IDLE, STANDBY, IDLE, STANDBY]),
        NOW, np.ones(5, bool), np.full(5, 10.0), np.full(5, 180.0),
    )  # fmt: skip
    assert r.reasons() == ["busy", "same", "waking", "offline", None]


def test_empty_batch():
    e = np.array([], dtype=np.int8)
    r = tr.apply_commands(e, e, NOW, np.array([], bool), np.array([]), np.array([]))
    assert r.code.shape == (0,)


def test_target_code():
    assert tr.target_code(TargetState.DEEP_SLEEP) == DEEP_SLEEP
    assert tr.target_code("IDLE") == IDLE
    assert tr.CODE_TARGETS[STANDBY] == TargetState.STANDBY
    with pytest.raises(ValueError):
        tr.target_code("CHARGING")
