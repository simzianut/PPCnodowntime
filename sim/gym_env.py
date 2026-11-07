"""Gymnasium env: one CP x one local day, siblings follow a fixed policy (spec 04 §4, 0023)."""

from __future__ import annotations

import warnings
import weakref
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

import gymnasium
import numpy as np
import pandas as pd
from gymnasium import spaces
from gymnasium.utils import seeding

from sim.baselines import NaiveQuietHours
from sim.config import power_profiles, sim_config
from sim.data import SimData, load_default, processed_available
from sim.engine import ZONE, Engine
from sim.forecast import PrecomputedForecaster, ZeroForecaster
from sim.obs import OBS_DIM, OBS_FIELDS, action_mask, build_obs
from sim.types import (
    ACTIONS,
    POWER_CLASSES,
    STATE_INDEX,
    Command,
    Forecaster,
    Policy,
    PowerClass,
    SimState,
    State,
)

AGENT = "agent"
_SLICE_CACHE = 1024
_EXO = OBS_FIELDS.index("state_charging")
_SECS = OBS_FIELDS.index("seconds_in_state")
_SIB_SHARE = OBS_FIELDS.index("siblings_idle_share")
_SIB_TOTAL = OBS_FIELDS.index("siblings_total")
_POWER = OBS_FIELDS.index("power_ac_22")
_PC_INDEX = {pc: i for i, pc in enumerate(POWER_CLASSES)}
_DEFAULT_DATA: dict[int, SimData] = {}
_INDEXES: dict[int, tuple[weakref.ref, _DataIndex]] = {}


def default_data(seed: int = 0) -> SimData:
    """load_default(seed), loaded once per process and shared by all envs."""
    key = -1 if processed_available() else seed
    if key not in _DEFAULT_DATA:
        _DEFAULT_DATA[key] = load_default(seed)
    return _DEFAULT_DATA[key]


def _ns(s: pd.Series) -> np.ndarray:
    s = pd.to_datetime(s, utc=True)
    return s.to_numpy(dtype="datetime64[ns]").view("int64")


def _group(keys: pd.Series, ts: np.ndarray) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """key -> (sorted ts, row positions in that order)."""
    codes, uniques = pd.factorize(keys)
    order = np.lexsort((ts, codes))
    codes = codes[order]
    cuts = np.flatnonzero(np.diff(codes)) + 1
    out = {}
    starts = np.concatenate([[0], cuts]).astype(np.int64)
    for s, part in zip(starts.tolist(), np.split(order, cuts), strict=True):
        if len(part) and codes[s] >= 0:
            out[str(uniques[codes[s]])] = (ts[part], part)
    return out


@dataclass
class _Slice:
    """One location-day: the cut SimData, its forecaster and cached state-free obs columns."""

    data: SimData
    cp_ids: list[str]
    forecasts: PrecomputedForecaster | None
    exo: np.ndarray  # (n_slots + 1, n_cps, _EXO) obs columns 0.._EXO-1 per slot
    exo_ok: np.ndarray  # (n_slots + 1,) bool


