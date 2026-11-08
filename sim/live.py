"""Live mode (decision 0070): collector statuses -> observed CP events -> Engine in shadow."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from sim.config import TZ
from sim.data import SimData
from sim.engine import ALWAYS_ON, TICK_S, Engine
from sim.types import CHARGING, DEEP_SLEEP, IDLE, OFFLINE, STANDBY, WAKING, SimState

log = logging.getLogger("sim.live")
ZONE = ZoneInfo(TZ)
UTC = ZoneInfo("UTC")
# Observed event kinds, from the CP's observed state change (codes are sim.types states).
ARRIVE, DEPART, GO_OFFLINE, GO_ONLINE, RESUME = range(5)
KIND_NAMES = ("arrive", "depart", "offline", "online", "resume")
SNAPSHOT_SHARE = 0.9  # a poll listing >= 90% of the keys seen so far is a snapshot (0016)
SIGNAL_COLUMNS = ["ts", "location_id", "signal", "value", "eta_ts"]
INTENT = "ppcblue_route_intent"


def cp_status(statuses: Iterable[str]) -> str:
    """One CP status from its connectors: any CHARGING, else any AVAILABLE, else all OFFLINE."""
    s = set(statuses)
    if "CHARGING" in s:
        return "CHARGING"
    if "AVAILABLE" in s:
        return "AVAILABLE"
    if s == {"OFFLINE"}:
        return "OFFLINE"
    return "UNKNOWN"


def obs_code(status: str) -> int:
    """CHARGING -> CHARGING, OFFLINE -> OFFLINE, AVAILABLE/UNKNOWN -> IDLE (decision 0070)."""
    return {"CHARGING": CHARGING, "OFFLINE": OFFLINE}.get(status, IDLE)


def event_kind(prev: int, new: int) -> int | None:
    """Observed transition -> event kind; None when nothing changes."""
    if prev == new:
        return None
    if new == OFFLINE:
        return GO_OFFLINE
    if prev == OFFLINE:
        return RESUME if new == CHARGING else GO_ONLINE
    return ARRIVE if new == CHARGING else DEPART


def _epoch(s: object) -> float | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).timestamp()


@dataclass
class ReaderStats:
    rows: int = 0
    matched_rows: int = 0
    ppc_rows: int = 0
    ppc_matched_rows: int = 0
    bad_lines: int = 0
    polls: int = 0
    snapshots: int = 0
    last_ts: float | None = None
    last_snapshot_ts: float | None = None
    keys: set[str] = field(default_factory=set)
    matched_keys: set[str] = field(default_factory=set)

    def summary(self, n_cps: int, n_ref_cps: int) -> dict:
        def iso(t):
            return None if t is None else datetime.fromtimestamp(t, UTC).isoformat()

        return {
            "rows": self.rows,
            "matched_rows": self.matched_rows,
            "row_match_rate": round(self.matched_rows / max(self.rows, 1), 4),
            "ppc_row_match_rate": round(self.ppc_matched_rows / max(self.ppc_rows, 1), 4),
            "keys": len(self.keys),
            "matched_keys": len(self.matched_keys),
            "cps_with_status": n_cps,
            "cps_in_source_refs": n_ref_cps,
            "polls": self.polls,
            "snapshots": self.snapshots,
            "bad_lines": self.bad_lines,
            "last_ts": iso(self.last_ts),
            "last_snapshot_ts": iso(self.last_snapshot_ts),
        }


class LiveStatusReader:
    """Tails statuses-YYYYMMDD.jsonl (UTC dates) into per-CP observed state timelines.

    Rows are matched with source_refs (`source_id = "<uid>|<charger id>"`); snapshot and delta
    rows are handled alike (latest wins per connector). Only rows with `ts <= now` are read.
    """

    def __init__(self, live_dir: Path | str, source_refs: pd.DataFrame,
                 cp_ids: Iterable[str] | None = None, ppc_provider_id: int | None = 5):  # fmt: skip
        self.live_dir = Path(live_dir)
        refs = source_refs[(source_refs["source"] == "ampwhere") & source_refs["cp_id"].notna()]
        if cp_ids is not None:
            refs = refs[refs["cp_id"].isin(set(map(str, cp_ids)))]
        self.key_cp: dict[str, str] = dict(
            zip(refs["source_id"].astype(str), refs["cp_id"].astype(str), strict=True)
        )
        self.ref_cps = set(self.key_cp.values())
        self.ppc_provider_id = ppc_provider_id
        self.stats = ReaderStats()
        self._offsets: dict[str, int] = {}
        self._key_status: dict[str, str] = {}
        self._key_ts: dict[str, float] = {}
        self._cp_conns: dict[str, dict[str, str]] = {}
        self.timeline: dict[str, list[tuple[float, int]]] = {}
        self._cursor: dict[str, int] = {}
        self._code: dict[str, int] = {}
        self._poll: tuple[str, int] | None = None
        self._ts_cache: tuple[str, float] = ("", 0.0)

    # ---- reading
    def files(self, now: float) -> list[Path]:
        """Files for UTC dates from the day before local midnight of `now` up to `now`."""
        local = datetime.fromtimestamp(now, ZONE)
        midnight = datetime.combine(local.date(), datetime.min.time(), tzinfo=ZONE)
        d0 = (midnight - timedelta(days=1)).astimezone(UTC).date()
        d1 = datetime.fromtimestamp(now, UTC).date()
        days = [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]
        return [self.live_dir / f"statuses-{d:%Y%m%d}.jsonl" for d in days]

    def existing_files(self, now: float) -> list[Path]:
        return [p for p in self.files(now) if p.exists()]

    def refresh(self, now: float, chunk: int = 1 << 20) -> None:
        """Read new complete lines with `ts <= now` from the files of `now`."""
        for path in self.existing_files(now):
            key = str(path)
            off = self._offsets.get(key, 0)
            with path.open("rb") as f:
                while True:
                    f.seek(off)
                    buf = f.read(chunk)
                    end = buf.rfind(b"\n")
                    if end < 0:
                        break
                    used, stop = self._lines(buf[: end + 1], now)
                    off += used
                    if stop or len(buf) < chunk:
                        break
            self._offsets[key] = off
        self._close_poll()

    def _lines(self, buf: bytes, now: float) -> tuple[int, bool]:
        used = 0
        for raw in buf.splitlines(keepends=True):
            try:
                row = json.loads(raw)
                ts = self._ts(row["ts"])
            except (ValueError, KeyError, TypeError):
                self.stats.bad_lines += 1
                used += len(raw)
                continue
            if ts > now:
                return used, True
            self._row(row, ts)
            used += len(raw)
        return used, False

    def _ts(self, s: str) -> float:
        if s != self._ts_cache[0]:
            t = _epoch(s)
            if t is None:
                raise ValueError(s)
            self._ts_cache = (s, t)
        return self._ts_cache[1]

    def _close_poll(self) -> None:
        if self._poll is None:
            return
        ts_s, n = self._poll
        self.stats.polls += 1
        if n >= SNAPSHOT_SHARE * len(self.stats.keys):
            self.stats.snapshots += 1
            self.stats.last_snapshot_ts = self._ts(ts_s)
        self._poll = None

    def _row(self, row: dict, ts: float) -> None:
        st = self.stats
        if self._poll is None or self._poll[0] != row["ts"]:
            self._close_poll()
            self._poll = (row["ts"], 0)
        self._poll = (self._poll[0], self._poll[1] + 1)
        st.rows += 1
        st.last_ts = ts if st.last_ts is None else max(st.last_ts, ts)
        key = f"{row.get('source_location_id')}|{row.get('source_cp_id')}"
        st.keys.add(key)
        ppc = self.ppc_provider_id is not None and row.get("provider_id") == self.ppc_provider_id
        st.ppc_rows += ppc
        cp = self.key_cp.get(key)
        if cp is None:
            return
        st.matched_rows += 1
        st.ppc_matched_rows += ppc
        st.matched_keys.add(key)
        status = str(row.get("status") or "UNKNOWN")
        prev_ts = self._key_ts.get(key)
        self._key_ts[key] = ts
        sua = _epoch(row.get("source_updated_at"))
        est = sua if sua is not None and sua <= ts else ts
        if prev_ts is not None:
            est = max(est, prev_ts)  # the change happened after we last saw the connector
        if self._key_status.get(key) == status:
            return
        self._key_status[key] = status
        conns = self._cp_conns.setdefault(cp, {})
        conns[key] = status
        code = obs_code(cp_status(conns.values()))
        tl = self.timeline.setdefault(cp, [])
        if not tl or tl[-1][1] != code:
            tl.append((max(est, tl[-1][0]) if tl else est, code))

    # ---- views
    def status_at(self, cp_id: str, t: float) -> int | None:
        """Observed state code of a CP at epoch `t` (events strictly before `t`), None if unseen."""
        code = None
        for et, c in self.timeline.get(cp_id, ()):
            if et >= t:
                break
            code = c
        return code

    def initial(self, t: float, cp_ids: Iterable[str] | None = None) -> dict[str, int]:
        """State per CP at epoch `t` and a cursor there; CPs without any status are left out."""
        out: dict[str, int] = {}
        for cp in cp_ids if cp_ids is not None else list(self.timeline):
            tl = self.timeline.get(cp, [])
            n = sum(1 for et, _ in tl if et < t)
            if n:
                out[cp] = tl[n - 1][1]
                self._cursor[cp], self._code[cp] = n, tl[n - 1][1]
        return out

    def take_events(self, s0: float, s1: float) -> list[tuple[float, str, int]]:
        """Unconsumed events before epoch `s1`, late ones clamped to `s0`, in time order."""
        out: list[tuple[float, str, int]] = []
        for cp, i in self._cursor.items():
            tl = self.timeline.get(cp, [])
            while i < len(tl) and tl[i][0] < s1:
                et, code = tl[i]
                kind = event_kind(self._code[cp], code)
                if kind is not None:
                    out.append((max(et, s0), cp, kind))
                self._code[cp] = code
                i += 1
            self._cursor[cp] = i
        out.sort(key=lambda e: e[0])
        return out

    def summary(self) -> dict:
        return self.stats.summary(len(self.timeline), len(self.ref_cps))


class SlotCachedForecaster:
    """Calls the inner forecaster once per slot, so a stateful online model sees each slot once."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self._cache: tuple[datetime, pd.DataFrame] | None = None

    def predict(self, state: SimState) -> pd.DataFrame:
        if self._cache is None or self._cache[0] != state.t:
            self._cache = (state.t, self.inner.predict(state))
        return self._cache[1].copy()


