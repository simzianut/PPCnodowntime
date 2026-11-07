"""Unit tests for Spec 03 §4 charge-point state model and transitions (decision 0018)."""

from __future__ import annotations

import numpy as np
import pytest

from sim import transitions as sim_tr
from sim.types import CHARGING, DEEP_SLEEP, IDLE, OFFLINE, STANDBY, WAKING
from stations.state import apply_event_transition, evaluate_command_transition

DC_PROFILE = {
    "idle_w": 105.0,
    "standby_w": 35.0,
    "deep_sleep_w": 20.0,
    "standby_wake_s": 10.0,
    "deep_sleep_wake_s": 180.0,
}
AC_PROFILE = {
    "idle_w": 25.0,
    "standby_w": 9.0,
    "deep_sleep_w": None,
    "standby_wake_s": 10.0,
    "deep_sleep_wake_s": None,
}

STATE_CODE = {
    "CHARGING": CHARGING,
    "IDLE": IDLE,
    "WAKING": WAKING,
    "STANDBY": STANDBY,
    "DEEP_SLEEP": DEEP_SLEEP,
    "OFFLINE": OFFLINE,
}

CASES = [
    # from_state, target, power_class, applied, status, eff, new_state, wake_s, wake_tgt, reason
    ("IDLE", "IDLE", "DC_50", False, "dropped", "IDLE", "IDLE", 0.0, None, "same"),
    ("IDLE", "STANDBY", "DC_50", True, "applied", "STANDBY", "STANDBY", 0.0, None, None),
    ("IDLE", "DEEP_SLEEP", "DC_50", True, "applied", "DEEP_SLEEP", "DEEP_SLEEP", 0.0, None, None),
    ("STANDBY", "IDLE", "DC_50", True, "applied", "IDLE", "WAKING", 10.0, "IDLE", None),
    ("STANDBY", "STANDBY", "DC_50", False, "dropped", "STANDBY", "STANDBY", 0.0, None, "same"),
    (
        "STANDBY",
        "DEEP_SLEEP",
        "DC_50",
        True,
        "applied",
        "DEEP_SLEEP",
        "DEEP_SLEEP",
        0.0,
        None,
        None,
    ),
    ("DEEP_SLEEP", "IDLE", "DC_50", True, "applied", "IDLE", "WAKING", 180.0, "IDLE", None),
    (
        "DEEP_SLEEP",
        "STANDBY",
        "DC_50",
        True,
        "applied",
        "STANDBY",
        "WAKING",
        180.0,
        "STANDBY",
        None,
    ),
    (
        "DEEP_SLEEP",
        "DEEP_SLEEP",
        "DC_50",
        False,
        "dropped",
        "DEEP_SLEEP",
        "DEEP_SLEEP",
        0.0,
        None,
        "same",
    ),
    ("WAKING", "IDLE", "DC_50", False, "dropped", "IDLE", "WAKING", 0.0, None, "waking"),
    ("WAKING", "STANDBY", "DC_50", False, "dropped", "STANDBY", "WAKING", 0.0, None, "waking"),
    (
        "WAKING",
        "DEEP_SLEEP",
        "DC_50",
        False,
        "dropped",
        "DEEP_SLEEP",
        "WAKING",
        0.0,
        None,
        "waking",
    ),
    ("CHARGING", "IDLE", "DC_50", False, "rejected", "IDLE", "CHARGING", 0.0, None, "busy"),
    ("CHARGING", "STANDBY", "DC_50", False, "rejected", "STANDBY", "CHARGING", 0.0, None, "busy"),
    (
        "CHARGING",
        "DEEP_SLEEP",
        "DC_50",
        False,
        "rejected",
        "DEEP_SLEEP",
        "CHARGING",
        0.0,
        None,
        "busy",
    ),
    ("OFFLINE", "IDLE", "DC_50", False, "dropped", "IDLE", "OFFLINE", 0.0, None, "offline"),
    ("OFFLINE", "STANDBY", "DC_50", False, "dropped", "STANDBY", "OFFLINE", 0.0, None, "offline"),
    (
        "OFFLINE",
        "DEEP_SLEEP",
        "DC_50",
        False,
        "dropped",
        "DEEP_SLEEP",
        "OFFLINE",
        0.0,
        None,
        "offline",
    ),
    # AC_22 coercion: DEEP_SLEEP is applied as STANDBY
    ("IDLE", "DEEP_SLEEP", "AC_22", True, "applied", "STANDBY", "STANDBY", 0.0, None, None),
    ("IDLE", "STANDBY", "AC_22", True, "applied", "STANDBY", "STANDBY", 0.0, None, None),
    ("STANDBY", "DEEP_SLEEP", "AC_22", False, "dropped", "STANDBY", "STANDBY", 0.0, None, "same"),
    ("STANDBY", "IDLE", "AC_22", True, "applied", "IDLE", "WAKING", 10.0, "IDLE", None),
    (
        "CHARGING",
        "DEEP_SLEEP",
        "AC_22",
        False,
        "rejected",
        "STANDBY",
        "CHARGING",
        0.0,
        None,
        "busy",
    ),
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c[0]}->{c[1]}-{c[2]}")
def test_command_transitions_match_spec_and_sim_engine(case):
    (
        from_state,
        target,
        power_class,
        applied,
        status,
        eff_target,
        new_state,
        wake_s,
        wake_target,
        drop_reason,
    ) = case
    prof = AC_PROFILE if power_class == "AC_22" else DC_PROFILE
    res = evaluate_command_transition(from_state, target, power_class, prof)

    assert res.applied is applied
    assert res.status == status
    assert res.effective_target == eff_target
    assert res.new_state == new_state
    assert res.wake_s == pytest.approx(wake_s)
    assert res.wake_target == wake_target
    assert res.drop_reason == drop_reason

    # Verify parity with M4 vectorised sim/transitions.py (decision 0018)
    now = 100.0
    sim_res = sim_tr.apply_commands(
        np.array([STATE_CODE[from_state]]),
        np.array([STATE_CODE[target]]),
        now,
        np.array([power_class != "AC_22"]),
        np.array([10.0]),
        np.array([np.nan if power_class == "AC_22" else 180.0]),
    )
    assert bool(sim_res.applied[0]) is res.applied
    assert int(sim_res.target[0]) == STATE_CODE[res.effective_target]
    assert int(sim_res.new_state[0]) == STATE_CODE[res.new_state]
    assert sim_res.reasons()[0] == res.drop_reason
    if res.wake_target is not None:
        assert float(sim_res.wake_end[0]) == pytest.approx(now + res.wake_s)
        assert int(sim_res.wake_target[0]) == STATE_CODE[res.wake_target]
    else:
        assert np.isinf(sim_res.wake_end[0])
        assert int(sim_res.wake_target[0]) == -1


