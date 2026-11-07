"""Fixture-mode API (T19): every contract route served from fixtures/api/ and a recorded day."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import anyio
import numpy as np
from fastapi import APIRouter, HTTPException, WebSocket
from fastapi.responses import JSONResponse

from api.fixture_store import FIXTURES_DIR, RecordedDay, load_json
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
from sim.config import sim_config
from sim.types import STATE_INDEX, Autonomy, CommandStatus, PolicyName, State

LIVE_SLOT_INTERVAL_S = 5.0
CLIENT_QUEUE_MAX = 5000
MAX_RUNS = 50
CHARGING_CODE, WAKING_CODE = STATE_INDEX[State.CHARGING], STATE_INDEX[State.WAKING]


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _dumps(msg: dict) -> str:
    return json.dumps(msg, separators=(",", ":"))


@dataclass(eq=False)
class Client:
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(CLIENT_QUEUE_MAX))

    def push(self, text: str | None) -> bool:
        try:
            self.queue.put_nowait(text)
            return True
        except asyncio.QueueFull:
            return False

    def kick(self) -> None:
        while not self.queue.empty():
            self.queue.get_nowait()
        self.queue.put_nowait(None)


@dataclass
class RecEntry:
    rec: Recommendation
    run_id: str
    cp: int
    slot: int


class Run:
    """One in-memory run over the recorded day; ticks are driven by an asyncio task."""

    def __init__(self, mgr: FixtureManager, body: RunCreate, run_date: date, start: int):
        self.mgr, self.day = mgr, mgr.day
        self.run_id = str(uuid.uuid4())
        self.mode = body.mode
        self.run_date = run_date
        self.policies = list(dict.fromkeys(p.value for p in body.policies))
        self.focus = (body.focus_policy or self._default_focus()).value
        self.lambda_ = body.lambda_ or sim_config().default_lambda
        self.speed = body.speed
        self.status, self.paused_reason = "running", None
        self.n_done, self.slot = start, start
        self.override: dict[int, int] = {}
        self.approved: list[RecEntry] = []
        self.changed: list[RecEntry] = []
        self.recs: dict[int, RecEntry] = {}
        self.pending_by_cp: dict[int, int] = {}
        self.rec_by_slot_cp: dict[tuple[str, int, int], RecEntry] = {}
        self.clients: set[Client] = set()
        self.running = asyncio.Event()
        self.running.set()
        self.task: asyncio.Task | None = None

    def _default_focus(self) -> PolicyName:
        return PolicyName.rl_ppo if "rl_ppo" in self.policies else PolicyName(self.policies[-1])

    # ---- views
    def sim_time(self, slot: int | None = None) -> datetime:
        return self.day.ts((self.slot if slot is None else slot) * self.day.slot_s, self.run_date)

    def displayed(self) -> np.ndarray:
        st = self.day.state_at(self.focus, self.n_done).copy()
        for c, s in self.override.items():
            st[c] = s
        return st

    def kpis(self) -> dict:
        return {p: self.day.kpis_at(p, self.n_done) for p in self.policies}

    def status_body(self) -> dict:
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "status": self.status,
            "paused_reason": self.paused_reason,
            "sim_time": _iso(self.sim_time()),
            "slot": self.slot,
            "speed": self.speed,
            "focus_policy": self.focus,
            "kpis": self.kpis(),
        }

    def status_msg(self) -> dict:
        b = self.status_body()
        del b["kpis"], b["mode"]
        return {"type": "status", **b}

    def snapshot_msg(self, consume: bool = True) -> dict:
        st = self.displayed()
        ids = self.day.cp_ids
        recs = [e for e in self.recs.values() if e.rec.status == CommandStatus.pending]
        if consume:
            recs += [e for e in self.changed if e.rec.status != CommandStatus.pending]
            self.changed = []
        return {
            "type": "snapshot",
            "run_id": self.run_id,
            "sim_time": _iso(self.sim_time()),
            "slot": self.slot,
            "kpis": self.kpis(),
            "focus_policy": self.focus,
            "changes": [
                {"cp_id": ids[c], "state": self.day.state_name(s)} for c, s in enumerate(st)
            ],
            "events": [],
            "recommendations": [e.rec.model_dump(mode="json") for e in recs],
        }

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
    def _new_rec(self, cp: int, slot: int, from_s: State, target: State, reason: str, status):
        ts = self.sim_time(slot)
        ttl = self.mgr.ttl_slots * self.day.slot_s
        rec = Recommendation(
            id=self.mgr.next_rec_id(),
            ts=ts,
            cp_id=self.day.cp_ids[cp],
            policy=PolicyName(self.focus),
            from_state=from_s,
            target_state=target.value,
            reason=reason,
            status=status,
            expires_at=ts + timedelta(seconds=ttl),
        )
        entry = RecEntry(rec, self.run_id, cp, slot)
        self.recs[rec.id] = entry
        self.mgr.recs[rec.id] = entry
        self.rec_by_slot_cp[(self.focus, slot, cp)] = entry
        self.changed.append(entry)
        return entry

    def _expire(self, entry: RecEntry) -> None:
        entry.rec.status = CommandStatus.expired
        self.pending_by_cp.pop(entry.cp, None)
        self.changed.append(entry)

    def expire_pending(self, before: datetime | None = None, other_policies: bool = False):
        for e in list(self.recs.values()):
            if e.rec.status != CommandStatus.pending:
                continue
            if other_policies and e.rec.policy == self.focus:
                continue
            if before is None or other_policies or before >= e.rec.expires_at:
                self._expire(e)

    def decide(self, entry: RecEntry, approve: bool) -> None:
        if entry.rec.status != CommandStatus.pending:
            raise HTTPException(409, f"recommendation is {entry.rec.status.value}")
        entry.rec.status = CommandStatus.approved if approve else CommandStatus.rejected
        self.pending_by_cp.pop(entry.cp, None)
        if approve:
            self.approved.append(entry)
        self.changed.append(entry)

    # ---- engine step
    def step(self) -> list[dict]:
        """Process slot `n_done` and return the messages to broadcast."""
        day, p, k = self.day, self.focus, self.n_done
        recommend = self.mgr.autonomy == Autonomy.RECOMMEND
        prev = self.displayed()
        t_k = self.sim_time(k)
        for e in self.approved:
            if e.rec.policy == p and int(prev[e.cp]) not in (CHARGING_CODE, WAKING_CODE):
                self.override[e.cp] = STATE_INDEX[State(e.rec.target_state)]
        self.approved = []
        self.expire_pending(before=t_k)

        self.n_done, self.slot = k + 1, k
        rec_st = day.state_at(p, self.n_done)
        cmds = day.commands[p][k]
        new_pending = 0
        for cmd in cmds:
            if not recommend:
                self._new_rec(cmd.cp, k, cmd.from_state, cmd.target_state, cmd.reason, "applied")
                continue
            cur = int(prev[cmd.cp])
            if cur == STATE_INDEX[cmd.target_state]:
                self.override.pop(cmd.cp, None)
                continue
            if cur in (CHARGING_CODE, WAKING_CODE):
                continue
            self.override[cmd.cp] = cur
            if cmd.cp in self.pending_by_cp:
                continue
            frm = State(day.state_name(cur))
            e = self._new_rec(cmd.cp, k, frm, cmd.target_state, cmd.reason, "pending")
            self.pending_by_cp[cmd.cp] = e.rec.id
            new_pending += 1
        if not recommend:
            self.override.clear()
        for c in {a.cp for a in day.arrivals[p][k]} | {c for c, _ in day.departures[p][k]}:
            self.override.pop(c, None)
        self.override = {c: s for c, s in self.override.items() if s != rec_st[c]}

        now = self.displayed()
        ids = day.cp_ids
        changes = [
            {"cp_id": ids[c], "state": day.state_name(now[c])}
            for c in np.flatnonzero(now != prev).tolist()
        ]
        events = [
            (a.t_s, {"type": "arrival", "cp_id": ids[a.cp], "ts": None, "wait_min": a.wait_min,
                     "moved_from": None, "queued": False})
            for a in day.arrivals[p][k]
        ] + [
            (t, {"type": "departure", "cp_id": ids[c], "ts": None})
            for c, t in day.departures[p][k]
        ]  # fmt: skip
        events.sort(key=lambda x: x[0])
        for t, ev in events:
            ev["ts"] = _iso(day.ts(t, self.run_date))
        tick = {
            "type": "tick",
            "run_id": self.run_id,
            "sim_time": _iso(t_k),
            "slot": k,
            "kpis": self.kpis(),
            "focus_policy": p,
            "changes": changes,
            "events": [ev for _, ev in events],
            "recommendations": [e.rec.model_dump(mode="json") for e in self.changed],
        }
        self.changed = []
        msgs = [tick]
        if self.n_done >= day.n_slots:
            self.expire_pending()
            self.changed = []
            self.set_status("done")
            msgs += [self.status_msg(), {"type": "done", "kpis": self.kpis()}]
        elif new_pending and recommend and self.mode == "replay":
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
            if self.status != "running":
                continue
            self.broadcast(*self.step())
            interval = LIVE_SLOT_INTERVAL_S if self.mode == "live" else 1 / self.speed
            await asyncio.sleep(interval)

    # ---- control
    def seek(self, slot: int) -> None:
        self.expire_pending()
        self.override.clear()
        self.approved = []
        self.n_done, self.slot = slot, slot

    def control(self, body: RunControl) -> list[dict]:
        a, v = body.action, body.value
        old = (self.status, self.paused_reason, self.speed, self.focus)
        msgs: list[dict] = []
        if a == "play":
            if self.status != "done":
                self.set_status("running")
        elif a == "pause":
            if self.status != "done":
                self.set_status("paused", "user")
        elif a == "speed":
            if self.mode == "live":
                raise HTTPException(400, "speed is fixed in live mode")
            if isinstance(v, bool) or not isinstance(v, int | float) or v != int(v):
                raise HTTPException(400, "speed must be an integer 1-288")
            if not 1 <= int(v) <= 288:
                raise HTTPException(400, "speed must be an integer 1-288")
            self.speed = int(v)
        elif a == "seek":
            if self.mode == "live":
                raise HTTPException(400, "seek is not available in live mode")
            n = self.day.n_slots
            if isinstance(v, bool) or not isinstance(v, int | float) or v != int(v):
                raise HTTPException(400, f"seek value must be a slot 0-{n - 1}")
            if not 0 <= int(v) < n:
                raise HTTPException(400, f"seek value must be a slot 0-{n - 1}")
            self.seek(int(v))
            if self.status == "done":
                self.set_status("paused", "user")
            msgs.append(self.snapshot_msg())
        elif a == "focus":
            if v not in self.policies:
                raise HTTPException(400, f"focus must be one of {self.policies}")
            if v != self.focus:
                self.focus = str(v)
                self.override.clear()
                self.approved = []
                self.expire_pending(other_policies=True)
            msgs.append(self.snapshot_msg())
        if (self.status, self.paused_reason, self.speed, self.focus) != old:
            msgs.insert(0, self.status_msg())
        return msgs


class FixtureManager:
    """In-memory state of fixture mode: runs, recommendations, autonomy."""

    def __init__(self, day: RecordedDay | None = None, fixtures_dir: Path = FIXTURES_DIR):
        self.fixtures_dir = fixtures_dir
        self.day = day or RecordedDay(fixtures_dir / "run_day.json")
        self.stations: list[dict] = load_json("stations.json", fixtures_dir)
        self.policies: list[dict] = load_json("policies.json", fixtures_dir)
        self.autonomy = Autonomy.ACT
        self.ttl_slots = sim_config().recommendation_ttl_slots
        self.runs: dict[str, Run] = {}
        self.recs: dict[int, RecEntry] = {}
        self._rec_id = 0

    def next_rec_id(self) -> int:
        self._rec_id += 1
        return self._rec_id

    def replay_days(self) -> set[date]:
        days = {self.day.date}
        with contextlib.suppress(FileNotFoundError, KeyError, ValueError):
            ev = load_json("eval_results.json", self.fixtures_dir)
            days |= {date.fromisoformat(d) for d in ev["test_days"]}
        return days

    def create_run(self, body: RunCreate) -> Run:
        names = [p.value for p in body.policies]
        unknown = [p for p in names if p not in self.day.policies]
        if unknown:
            raise HTTPException(400, f"policies not in the recording: {unknown}")
        if body.focus_policy is not None and body.focus_policy.value not in names:
            raise HTTPException(400, "focus_policy must be one of policies")
        if body.mode == "replay":
            if body.date not in self.replay_days():
                days = sorted(d.isoformat() for d in self.replay_days())
                raise HTTPException(400, f"date must be one of the test days {days}")
            run = Run(self, body, body.date, 0)
        else:
            now = datetime.now(self.day.tz)
            secs = now.hour * 3600 + now.minute * 60 + now.second
            start = min(secs // self.day.slot_s, self.day.n_slots - 1)
            run = Run(self, body, now.date(), start)
        self.runs[run.run_id] = run
        while len(self.runs) > MAX_RUNS:
            self.drop_run(next(iter(self.runs)))
        run.ensure_task()
        return run

    def drop_run(self, run_id: str) -> None:
        run = self.runs.pop(run_id)
        if run.task:
            run.task.cancel()
        for rid in run.recs:
            self.recs.pop(rid, None)
        for c in list(run.clients):
            c.kick()

    def get_run(self, run_id: str) -> Run:
        run = self.runs.get(run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        return run

    def latest_states(self) -> dict[str, str] | None:
        if not self.runs:
            return None
        run = next(reversed(self.runs.values()))
        st = run.displayed()
        return {cp: run.day.state_name(st[i]) for i, cp in enumerate(run.day.cp_ids)}

    def with_state(self, loc: dict, states: dict[str, str] | None) -> dict:
        if states is None:
            return loc
        cps = [
            {**cp, "state": states[cp["cp_id"]]} if cp["cp_id"] in states else cp
            for cp in loc["charge_points"]
        ]
        return {**loc, "charge_points": cps}

    def timeline(self, run: Run, cp_id: str) -> dict:
        day, p = run.day, run.focus
        c = day.cp_index.get(cp_id)
        if c is None:
            raise HTTPException(404, "charge point not found")
        n, slot_s = run.n_done, day.slot_s

        def ts(t: int) -> str:
            return _iso(day.ts(t, run.run_date))

        points = [(0, day.state_name(day.states[p][0][c]))]
        arrivals, commands, forecast = [], [], []
        for k in range(n):
            points += [(t, s.value) for cc, t, s in day.transitions[p][k] if cc == c]
            arrivals += [{"ts": ts(a.t_s), "wait_min": a.wait_min} for a in day.arrivals[p][k]
                         if a.cp == c]  # fmt: skip
            for cmd in day.commands[p][k]:
                if cmd.cp != c:
                    continue
                e = run.rec_by_slot_cp.get((p, k, c))
                status = e.rec.status.value if e else CommandStatus.applied.value
                commands.append({"ts": ts(k * slot_s), "target_state": cmd.target_state.value,
                                 "reason": cmd.reason, "status": status})  # fmt: skip
            if day.p30 is not None:
                forecast.append({"ts": ts(k * slot_s), "p_arrival_30": float(day.p30[k, c])})
        end = n * slot_s
        states = []
        for i, (t, s) in enumerate(points):
            t_next = points[i + 1][0] if i + 1 < len(points) else end
            if states and states[-1]["state"] == s:
                states[-1]["to"] = ts(t_next)
            elif t_next > t or i + 1 == len(points):
                states.append({"from": ts(t), "to": ts(t_next), "state": s})
        intents = [{"ts": ts(t), "eta_ts": ts(eta)} for cc, t, eta in day.intents
                   if cc == c and t < end]  # fmt: skip
        return {"cp_id": cp_id, "policy": p, "states": states, "arrivals": arrivals,
                "intents": intents, "forecast": forecast, "commands": commands}  # fmt: skip

    async def shutdown(self) -> None:
        for run in self.runs.values():
            if run.task:
                run.task.cancel()


def build_router(mgr: FixtureManager) -> APIRouter:
    r = APIRouter()

    @r.get("/api/health", response_model=Health)
    async def health():
        return {"ok": True}

    @r.get("/api/stations", response_model=list[Location], response_model_exclude_unset=True)
    async def stations(bbox: str | None = None, county: str | None = None):
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
        for s in mgr.stations:
            if s["location_id"] == location_id:
                return mgr.with_state(s, mgr.latest_states())
        raise HTTPException(404, "location not found")

    @r.get("/api/policies", response_model=list[PolicyInfo])
    async def policies():
        return mgr.policies

    @r.get("/api/settings/autonomy", response_model=AutonomyBody)
    async def get_autonomy():
        return {"mode": mgr.autonomy}

    @r.put("/api/settings/autonomy", response_model=AutonomyBody)
    async def put_autonomy(body: AutonomyBody):
        mgr.autonomy = body.mode
        return {"mode": mgr.autonomy}

    @r.post("/api/runs", status_code=201, response_model=RunCreated)
    async def create_run(body: RunCreate):
        return {"run_id": mgr.create_run(body).run_id}

    @r.get("/api/runs/{run_id}", response_model=RunStatus)
    async def get_run(run_id: str):
        return mgr.get_run(run_id).status_body()

    @r.post("/api/runs/{run_id}/control", response_model=RunStatus)
    async def control(run_id: str, body: RunControl):
        run = mgr.get_run(run_id)
        run.broadcast(*run.control(body))
        return run.status_body()

    @r.get("/api/runs/{run_id}/cp/{cp_id}/timeline", response_model=Timeline)
    async def timeline(run_id: str, cp_id: str):
        return mgr.timeline(mgr.get_run(run_id), cp_id)

    @r.get("/api/recommendations", response_model=list[Recommendation])
    async def recommendations(run_id: str | None = None, status: CommandStatus | None = None):
        return [
            e.rec
            for e in mgr.recs.values()
            if (run_id is None or e.run_id == run_id) and (status is None or e.rec.status == status)
        ]

    @r.post("/api/recommendations/{rec_id}", response_model=Recommendation)
    async def decide(rec_id: int, body: RecommendationDecision):
        entry = mgr.recs.get(rec_id)
        if entry is None:
            raise HTTPException(404, "recommendation not found")
        mgr.runs[entry.run_id].decide(entry, body.decision == "approve")
        return entry.rec

    @r.get("/api/eval")
    async def eval_results():
        try:
            return JSONResponse(load_json("eval_results.json", mgr.fixtures_dir))
        except FileNotFoundError as e:
            raise HTTPException(404, "eval_results.json not found") from e

    @r.websocket("/ws/runs/{run_id}")
    async def ws_run(websocket: WebSocket, run_id: str):
        await websocket.accept()
        run = mgr.runs.get(run_id)
        if run is None:
            await websocket.close(code=4404)
            return
        client = run.add_client()

        try:
            async with anyio.create_task_group() as tg:

                async def sender():
                    while (text := await client.queue.get()) is not None:
                        await websocket.send_text(text)
                    await websocket.close(code=1013)
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