class _DataIndex:
    """Per-bundle lookups that cut SimData to one location and day, with an LRU cache."""

    def __init__(self, data: SimData) -> None:
        self.data = data
        cps = data.cps.drop_duplicates("cp_id").reset_index(drop=True)
        self.cps = cps
        self.cp_ids: list[str] = [str(c) for c in cps["cp_id"]]
        self.cp_loc = dict(zip(self.cp_ids, cps["location_id"].astype(str), strict=True))
        self.cp_pc = dict(zip(self.cp_ids, cps["power_class"].map(PowerClass), strict=True))
        self.loc_cps = {str(k): v for k, v in cps.groupby("location_id").indices.items()}
        self.days: dict[str | None, list[date]] = {}
        s = data.sessions
        self.sess = _group(s["location_id"].astype(str), _ns(s["arrival_ts"]))
        self.sess_dep = _ns(s["departure_ts"])
        g = data.signals
        self.sig = _group(g["location_id"].astype(str), _ns(g["ts"]))
        self.ctx_ts = _ns(data.context["ts_hour"])
        self.fc = None
        if data.forecasts is not None:
            f = data.forecasts
            loc = f["cp_id"].astype(str).map(self.cp_loc).fillna("")
            self.fc = _group(loc, _ns(f["ts"]))
        self._slices: OrderedDict[tuple[str, date], _Slice] = OrderedDict()
        self._naive: NaiveQuietHours | None = None

    def split_days(self, split: str) -> list[date]:
        if split not in self.days:
            self.days[split] = self.data.days(split)
        return self.days[split]

    def naive(self) -> NaiveQuietHours:
        if self._naive is None:
            self._naive = NaiveQuietHours(self.data.sessions, self.data.cps)
        return self._naive

    def slice(self, loc: str, day: date) -> _Slice:
        key = (loc, day)
        hit = self._slices.get(key)
        if hit is not None:
            self._slices.move_to_end(key)
            return hit
        out = self._build(loc, day)
        self._slices[key] = out
        if len(self._slices) > _SLICE_CACHE:
            self._slices.popitem(last=False)
        return out

    def _build(self, loc: str, day: date) -> _Slice:
        d = self.data
        t0 = int(datetime.combine(day, time(0), tzinfo=ZONE).timestamp()) * 10**9
        nxt = day + timedelta(days=1)
        t1 = int(datetime.combine(nxt, time(0), tzinfo=ZONE).timestamp()) * 10**9
        hour = 3600 * 10**9
        cps = self.cps.iloc[self.loc_cps[loc]].reset_index(drop=True)

        empty = (np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64))
        a_ts, a_pos = self.sess.get(loc, empty)
        live = a_pos[(a_ts < t1) & (self.sess_dep[a_pos] > t0)]
        sessions = d.sessions.iloc[np.sort(live)].reset_index(drop=True)

        g_ts, g_pos = self.sig.get(loc, empty)
        lo = np.searchsorted(g_ts, t0 - hour, side="left")
        hi = np.searchsorted(g_ts, t1, side="right")
        signals = d.signals.iloc[g_pos[lo:hi]].reset_index(drop=True)

        counties = set(cps["county_code"].astype(str)) if "county_code" in cps else None
        m = (self.ctx_ts >= t0 - 6 * hour) & (self.ctx_ts <= t1)
        context = d.context[m]
        if counties is not None:
            context = context[context["county_code"].astype(str).isin(counties)]
        context = context.reset_index(drop=True)

        fc = None
        if self.fc is not None:
            f_ts, f_pos = self.fc.get(loc, empty)
            lo = np.searchsorted(f_ts, t0, side="left")
            hi = np.searchsorted(f_ts, t1, side="right")
            frame = d.forecasts.iloc[f_pos[lo:hi]].reset_index(drop=True)
            fc = PrecomputedForecaster(frame) if len(frame) else None
        locs = d.locations[d.locations["location_id"].astype(str) == loc].reset_index(drop=True)
        sub = SimData(locs, cps, sessions, signals, context, None)
        n_slots = (t1 - t0) // (sim_config().slot_s * 10**9)
        exo = np.zeros((n_slots + 1, len(cps), _EXO), dtype=np.float32)
        ids = [str(c) for c in cps["cp_id"]]
        return _Slice(sub, ids, fc, exo, np.zeros(n_slots + 1, dtype=bool))


def _index(data: SimData) -> _DataIndex:
    """Shared index per SimData object, so parallel envs in one process build it once."""
    hit = _INDEXES.get(id(data))
    if hit is not None and hit[0]() is data:
        return hit[1]
    idx = _DataIndex(data)
    _INDEXES[id(data)] = (weakref.ref(data), idx)
    return idx


class _CachedForecaster:
    """Reuses the last prediction while t and the CP list stay the same."""

    def __init__(self, inner: Forecaster) -> None:
        self.inner = inner
        self._key: tuple | None = None
        self._val: pd.DataFrame | None = None

    def predict(self, state: SimState) -> pd.DataFrame:
        key = (state.t, len(state.cps))
        if key != self._key:
            self._key, self._val = key, self.inner.predict(state)
        return self._val


class _AgentPolicy:
    """Siblings follow `sibling`; the agent CP gets only the env's pending command."""

    name = AGENT

    def __init__(self, sibling: Policy, cp_id: str) -> None:
        self.sibling = sibling
        self.cp_id = cp_id
        self.pending: Command | None = None

    def decide(self, state: SimState) -> list[Command]:
        out = [c for c in self.sibling.decide(state) if c.cp_id != self.cp_id]
        if self.pending is not None:
            out.append(self.pending)
            self.pending = None
        return out


def _default_sibling(index: _DataIndex, lambda_wait: float) -> Policy:
    # Decision 0023: cost_rule once M2 ships it, naive_quiet_hours until then.
    # OSError: LightGBM's native lib (libomp) missing; model/ imports it on load.
    try:
        from model.cost_rule import CostRulePolicy
    except (ImportError, OSError) as exc:
        warnings.warn(
            f"cost_rule unavailable ({exc}); siblings use naive_quiet_hours", stacklevel=3
        )
        return index.naive()
    mapl = sim_config().min_awake_per_location
    return CostRulePolicy(lambda_wait=lambda_wait, min_awake_per_location=mapl)


def _obs_space() -> spaces.Box:
    low = np.zeros(OBS_DIM, dtype=np.float32)
    high = np.ones(OBS_DIM, dtype=np.float32)
    for name in ("hour_sin", "hour_cos"):
        low[OBS_FIELDS.index(name)] = -1
    for name in ("expected_arrivals_60", "ev_flow_15", "traffic_index"):
        high[OBS_FIELDS.index(name)] = np.inf
    return spaces.Box(low, high, dtype=np.float32)


