"""Baseline policies: always_on and naive_quiet_hours (spec 04 §3, decision 0022)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from sim.config import TZ, PowerProfiles, power_profiles, sim_config
from sim.types import Command, PolicyName, SimState, State, TargetState

HOURS_PER_WEEK = 168
_SKIP = {State.CHARGING, State.WAKING, State.OFFLINE}
_ASLEEP = {State.STANDBY, State.DEEP_SLEEP}


class AlwaysOn:
    """Never sleeps any CP."""

    name = PolicyName.always_on.value

    def decide(self, state: SimState) -> list[Command]:
        return []


def hour_of_week(ts: pd.Timestamp | pd.Series) -> int | pd.Series:
    """Local (Europe/Bucharest) hour of week, Monday 00:00 = 0."""
    if isinstance(ts, pd.Series):
        local = ts.dt.tz_convert(TZ)
        return local.dt.day_of_week * 24 + local.dt.hour
    local = pd.Timestamp(ts).tz_convert(TZ)
    return local.day_of_week * 24 + local.hour


def _start_order(hours: int) -> np.ndarray:
    """Start hours 0..23 sorted by tie-break: centre nearest 03:00, then earliest."""
    h = np.arange(24)
    d = np.abs(((h + hours / 2) % 24) - 3)
    dist = np.minimum(d, 24 - d)
    return np.lexsort((h, dist))


def choose_windows(counts: np.ndarray, hours: int) -> np.ndarray:
    """Start hour (n_cps, 7) of the lowest-sum contiguous window per CP and weekday."""
    n = counts.shape[0]
    ext = np.concatenate([counts, counts[:, : hours - 1]], axis=1) if hours > 1 else counts
    csum = np.concatenate([np.zeros((n, 1)), np.cumsum(ext, axis=1)], axis=1)
    sums = csum[:, hours : hours + HOURS_PER_WEEK] - csum[:, :HOURS_PER_WEEK]
    sums = sums.reshape(n, 7, 24)
    order = _start_order(hours)
    best = np.argmin(sums[:, :, order], axis=2)  # argmin returns the first minimum
    return order[best]


class NaiveQuietHours:
    """Sleeps each CP in its quietest contiguous window per weekday, from training sessions."""

    name = PolicyName.naive_quiet_hours.value

    def __init__(
        self,
        sessions: pd.DataFrame,
        cps: pd.DataFrame,
        hours: int | None = None,
        profiles: PowerProfiles | None = None,
    ) -> None:
        self.hours = int(hours if hours is not None else sim_config().naive_quiet_hours_per_day)
        if not 1 <= self.hours <= 24:
            raise ValueError(f"hours must be in 1..24, got {self.hours}")
        self.profiles = profiles or power_profiles()
        self.cp_ids = list(cps["cp_id"])
        self.cp_index = {c: i for i, c in enumerate(self.cp_ids)}
        n = len(self.cp_ids)

        train = sessions[sessions["split"] == "train"]
        idx = pd.Index(self.cp_ids).get_indexer(train["cp_id"])
        keep = idx >= 0
        how = np.asarray(hour_of_week(train["arrival_ts"]))[keep]
        counts = np.zeros((n, HOURS_PER_WEEK))
        np.add.at(counts, (idx[keep], how), 1)
        local = train["arrival_ts"].dt.tz_convert(TZ)
        iso = local.dt.isocalendar()
        n_weeks = max(1, len(set(zip(iso["year"], iso["week"], strict=True))))
        self.rates = counts / n_weeks

        self.starts = choose_windows(counts, self.hours)
        # window_start[cp, how] = start hour of the window covering that slot, else -1
        self.window_start = np.full((n, HOURS_PER_WEEK), -1, dtype=np.int16)
        rows = np.arange(n)[:, None]
        for d in range(7):
            for k in range(self.hours):
                slot = (d * 24 + self.starts[:, d] + k) % HOURS_PER_WEEK
                self.window_start[rows[:, 0], slot] = self.starts[:, d]
        self.sleeping = self.window_start >= 0
        self._deep = {pc: self.profiles[pc].has_deep_sleep for pc in self.profiles.by_class}

    def window_label(self, start: int) -> str:
        return f"{start:02d}-{(start + self.hours) % 24:02d}"

    def decide(self, state: SimState) -> list[Command]:
        how = hour_of_week(pd.Timestamp(state.t))
        col = self.window_start[:, how]
        out: list[Command] = []
        for cp in state.cps:
            if cp.state in _SKIP:
                continue
            i = self.cp_index.get(cp.cp_id)
            start = int(col[i]) if i is not None else -1
            if start >= 0:
                target = State.DEEP_SLEEP if self._deep[cp.power_class] else State.STANDBY
                if cp.state != target:
                    reason = f"quiet window {self.window_label(start)}"
                    out.append(Command(cp.cp_id, TargetState(target.value), reason))
            elif cp.state in _ASLEEP:
                out.append(Command(cp.cp_id, TargetState.IDLE, "window end"))
        return out
