"""Vectorised command transitions (spec 03 §4, decision 0018)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from sim.types import CHARGING, DEEP_SLEEP, IDLE, OFFLINE, STANDBY, WAKING, TargetState

APPLIED, DROP_SAME, DROP_BUSY, DROP_WAKING, DROP_OFFLINE = range(5)
DROP_REASONS: dict[int, str] = {
    DROP_SAME: "same",
    DROP_BUSY: "busy",
    DROP_WAKING: "waking",
    DROP_OFFLINE: "offline",
}
TARGET_CODES: dict[str, int] = {
    TargetState.IDLE.value: IDLE,
    TargetState.STANDBY.value: STANDBY,
    TargetState.DEEP_SLEEP.value: DEEP_SLEEP,
}
CODE_TARGETS: dict[int, TargetState] = {v: TargetState(k) for k, v in TARGET_CODES.items()}


def target_code(target: TargetState | str) -> int:
    """State code of a command target; ValueError if it is not a TargetState."""
    try:
        return TARGET_CODES[str(target)]
    except KeyError:
        raise ValueError(f"not a TargetState: {target!r}") from None


@dataclass
class TransitionResult:
    """Per-command outcome, aligned with the input arrays."""

    code: np.ndarray  # APPLIED or a DROP_* code
    target: np.ndarray  # effective target: DEEP_SLEEP becomes STANDBY without deep sleep
    new_state: np.ndarray  # state right after the command (unchanged when dropped)
    wake_end: np.ndarray  # time the started wake ends, inf otherwise
    wake_target: np.ndarray  # state reached when the wake ends, -1 otherwise

    @property
    def applied(self) -> np.ndarray:
        return self.code == APPLIED

    @property
    def started_waking(self) -> np.ndarray:
        return (self.code == APPLIED) & (self.new_state == WAKING)

    def reasons(self) -> list[str | None]:
        """Drop reason per command, None when applied."""
        return [DROP_REASONS.get(int(c)) for c in self.code]


def apply_commands(
    state: np.ndarray,
    target: np.ndarray,
    now: float,
    has_deep_sleep: np.ndarray,
    standby_wake_s: np.ndarray,
    deep_sleep_wake_s: np.ndarray,
) -> TransitionResult:
    """Apply one command per entry at time `now`; pure, inputs are not modified."""
    state = np.asarray(state, dtype=np.int8)
    target = np.asarray(target, dtype=np.int8)
    has_deep = np.asarray(has_deep_sleep, dtype=bool)
    eff = np.where((target == DEEP_SLEEP) & ~has_deep, STANDBY, target).astype(np.int8)

    code = np.full(state.shape, APPLIED, dtype=np.int8)
    code[eff == state] = DROP_SAME
    code[state == CHARGING] = DROP_BUSY
    code[state == WAKING] = DROP_WAKING
    code[state == OFFLINE] = DROP_OFFLINE
    ok = code == APPLIED

    deep_to_standby = (state == DEEP_SLEEP) & (eff == STANDBY)
    sleep = ok & (eff != IDLE) & ~deep_to_standby
    wake = ok & ~sleep

    new_state = state.copy()
    new_state[sleep] = eff[sleep]
    new_state[wake] = WAKING
    wake_s = np.where(state == DEEP_SLEEP, deep_sleep_wake_s, standby_wake_s).astype(float)
    wake_end = np.where(wake, now + np.nan_to_num(wake_s), np.inf)
    wake_target = np.where(wake, eff, -1).astype(np.int8)
    return TransitionResult(code, eff, new_state, wake_end, wake_target)
