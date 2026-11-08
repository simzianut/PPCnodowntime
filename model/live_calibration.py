"""Fit the live model's hazard calibration on real AmpWhere arrivals (decision 0084).

Replays the collector's statuses through the online GRUForecaster exactly as a live run does
(empty history at start, mocked signals from F0 rates), collects the raw hazard logits per CP
and slot, and fits one affine map a*l + b on the observed first-arrival buckets. The result goes
to artifacts/f2_live_calibration.json, which api.live_mode applies when the model name matches.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from model import forecast_data as fd
from model import forecast_eval as fe
from model import repro
from model.experiments import f2_gru
from model.gru_forecaster import GRUForecaster
from sim.baselines import AlwaysOn
from sim.config import TZ
from sim.data import SimData
from sim.live import LiveEngine, LiveStatusReader, empty_signals
from sim.stations_seed import load_stations
from sim.types import CHARGING

log = logging.getLogger("model.live_calibration")
SLOT, LAG, POLL_TOL = 300, 60.0, 310.0
N_BUCKETS = fd.N_BUCKETS


def poll_times(live_dir: Path) -> np.ndarray:
    ts = set()
    for p in sorted(live_dir.glob("statuses-*.jsonl")):
        with p.open() as f:
            for line in f:
                ts.add(json.loads(line)["ts"])
    return np.array(sorted(datetime.fromisoformat(t).timestamp() for t in ts))


def _sim_data() -> SimData:
    locs, cps = load_stations()
    sessions = pd.DataFrame(
        {
            "session_id": pd.Series(dtype=str),
            "cp_id": pd.Series(dtype=str),
            "arrival_ts": pd.Series(dtype="datetime64[ns, UTC]"),
            "departure_ts": pd.Series(dtype="datetime64[ns, UTC]"),
            "split": pd.Series(dtype=str),
        }
    )
    context = pd.DataFrame(
        {
            "ts_hour": pd.Series(dtype="datetime64[ns, UTC]"),
            "county_code": pd.Series(dtype=str),
            "temp_c": pd.Series(dtype=float),
            "precip_mm": pd.Series(dtype=float),
            "wind_kmh": pd.Series(dtype=float),
            "is_holiday": pd.Series(dtype=bool),
        }
    )
    return SimData(locs, cps, sessions, empty_signals(), context, None)


def _reader(live_dir: Path, cps: pd.DataFrame) -> LiveStatusReader:
    refs = pd.read_parquet(fd.data_root() / "source_refs.parquet")
    return LiveStatusReader(live_dir, refs, cp_ids=cps["cp_id"].astype(str))


def observed_grid(reader: LiveStatusReader, polls: np.ndarray, t0: float, ks: np.ndarray):
    """(cp ids, observed slot mask, arrival mask (slots, cps)) on the 5-minute grid from t0."""
    cps = sorted(reader.timeline)
    t = t0 + ks * SLOT
    nxt = np.searchsorted(polls, t, side="left")
    observed = (nxt < len(polls)) & (polls[np.minimum(nxt, len(polls) - 1)] - t <= POLL_TOL)
    codes = np.full((len(ks), len(cps)), -1, dtype=np.int8)
    for j, cp in enumerate(cps):
        for i, tt in enumerate(t):
            c = reader.status_at(cp, tt)
            codes[i, j] = -1 if c is None else c
    chg = codes == CHARGING
    arr = np.zeros_like(chg)
    arr[1:] = chg[1:] & ~chg[:-1] & (codes[:-1] >= 0) & observed[1:, None] & observed[:-1, None]
    return cps, observed, arr


def first_arrival_bucket(arr: np.ndarray, observed: np.ndarray) -> np.ndarray:
    """bucket[k, cp] = 0..11 (first arrival in slots k+1..k+12), 12 (none), -1 (not observed)."""
    n = len(arr)
    out = np.full(arr.shape, -1, dtype=np.int64)
    for k in range(n - N_BUCKETS):
        if not observed[k : k + N_BUCKETS + 1].all():
            continue
        win = arr[k + 1 : k + N_BUCKETS + 1]
        hit = win.any(axis=0)
        out[k] = np.where(hit, win.argmax(axis=0), N_BUCKETS)
    return out


class _Recorder:
    def __init__(self, inner: GRUForecaster) -> None:
        self.inner, self.logits, self.ids = inner, [], []

    def predict(self, state):
        df = self.inner.predict(state)
        self.logits.append(self.inner.model.last_raw["logits"].astype(np.float32))
        self.ids.append(list(df["cp_id"]))
        return df


def replay(model_file: Path, live_dir: Path, with_signals: bool = True):
    """Raw hazard logits of every (slot, cp) as a live run of `model_file` would see them."""
    from api.live_mode import SignalFeed, location_rates, typical_traffic

    polls = poll_times(live_dir)
    if not len(polls):
        raise SystemExit(f"no statuses-*.jsonl under {live_dir}")
    zone = ZoneInfo(TZ)
    day: date = datetime.fromtimestamp(polls[0], zone).date()
    t0 = datetime.combine(day, time(0), tzinfo=zone).timestamp()
    k0 = int(np.ceil((polls[0] - t0) / SLOT))
    k1 = min(int((polls[-1] - t0) // SLOT) - 1, fd.SLOTS - 1)
    data = _sim_data()
    reader = _reader(live_dir, data.cps)
    reader.refresh(t0 + (k0 + 1) * SLOT + LAG + 1)
    initial = reader.initial(t0 + k0 * SLOT, sorted(reader.timeline))
    fc = GRUForecaster.from_data(model_file, sessions=None)
    rec = _Recorder(fc)
    eng = LiveEngine(data, day, [AlwaysOn()], initial=initial, start_slot=k0, forecaster=rec)
    feed = None
    if with_signals:
        rates, _ = location_rates(data)
        feed = SignalFeed(sorted(set(eng._locs)), rates, typical_traffic(), eng.slot_s)
    for k in range(k0, k1 + 1):
        reader.refresh(eng.slot_epoch(k + 1) + LAG)
        events = reader.take_events(eng.slot_epoch(k), eng.slot_epoch(k + 1))
        eng.feed(events, feed.sample(eng.t) if feed is not None else None)
        eng.step()
    full = _reader(live_dir, data.cps)
    full.refresh(polls[-1] + 3600)
    ks = np.arange(k0, k1 + 2)
    cps, observed, arr = observed_grid(full, polls, t0, ks)
    bucket = first_arrival_bucket(arr, observed)[: len(rec.logits)]
    idx = {c: j for j, c in enumerate(cps)}
    L = np.full((len(rec.logits), len(cps), N_BUCKETS), np.nan, np.float32)
    for i, (lg, ids) in enumerate(zip(rec.logits, rec.ids, strict=True)):
        pos = np.array([idx.get(c, -1) for c in ids])
        L[i, pos[pos >= 0]] = lg[pos >= 0]
    ok = (bucket >= 0) & ~np.isnan(L[:, :, 0])
    info = {
        "day": str(day),
        "slots": [int(k0), int(k1)],
        "cps": len(cps),
        "rows": int(ok.sum()),
        "arrivals": int(arr.sum()),
        "first_poll": datetime.fromtimestamp(polls[0], UTC).isoformat(timespec="minutes"),
        "last_poll": datetime.fromtimestamp(polls[-1], UTC).isoformat(timespec="minutes"),
    }
    return L[ok], bucket[ok], info


def _scores(logits: np.ndarray, bucket: np.ndarray, a: float, b: float) -> dict:
    p = f2_gru.affine_probs(logits, a, b)
    out = {}
    for h, k in ((15, 3), (30, 6), (60, 12)):
        y = (bucket < k).astype(float)
        out[f"y{h}"] = {
            "obs_rate": round(float(y.mean()), 5),
            "mean_p": round(float(p[f"p{h}"].mean()), 5),
            "auc": round(fe.roc_auc(y, p[f"p{h}"]), 4),
            "brier": round(fe.brier_score(y, p[f"p{h}"]), 6),
            "log_loss": round(fe.log_loss(y, p[f"p{h}"]), 6),
        }
    return out


def fit(model_file: Path, live_dir: Path, out: Path | None) -> dict:
    logits, bucket, info = replay(model_file, live_dir)
    if int((bucket < N_BUCKETS).sum()) < 10:
        raise SystemExit(f"only {int((bucket < N_BUCKETS).sum())} arrivals observed; not fitting")
    half = len(logits) // 2
    a1, b1 = f2_gru.fit_temp_bias(logits[:half], bucket[:half])
    a, b = f2_gru.fit_temp_bias(logits, bucket)
    res = {
        "model": model_file.name,
        "model_sha256": repro.sha256_file(model_file)["sha256"],
        "a": round(a, 6),
        "b": round(b, 6),
        "fitted_at": datetime.now(UTC).isoformat(timespec="seconds"),
        **info,
        "before": _scores(logits, bucket, 1.0, 0.0),
        "after": _scores(logits, bucket, a, b),
        "holdout_second_half": {
            "fit_first_half": [round(a1, 4), round(b1, 4)],
            "before": _scores(logits[half:], bucket[half:], 1.0, 0.0),
            "after": _scores(logits[half:], bucket[half:], a1, b1),
        },
    }
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, indent=2))
    return res


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m model.live_calibration")
    ap.add_argument("--model", default="artifacts/f2_gru_live.pt")
    ap.add_argument("--live-dir", default=None, help="default $LIVE_DIR or data/raw/live")
    ap.add_argument("--out", default="artifacts/f2_live_calibration.json")
    ap.add_argument("--dry-run", action="store_true", help="fit and print, write nothing")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import os

    live = Path(
        a.live_dir
        or os.environ.get("LIVE_DIR")
        or Path(os.environ.get("DATA_DIR", "data")) / "raw" / "live"
    )
    res = fit(Path(a.model), live, None if a.dry_run else Path(a.out))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
