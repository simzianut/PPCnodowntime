"""Slot engine: one local day, several policies side by side (spec 04 §1, decisions 0018-0026)."""

from __future__ import annotations

import heapq
import itertools
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from sim import transitions as tr
from sim.config import TZ, PowerProfiles, SimConfig, power_profiles, sim_config
from sim.data import SimData
from sim.metrics import PolicyMetrics
from sim.types import (
    CHARGING,
    DEEP_SLEEP,
    IDLE,
    STANDBY,
    STATES,
    WAKING,
    Autonomy,
    CommandStatus,
    CpView,
    Forecaster,
    Policy,
    PowerClass,
    SimState,
    State,
)

ZONE = ZoneInfo(TZ)
ALWAYS_ON = "always_on"
INTENT = "ppcblue_route_intent"
_EPOCH = pd.Timestamp(0, tz="UTC")
_DEP, _WAKE, _ARR = 0, 1, 2  # event order at equal timestamps
_STATE_OBJ = np.array(STATES, dtype=object)
_SLEEPING = np.zeros(len(STATES), dtype=bool)
_SLEEPING[[STANDBY, DEEP_SLEEP]] = True
_PENDING = CommandStatus.pending.value
_EXPIRED = CommandStatus.expired.value


@dataclass
class StepResult:
    """Outcome of one simulated slot."""

    slot: int
    sim_time: datetime
    kpis: dict[str, dict]
    changes: dict[str, list[tuple[str, State]]]
    events: dict[str, list[dict]]
    overhead_kwh: dict[str, np.ndarray]
    wait_min: dict[str, np.ndarray]
    new_recommendations: list[dict]


class _AlwaysOn:
    name = ALWAYS_ON

    def decide(self, state: SimState) -> list:
        return []


@dataclass(eq=False)
class _Cmd:
    """A command record: applied directly (ACT) or a recommendation (id > 0)."""

    ts: float
    cp: int
    policy: str
    from_state: int
    target: int
    reason: str
    status: str
    id: int = 0
    expires: float = 0.0


class _PolicySim:
    """One policy's per-CP state arrays, metrics and history."""

    def __init__(
        self,
        policy: Policy,
        hidden: bool,
        state0: np.ndarray,
        dep0: np.ndarray,
        forecast_slots: int,
    ) -> None:
        n = len(state0)
        self.policy = policy
        self.name = str(policy.name)
        self.hidden = hidden
        self.state = state0.copy()
        self.prev_state = state0.copy()
        self.since = np.zeros(n)
        self.acct = np.zeros(n)  # energy accounted up to this time
        self.wake_end = np.full(n, np.inf)
        self.wake_target = np.full(n, -1, dtype=np.int8)
        self.dep_at = dep0.copy()  # departure of the session on the CP (CHARGING or claimed WAKING)
        self.queues: dict[int, deque[float]] = {}
        self.metrics = PolicyMetrics()
        self.slot_wh = np.zeros(n)
        self.slot_sleep_s = 0.0
        self.wait = np.zeros(n)
        self.events: list[dict] = []
        self.hist_states: list[list[tuple[float, int]]] = [[(0.0, s)] for s in state0.tolist()]
        self.hist_arrivals: list[list[tuple[float, float]]] = [[] for _ in range(n)]
        self.hist_cmds: list[list[_Cmd]] = [[] for _ in range(n)]
        self.p30 = (
            np.full((forecast_slots, n), np.nan, dtype=np.float32) if forecast_slots else None
        )


def compatible(
    ct_a: str, conns_a: Sequence[str], pc_a: str, ct_b: str, conns_b: Sequence[str], pc_b: str
) -> bool:
    """Decision 0019: same current type and a shared connector, else same power class."""
    if ct_a != ct_b:
        return False
    if not len(conns_a) or not len(conns_b):
        return pc_a == pc_b
    return bool(set(conns_a) & set(conns_b))


def _epoch_s(values: pd.Series) -> np.ndarray:
    s = pd.to_datetime(pd.Series(values), utc=True)
    return (s - _EPOCH).dt.total_seconds().to_numpy(dtype=float)


def _as_list(x: object) -> list[str]:
    if isinstance(x, str):
        return [c for c in x.split(";") if c]
    if isinstance(x, list | tuple | np.ndarray):
        return [str(c) for c in x]
    return []


