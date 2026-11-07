"""Charge-point state model and transition rules (spec 03 §4, decision 0018)."""

from __future__ import annotations

from dataclasses import dataclass

VALID_STATES = ("CHARGING", "IDLE", "WAKING", "STANDBY", "DEEP_SLEEP", "OFFLINE")
VALID_TARGET_STATES = ("IDLE", "STANDBY", "DEEP_SLEEP")
VALID_CAUSES = ("agent", "operator", "arrival", "departure", "fault", "live_status")


@dataclass(frozen=True)
class CommandTransition:
    """Outcome of evaluating a policy/operator command on a single charge point."""

    applied: bool
    status: str
    from_state: str
    effective_target: str
    new_state: str
    wake_s: float
    wake_target: str | None
    drop_reason: str | None


def evaluate_command_transition(
    from_state: str,
    target_state: str,
    power_class: str = "DC_50",
    profile: dict | None = None,
) -> CommandTransition:
    """Evaluate a target-state command per spec 03 §4 and decision 0018."""
    st = str(from_state)
    tgt = str(target_state)
    if st not in VALID_STATES:
        raise ValueError(f"invalid state: {from_state!r}")
    if tgt not in VALID_TARGET_STATES:
        raise ValueError(f"invalid target_state: {target_state!r}")

    pc = str(power_class)
    has_deep = pc != "AC_22" and (profile is None or profile.get("deep_sleep_w") is not None)
    eff = "STANDBY" if (tgt == "DEEP_SLEEP" and not has_deep) else tgt

    if st == "CHARGING":
        return CommandTransition(False, "rejected", st, eff, st, 0.0, None, "busy")
    if st == "WAKING":
        return CommandTransition(False, "dropped", st, eff, st, 0.0, None, "waking")
    if st == "OFFLINE":
        return CommandTransition(False, "dropped", st, eff, st, 0.0, None, "offline")
    if eff == st:
        return CommandTransition(False, "dropped", st, eff, st, 0.0, None, "same")

    deep_to_standby = st == "DEEP_SLEEP" and eff == "STANDBY"
    if eff != "IDLE" and not deep_to_standby:
        return CommandTransition(True, "applied", st, eff, eff, 0.0, None, None)

    sb_wake = float((profile or {}).get("standby_wake_s") or 10.0)
    ds_wake = float((profile or {}).get("deep_sleep_wake_s") or 180.0)
    wake_s = ds_wake if st == "DEEP_SLEEP" else sb_wake
    return CommandTransition(True, "applied", st, eff, "WAKING", wake_s, eff, None)


def apply_event_transition(
    from_state: str,
    event: str,
    profile: dict | None = None,
    *,
    wake_target: str = "IDLE",
    remaining_wake_s: float | None = None,
) -> tuple[str, float]:
    """Return (new_state, added_wait_s) for arrival, wake_done, departure, fault or recovered."""
    st = str(from_state)
    if st not in VALID_STATES:
        raise ValueError(f"invalid state: {from_state!r}")

    sb_wake = float((profile or {}).get("standby_wake_s") or 10.0)
    ds_wake = float((profile or {}).get("deep_sleep_wake_s") or 180.0)

    if event == "arrival":
        if st == "IDLE":
            return "CHARGING", 0.0
        if st == "STANDBY":
            return "WAKING", sb_wake
        if st == "DEEP_SLEEP":
            return "WAKING", ds_wake
        if st == "WAKING":
            wait_s = float(remaining_wake_s if remaining_wake_s is not None else sb_wake)
            return "WAKING", wait_s
        raise ValueError(f"cannot process arrival in state {st}")

    if event == "wake_done":
        if st != "WAKING":
            raise ValueError(f"wake_done only valid from WAKING, got {st}")
        if wake_target not in ("IDLE", "STANDBY", "CHARGING"):
            raise ValueError(f"invalid wake_target: {wake_target!r}")
        return wake_target, 0.0

    if event == "departure":
        if st != "CHARGING":
            raise ValueError(f"departure only valid from CHARGING, got {st}")
        return "IDLE", 0.0

    if event == "fault":
        return "OFFLINE", 0.0

    if event == "recovered":
        if st != "OFFLINE":
            raise ValueError(f"recovered only valid from OFFLINE, got {st}")
        return "IDLE", 0.0

    raise ValueError(f"unknown event: {event!r}")