def empty_signals() -> pd.DataFrame:
    return pd.DataFrame({
        "ts": pd.Series(dtype="datetime64[ns, UTC]"),
        "location_id": pd.Series(dtype=object),
        "signal": pd.Series(dtype=object),
        "value": pd.Series(dtype=float),
        "eta_ts": pd.Series(dtype="datetime64[ns, UTC]"),
    })  # fmt: skip


class LiveEngine(Engine):
    """Engine on observed statuses: sessions come from `feed()` events, not from replayed data.

    Accounting (energy, sleep time, waits, recommendations) is the base Engine's. Observed
    events go through the arrival queue; `_arrive` dispatches them by kind.
    """

    def __init__(self, data: SimData, day: date, policies, *, initial: dict[str, int],
                 start_slot: int = 0, forecaster=None, **kw) -> None:  # fmt: skip
        if not initial:
            raise ValueError("live engine needs at least one CP with an observed status")
        self._initial = initial
        live = replace(data, sessions=data.sessions.iloc[:0], signals=empty_signals())
        fc = SlotCachedForecaster(forecaster) if forecaster is not None else None
        super().__init__(live, day, policies, forecaster=fc, cp_ids=list(initial), **kw)
        if not 0 <= start_slot < self.n_slots:
            raise ValueError(f"start_slot must be in 0..{self.n_slots - 1}")
        self.slot = start_slot
        self.minute = start_slot * self.slot_minutes
        t = float(start_slot * self.slot_s)
        for ps in self._sims.values():
            ps.acct[:] = t
            ps.since[:] = t
            ps.hist_states = [[(t, s)] for s in ps.state.tolist()]
        self.start_slot = start_slot

    def _init_sessions(self, sessions: pd.DataFrame) -> None:
        self._state0 = np.array([self._initial[c] for c in self.cp_ids], dtype=np.int8)
        self._dep0 = np.full(len(self.cp_ids), np.inf)
        self._set_events([])

    def _set_events(self, events: list[tuple[float, int, int]]) -> None:
        self._arr = np.array([e[0] for e in events], dtype=float)
        self._arr_cp = np.array([e[1] for e in events], dtype=np.int64)
        self._kind = np.array([e[2] for e in events], dtype=np.int8)
        self._dur = np.full(len(events), np.inf)

    def slot_epoch(self, k: int) -> float:
        """Epoch seconds of the start of slot `k` (k = n_slots is the next midnight)."""
        return self._t0 + k * self.slot_s

    def feed(self, events: list[tuple[float, str, int]], signals: pd.DataFrame | None = None):
        """Observed events (epoch, cp_id, kind) of the rest of this slot, and signals sampled at
        its start. Call it once per slot, before its first tick; events apply at their minute."""
        s0 = float(self.minute * TICK_S)
        s1 = float((self.slot + 1) * self.slot_s)
        evs = []
        for t, cp, kind in events:
            c = self._index.get(cp)
            if c is not None:
                evs.append((min(max(t - self._t0, s0), np.nextafter(s1, -np.inf)), c, kind))
        evs.sort(key=lambda e: e[0])
        self._set_events(evs)
        if signals is not None and len(signals):
            self._add_signals(signals, s0)

    def _add_signals(self, signals: pd.DataFrame, s0: float) -> None:
        new = signals[SIGNAL_COLUMNS].copy()
        new["ts"] = pd.to_datetime(new["ts"], utc=True)
        new["eta_ts"] = pd.to_datetime(new["eta_ts"], utc=True)
        rel = (new["ts"] - pd.Timestamp(0, tz="UTC")).dt.total_seconds().to_numpy() - self._t0
        order = np.argsort(rel, kind="stable")
        keep = (self._sig["signal"] == INTENT).to_numpy() | (self._sig_t > s0 - 3600.0)
        old = self._sig[keep] if len(self._sig) else self._sig
        new = new.iloc[order]
        self._sig = pd.concat([old, new], ignore_index=True) if len(old) else new.reset_index(
            drop=True)  # fmt: skip
        self._sig_t = np.concatenate([self._sig_t[keep], rel[order]])
        self._obs_cache = None

    def _tick(self, m1: int):
        if self.minute % self.slot_minutes == 0 and self.forecaster is not None:
            self._observe(self._sims[ALWAYS_ON], self.slot)  # the forecaster sees observed states
        res = super()._tick(m1)
        if self.minute % self.slot_minutes == 0:
            self._set_events([])  # the slot is over
        return res

    def _arrive(self, ps, j: int, t: float, s1: float, heap: list) -> None:
        c, kind = int(self._arr_cp[j]), int(self._kind[j])
        st = int(ps.state[c])
        if kind == ARRIVE:
            if st == IDLE:
                return self._charge(ps, c, c, t, np.inf, s1, heap)
            if st in (STANDBY, DEEP_SLEEP):
                wake = self._sb_wake[c] if st == STANDBY else self._ds_wake[c]
                return self._wake_for(ps, c, c, t, t + float(wake), np.inf, s1, heap)
            if st == WAKING and ps.wake_target[c] != CHARGING:
                return self._wake_for(ps, c, c, t, float(ps.wake_end[c]), np.inf, s1, heap)
            if st == OFFLINE:
                self._force(ps, c, CHARGING, t)
            return self._arrival(ps, c, c, t, 0.0, queued=False)
        if kind == DEPART:
            if st == CHARGING:
                return self._depart(ps, c, t, s1, heap)
            if st == WAKING and ps.wake_target[c] == CHARGING:
                ps.dep_at[c] = ps.wake_end[c]  # leaves once the wake it waited for ends
            elif st == OFFLINE:
                self._force(ps, c, IDLE, t)
            return None
        target = {GO_OFFLINE: OFFLINE, GO_ONLINE: IDLE, RESUME: CHARGING}[kind]
        if kind == GO_ONLINE and st != OFFLINE:
            return None
        self._force(ps, c, target, t)
        return None

    def _force(self, ps, c: int, s: int, t: float) -> None:
        ps.dep_at[c] = np.inf
        ps.wake_end[c] = np.inf
        ps.wake_target[c] = -1
        self._set(ps, c, s, t)