class Engine:
    """Simulates one local day in slots for several policies over the same sessions."""

    def __init__(
        self,
        data: SimData,
        day: date,
        policies: Sequence[Policy],
        *,
        forecaster: Forecaster | None = None,
        cp_ids: Sequence[str] | None = None,
        autonomy: Autonomy = Autonomy.ACT,
        focus_policy: str | None = None,
        seed: int = 0,
        sim_cfg: SimConfig | None = None,
        profiles: PowerProfiles | None = None,
    ) -> None:
        cfg = sim_cfg or sim_config()
        prof = profiles or power_profiles()
        self.day = day
        self.seed = seed
        self.forecaster = forecaster
        self.slot_s = int(cfg.slot_s)
        self._ttl_s = float(cfg.recommendation_ttl_slots * self.slot_s)
        self._t0 = datetime.combine(day, time(0), tzinfo=ZONE).timestamp()
        next_t0 = datetime.combine(day + timedelta(days=1), time(0), tzinfo=ZONE).timestamp()
        self.n_slots = int(next_t0 - self._t0) // self.slot_s  # 276 / 288 / 300 on DST days
        self._end = float(self.n_slots * self.slot_s)
        self._slot_dt = [
            datetime.fromtimestamp(self._t0 + k * self.slot_s, ZONE)
            for k in range(self.n_slots + 1)
        ]
        self._init_cps(data.cps, cp_ids, prof)
        self._init_sessions(data.sessions)
        self._init_signals(data.signals)
        self._init_context(data.context)

        names = [str(p.name) for p in policies]
        if not names:
            raise ValueError("at least one policy is required")
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate policy names: {names}")
        self.policy_names: list[str] = names
        runs = [(p, False) for p in policies]
        if ALWAYS_ON not in names:
            runs.append((_AlwaysOn(), True))
        fslots = self.n_slots if forecaster is not None else 0
        self._sims = {
            str(p.name): _PolicySim(p, hidden, self._state0, self._dep0, fslots)
            for p, hidden in runs
        }
        self.metrics: dict[str, PolicyMetrics] = {k: v.metrics for k, v in self._sims.items()}
        self.autonomy = Autonomy(autonomy)
        self._focus = names[0]
        if focus_policy is not None:
            self.focus_policy = focus_policy
        self.slot = 0
        self._ar = np.arange(len(self.cp_ids))
        self._seq = itertools.count()
        self._recs: dict[int, _Cmd] = {}
        self._pending: dict[tuple[str, int], _Cmd] = {}
        self._approved: list[_Cmd] = []
        self._rec_changed: list[_Cmd] = []
        self._obs_cache: tuple[int, pd.DataFrame, dict] | None = None

    # ---- setup -------------------------------------------------------------------------------

    def _init_cps(self, cps: pd.DataFrame, cp_ids: Sequence[str] | None, prof: PowerProfiles):
        by_id = cps.drop_duplicates("cp_id").set_index("cp_id", drop=False)
        ids = [str(c) for c in (by_id.index if cp_ids is None else cp_ids)]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate cp_ids")
        missing = [c for c in ids if c not in by_id.index]
        if missing:
            raise KeyError(f"unknown cp_ids: {missing[:5]}")
        sel = by_id.loc[ids]
        n = len(ids)
        self.cp_ids: list[str] = ids
        self._index = {c: i for i, c in enumerate(ids)}
        self._cp_pos = pd.Index(ids)
        self._locs = [str(x) for x in sel["location_id"]]
        self._pcs = [PowerClass(x) for x in sel["power_class"]]
        conns = sel["connectors"] if "connectors" in sel else [None] * n
        self._conns = [_as_list(x) for x in conns]
        self._counties = {str(x) for x in sel["county_code"]} if "county_code" in sel else None
        if "current_type" in sel:
            cts = [str(x) for x in sel["current_type"]]
        else:
            cts = ["AC" if pc == PowerClass.AC_22 else "DC" for pc in self._pcs]

        self._pw = np.zeros((n, len(STATES)))
        self._has_deep = np.zeros(n, dtype=bool)
        self._sb_wake = np.zeros(n)
        self._ds_wake = np.full(n, np.nan)
        for i, pc in enumerate(self._pcs):
            p = prof[pc]
            deep_w = p.deep_sleep_w if p.has_deep_sleep else p.standby_w
            self._pw[i] = (0.0, p.idle_w, p.idle_w, p.standby_w, deep_w, 0.0)
            self._has_deep[i] = p.has_deep_sleep
            self._sb_wake[i] = p.standby_wake_s
            if p.has_deep_sleep:
                self._ds_wake[i] = p.deep_sleep_wake_s or 0.0
        self._extra_wh = float(prof.wake_extra_wh)

        by_loc: dict[str, list[int]] = {}
        for i, loc in enumerate(self._locs):
            by_loc.setdefault(loc, []).append(i)
        self._sibs: list[tuple[int, ...]] = [
            tuple(
                j
                for j in by_loc[self._locs[i]]
                if j != i
                and compatible(
                    cts[i], self._conns[i], self._pcs[i], cts[j], self._conns[j], self._pcs[j]
                )
            )
            for i in range(n)
        ]
        self._cands = [(i, *s) for i, s in enumerate(self._sibs)]

    def _init_sessions(self, sessions: pd.DataFrame) -> None:
        n = len(self.cp_ids)
        self._state0 = np.full(n, IDLE, dtype=np.int8)
        self._dep0 = np.full(n, np.inf)
        arr = _epoch_s(sessions["arrival_ts"]) - self._t0
        dep = _epoch_s(sessions["departure_ts"]) - self._t0
        live = (arr < self._end) & (dep > 0)
        sub = sessions[live]
        arr, dep = arr[live], dep[live]
        cp = self._cp_pos.get_indexer(sub["cp_id"].astype(str))
        keep = cp >= 0

        span = keep & (arr < 0)
        last_dep = np.full(n, -np.inf)
        np.maximum.at(last_dep, cp[span], dep[span])
        mid = last_dep > 0
        self._state0[mid] = CHARGING
        self._dep0[mid] = last_dep[mid]

        m = keep & (arr >= 0)
        sid = (
            sub["session_id"].astype(str).to_numpy()[m]
            if "session_id" in sub
            else np.arange(m.sum())
        )
        df = pd.DataFrame({"a": arr[m], "d": dep[m] - arr[m], "cp": cp[m], "sid": sid})
        df = df.sort_values(["a", "sid"], kind="mergesort")
        self._arr = df["a"].to_numpy(dtype=float)
        self._dur = np.maximum(df["d"].to_numpy(dtype=float), 0.0)
        self._arr_cp = df["cp"].to_numpy(dtype=np.int64)
        edges = np.arange(self.n_slots + 1, dtype=float) * self.slot_s
        self._arr_bounds = np.searchsorted(self._arr, edges, side="left")

    def _init_signals(self, signals: pd.DataFrame) -> None:
        col = pd.to_datetime(signals["ts"], utc=True)
        lo = pd.Timestamp(self._t0 - 3600.0, unit="s", tz="UTC")
        hi = pd.Timestamp(self._t0 + self._end, unit="s", tz="UTC")
        sub = signals[((col > lo) & (col <= hi)).to_numpy()]
        sub = sub[sub["location_id"].isin(set(self._locs)).to_numpy()]
        ts = _epoch_s(sub["ts"]) - self._t0
        order = np.argsort(ts, kind="stable")
        self._sig = sub.iloc[order].reset_index(drop=True)
        self._sig_t = ts[order]

    def _init_context(self, context: pd.DataFrame) -> None:
        th = _epoch_s(context["ts_hour"]) - self._t0
        m = (th > -6 * 3600.0) & (th <= self._end)
        if self._counties is not None:
            m &= context["county_code"].isin(self._counties).to_numpy()
        sub, th = context[m], th[m]
        by_t: dict[float, dict[str, dict]] = {}
        cols = [sub[c].tolist() for c in ("county_code", "temp_c", "precip_mm", "wind_kmh")]
        hol = sub["is_holiday"].tolist()
        for t, county, temp, precip, wind, h in zip(th.tolist(), *cols, hol, strict=True):
            by_t.setdefault(t, {})[str(county)] = {
                "temp_c": float(temp),
                "precip_mm": float(precip),
                "wind_kmh": float(wind),
                "is_holiday": bool(h),
            }
        self._ctx_t = np.array(sorted(by_t), dtype=float)
        self._ctx = [by_t[t] for t in sorted(by_t)]

    # ---- public API --------------------------------------------------------------------------

    @property
    def focus_policy(self) -> str:
        return self._focus

    @focus_policy.setter
    def focus_policy(self, name: str) -> None:
        if str(name) not in self.policy_names:
            raise ValueError(f"focus policy {name!r} is not one of {self.policy_names}")
        self._focus = str(name)

    @property
    def done(self) -> bool:
        return self.slot >= self.n_slots

    @property
    def t(self) -> datetime:
        return self._slot_dt[self.slot]

    def step(self) -> StepResult:
        """Simulate the next slot: observe, decide, apply, events, account."""
        if self.done:
            raise RuntimeError("the day is finished")
        k = self.slot
        s0 = float(k * self.slot_s)
        s1 = s0 + self.slot_s
        n = len(self.cp_ids)
        for ps in self._sims.values():
            ps.slot_wh = np.zeros(n)
            ps.slot_sleep_s = 0.0
            ps.wait = np.zeros(n)
            ps.events = []
            self._events(ps, np.nextafter(s0, np.inf), 0, 0)  # departures/wakes at exactly s0
        changed, self._rec_changed = self._rec_changed, []
        self._expire(s0, changed)
        self._apply_approved(s0)

        for ps in self._sims.values():
            if ps.hidden:
                continue
            state = self._observe(ps, k)
            if ps.p30 is not None and state.forecast is not None:
                self._record_forecast(ps, k, state.forecast)
            cmds = ps.policy.decide(state)
            if not cmds:
                continue
            idx, tgt, reasons = self._parse(cmds)
            if not len(idx):
                continue
            if self.autonomy == Autonomy.RECOMMEND and ps.name == self._focus:
                self._recommend(ps, idx, tgt, reasons, s0, changed)
            else:
                self._apply(ps, idx, tgt, reasons, s0, record=True)

        changes: dict[str, list[tuple[str, State]]] = {}
        a, b = int(self._arr_bounds[k]), int(self._arr_bounds[k + 1])
        for ps in self._sims.values():
            self._events(ps, s1, a, b)
            self._close_slot(ps, s1)
            if not ps.hidden:
                diff = np.flatnonzero(ps.state != ps.prev_state)
                changes[ps.name] = [(self.cp_ids[c], STATES[ps.state[c]]) for c in diff.tolist()]
                ps.prev_state[diff] = ps.state[diff]

        self.slot += 1
        if self.done:
            self._expire(np.inf, changed)
        req = [self._sims[nm] for nm in self.policy_names]
        recs = {r.id: r for r in changed}
        return StepResult(
            slot=k,
            sim_time=self._slot_dt[k],
            kpis=self.kpis(),
            changes=changes,
            events={ps.name: ps.events for ps in req},
            overhead_kwh={ps.name: ps.slot_wh / 1000.0 for ps in req},
            wait_min={ps.name: ps.wait for ps in req},
            new_recommendations=[self._rec_dict(r) for r in recs.values()],
        )

    def run_to(self, slot: int) -> None:
        """Step until `self.slot == slot`; going backwards is not allowed."""
        if slot < self.slot or slot > self.n_slots:
            raise ValueError(f"cannot run to slot {slot} from slot {self.slot}")
        while self.slot < slot:
            self.step()

    def run(self) -> dict[str, dict]:
        """Run to the end of the day and return the KPIs."""
        self.run_to(self.n_slots)
        return self.kpis()

    def kpis(self) -> dict[str, dict]:
        base = self._sims[ALWAYS_ON].metrics.overhead_wh
        return {nm: self._sims[nm].metrics.kpis(base) for nm in self.policy_names}

    def states(self, policy: str) -> np.ndarray:
        return self._sims[policy].state.copy()

    def sim_state(self, policy: str) -> SimState:
        return self._observe(self._sims[policy], self.slot)

    def recommendations(self, status: str | None = None) -> list[dict]:
        """Recommendation dicts in id order; pending ones past `expires_at` become expired."""
        self._expire(float(self.slot * self.slot_s), self._rec_changed)
        return [
            self._rec_dict(r)
            for r in self._recs.values()
            if status is None or r.status == str(status)
        ]

    def decide_recommendation(self, rec_id: int, approve: bool) -> dict:
        """Approve (applied at the start of the next slot) or reject a pending recommendation."""
        rec = self._recs[int(rec_id)]
        self._expire(float(self.slot * self.slot_s), self._rec_changed)
        if rec.status != _PENDING:
            raise ValueError(f"recommendation {rec_id} is {rec.status}")
        del self._pending[(rec.policy, rec.cp)]
        rec.status = (CommandStatus.approved if approve else CommandStatus.rejected).value
        self._rec_changed.append(rec)
        if approve:
            self._approved.append(rec)
        return self._rec_dict(rec)

    def expire_pending(self) -> list[dict]:
        """Expire every pending recommendation now (seek or focus change in the API)."""
        changed: list[_Cmd] = []
        self._expire(np.inf, changed)
        return [self._rec_dict(r) for r in changed]

    def applied_commands(self, policy: str, slot: int) -> list[dict]:
        """Commands `policy` applied itself at the start of `slot`, as recommendation dicts."""
        t = float(slot * self.slot_s)
        applied = CommandStatus.applied.value
        out = []
        for h in self._sims[policy].hist_cmds:
            r = h[-1] if h else None
            if r is not None and r.ts == t and r.status == applied:
                out.append({**self._rec_dict(r), "expires_at": self._iso(t)})
        return out

    def timeline(self, policy: str, cp_id: str) -> dict:
        """States, arrivals, intents, forecast and commands of one CP up to the current slot."""
        ps = self._sims[policy]
        c = self._index[cp_id]
        now = float(self.slot * self.slot_s)
        hist = ps.hist_states[c]
        iv: list[list] = []
        for i, (a, s) in enumerate(hist):
            b = min(hist[i + 1][0] if i + 1 < len(hist) else now, now)
            if b <= a:
                continue
            if iv and iv[-1][2] == s:
                iv[-1][1] = b
            else:
                iv.append([a, b, s])
        sig = self._sig
        m = (sig["signal"] == INTENT).to_numpy() & (sig["location_id"] == self._locs[c]).to_numpy()
        m &= self._sig_t <= now
        etas = (
            _epoch_s(sig.loc[m, "eta_ts"]) - self._t0
            if "eta_ts" in sig
            else np.full(m.sum(), np.nan)
        )
        intents = [
            {"ts": self._iso(t), "eta_ts": None if np.isnan(e) else self._iso(e)}
            for t, e in zip(self._sig_t[m].tolist(), etas.tolist(), strict=True)
        ]
        forecast = []
        if ps.p30 is not None:
            for k in range(self.slot):
                v = ps.p30[k, c]
                if not np.isnan(v):
                    forecast.append(
                        {"ts": self._iso(k * self.slot_s), "p_arrival_30": round(float(v), 4)}
                    )
        return {
            "cp_id": cp_id,
            "policy": ps.name,
            "states": [
                {"from": self._iso(a), "to": self._iso(b), "state": STATES[s].value}
                for a, b, s in iv
            ],
            "arrivals": [
                {"ts": self._iso(t), "wait_min": round(w, 3)} for t, w in ps.hist_arrivals[c]
            ],
            "intents": intents,
            "forecast": forecast,
            "commands": [
                {
                    "ts": self._iso(r.ts),
                    "target_state": tr.CODE_TARGETS[r.target].value,
                    "reason": r.reason,
                    "status": r.status,
                }
                for r in ps.hist_cmds[c]
            ],
        }

    # ---- observe / decide --------------------------------------------------------------------

    def _iso(self, rel: float) -> str:
        return datetime.fromtimestamp(self._t0 + rel, ZONE).isoformat(timespec="seconds")

    def _shared_obs(self, k: int) -> tuple[pd.DataFrame, dict]:
        if self._obs_cache is None or self._obs_cache[0] != k:
            t = float(k * self.slot_s)
            a = np.searchsorted(self._sig_t, t - 3600.0, side="right")
            b = np.searchsorted(self._sig_t, t, side="right")
            i = np.searchsorted(self._ctx_t, t, side="right") - 1
            self._obs_cache = (k, self._sig.iloc[a:b], self._ctx[i] if i >= 0 else {})
        return self._obs_cache[1], self._obs_cache[2]

    def _observe(self, ps: _PolicySim, k: int) -> SimState:
        """A SimState with its own copies of the shared inputs, so policies can't affect others."""
        sig, ctx = self._shared_obs(k)
        secs = (k * self.slot_s - ps.since).astype(np.int64).tolist()
        cps = [
            CpView(cp, loc, pc, list(conns), st, sec)
            for cp, loc, pc, conns, st, sec in zip(
                self.cp_ids,
                self._locs,
                self._pcs,
                self._conns,
                _STATE_OBJ[ps.state].tolist(),
                secs,
                strict=True,
            )
        ]
        state = SimState(
            t=self._slot_dt[k],
            cps=cps,
            signals=sig.copy(),
            context={c: dict(v) for c, v in ctx.items()},
            forecast=None,
        )
        if self.forecaster is not None:
            state.forecast = self.forecaster.predict(state)
        return state

    def _record_forecast(self, ps: _PolicySim, k: int, fc: pd.DataFrame) -> None:
        p30 = fc["p_arrival_30"].to_numpy(dtype=float)
        if fc["cp_id"].tolist() == self.cp_ids:
            ps.p30[k] = p30
            return
        pos = self._cp_pos.get_indexer(fc["cp_id"])
        m = pos >= 0
        ps.p30[k, pos[m]] = p30[m]

    def _parse(self, cmds: list) -> tuple[np.ndarray, np.ndarray, list[str]]:
        last: dict[int, tuple[int, str]] = {}
        for cmd in cmds:  # the last command per CP wins
            i = self._index.get(cmd.cp_id)
            if i is not None:
                last[i] = (tr.target_code(cmd.target_state), str(cmd.reason))
        idx = np.fromiter(last.keys(), dtype=np.int64, count=len(last))
        tgt = np.fromiter((v[0] for v in last.values()), dtype=np.int8, count=len(last))
        return idx, tgt, [v[1] for v in last.values()]

    def _transition(self, ps: _PolicySim, idx: np.ndarray, tgt: np.ndarray, t: float):
        return tr.apply_commands(
            ps.state[idx], tgt, t, self._has_deep[idx], self._sb_wake[idx], self._ds_wake[idx]
        )

    def _apply(self, ps, idx, tgt, reasons, t: float, record: bool) -> None:
        res = self._transition(ps, idx, tgt, t)
        ok = np.flatnonzero(res.code == tr.APPLIED)
        if not ok.size:
            return
        ci = idx[ok]
        old = ps.state[ci].copy()
        new = res.new_state[ok]
        self._flush(ps, ci, t)
        ps.state[ci] = new
        ps.since[ci] = t
        w = new == WAKING
        if w.any():
            wi = ci[w]
            ps.wake_end[wi] = res.wake_end[ok][w]
            ps.wake_target[wi] = res.wake_target[ok][w]
            ps.slot_wh[wi] += self._extra_wh
        eff = res.target[ok]
        for c, o, s, e, j in zip(
            ci.tolist(), old.tolist(), new.tolist(), eff.tolist(), ok.tolist(), strict=True
        ):
            ps.hist_states[c].append((t, s))
            if record:
                applied = CommandStatus.applied.value
                ps.hist_cmds[c].append(_Cmd(t, c, ps.name, o, e, reasons[j], applied))

    # ---- recommendations ---------------------------------------------------------------------

    def _recommend(self, ps, idx, tgt, reasons, t: float, changed: list[_Cmd]) -> None:
        res = self._transition(ps, idx, tgt, t)
        for j, c in enumerate(idx.tolist()):
            eff = int(res.target[j])
            key = (ps.name, c)
            cur = self._pending.get(key)
            if cur is not None:
                if cur.target == eff:
                    continue
                self._expire_one(cur)
                changed.append(cur)
            if res.code[j] != tr.APPLIED:
                continue
            rec = _Cmd(t, c, ps.name, int(ps.state[c]), eff, reasons[j], _PENDING)
            rec.id, rec.expires = len(self._recs) + 1, t + self._ttl_s
            self._recs[rec.id] = rec
            self._pending[key] = rec
            ps.hist_cmds[c].append(rec)
            changed.append(rec)

    def _expire_one(self, rec: _Cmd) -> None:
        rec.status = _EXPIRED
        self._pending.pop((rec.policy, rec.cp), None)

    def _expire(self, t: float, changed: list[_Cmd]) -> None:
        for rec in [r for r in self._pending.values() if r.expires <= t]:
            self._expire_one(rec)
            changed.append(rec)

    def _apply_approved(self, t: float) -> None:
        by_policy: dict[str, dict[int, _Cmd]] = {}
        for rec in self._approved:
            by_policy.setdefault(rec.policy, {})[rec.cp] = rec
        self._approved = []
        for name, recs in by_policy.items():
            idx = np.fromiter(recs.keys(), dtype=np.int64, count=len(recs))
            tgt = np.array([r.target for r in recs.values()], dtype=np.int8)
            self._apply(self._sims[name], idx, tgt, [r.reason for r in recs.values()], t, False)

    def _rec_dict(self, r: _Cmd) -> dict:
        return {
            "id": r.id,
            "ts": self._iso(r.ts),
            "cp_id": self.cp_ids[r.cp],
            "policy": r.policy,
            "from_state": STATES[r.from_state].value,
            "target_state": tr.CODE_TARGETS[r.target].value,
            "reason": r.reason,
            "status": r.status,
            "expires_at": self._iso(r.expires),
        }

    # ---- events and accounting ---------------------------------------------------------------

    def _flush(self, ps: _PolicySim, idx: np.ndarray, t: float) -> None:
        dt = t - ps.acct[idx]
        st = ps.state[idx]
        ps.slot_wh[idx] += self._pw[idx, st] * dt / 3600.0
        ps.slot_sleep_s += float(dt[_SLEEPING[st]].sum())
        ps.acct[idx] = t

    def _close_slot(self, ps: _PolicySim, s1: float) -> None:
        dt = s1 - ps.acct
        st = ps.state
        ps.slot_wh += self._pw[self._ar, st] * dt / 3600.0
        ps.slot_sleep_s += float(dt[_SLEEPING[st]].sum())
        ps.acct[:] = s1
        ps.metrics.add_energy_wh(float(ps.slot_wh.sum()))
        ps.metrics.add_time(len(self.cp_ids) * self.slot_s, ps.slot_sleep_s)

    def _set(self, ps: _PolicySim, c: int, s: int, t: float) -> None:
        old = int(ps.state[c])
        if old == s:
            return
        dt = t - ps.acct[c]
        ps.slot_wh[c] += self._pw[c, old] * dt / 3600.0
        if _SLEEPING[old]:
            ps.slot_sleep_s += dt
        ps.acct[c] = t
        ps.state[c] = s
        ps.since[c] = t
        if s == WAKING:
            ps.slot_wh[c] += self._extra_wh
        ps.hist_states[c].append((t, s))

    def _push(self, heap: list, t: float, kind: int, x: int) -> None:
        heapq.heappush(heap, (t, kind, next(self._seq), x))

    def _events(self, ps: _PolicySim, s1: float, a: int, b: int) -> None:
        """Process departures and wake ends before `s1`, plus arrivals a..b-1, in time order."""
        heap: list[tuple[float, int, int, int]] = []
        for c in np.flatnonzero((ps.dep_at < s1) | (ps.wake_end < s1)).tolist():
            if ps.state[c] == CHARGING and ps.dep_at[c] < s1:
                heap.append((float(ps.dep_at[c]), _DEP, next(self._seq), c))
            elif ps.state[c] == WAKING and ps.wake_end[c] < s1:
                heap.append((float(ps.wake_end[c]), _WAKE, next(self._seq), c))
        heap.extend((float(self._arr[j]), _ARR, next(self._seq), j) for j in range(a, b))
        heapq.heapify(heap)
        while heap:
            t, kind, _, x = heapq.heappop(heap)
            if kind == _DEP:
                if ps.state[x] == CHARGING and ps.dep_at[x] == t:
                    self._depart(ps, x, t, s1, heap)
            elif kind == _WAKE:
                if ps.state[x] == WAKING and ps.wake_end[x] == t:
                    self._wake_done(ps, x, t, s1, heap)
            else:
                self._arrive(ps, x, t, s1, heap)

    def _depart(self, ps: _PolicySim, c: int, t: float, s1: float, heap: list) -> None:
        if not ps.hidden:
            ps.events.append({"type": "departure", "cp_id": self.cp_ids[c], "ts": self._iso(t)})
        q = ps.queues.get(c)
        if q:
            dep = t + q.popleft()
            if not q:
                del ps.queues[c]
            ps.dep_at[c] = dep
            if dep < s1:
                self._push(heap, dep, _DEP, c)
        else:
            ps.dep_at[c] = np.inf
            self._set(ps, c, IDLE, t)

    def _wake_done(self, ps: _PolicySim, c: int, t: float, s1: float, heap: list) -> None:
        target = int(ps.wake_target[c])
        ps.wake_end[c] = np.inf
        ps.wake_target[c] = -1
        self._set(ps, c, target, t)
        if target == CHARGING and ps.dep_at[c] < s1:
            self._push(heap, float(ps.dep_at[c]), _DEP, c)

    def _arrive(self, ps: _PolicySim, j: int, t: float, s1: float, heap: list) -> None:
        o = int(self._arr_cp[j])
        d = float(self._dur[j])
        st = ps.state
        if st[o] == IDLE:
            return self._charge(ps, o, o, t, d, s1, heap)
        for c in self._sibs[o]:
            if st[c] == IDLE:
                return self._charge(ps, c, o, t, d, s1, heap)
        best, ready = -1, np.inf
        for c in self._cands[o]:  # own CP first, so it wins ties
            s = st[c]
            if s == STANDBY:
                r = t + self._sb_wake[c]
            elif s == DEEP_SLEEP:
                r = t + self._ds_wake[c]
            elif s == WAKING and ps.wake_target[c] != CHARGING:
                r = ps.wake_end[c]
            else:
                continue
            if r < ready:
                best, ready = c, float(r)
        if best >= 0:
            return self._wake_for(ps, best, o, t, ready, d, s1, heap)
        best, free = -1, np.inf
        for c in self._cands[o]:
            s = st[c]
            if s == CHARGING or (s == WAKING and ps.wake_target[c] == CHARGING):
                f = ps.dep_at[c] + sum(ps.queues.get(c, ()))
                if best < 0 or f < free:
                    best, free = c, f
        if best >= 0:
            ps.queues.setdefault(best, deque()).append(d)
            self._arrival(ps, best, o, t, 0.0, queued=True)
        else:  # no usable CP at all (only OFFLINE ones): the session is counted, nothing charges
            self._arrival(ps, o, o, t, 0.0, queued=False)

    def _charge(self, ps, c: int, o: int, t: float, d: float, s1: float, heap: list) -> None:
        self._set(ps, c, CHARGING, t)
        ps.dep_at[c] = t + d
        if t + d < s1:
            self._push(heap, t + d, _DEP, c)
        self._arrival(ps, c, o, t, 0.0, queued=False)

    def _wake_for(self, ps, c: int, o: int, t: float, ready: float, d: float, s1, heap) -> None:
        if ps.state[c] != WAKING:
            self._set(ps, c, WAKING, t)
            ps.wake_end[c] = ready
            if ready < s1:
                self._push(heap, ready, _WAKE, c)
        ps.wake_target[c] = CHARGING
        ps.dep_at[c] = ready + d
        wait = (min(ready, self._end) - t) / 60.0
        ps.wait[c] += wait
        self._arrival(ps, c, o, t, wait, queued=False)

    def _arrival(self, ps: _PolicySim, c: int, o: int, t: float, wait: float, queued: bool):
        ps.metrics.add_session(wait)
        ps.hist_arrivals[c].append((t, wait))
        if not ps.hidden:
            ps.events.append(
                {
                    "type": "arrival",
                    "cp_id": self.cp_ids[c],
                    "ts": self._iso(t),
                    "wait_min": round(wait, 3),
                    "moved_from": self.cp_ids[o] if c != o else None,
                    "queued": queued,
                }
            )
