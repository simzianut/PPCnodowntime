"""Real API mode (T23): replays run on sim.Engine in memory, no DB (spec 04 §6, CONTRACTS API)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import anyio
import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from api.fixture_mode import Client
from api.fixture_store import with_ron
from api.models import (
    AutonomyBody,
    Health,
    Location,
    PolicyInfo,
    Recommendation,
    RecommendationDecision,
    RunControl,
    RunCreate,
    RunCreated,
    RunStatus,
    Timeline,
)
from model import rl_select
from model.oracle import HindsightOraclePolicy
from sim.config import CONFIG_DIR, ROOT, TZ, sim_config
from sim.data import SimData, data_dir
from sim.engine import Engine
from sim.types import STATES, Autonomy, CommandStatus, PolicyName, State
from stations import store

log = logging.getLogger("uvicorn.error")
ZONE = ZoneInfo(TZ)
EVAL_PATH = ROOT / "artifacts" / "eval_results.json"
MAX_RUNS = 10
PENDING = CommandStatus.pending.value
EXPIRED = CommandStatus.expired.value
APPLIED = CommandStatus.applied.value
HISTORY_FLUSH_TICKS = 12
ORACLE_LIVE_DETAIL = "oracle is replay-only (it reads the true sessions); live runs cannot use it"


def _dumps(msg: dict) -> str:
    return json.dumps(msg, separators=(",", ":"))


def _missing(v: object) -> bool:
    try:
        return v is None or bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _opt_str(v: object) -> str | None:
    return None if _missing(v) else str(v)


def _connectors(x: object) -> list[str]:
    if isinstance(x, str):
        return [c for c in x.split(";") if c]
    if isinstance(x, list | tuple | np.ndarray):
        return [str(c) for c in x]
    return []


def build_stations(data: SimData) -> list[dict]:
    """Location payloads (CONTRACTS shared payloads); locations without coordinates are skipped."""
    by_loc: dict[str, list[dict]] = {}
    for r in data.cps.to_dict("records"):
        pc = str(r["power_class"])
        ct = r.get("current_type")
        by_loc.setdefault(str(r["location_id"]), []).append(
            {
                "cp_id": str(r["cp_id"]),
                "power_class": pc,
                "max_kw": None if _missing(r.get("max_kw")) else float(r["max_kw"]),
                "connectors": _connectors(r.get("connectors")),
                "current_type": str(ct) if not _missing(ct) else ("AC" if pc == "AC_22" else "DC"),
            }
        )
    out = []
    for r in data.locations.to_dict("records"):
        if _missing(r.get("lat")) or _missing(r.get("lon")):
            continue
        loc = str(r["location_id"])
        out.append(
            {
                "location_id": loc,
                "name": _opt_str(r.get("name")) or loc,
                "partner": _opt_str(r.get("partner")),
                "address": _opt_str(r.get("address")),
                "county_code": str(r["county_code"]),
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
                "context_type": _opt_str(r.get("context_type")) or "other",
                "primary_source": _opt_str(r.get("primary_source")) or "unknown",
                "confidence": _opt_str(r.get("confidence")) or "low",
                "charge_points": by_loc.get(loc, []),
            }
        )
    return out


def _kpis(k: dict[str, dict]) -> dict[str, dict]:
    return {p: with_ron(v) for p, v in k.items()}


def _short_error(e: BaseException) -> str:
    return f"{type(e).__name__}: {str(e).splitlines()[0][:160] if str(e) else ''}"


@dataclass
class RecEntry:
    run_id: str
    gen: int
    engine_id: int
    rec: dict


class EngineRun:
    """One replay: an Engine stepped by an asyncio task; `lock` serialises engine access."""

    def __init__(self, mgr: EngineManager, body: RunCreate, day: date, names: list[str],
                 focus: str, lam: float, policies: list, engine: Engine):  # fmt: skip
        self.mgr = mgr
        self.run_id = str(uuid.uuid4())
        self.mode = body.mode
        self.day = day
        self.policies = names
        self.lambda_ = lam
        self.policy_objs = policies
        self.engine = engine
        self.gen = 0
        self.focus = focus
        self.speed = body.speed
        self.status, self.paused_reason = "running", None
        self.slot = 0
        self.kpis = _kpis(engine.kpis())
        self.states = engine.states(focus)
        self.cp_index = {c: i for i, c in enumerate(engine.cp_ids)}
        self.rec_ids: list[int] = []
        self.outbox: dict[int, dict] = {}
        self.clients: set[Client] = set()
        self.lock = asyncio.Lock()
        self.running = asyncio.Event()
        self.running.set()
        self.task: asyncio.Task | None = None
        self._t0 = datetime.combine(day, dtime(0), tzinfo=ZONE).timestamp()
        self.history = False  # write commands/state_events through stations.store (decision 0049)
        self._hist_cmds: list[dict] = []
        self._hist_events: list[dict] = []
        self._hist_done: set[int] = set()
        self._hist_ticks = 0
        self._approved_cps: set[str] = set()

    # ---- views
    def sim_time(self, slot: int | None = None) -> str:
        k = self.slot if slot is None else slot
        dt = datetime.fromtimestamp(self._t0 + k * self.engine.slot_s, ZONE)
        return dt.isoformat(timespec="seconds")

    def status_body(self) -> dict:
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "status": self.status,
            "paused_reason": self.paused_reason,
            "sim_time": self.sim_time(),
            "slot": self.slot,
            "speed": self.speed,
            "focus_policy": self.focus,
            "kpis": self.kpis,
        }

    def status_msg(self) -> dict:
        b = self.status_body()
        del b["kpis"], b["mode"]
        return {"type": "status", **b}

    def snapshot_msg(self, consume: bool = True) -> dict:
        recs = [
            e.rec
            for e in (self.mgr.recs[i] for i in self.rec_ids)
            if e.gen == self.gen and e.rec["status"] == PENDING
        ]
        if consume:
            recs += [r for r in self.outbox.values() if r["status"] != PENDING]
            self.outbox.clear()
        ids = self.engine.cp_ids
        return {
            "type": "snapshot",
            "run_id": self.run_id,
            "sim_time": self.sim_time(),
            "slot": self.slot,
            "kpis": self.kpis,
            "focus_policy": self.focus,
            "changes": [
                {"cp_id": ids[c], "state": STATES[s].value}
                for c, s in enumerate(self.states.tolist())
            ],  # fmt: skip
            "events": [],
            "recommendations": recs,
        }

    def cp_states(self) -> dict[str, str]:
        st = self.states.tolist()
        return {cp: STATES[s].value for cp, s in zip(self.engine.cp_ids, st, strict=True)}

    # ---- broadcasting
    def add_client(self) -> Client:
        client = Client()
        client.push(_dumps(self.snapshot_msg(consume=False)))
        self.clients.add(client)
        return client

    def broadcast(self, *msgs: dict) -> None:
        texts = [_dumps(m) for m in msgs]
        for client in list(self.clients):
            if not all(client.push(t) for t in texts):
                self.clients.discard(client)
                client.kick()

    # ---- recommendations
    def _expire_known(self) -> None:
        for gid in self.rec_ids:
            e = self.mgr.recs[gid]
            if e.rec["status"] == PENDING:
                e.rec = {**e.rec, "status": EXPIRED}
                self.outbox[gid] = e.rec
                self._hist_command(e.rec)

    # ---- run history (stations.store.record_run_history)
    def _hist_command(self, rec: dict, decided_by: str | None = None) -> None:
        if not self.history or rec["id"] in self._hist_done:
            return
        self._hist_done.add(rec["id"])
        applied = rec["status"] == APPLIED
        self._hist_cmds.append({
            **{k: rec[k] for k in ("ts", "cp_id", "policy", "from_state", "target_state",
                                   "reason", "status")},
            "expires_at": None if applied else rec["expires_at"],
            "decided_by": decided_by,
            "decided_at": self.sim_time() if decided_by else None,
        })  # fmt: skip

    def _hist_states(self, res, prev: np.ndarray, operator: set[str]) -> None:
        events: dict[str, dict[str, str]] = {}
        for ev in res.events.get(self.focus, []):
            events.setdefault(ev["cp_id"], {})[ev["type"]] = ev["ts"]
        slot_ts = self.sim_time(res.slot)
        for cp, st in res.changes.get(self.focus, []):
            frm, to = STATES[int(prev[self.cp_index[cp]])], State(st)
            if frm == to:
                continue
            seen = events.get(cp, {})
            if to == State.CHARGING or ("arrival" in seen and frm != State.CHARGING):
                cause = "arrival"
            elif frm == State.CHARGING or "departure" in seen:
                cause = "departure"
            else:
                cause = "operator" if cp in operator else "agent"
            self._hist_events.append({"ts": seen.get(cause, slot_ts), "cp_id": cp,
                                      "from_state": frm.value, "to_state": to.value,
                                      "cause": cause})  # fmt: skip

    async def flush_history(self) -> None:
        """Best effort: on any store error, log it and stop writing history for this run."""
        if not self.history or not (self._hist_cmds or self._hist_events):
            return
        cmds, evs = self._hist_cmds, self._hist_events
        self._hist_cmds, self._hist_events, self._hist_ticks = [], [], 0
        try:
            await asyncio.to_thread(store.record_run_history, self.run_id, cmds, evs)
        except Exception:
            log.exception("run %s: writing run history failed; history disabled", self.run_id)
            self.history = False

    # ---- stepping
    def _step_sync(self):
        res = self.engine.step()
        return res, self.engine.applied_commands(self.focus, res.slot)

    def _after_step(self, res, applied: list[dict], operator: set[str]) -> list[dict]:
        new_pending = any(d["status"] == PENDING for d in res.new_recommendations)
        for d in [*res.new_recommendations, *applied]:
            rec = self.mgr.register(self, d)
            self.outbox[rec["id"]] = rec
            if rec["status"] in (APPLIED, EXPIRED):
                self._hist_command(rec)
        if self.history:
            self._hist_states(res, self.states, operator)
            self._hist_ticks += 1
        self.slot, self.kpis = res.slot, _kpis(res.kpis)
        self.states = self.engine.states(self.focus)
        tick = {
            "type": "tick",
            "run_id": self.run_id,
            "sim_time": self.sim_time(),
            "slot": res.slot,
            "kpis": self.kpis,
            "focus_policy": self.focus,
            "changes": [{"cp_id": c, "state": s.value} for c, s in res.changes.get(self.focus, [])],
            "events": res.events.get(self.focus, []),
            "recommendations": list(self.outbox.values()),
        }
        self.outbox.clear()
        msgs = [tick]
        if self.engine.done:
            self.set_status("done")
            msgs += [self.status_msg(), {"type": "done", "kpis": self.kpis}]
        elif new_pending and self.status == "running" and self.mode == "replay":
            self.set_status("paused", "recommendations")
            msgs.append(self.status_msg())
        return msgs

    def set_status(self, status: str, reason: str | None = None) -> None:
        self.status, self.paused_reason = status, reason
        if status == "running":
            self.running.set()
            self.ensure_task()
        else:
            self.running.clear()

    def ensure_task(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.get_running_loop().create_task(self.loop())

    async def loop(self) -> None:
        while self.status != "done":
            await self.running.wait()
            t0 = time.perf_counter()
            async with self.lock:
                if self.status != "running":
                    continue
                if self.engine.done:
                    self.set_status("done")
                    continue
                operator, self._approved_cps = self._approved_cps, set()
                try:
                    res, applied = await asyncio.to_thread(self._step_sync)
                except Exception:
                    log.exception("run %s: engine step failed; pausing", self.run_id)
                    self.set_status("paused", "user")
                    self.broadcast(self.status_msg())
                    continue
                msgs = self._after_step(res, applied, operator)
            self.broadcast(*msgs)
            if self._hist_ticks >= HISTORY_FLUSH_TICKS or self.status != "running":
                await self.flush_history()
            await asyncio.sleep(max(0.0, 1 / self.speed - (time.perf_counter() - t0)))

    # ---- control
    def _rebuild_to(self, slot: int) -> Engine:
        eng = Engine(self.mgr.data, self.day, self.policy_objs, forecaster=self.mgr.forecaster,
                     autonomy=self.mgr.autonomy, focus_policy=self.focus)  # fmt: skip
        eng.run_to(slot)
        return eng

    async def _seek(self, slot: int) -> None:
        if slot >= self.engine.slot:
            await asyncio.to_thread(self.engine.run_to, slot)
        else:
            self.engine = await asyncio.to_thread(self._rebuild_to, slot)
            self.gen += 1
        self.engine.expire_pending()
        self._expire_known()
        self._approved_cps.clear()
        self.slot = slot
        self.kpis = _kpis(self.engine.kpis())
        self.states = self.engine.states(self.focus)

    async def control(self, body: RunControl) -> list[dict]:
        a, v = body.action, body.value
        async with self.lock:
            old = (self.status, self.paused_reason, self.speed, self.focus)
            msgs: list[dict] = []
            if a == "play":
                if self.status != "done":
                    self.set_status("running")
            elif a == "pause":
                if self.status != "done":
                    self.set_status("paused", "user")
            elif a == "speed":
                if isinstance(v, bool) or not isinstance(v, int | float) or v != int(v):
                    raise HTTPException(400, "speed must be an integer 1-288")
                if not 1 <= int(v) <= 288:
                    raise HTTPException(400, "speed must be an integer 1-288")
                self.speed = int(v)
            elif a == "seek":
                n = self.engine.n_slots
                if isinstance(v, bool) or not isinstance(v, int | float) or v != int(v):
                    raise HTTPException(400, f"seek value must be a slot 0-{n - 1}")
                if not 0 <= int(v) < n:
                    raise HTTPException(400, f"seek value must be a slot 0-{n - 1}")
                await self._seek(int(v))
                if self.status == "done":
                    self.set_status("paused", "user")
                msgs.append(self.snapshot_msg())
            elif a == "focus":
                if v not in self.policies:
                    raise HTTPException(400, f"focus must be one of {self.policies}")
                if v != self.focus:
                    self.engine.expire_pending()
                    self._expire_known()
                    self._approved_cps.clear()
                    self.focus = str(v)
                    self.engine.focus_policy = self.focus
                    self.states = self.engine.states(self.focus)
                msgs.append(self.snapshot_msg())
            if (self.status, self.paused_reason, self.speed, self.focus) != old:
                msgs.insert(0, self.status_msg())
        return msgs

    async def decide(self, gid: int, approve: bool) -> dict:
        entry = self.mgr.recs[gid]
        async with self.lock:
            if entry.rec["status"] != PENDING or entry.gen != self.gen:
                raise HTTPException(409, f"recommendation is {entry.rec['status']}")
            try:
                d = self.engine.decide_recommendation(entry.engine_id, approve)
            except (KeyError, ValueError) as e:
                entry.rec = {**entry.rec, "status": EXPIRED}
                self.outbox[gid] = entry.rec
                self._hist_command(entry.rec)
                raise HTTPException(409, "recommendation has expired") from e
            entry.rec = {**d, "id": gid}
            self.outbox[gid] = entry.rec
            self._hist_command(entry.rec, decided_by="operator")
            if approve:
                self._approved_cps.add(entry.rec["cp_id"])
            return entry.rec


class EngineManager:
    """In-memory state of real mode: data, policies, runs, recommendations, autonomy."""

    def __init__(self, data: SimData | None = None, eval_path: Path = EVAL_PATH) -> None:
        self._given = data
        self.data: SimData | None = None
        self.eval_path = eval_path
        self.autonomy = Autonomy.ACT
        self.runs: dict[str, EngineRun] = {}
        self.recs: dict[int, RecEntry] = {}
        self._rec_keys: dict[tuple[str, int, int], int] = {}
        self._rec_id = 0
        self.stations: list[dict] = []
        self.loc_ids: set[str] = set()
        self.test_days: list[date] = []
        self.unavailable: dict[str, str] = {}
        self.rl_lambdas: list[float] = []
        self.forecaster = None
        self._naive = None
        self._sessions = None
        self._cost_rule_cls = None
        self._ready_lock: asyncio.Lock | None = None
        self._bg: set[asyncio.Task] = set()
        # live mode (decision 0070); tests replace these attributes
        from api import live_mode

        self.live_dir = Path(os.environ.get("LIVE_DIR", "") or data_dir() / "raw" / "live")
        self.live_clock = live_mode.clock_from_env
        self.live_lag_s = float(os.environ.get("LIVE_LAG_S", live_mode.DEFAULT_LAG_S))
        self.live_poll_s = 1.0
        self.source_refs: pd.DataFrame | None = None
        self.live_forecaster_factory = live_mode.live_forecaster
        self._live_fc = None
        self._live_rates: tuple[dict, str] | None = None
        self._live_traffic: dict | None = None
        # replay of collected days (decision 0090): on when the manager loads real data itself
        self.collected_dir: Path | None = None
        self.synthetic = False

    # ---- loading
    def load(self) -> None:
        if self.data is not None:
            return
        from sim.baselines import NaiveQuietHours
        from sim.forecast import PrecomputedForecaster

        t0 = time.perf_counter()
        if self._given is not None:
            data = self._given
        else:
            from sim.data import load_default, processed_available

            log.info("real mode: loading sim data (synthetic if data/processed is missing)...")
            self.synthetic = not processed_available()
            self.collected_dir = self.live_dir
            data = load_default()
        self.stations = build_stations(data)
        self.loc_ids = {s["location_id"] for s in self.stations}
        self.test_days = data.days("test")
        fc = data.forecasts
        self.covered: list[date] | None = None
        if fc is not None and not fc.empty:  # replay only the days the forecasts cover (0069)
            from sim.forecast import covered_days

            self.covered = covered_days(fc)
            self.test_days = [d for d in self.test_days if d in set(self.covered)]
        self.forecaster = PrecomputedForecaster(fc) if fc is not None else None
        self._naive = NaiveQuietHours(data.sessions, data.cps)
        self._sessions = data.sessions
        self._probe_policies()
        self.data = data
        log.info(
            "real mode: %d locations, %d CPs, %d test days loaded in %.1f s; unavailable: %s",
            len(self.stations), len(data.cps), len(self.test_days), time.perf_counter() - t0,
            self.unavailable or "none",
        )  # fmt: skip

    def _probe_policies(self) -> None:
        cfg = sim_config()
        self.rl_lambdas = [cfg.default_lambda]
        try:
            from model.cost_rule import CostRulePolicy

            self._cost_rule_cls = CostRulePolicy
        except Exception as e:  # import can fail with OSError, not only ImportError
            self.unavailable["cost_rule"] = f"model.cost_rule failed to import ({_short_error(e)})"
        for name in ("rl_ppo", "rl_maskppo", "rl_ddqn"):
            try:
                rl_select.check(name)
            except rl_select.PolicyUnavailable as e:
                self.unavailable[name] = f"{name} unavailable ({e})"
        self.rl_lambdas = rl_select.lambdas("rl_ppo") if "rl_ppo" not in self.unavailable else (
            self.rl_lambdas)  # fmt: skip
        try:
            log.info("rl_ppo served by %s", rl_select.resolve_rl_agent())
        except ValueError as e:
            log.warning("%s", e)

    async def ready(self) -> SimData:
        if self.data is None:
            if self._ready_lock is None:
                self._ready_lock = asyncio.Lock()
            async with self._ready_lock:
                if self.data is None:
                    await asyncio.to_thread(self.load)
        return self.data

    async def startup(self) -> None:
        await self.ready()
        await self.load_autonomy()

    async def shutdown(self) -> None:
        for run in self.runs.values():
            if run.task:
                run.task.cancel()
        for run in self.runs.values():
            await run.flush_history()

    # ---- autonomy (stations.store: the settings table when the DB is up)
    async def load_autonomy(self) -> Autonomy:
        try:
            self.autonomy = Autonomy(await asyncio.to_thread(store.get_autonomy))
        except Exception:
            log.exception("reading autonomy from stations.store failed; using %s", self.autonomy)
        return self.autonomy

    async def save_autonomy(self, mode: Autonomy) -> Autonomy:
        try:
            await asyncio.to_thread(store.set_autonomy, mode.value)
        except Exception:
            log.exception("writing autonomy to stations.store failed; kept in memory only")
        self.autonomy = mode
        for run in self.runs.values():
            if run.status != "done":
                run.engine.autonomy = mode
        return mode

    @staticmethod
    def _history_enabled() -> bool:
        try:
            return store.run_history_enabled()
        except Exception:
            log.exception("run history check failed; not writing history")
            return False

    # ---- policies
    def policy_infos(self) -> list[dict]:
        lambdas = {"cost_rule": list(sim_config().lambda_grid), "rl_ppo": self.rl_lambdas}
        for n in ("rl_maskppo", "rl_ddqn"):
            lambdas[n] = rl_select.lambdas(n) if n not in self.unavailable else []
        return [
            {"name": p.value, "lambdas": lambdas.get(p.value, []),
             "available": p.value not in self.unavailable}
            for p in PolicyName
        ]  # fmt: skip

    # ---- live mode helpers (lazy, cached)
    def live_source_refs(self) -> pd.DataFrame | None:
        if self.source_refs is None:
            p = data_dir() / "processed" / "source_refs.parquet"
            self.source_refs = pd.read_parquet(p) if p.exists() else None
        return self.source_refs

    def get_live_forecaster(self):
        if self._live_fc is None:
            self._live_fc = self.live_forecaster_factory()
            log.info("live forecaster: %s", self._live_fc[1])
        return self._live_fc

    def get_live_rates(self) -> tuple[dict, str]:
        from api.live_mode import location_rates

        if self._live_rates is None:
            self._live_rates = location_rates(self.data)
            log.info("live signal rates: %s (%d locations)", self._live_rates[1],
                     len(self._live_rates[0]))  # fmt: skip
        return self._live_rates

    def get_live_traffic(self) -> dict:
        from api.live_mode import typical_traffic

        if self._live_traffic is None:
            self._live_traffic = typical_traffic()
        return self._live_traffic

    def collected_windows(self) -> dict[date, tuple[int, int]]:
        if self.collected_dir is None:
            return {}
        from api.collected_replay import collected_windows

        return collected_windows(self.collected_dir, sim_config().slot_s)

    def replay_days(self, windows: dict[date, tuple[int, int]]) -> list[date]:
        """Collected days, then real test days; synthetic days only when nothing else (0090)."""
        if not windows:
            return list(self.test_days)
        real = [] if self.synthetic else [d for d in self.test_days if d not in windows]
        return sorted(windows) + real

    def make_policies(self, names: list[str], lam: float, forecaster=None) -> list:
        from sim.baselines import AlwaysOn

        fc = self.forecaster if forecaster is None else forecaster
        out: list = []
        for n in names:
            if n == "always_on":
                out.append(AlwaysOn())
            elif n == "naive_quiet_hours":
                out.append(self._naive)
            elif n == "cost_rule":
                out.append(self._cost_rule_cls(
                    lambda_wait=lam, power_profiles_path=CONFIG_DIR / "power_profiles.yaml",
                    sim_config_path=CONFIG_DIR / "sim.yaml",
                ))  # fmt: skip
            elif n in ("rl_ppo", "rl_maskppo", "rl_ddqn"):
                out.append(rl_select.load_policy(n, forecaster=fc, lam=lam))
            elif n == "oracle":
                out.append(HindsightOraclePolicy(self._sessions, sim_config().slot_s))
        return out

    # ---- runs
    async def create_run(self, body: RunCreate) -> EngineRun:
        data = await self.ready()
        if body.mode == "live" and PolicyName.oracle in body.policies:
            raise HTTPException(400, ORACLE_LIVE_DETAIL)
        names = list(dict.fromkeys(p.value for p in body.policies))
        bad = {n: self.unavailable[n] for n in names if n in self.unavailable}
        if bad:
            raise HTTPException(400, f"policies not available: {bad}")
        if body.focus_policy is not None and body.focus_policy.value not in names:
            raise HTTPException(400, "focus_policy must be one of policies")
        focus = body.focus_policy.value if body.focus_policy is not None else (
            "rl_ppo" if "rl_ppo" in names else names[-1])  # fmt: skip
        lam = body.lambda_ or sim_config().default_lambda
        if body.mode == "live":
            from api.live_mode import create_live_run

            return self._add_run(await create_live_run(self, body, names, focus, lam))
        windows = await asyncio.to_thread(self.collected_windows)
        offered = self.replay_days(windows)
        day = (offered[0] if offered else None) if body.date is None else body.date
        if day in windows:
            from api.collected_replay import create_collected_run

            run = await create_collected_run(self, body, names, focus, lam, day, windows[day])
            return self._add_run(run)
        if not self.test_days:
            raise HTTPException(400, "the loaded data has no test days")
        if day not in self.test_days:
            if windows:
                raise HTTPException(400, f"date must be one of {[d.isoformat() for d in offered]}")
            days = [d.isoformat() for d in self.test_days]
            if self.covered is not None:
                rng = f"{days[0]}..{days[-1]}" if days else "none"
                raise HTTPException(
                    400, f"date must be a test day covered by forecasts.parquet ({rng}): {days}"
                )
            raise HTTPException(400, f"date must be one of the test days {days}")
        await self.load_autonomy()
        history = await asyncio.to_thread(self._history_enabled)

        def build():
            pols = self.make_policies(names, lam)
            eng = Engine(data, day, pols, forecaster=self.forecaster, autonomy=self.autonomy,
                         focus_policy=focus)  # fmt: skip
            return pols, eng

        try:
            pols, eng = await asyncio.to_thread(build)
        except Exception as e:
            log.exception("run creation failed")
            raise HTTPException(500, f"run creation failed ({_short_error(e)})") from e
        run = EngineRun(self, body, day, names, focus, lam, pols, eng)
        run.history = history
        return self._add_run(run)

    def _add_run(self, run: EngineRun) -> EngineRun:
        self.runs[run.run_id] = run
        while len(self.runs) > MAX_RUNS:
            self.drop_run(next(iter(self.runs)))
        run.ensure_task()
        return run

    def drop_run(self, run_id: str) -> None:
        run = self.runs.pop(run_id)
        if run.task:
            run.task.cancel()
        if run.history:
            task = asyncio.get_running_loop().create_task(run.flush_history())
            self._bg.add(task)
            task.add_done_callback(self._bg.discard)
        for gid in run.rec_ids:
            e = self.recs.pop(gid, None)
            if e is not None:
                self._rec_keys.pop((e.run_id, e.gen, e.engine_id), None)
        for c in list(run.clients):
            c.kick()

    def get_run(self, run_id: str) -> EngineRun:
        run = self.runs.get(run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        return run

    def register(self, run: EngineRun, d: dict) -> dict:
        """Map an engine recommendation dict to a global id (unique across runs and rebuilds)."""
        key = (run.run_id, run.gen, int(d["id"]))
        gid = self._rec_keys.get(key) if d["id"] else None
        if gid is None:
            self._rec_id += 1
            gid = self._rec_id
            if d["id"]:
                self._rec_keys[key] = gid
            self.recs[gid] = RecEntry(run.run_id, run.gen, int(d["id"]), {})
            run.rec_ids.append(gid)
        entry = self.recs[gid]
        entry.rec = {**d, "id": gid}
        return entry.rec

    def latest_states(self) -> dict[str, str] | None:
        """States of the newest live run, else of the newest run (replay maps keep working)."""
        if not self.runs:
            return None
        live = [r for r in self.runs.values() if r.mode == "live"]
        return (live[-1] if live else next(reversed(self.runs.values()))).cp_states()

    def with_state(self, loc: dict, states: dict[str, str] | None) -> dict:
        if states is None:
            return loc
        cps = [
            {**cp, "state": states[cp["cp_id"]]} if cp["cp_id"] in states else cp
            for cp in loc["charge_points"]
        ]
        return {**loc, "charge_points": cps}


def build_router(mgr: EngineManager) -> APIRouter:
    r = APIRouter()

    @r.get("/api/health", response_model=Health)
    async def health():
        return {"ok": True}

    @r.get("/api/stations", response_model=list[Location], response_model_exclude_unset=True)
    async def stations(bbox: str | None = None, county: str | None = None):
        await mgr.ready()
        locs = mgr.stations
        if bbox:
            try:
                x0, y0, x1, y1 = (float(v) for v in bbox.split(","))
            except ValueError as e:
                raise HTTPException(400, "bbox must be minLon,minLat,maxLon,maxLat") from e
            locs = [s for s in locs if x0 <= s["lon"] <= x1 and y0 <= s["lat"] <= y1]
        if county:
            locs = [s for s in locs if s["county_code"].lower() == county.lower()]
        states = mgr.latest_states()
        return [mgr.with_state(s, states) for s in locs]

    @r.get(
        "/api/stations/{location_id}", response_model=Location, response_model_exclude_unset=True
    )
    async def station(location_id: str):
        await mgr.ready()
        for s in mgr.stations:
            if s["location_id"] == location_id:
                return mgr.with_state(s, mgr.latest_states())
        raise HTTPException(404, "location not found")

    @r.get("/api/policies", response_model=list[PolicyInfo])
    async def policies():
        await mgr.ready()
        return mgr.policy_infos()

    @r.get("/api/settings/autonomy", response_model=AutonomyBody)
    async def get_autonomy():
        return {"mode": await mgr.load_autonomy()}

    @r.put("/api/settings/autonomy", response_model=AutonomyBody)
    async def put_autonomy(body: AutonomyBody):
        return {"mode": await mgr.save_autonomy(body.mode)}

    @r.post("/api/runs", status_code=201, response_model=RunCreated)
    async def create_run(body: RunCreate):
        return {"run_id": (await mgr.create_run(body)).run_id}

    @r.get("/api/runs/{run_id}", response_model=RunStatus)
    async def get_run(run_id: str):
        return mgr.get_run(run_id).status_body()

    @r.post("/api/runs/{run_id}/control", response_model=RunStatus)
    async def control(run_id: str, body: RunControl):
        run = mgr.get_run(run_id)
        run.broadcast(*(await run.control(body)))
        await run.flush_history()
        return run.status_body()

    @r.get("/api/runs/{run_id}/cp/{cp_id}/timeline", response_model=Timeline)
    async def timeline(run_id: str, cp_id: str):
        run = mgr.get_run(run_id)
        if cp_id not in run.engine.cp_ids:
            raise HTTPException(404, "charge point not found")
        async with run.lock:
            tl = await asyncio.to_thread(run.engine.timeline, run.focus, cp_id)
        tl["intents"] = [i for i in tl["intents"] if i["eta_ts"] is not None]
        return tl

    @r.get("/api/runs/{run_id}/locations/{location_id}/summary")
    async def summary(run_id: str, location_id: str):
        mgr.get_run(run_id)
        if location_id not in mgr.loc_ids:
            raise HTTPException(404, "location not found")
        return {"location_id": location_id, "current_kw": None, "expected_wait_min": None,
                "anomaly_score": None}  # fmt: skip

    @r.get("/api/recommendations", response_model=list[Recommendation])
    async def recommendations(run_id: str | None = None, status: CommandStatus | None = None):
        return [
            e.rec
            for e in mgr.recs.values()
            if (run_id is None or e.run_id == run_id)
            and (status is None or e.rec["status"] == status.value)
        ]

    @r.post("/api/recommendations/{rec_id}", response_model=Recommendation)
    async def decide(rec_id: int, body: RecommendationDecision):
        entry = mgr.recs.get(rec_id)
        if entry is None or entry.run_id not in mgr.runs:
            raise HTTPException(404, "recommendation not found")
        run = mgr.runs[entry.run_id]
        try:
            return await run.decide(rec_id, body.decision == "approve")
        finally:
            await run.flush_history()

    @r.get("/api/eval")
    async def eval_results():
        try:
            body = json.loads(mgr.eval_path.read_text())
        except FileNotFoundError as e:
            raise HTTPException(404, "eval_results.json not found") from e
        await mgr.ready()  # only offer days POST /api/runs accepts (decisions 0083, 0090)
        windows = await asyncio.to_thread(mgr.collected_windows)
        body["test_days"] = [d.isoformat() for d in mgr.replay_days(windows)]
        return JSONResponse(body)

    @r.websocket("/ws/runs/{run_id}")
    async def ws_run(websocket: WebSocket, run_id: str):
        await websocket.accept()
        run = mgr.runs.get(run_id)
        if run is None:
            await websocket.close(code=4404)
            return
        async with run.lock:
            client = run.add_client()

        try:
            async with anyio.create_task_group() as tg:

                async def sender():
                    try:
                        while (text := await client.queue.get()) is not None:
                            await websocket.send_text(text)
                        await websocket.close(code=1013)
                    except (WebSocketDisconnect, RuntimeError):
                        pass  # the browser left (reload, run switch): end quietly
                    tg.cancel_scope.cancel()

                async def receiver():
                    while (await websocket.receive())["type"] != "websocket.disconnect":
                        pass
                    tg.cancel_scope.cancel()

                tg.start_soon(sender)
                tg.start_soon(receiver)
        finally:
            run.clients.discard(client)

    return r
