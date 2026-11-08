"""Replay of collected AmpWhere days (decision 0090): the live engine on a replay clock."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import numpy as np
from fastapi import HTTPException

from api.engine_mode import EngineRun, _kpis, _short_error
from api.live_mode import SignalFeed
from sim.config import TZ, sim_config
from sim.live import LiveEngine, LiveStatusReader, _epoch

if TYPE_CHECKING:
    from api.engine_mode import EngineManager
    from api.models import RunCreate

log = logging.getLogger("uvicorn.error")
ZONE = ZoneInfo(TZ)


# ---- which days were collected
def _line_ts(line: bytes) -> float | None:
    try:
        return _epoch(json.loads(line)["ts"])
    except (ValueError, KeyError, TypeError):
        return None


def poll_span(path: Path, tail: int = 1 << 16) -> tuple[float, float] | None:
    """(first, last) poll epoch of one statuses file; reads its first and last complete lines."""
    with path.open("rb") as f:
        first = None
        for line in f:
            if (first := _line_ts(line)) is not None:
                break
        if first is None:
            return None
        f.seek(max(0, f.seek(0, 2) - tail))
        lines = f.read().split(b"\n")
    for line in reversed(lines):  # a half-written last line does not parse and is skipped
        if (last := _line_ts(line)) is not None:
            return first, max(first, last)
    return first, first


def _midnight(d: date) -> float:
    return datetime.combine(d, dtime(0), tzinfo=ZONE).timestamp()


def collected_windows(live_dir: Path, slot_s: int) -> dict[date, tuple[int, int]]:
    """Local day -> (first, end) slot: slots that start after the first poll and end by the last."""
    span: dict[date, tuple[float, float]] = {}
    for p in sorted(Path(live_dir).glob("statuses-*.jsonl")):
        s = poll_span(p)
        if s is None:
            continue
        d, last_day = (datetime.fromtimestamp(x, ZONE).date() for x in s)
        while d <= last_day:
            lo = max(s[0], _midnight(d))
            hi = min(s[1], _midnight(d + timedelta(days=1)))
            old = span.get(d)
            span[d] = (lo, hi) if old is None else (min(old[0], lo), max(old[1], hi))
            d += timedelta(days=1)
    out: dict[date, tuple[int, int]] = {}
    for d, (lo, hi) in span.items():
        t0 = _midnight(d)
        a, b = math.ceil((lo - t0) / slot_s), math.floor((hi - t0) / slot_s)
        if b > a:
            out[d] = (a, b)
    return out


# ---- engine and run
class WindowEngine(LiveEngine):
    """A live engine that is done at `end_slot` instead of midnight."""

    def __init__(self, *args, end_slot: int, **kw) -> None:
        super().__init__(*args, **kw)
        self.end_slot = min(end_slot, self.n_slots)

    @property
    def done(self) -> bool:
        return self.slot >= getattr(self, "end_slot", self.n_slots)


class NoStatus(Exception):
    pass


@dataclass
class Built:
    eng: WindowEngine
    reader: LiveStatusReader
    feed: SignalFeed
    pols: list
    forecaster: str
    rates: str


def _build(mgr: EngineManager, refs, day: date, window: tuple[int, int], names: list[str],
           lam: float, focus: str) -> Built:  # fmt: skip
    """Fresh reader, forecaster (it is stateful), policies and engine at the window's first slot."""
    data = mgr.data
    start, end = window
    reader = LiveStatusReader(mgr.collected_dir, refs, cp_ids=data.cps["cp_id"].astype(str))
    t_start = _midnight(day) + start * sim_config().slot_s
    reader.refresh(t_start)
    initial = reader.initial(t_start, sorted(reader.timeline))
    if not initial:
        raise NoStatus
    fc, fc_name = mgr.live_forecaster_factory()
    pols = mgr.make_policies(names, lam, forecaster=fc)
    eng = WindowEngine(data, day, pols, initial=initial, start_slot=start, end_slot=end,
                       forecaster=fc, autonomy=mgr.autonomy, focus_policy=focus)  # fmt: skip
    rates, rate_src = mgr.get_live_rates()
    feed = SignalFeed(sorted(set(eng._locs)), rates, mgr.get_live_traffic(), eng.slot_s)
    return Built(eng, reader, feed, pols, fc_name, rate_src)


