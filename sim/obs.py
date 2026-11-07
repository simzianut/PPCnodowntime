"""Observation builder and action mask for the gym env and rl_ppo (CONTRACTS, decision 0017)."""

from __future__ import annotations

from collections.abc import Sequence
from functools import cache

import numpy as np
import pandas as pd

from sim.config import TZ, power_profiles, signals_config
from sim.types import (
    FORECAST_COLUMNS,
    IDLE,
    POWER_CLASSES,
    STATE_INDEX,
    STATES,
    CpView,
    PowerClass,
    Signal,
    SimState,
    State,
)

OBS_FIELDS: list[str] = [
    "p_arrival_15",
    "p_arrival_30",
    "p_arrival_60",
    "expected_arrivals_60",
    "intents_0_15",
    "intents_15_30",
    "intents_30_60",
    "ev_flow_15",
    "traffic_index",
    "hour_sin",
    "hour_cos",
    *[f"weekday_{d}" for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")],
    "is_holiday",
    *[f"state_{s.value.lower()}" for s in STATES],
    "seconds_in_state",
    "siblings_idle_share",
    "siblings_total",
    *[f"power_{pc.value.lower()}" for pc in POWER_CLASSES],
]
OBS_DIM = 31
assert len(OBS_FIELDS) == OBS_DIM

_NS_MIN = 60 * 10**9
_BUCKETS_NS = np.array([0, 15, 30, 60], dtype=np.int64) * _NS_MIN
_PC_INDEX = {pc: i for i, pc in enumerate(POWER_CLASSES)}
_NO_SLEEP = {State.CHARGING, State.WAKING, State.OFFLINE}


@cache
def _w_ice() -> float:
    return float(signals_config()["w_ice"])


@cache
def _has_deep_sleep(pc: PowerClass) -> bool:
    return power_profiles()[pc].has_deep_sleep


def _ns(s: pd.Series) -> np.ndarray:
    """UTC int64 nanoseconds; NaT becomes INT64 min."""
    if s.dt.tz is None:
        s = s.dt.tz_localize("UTC")
    return s.to_numpy(dtype="datetime64[ns]").view("int64")


def _signal_features(
    sig: pd.DataFrame | None, loc_index: pd.Index, t_ns: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-location intent counts (n_loc, 3), weighted EV flow and latest traffic index."""
    n_loc = len(loc_index)
    intents = np.zeros((n_loc, 3))
    flow = np.zeros(n_loc)
    traffic = np.zeros(n_loc)
    if sig is None or len(sig) == 0:
        return intents, flow, traffic
    loc = loc_index.get_indexer(sig["location_id"])
    ts = _ns(sig["ts"])
    kind = sig["signal"].to_numpy().astype(str)
    value = sig["value"].to_numpy(dtype=np.float64)
    ok = (loc >= 0) & (ts <= t_ns)

    m = ok & (kind == Signal.ppcblue_route_intent.value)
    if m.any() and "eta_ts" in sig:
        eta = _ns(sig["eta_ts"])[m]
        valid = eta != np.iinfo(np.int64).min
        d = eta[valid] - t_ns
        b = np.searchsorted(_BUCKETS_NS, d, side="left") - 1  # (lo, hi] buckets
        inb = (b >= 0) & (b < 3)
        idx = loc[m][valid][inb] * 3 + b[inb]
        intents += np.bincount(idx, minlength=n_loc * 3).reshape(n_loc, 3)

    recent = ok & (ts > t_ns - 15 * _NS_MIN)
    w = np.where(
        kind == Signal.maps_ev_count.value,
        1.0,
        np.where(kind == Signal.maps_ice_count.value, _w_ice(), 0.0),
    )
    m = recent & (w > 0)
    if m.any():
        flow += np.bincount(loc[m], weights=w[m] * value[m], minlength=n_loc)

    m = ok & (kind == Signal.maps_traffic_index.value)
    if m.any():
        tl, tt, tv = loc[m], ts[m], value[m]
        order = np.lexsort((tt, tl))
        tl, tv = tl[order], tv[order]
        last = np.flatnonzero(np.append(tl[1:] != tl[:-1], True))
        traffic[tl[last]] = tv[last]
    return intents, flow, traffic


def build_obs(state: SimState, cp_ids: Sequence[str]) -> np.ndarray:
    """Observation matrix (len(cp_ids), 31), float32, in OBS_FIELDS order."""
    cps = state.cps
    n_all = len(cps)
    pos = {cp.cp_id: i for i, cp in enumerate(cps)}
    sel = np.fromiter((pos[c] for c in cp_ids), dtype=np.int64, count=len(cp_ids))
    n = len(sel)
    obs = np.zeros((n, OBS_DIM), dtype=np.float32)
    if n == 0:
        return obs

    loc_codes, loc_index = pd.factorize(pd.Index([cp.location_id for cp in cps]))
    loc_index = pd.Index(loc_index)
    state_code = np.fromiter((STATE_INDEX[cp.state] for cp in cps), dtype=np.int64, count=n_all)
    secs = np.fromiter((cp.seconds_in_state for cp in cps), dtype=np.float64, count=n_all)
    pc_code = np.fromiter(
        (_PC_INDEX[PowerClass(cp.power_class)] for cp in cps), dtype=np.int64, count=n_all
    )

    if state.forecast is not None and len(state.forecast):
        f = state.forecast
        rows = pd.Index(f["cp_id"]).get_indexer(list(cp_ids))
        vals = f[FORECAST_COLUMNS[1:]].to_numpy(dtype=np.float64)
        hit = rows >= 0
        fc = np.zeros((n, 4))
        fc[hit] = np.nan_to_num(vals[rows[hit]])
        obs[:, 0:3] = fc[:, 0:3]
        obs[:, 3] = np.log1p(np.maximum(fc[:, 3], 0))

    t = pd.Timestamp(state.t)
    if t.tzinfo is None:
        t = t.tz_localize(TZ)
    t_ns = int(t.tz_convert("UTC").as_unit("ns").value)
    intents, flow, traffic = _signal_features(state.signals, loc_index, t_ns)
    lc = loc_codes[sel]
    obs[:, 4:7] = np.minimum(intents[lc], 5) / 5
    obs[:, 7] = np.log1p(np.maximum(flow[lc], 0))
    obs[:, 8] = traffic[lc]

    local = t.tz_convert(TZ)
    angle = 2 * np.pi * (local.hour * 60 + local.minute) / 1440
    obs[:, 9] = np.sin(angle)
    obs[:, 10] = np.cos(angle)
    obs[:, 11 + local.day_of_week] = 1
    obs[:, 18] = float(any(bool(v.get("is_holiday")) for v in (state.context or {}).values()))

    obs[np.arange(n), 19 + state_code[sel]] = 1
    obs[:, 25] = np.minimum(secs[sel] / 3600, 1)

    n_loc = len(loc_index)
    total = np.bincount(loc_codes, minlength=n_loc)
    idle = np.bincount(loc_codes, weights=(state_code == IDLE).astype(float), minlength=n_loc)
    sib_total = total[lc] - 1
    sib_idle = idle[lc] - (state_code[sel] == IDLE)
    obs[:, 26] = np.divide(sib_idle, sib_total, out=np.zeros(n), where=sib_total > 0)
    obs[:, 27] = np.minimum(sib_total / 10, 1)
    obs[np.arange(n), 28 + pc_code[sel]] = 1
    return obs


def action_mask(cp: CpView) -> np.ndarray:
    """Valid actions in ACTIONS order (IDLE, STANDBY, DEEP_SLEEP)."""
    if cp.state in _NO_SLEEP:
        return np.array([True, False, False])
    return np.array([True, True, _has_deep_sleep(PowerClass(cp.power_class))])