def test_event_transitions_arrival_departure_fault_recovery():
    assert apply_event_transition("IDLE", "arrival", DC_PROFILE) == ("CHARGING", 0.0)
    assert apply_event_transition("STANDBY", "arrival", DC_PROFILE) == ("WAKING", 10.0)
    assert apply_event_transition("DEEP_SLEEP", "arrival", DC_PROFILE) == ("WAKING", 180.0)
    assert apply_event_transition("WAKING", "arrival", DC_PROFILE, remaining_wake_s=45.0) == (
        "WAKING",
        45.0,
    )
    assert apply_event_transition("WAKING", "wake_done", DC_PROFILE, wake_target="IDLE") == (
        "IDLE",
        0.0,
    )
    assert apply_event_transition("WAKING", "wake_done", DC_PROFILE, wake_target="STANDBY") == (
        "STANDBY",
        0.0,
    )
    assert apply_event_transition("WAKING", "wake_done", DC_PROFILE, wake_target="CHARGING") == (
        "CHARGING",
        0.0,
    )
    assert apply_event_transition("CHARGING", "departure", DC_PROFILE) == ("IDLE", 0.0)

    for st in ("CHARGING", "IDLE", "WAKING", "STANDBY", "DEEP_SLEEP", "OFFLINE"):
        assert apply_event_transition(st, "fault", DC_PROFILE) == ("OFFLINE", 0.0)
    assert apply_event_transition("OFFLINE", "recovered", DC_PROFILE) == ("IDLE", 0.0)


def test_forbidden_event_transitions_raise():
    with pytest.raises(ValueError):
        evaluate_command_transition("INVALID", "IDLE")
    with pytest.raises(ValueError):
        evaluate_command_transition("IDLE", "CHARGING")
    with pytest.raises(ValueError):
        apply_event_transition("CHARGING", "arrival", DC_PROFILE)
    with pytest.raises(ValueError):
        apply_event_transition("OFFLINE", "arrival", DC_PROFILE)
    with pytest.raises(ValueError):
        apply_event_transition("IDLE", "departure", DC_PROFILE)
    with pytest.raises(ValueError):
        apply_event_transition("IDLE", "wake_done", DC_PROFILE)
    with pytest.raises(ValueError):
        apply_event_transition("IDLE", "recovered", DC_PROFILE)