class CollectedRun(EngineRun):
    """A replay of a collected day: each step feeds that slot's observed events, then steps."""

    def __init__(self, *args, built: Built, rebuild, window: tuple[int, int], lag_s: float,
                 info: dict, **kw) -> None:  # fmt: skip
        super().__init__(*args, **kw)
        self.reader, self.feed = built.reader, built.feed
        self._rebuild = rebuild
        self.start_slot, self.end_slot = window
        self.lag_s = lag_s
        self.info = info
        self.slot = self.engine.slot
        self.kpis = _kpis(self.engine.kpis())

    def _feed_slot(self) -> None:
        eng = self.engine
        s0, s1 = eng.slot_epoch(eng.slot), eng.slot_epoch(eng.slot + 1)
        self.reader.refresh(s1 + self.lag_s)  # what a live run would have read when due
        eng.feed(self.reader.take_events(s0, s1), self.feed.sample(eng.t))

    def _step_sync(self):
        self._feed_slot()
        return super()._step_sync()

    def _seek_sync(self, slot: int) -> bool:
        rebuilt = slot < self.engine.slot
        if rebuilt:
            b = self._rebuild()
            self.engine, self.reader, self.feed, self.policy_objs = b.eng, b.reader, b.feed, b.pols
        while self.engine.slot < slot:
            self._feed_slot()
            self.engine.step()
        return rebuilt

    async def _seek(self, slot: int) -> None:
        slot = min(max(slot, self.start_slot), self.end_slot)
        if await asyncio.to_thread(self._seek_sync, slot):
            self.gen += 1
        self.engine.expire_pending()
        self._expire_known()
        self._approved_cps.clear()
        self.slot = slot
        self.kpis = _kpis(self.engine.kpis())
        self.states = self.engine.states(self.focus)


def _fail(detail: str):
    raise HTTPException(400, detail)


async def create_collected_run(mgr: EngineManager, body: RunCreate, names: list[str], focus: str,
                               lam: float, day: date, window: tuple[int, int]):  # fmt: skip
    if "oracle" in names:
        _fail("oracle needs sessions; a collected day has only observed statuses")
    refs = await asyncio.to_thread(mgr.live_source_refs)
    if refs is None:
        _fail("replaying collected statuses needs data/processed/source_refs.parquet")
    await mgr.load_autonomy()
    history = await asyncio.to_thread(mgr._history_enabled)

    def rebuild() -> Built:
        return _build(mgr, refs, day, window, names, lam, focus)

    try:
        b = await asyncio.to_thread(rebuild)
    except NoStatus:
        at = datetime.fromtimestamp(_midnight(day) + window[0] * sim_config().slot_s, ZONE)
        _fail(f"no collected status matched a charge point (source_refs) before {at:%H:%M} "
              f"on {day.isoformat()}")  # fmt: skip
    except Exception as e:
        log.exception("collected replay creation failed")
        raise HTTPException(500, f"collected replay creation failed ({_short_error(e)})") from e
    eng = b.eng
    info = {**b.reader.summary(), "source": "collected", "forecaster": b.forecaster,
            "signal_rates": b.rates, "cps_in_run": len(eng.cp_ids), "start_slot": window[0],
            "end_slot": eng.end_slot,
            "state_counts": np.bincount(eng.states(focus), minlength=6).tolist()}  # fmt: skip
    win = (window[0], eng.end_slot)
    run = CollectedRun(mgr, body, day, names, focus, lam, b.pols, eng, built=b, rebuild=rebuild,
                       window=win, lag_s=mgr.live_lag_s, info=info)  # fmt: skip
    run.history = history
    log.info("collected replay %s on %s: %s", run.run_id, day.isoformat(), info)
    return run
