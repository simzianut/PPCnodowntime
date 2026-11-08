"""Live runs (decision 0070): the agent in shadow on real collector statuses, one slot per 5 min."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from fastapi import HTTPException

from api.engine_mode import HISTORY_FLUSH_TICKS, EngineRun, _short_error
from model.live_signals import LiveSignalSampler
from sim.baselines import hour_of_week
from sim.config import ROOT, TZ, sim_config
from sim.data import SimData, data_dir
from sim.live import LiveEngine, LiveStatusReader

if TYPE_CHECKING:
    from api.engine_mode import EngineManager
    from api.models import RunCreate

log = logging.getLogger("uvicorn.error")
STALE_S = 1800.0
DEFAULT_LAG_S = 60.0
HOURS = 168


# ---- clocks (injectable; LIVE_CLOCK_START / LIVE_CLOCK_SPEED replay collected days for demos)
class WallClock:
    speed = 1.0

    def now(self) -> float:
        return time.time()

    def describe(self) -> str:
        return "real time"


class ShiftedClock:
    """Starts at `start` (epoch s) when created and runs `speed` times faster than real time."""

    def __init__(self, start: float, speed: float = 1.0) -> None:
        self.speed = float(speed)
        self._start, self._origin = float(start), time.monotonic()

    def now(self) -> float:
        return self._start + (time.monotonic() - self._origin) * self.speed

    def describe(self) -> str:
        start = datetime.fromtimestamp(self._start, _zone()).isoformat(timespec="seconds")
        return f"demo from {start} at {self.speed:g}x"


def clock_from_env():
    start = os.environ.get("LIVE_CLOCK_START")
    speed = float(os.environ.get("LIVE_CLOCK_SPEED", "1") or 1)
    if not start:
        return WallClock() if speed == 1 else ShiftedClock(time.time(), speed)
    dt = datetime.fromisoformat(start)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_zone())
    return ShiftedClock(dt.timestamp(), speed)


def live_signals_mode() -> str:
    """LIVE_SIGNALS: `off` (default, decision 0093) or an explicit `mock` opt-in (decision 0070)."""
    v = (os.environ.get("LIVE_SIGNALS") or "off").strip().lower()
    return "mock" if v == "mock" else "off"


def describe_clock(clock) -> str:
    d = getattr(clock, "describe", None)
    return d() if d else type(clock).__name__


def _zone():
    from zoneinfo import ZoneInfo

    return ZoneInfo(TZ)


# ---- forecaster hook: F2 GRU (decision 0069)
def live_model_path(art_dir: Path | None = None) -> Path:
    """LIVE_F2_MODEL, else artifacts/f2_gru_live.pt (honest model) if present, else f2_gru.pt."""
    art = Path(art_dir) if art_dir is not None else ROOT / "artifacts"
    env = os.environ.get("LIVE_F2_MODEL")
    if env:
        return Path(env)
    live = art / "f2_gru_live.pt"
    return live if live.exists() else art / "f2_gru.pt"


def live_calibration_for(model_file: Path) -> dict | None:
    """Affine hazard calibration fitted on real AmpWhere arrivals (decision 0084), if it matches."""
    p = Path(
        os.environ.get("LIVE_F2_CALIBRATION") or model_file.parent / "f2_live_calibration.json"
    )
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
    except ValueError as e:
        log.warning("live: ignoring %s (%s)", p, _short_error(e))
        return None
    if d.get("model") != model_file.name:
        log.warning(
            "live: %s was fitted for %s, not %s; ignored", p, d.get("model"), model_file.name
        )
        return None
    return {"kind": "temp_bias", "a": float(d["a"]), "b": float(d["b"])}


def live_forecaster(art_dir: Path | None = None):
    """(forecaster, name) for live runs: the F2 GRU; raises if it cannot load (decision 0093)."""
    f2 = live_model_path(art_dir)
    if not f2.exists():
        raise RuntimeError(f"live forecaster {f2} is missing; no stub is used (decision 0093)")
    from model.gru_forecaster import GRUForecaster

    # live: no session history; arrivals are inferred from CHARGING transitions (0070)
    name = "f2_gru_live" if f2.name == "f2_gru_live.pt" else "f2_gru"
    try:
        fc = GRUForecaster.from_data(f2, sessions=None)
    except Exception as e:
        raise RuntimeError(f"live forecaster {f2.name} failed to load ({_short_error(e)})") from e
    cal = live_calibration_for(f2)
    if cal is not None:
        fc.model.calibration, fc.model.calibrators = cal, {}
        log.info("live: %s uses the real-data calibration a=%.3f b=%.3f", name, cal["a"], cal["b"])
    else:
        log.warning("live: %s runs without the real-data calibration", name)
    return fc, name


# ---- signal rates: F0 seasonal rate, else hour-of-week rate from training sessions
def location_rates(data: SimData, art_dir: Path | None = None) -> tuple[dict[str, np.ndarray], str]:
    """Arrivals per hour by location and local hour of week (Monday 00:00 = 0)."""
    art = Path(art_dir) if art_dir is not None else ROOT / "artifacts"
    cp_loc = data.cps.drop_duplicates("cp_id").set_index("cp_id")["location_id"].astype(str)
    f0 = Path(os.environ.get("LIVE_F0_RATES") or art / "f0_rates.parquet")
    if f0.exists():
        try:
            r = pd.read_parquet(f0, columns=["cp_id", "how", "rate"])
            r["location_id"] = r["cp_id"].astype(str).map(cp_loc)
            g = r.dropna(subset=["location_id"]).groupby(["location_id", "how"])["rate"].sum()
            return _to_arrays(g), "f0_rates"
        except Exception as e:
            log.warning("live: reading %s failed (%s); using session rates", f0, _short_error(e))
    s = data.sessions
    if "split" in s and (s["split"] == "train").any():
        s = s[s["split"] == "train"]
    if s.empty:
        return {}, "none"
    weeks = max((s["arrival_ts"].max() - s["arrival_ts"].min()).total_seconds() / 604800.0, 1.0)
    loc = s["cp_id"].astype(str).map(cp_loc)
    df = pd.DataFrame({"location_id": loc, "how": np.asarray(hour_of_week(s["arrival_ts"]))})
    g = df.dropna().groupby(["location_id", "how"]).size() / weeks
    return _to_arrays(g), "session_hour_of_week"


def _to_arrays(g: pd.Series) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    for (loc, how), v in g.items():
        out.setdefault(str(loc), np.zeros(HOURS))[int(how) % HOURS] = float(v)
    return out


def typical_traffic(base: Path | None = None) -> dict[str, np.ndarray]:
    """Mapbox typical traffic index by location and hour of week (decision 0028), if built."""
    p = (base or data_dir()) / "processed" / "traffic_typical.parquet"
    if not p.exists():
        log.warning("live: %s missing; LIVE_SIGNALS=mock would use traffic 0.5", p)
        return {}
    t = pd.read_parquet(p, columns=["location_id", "hour_of_week", "typical_index"])
    out: dict[str, np.ndarray] = {}
    for loc, g in t.groupby("location_id", observed=True):
        a = np.full(HOURS, 0.5)
        a[g["hour_of_week"].to_numpy() % HOURS] = g["typical_index"].to_numpy()
        out[str(loc)] = a
    return out


class SignalFeed:
    """Samples mocked PPC Blue intents and Maps counts per location from the F0 rate (0070)."""

    def __init__(self, locations: list[str], rates: dict[str, np.ndarray],
                 traffic: dict[str, np.ndarray], slot_s: int, seed: int = 0,
                 enabled: bool = True) -> None:  # fmt: skip
        self.locations = locations
        self.rates, self.traffic = rates, traffic
        self.slot_s = slot_s
        self.enabled = enabled  # only LIVE_SIGNALS=mock samples intents/Maps counts (0093)
        self.sampler = LiveSignalSampler(seed=seed)

    def n60(self, loc: str, t: datetime) -> float:
        r = self.rates.get(loc)
        if r is None:
            return 0.0
        h = int(hour_of_week(pd.Timestamp(t)))
        f = (t.minute * 60 + t.second) / 3600.0
        return float((1 - f) * r[h] + f * r[(h + 1) % HOURS])

    def sample(self, t: datetime) -> pd.DataFrame:
        h = int(hour_of_week(pd.Timestamp(t)))
        per_slot = self.slot_s / 3600.0
        rows: list[dict] = []
        for loc in self.locations if self.enabled else []:
            n60 = self.n60(loc, t)
            tr = self.traffic.get(loc)
            ti = float(tr[h]) if tr is not None else 0.5
            rate = self.sampler.p_app * n60 * per_slot
            rows += self.sampler.sample(loc, t, n60_forecast=n60, traffic_index=ti,
                                        intent_rate=rate)  # fmt: skip
        df = pd.DataFrame(rows, columns=["ts", "location_id", "signal", "value", "eta_ts"])
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        df["eta_ts"] = pd.to_datetime(df["eta_ts"], utc=True)
        return df


class LiveRun(EngineRun):
    """A live run: ticks minute m once the wall clock passes m + one slot + the collector lag.

    By then the polls covering minute m are read, so its events apply at their own minute.
    """

    def __init__(self, *args, clock, reader: LiveStatusReader, feed: SignalFeed,
                 lag_s: float, poll_s: float, info: dict, **kw) -> None:  # fmt: skip
        super().__init__(*args, **kw)
        self.clock, self.reader, self.feed = clock, reader, feed
        self.lag_s, self.poll_s = lag_s, poll_s
        self.info = info
        self.speed = max(1, int(round(getattr(clock, "speed", 1))))

    def due(self) -> float:
        eng = self.engine
        return eng.minute_epoch(eng.minute) + eng.slot_s + self.lag_s

    def _live_step_sync(self, now: float):
        eng = self.engine
        if eng.minute % eng.slot_minutes == 0:  # a slot starts: read and feed all of it
            self.reader.refresh(now)
            s0, s1 = eng.slot_epoch(eng.slot), eng.slot_epoch(eng.slot + 1)
            eng.feed(self.reader.take_events(s0, s1), self.feed.sample(eng.t))
        return self._step_sync()

    async def loop(self) -> None:
        while self.status != "done":
            await self.running.wait()
            now = self.clock.now()
            if now < self.due():
                speed = max(getattr(self.clock, "speed", 1.0), 1e-9)
                await asyncio.sleep(min(self.poll_s, (self.due() - now) / speed))
                continue
            async with self.lock:
                if self.status != "running":
                    continue
                if self.engine.done:
                    self.set_status("done")
                    continue
                operator, self._approved_cps = self._approved_cps, set()
                try:
                    res, applied = await asyncio.to_thread(self._live_step_sync, now)
                except Exception:
                    log.exception("live run %s: step failed; pausing", self.run_id)
                    self.set_status("paused", "user")
                    self.broadcast(self.status_msg())
                    continue
                msgs = self._after_step(res, applied, operator)
            self.broadcast(*msgs)
            if self._hist_ticks >= HISTORY_FLUSH_TICKS or self.status != "running":
                await self.flush_history()
            await asyncio.sleep(0)

    async def control(self, body) -> list[dict]:
        if body.action in ("speed", "seek"):
            raise HTTPException(400, f"{body.action} is replay-only; live runs follow the clock")
        return await super().control(body)


def _fail(detail: str):
    raise HTTPException(400, detail)


async def create_live_run(mgr: EngineManager, body: RunCreate, names: list[str], focus: str,
                          lam: float) -> LiveRun:  # fmt: skip
    """Build a live run on today's statuses; 400 when the collector feed is missing or stale."""
    data = await mgr.ready()
    clock = mgr.live_clock()
    now = clock.now()
    clock_desc = describe_clock(clock)
    slot_s = sim_config().slot_s
    refs = await asyncio.to_thread(mgr.live_source_refs)
    if refs is None:
        _fail("live mode needs data/processed/source_refs.parquet to match AmpWhere statuses")
    reader = LiveStatusReader(mgr.live_dir, refs, cp_ids=data.cps["cp_id"].astype(str))
    if not reader.existing_files(now):
        files = ", ".join(p.name for p in reader.files(now))
        _fail(f"no live statuses ({files}) in {mgr.live_dir}; is ppc-collector running?")
    await asyncio.to_thread(reader.refresh, now)
    last = reader.stats.last_ts
    if last is None or now - last > STALE_S:
        when = "never" if last is None else datetime.fromtimestamp(last, _zone()).isoformat()
        _fail(f"live feed is stale (newest status: {when}, older than {int(STALE_S // 60)} min; "
              f"clock: {clock_desc}); is ppc-collector running?")  # fmt: skip
    local = datetime.fromtimestamp(now, _zone())
    day = local.date()
    t0 = datetime.combine(day, datetime.min.time(), tzinfo=_zone()).timestamp()
    start = max(int((now - mgr.live_lag_s - t0) // slot_s) - 1, 0)
    initial = reader.initial(t0 + start * slot_s, sorted(reader.timeline))
    if not initial:
        _fail("no live status matched a charge point (source_refs) before the current slot")
    await mgr.load_autonomy()
    history = await asyncio.to_thread(mgr._history_enabled)

    def build():
        fc, fc_name = mgr.get_live_forecaster()
        pols = mgr.make_policies(names, lam, forecaster=fc)
        eng = LiveEngine(data, day, pols, initial=initial, start_slot=start, forecaster=fc,
                         autonomy=mgr.autonomy, focus_policy=focus)  # fmt: skip
        rates, rate_src = mgr.get_live_rates()
        feed = SignalFeed(sorted(set(eng._locs)), rates, mgr.get_live_traffic(), eng.slot_s,
                          enabled=live_signals_mode() == "mock")  # fmt: skip
        return pols, eng, feed, fc_name, rate_src

    try:
        pols, eng, feed, fc_name, rate_src = await asyncio.to_thread(build)
    except Exception as e:
        log.exception("live run creation failed")
        raise HTTPException(500, f"live run creation failed ({_short_error(e)})") from e
    states = np.bincount(eng.states(focus), minlength=6).tolist()
    info = {**reader.summary(), "clock": clock_desc, "forecaster": fc_name,
            "signal_rates": rate_src, "signals": live_signals_mode(), "cps_in_run": len(initial),
            "start_slot": start, "state_counts": states}  # fmt: skip
    run = LiveRun(mgr, body, day, names, focus, lam, pols, eng, clock=clock, reader=reader,
                  feed=feed, lag_s=mgr.live_lag_s, poll_s=mgr.live_poll_s, info=info)  # fmt: skip
    run.history = history
    log.info("live run %s: %s", run.run_id, info)
    return run