class ChargePointEnv(gymnasium.Env):
    """One CP over one local day in 5-min slots; reward per CONTRACTS (decision 0020)."""

    metadata = {"render_modes": []}

    def __init__(
        self,
        split: str = "train",
        lambda_wait: float | None = None,
        sibling_policy: Policy | None = None,
        seed: int = 0,
        days: Sequence[date] | None = None,
        data: SimData | None = None,
    ) -> None:
        super().__init__()
        cfg = sim_config()
        self.split = split
        self.lambda_wait = float(cfg.default_lambda if lambda_wait is None else lambda_wait)
        self.data = data if data is not None else default_data(seed)
        self._idx = _index(self.data)
        self.days = list(days) if days is not None else self._idx.split_days(split)
        if not self.days:
            raise ValueError(f"no days to sample from (split={split!r}, days={days!r})")
        self.sibling_policy = sibling_policy or _default_sibling(self._idx, self.lambda_wait)
        self.sibling_policy_name = str(self.sibling_policy.name)
        self.slot_s = int(cfg.slot_s)
        prof = power_profiles()
        self._norm = {pc: prof[pc].idle_w * self.slot_s / 3.6e6 for pc in PowerClass}

        self.action_space = spaces.Discrete(len(ACTIONS))
        self.observation_space = _obs_space()
        self._np_random, self._np_random_seed = seeding.np_random(seed)

        self.engine: Engine | None = None
        self.cp_id = ""
        self.day: date | None = None
        self._agent: _AgentPolicy | None = None
        self._i = 0
        self._norm_cp = 1.0
        self._state: SimState | None = None
        self._slice: _Slice | None = None
        self._static = np.zeros(OBS_DIM - _EXO, dtype=np.float32)
        self._obs = np.zeros(OBS_DIM, dtype=np.float32)

    def reset(
        self, *, seed: int | None = None, options: dict[str, Any] | None = None
    ) -> tuple[np.ndarray, dict]:
        super().reset(seed=seed)
        rng = self.np_random
        self.day = self.days[int(rng.integers(len(self.days)))]
        self.cp_id = self._idx.cp_ids[int(rng.integers(len(self._idx.cp_ids)))]
        sl = self._idx.slice(self._idx.cp_loc[self.cp_id], self.day)
        inner = sl.forecasts if sl.forecasts is not None else ZeroForecaster()
        self._agent = _AgentPolicy(self.sibling_policy, self.cp_id)
        self._slice = sl
        self.engine = Engine(
            sl.data, self.day, [self._agent], forecaster=_CachedForecaster(inner), cp_ids=sl.cp_ids
        )
        self._i = sl.cp_ids.index(self.cp_id)
        pc = self._idx.cp_pc[self.cp_id]
        self._static = np.zeros(OBS_DIM - _EXO, dtype=np.float32)
        self._static[_SIB_TOTAL - _EXO] = min((len(sl.cp_ids) - 1) / 10, 1)
        self._static[_POWER + _PC_INDEX[pc] - _EXO] = 1
        self._norm_cp = self._norm[self._idx.cp_pc[self.cp_id]]
        self._observe()
        return self._obs, self._info(0)

    def step(self, action: int) -> tuple[np.ndarray, float, bool, bool, dict]:
        if self.engine is None or self.engine.done:
            raise RuntimeError("call reset() first")
        a = int(action)
        if self.action_masks()[a]:
            self._agent.pending = Command(self.cp_id, ACTIONS[a], reason="rl")
        res = self.engine.step()
        e = float(res.overhead_kwh[AGENT][self._i])
        w = float(res.wait_min[AGENT][self._i])
        reward = -(e + self.lambda_wait * w) / self._norm_cp
        # The terminal observation is built at the end-of-day time like any other.
        self._observe()
        info = self._info(res.slot)
        info["overhead_kwh"], info["wait_min"] = e, w
        return self._obs, reward, self.engine.done, False, info

    def action_masks(self) -> np.ndarray:
        """Valid actions (IDLE, STANDBY, DEEP_SLEEP) for the agent CP now, for MaskablePPO."""
        if self._state is None:
            return np.ones(len(ACTIONS), dtype=bool)
        return action_mask(self._state.cps[self._i])

    def _observe(self) -> None:
        """Equals build_obs(state, [cp_id])[0]; columns < _EXO are cached per location-day."""
        st = self._state = self.engine.sim_state(AGENT)
        sl, k = self._slice, self.engine.slot
        if not sl.exo_ok[k]:
            full = build_obs(st, sl.cp_ids)
            sl.exo[k], sl.exo_ok[k] = full[:, :_EXO], True
            self._obs = full[self._i]
            return
        obs = np.empty(OBS_DIM, dtype=np.float32)
        obs[:_EXO] = sl.exo[k, self._i]
        obs[_EXO:] = self._static
        cps = st.cps
        me = cps[self._i]
        obs[_EXO + STATE_INDEX[me.state]] = 1
        obs[_SECS] = min(me.seconds_in_state / 3600, 1)
        if len(cps) > 1:
            idle = sum(cp.state == State.IDLE for cp in cps) - (me.state == State.IDLE)
            obs[_SIB_SHARE] = idle / (len(cps) - 1)
        self._obs = obs

    def _info(self, slot: int) -> dict:
        return {
            "overhead_kwh": 0.0,
            "wait_min": 0.0,
            "state": self._state.cps[self._i].state.value,
            "cp_id": self.cp_id,
            "day": self.day,
            "slot": slot,
            "action_mask": self.action_masks(),
        }
