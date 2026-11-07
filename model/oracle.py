"""Hindsight oracle: an upper bound that reads the true sessions. Never used in live mode."""

from __future__ import annotations

import numpy as np
import pandas as pd

from sim.config import cp_power, power_profiles, sim_config
from sim.types import Command, SimState, State, TargetState

_BUSY = {State.CHARGING, State.WAKING, State.OFFLINE}
_ASLEEP = {State.STANDBY, State.DEEP_SLEEP}


class HindsightOraclePolicy:
    """Sleeps each idle CP as deep as its next own arrival allows, wakes it just in time.

    Simplification: only arrivals at the CP itself count (no sibling routing, decision 0019).
    """

    name = "oracle"

    def __init__(self, sessions: pd.DataFrame, slot_s: int | None = None) -> None:
        self.slot_s = int(slot_s or sim_config().slot_s)
        self.extra_wh = power_profiles().wake_extra_wh
        ts = pd.to_datetime(sessions["arrival_ts"], utc=True)
        secs = ts.astype("int64").to_numpy() / 1e9 if len(ts) else np.empty(0)
        self._arr: dict[str, np.ndarray] = {}
        for cp, grp in pd.Series(secs).groupby(sessions["cp_id"].astype(str).to_numpy()):
            self._arr[str(cp)] = np.sort(grp.to_numpy())

    def _next_arrival(self, cp_id: str, now: float) -> float:
        a = self._arr.get(cp_id)
        if a is None:
            return np.inf
        i = int(np.searchsorted(a, now, side="left"))
        return float(a[i]) if i < len(a) else np.inf

    def _fits(self, gap: float, wake_s: float, idle_w: float, sleep_w: float) -> bool:
        if gap < self.slot_s + wake_s:
            return False
        return (idle_w - sleep_w) * (gap - wake_s) / 3600.0 > self.extra_wh

    def decide(self, state: SimState) -> list[Command]:
        now = state.t.timestamp()
        out: list[Command] = []
        for cp in state.cps:
            if cp.state in _BUSY:
                continue
            p = cp_power(cp.power_class, cp.max_kw)
            gap = self._next_arrival(cp.cp_id, now) - now
            if cp.state in _ASLEEP:
                wake = p.standby_wake_s if cp.state == State.STANDBY else p.deep_sleep_wake_s
                if gap < self.slot_s + float(wake or 0.0):
                    out.append(Command(cp.cp_id, TargetState.IDLE, "oracle wake"))
                continue
            if p.has_deep_sleep and self._fits(
                gap, p.deep_sleep_wake_s or 0.0, p.idle_w, p.deep_sleep_w
            ):
                out.append(Command(cp.cp_id, TargetState.DEEP_SLEEP, "oracle"))
            elif self._fits(gap, p.standby_wake_s, p.idle_w, p.standby_w):
                out.append(Command(cp.cp_id, TargetState.STANDBY, "oracle"))
        return out
