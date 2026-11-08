"""Shared dataset for the three forecasters (decision 0052).

A deterministic, power_class-stratified sample of active CP-days (train, val, test), the
per-slot feature matrix (vectorised, same definitions as model/features.py), the targets, and
an on-disk cache so F0/F1/F2 and the ablation all see exactly the same rows.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from model import repro
from model.features import CONTEXT_TYPE_MAP, FEATURE_COLUMNS, POWER_CLASS_MAP

FEATURE_VERSION = 3
SLOTS = 288
SLOT_NS = 300 * 10**9
NS = 10**9
SPLITS = ("train", "val", "test")
POWER_CLASSES = ("AC_22", "DC_50", "DC_HPC")
MINS_CAP = 28 * 24 * 60
N_BUCKETS = 12  # first-arrival buckets of 5 min; bucket index 12 = censored (none within 60 min)

FEATURE_GROUPS: dict[str, list[str]] = {
    "calendar_history": [
        "hour_sin", "hour_cos", "weekday", "is_holiday", "minute_of_day", "temp_c", "precip_mm",
        "wind_kmh", "arrivals_cp_1h", "arrivals_cp_3h", "arrivals_same_slot_4w", "is_charging",
        "power_class_code", "context_type_code", "county_ev_per_1000", "cps_at_location",
    ],
    "traffic": ["traffic_index"],
    "ev_flow": ["maps_ev_count_15m", "weighted_flow_15m"],
    "intents": ["intent_eta_15", "intent_eta_30", "intent_eta_60"],
    "cp_level": ["arrivals_cp_7d", "arrivals_cp_28d", "mins_since_last_arrival"],
    "site_level": [
        "loc_arrivals_1h", "loc_arrivals_3h", "loc_same_slot_4w", "loc_rate_7d", "loc_charging_now",
    ],
}  # fmt: skip
STATIC_COLUMNS = ["power_class_code", "context_type_code", "county_ev_per_1000", "cps_at_location"]
COUNT_COLUMNS = [
    "arrivals_cp_1h", "arrivals_cp_3h", "arrivals_same_slot_4w", "maps_ev_count_15m",
    "weighted_flow_15m", "intent_eta_15", "intent_eta_30", "intent_eta_60",
    "arrivals_cp_7d", "arrivals_cp_28d", "mins_since_last_arrival",
    "loc_arrivals_1h", "loc_arrivals_3h", "loc_same_slot_4w", "loc_rate_7d", "loc_charging_now",
]  # fmt: skip

DEFAULT_PARAMS: dict[str, Any] = {
    "sample_seed": 20260101,
    "n_train_cpdays": 40000,
    "n_val_cpdays": 10000,
    "n_test_cpdays": 20000,
    "max_cps": None,
    "train_end": "2023-09-30",
    "val_start": "2023-10-01",
    "val_end": "2023-12-31",
    "test_start": "2024-01-01",
    "feature_groups": ["calendar_history", "traffic", "ev_flow", "intents"],
}
DEFAULT_DATA = {
    "sessions": "sessions.parquet",
    "charge_points": "charge_points.parquet",
    "locations": "locations.parquet",
    "cp_active_days": "cp_active_days.parquet",
    "context": "context.parquet",
    "signals": "signals.parquet",
}


def feature_names(groups: list[str]) -> list[str]:
    """Selected feature columns, in FEATURE_COLUMNS order."""
    bad = [g for g in groups if g not in FEATURE_GROUPS]
    if bad:
        raise ValueError(f"unknown feature group(s) {bad}; known {list(FEATURE_GROUPS)}")
    want = {c for g in groups for c in FEATURE_GROUPS[g]}
    return [c for c in FEATURE_COLUMNS if c in want]


def data_params(cfg: dict) -> dict:
    return {**DEFAULT_PARAMS, **(cfg.get("params") or {})}


def data_root() -> Path:
    return Path(os.environ.get("DATA_DIR", "data")) / "processed"


def _data_file(cfg: dict, key: str) -> Path:
    return data_root() / (cfg.get("data") or {}).get(key, DEFAULT_DATA.get(key, ""))


def _sha(path: Path) -> str:
    return repro.sha256_file(path)["sha256"] if path.exists() else "missing"


def _hash(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ---------------------------------------------------------------- tables and sampling


def load_cps(cfg: dict) -> pd.DataFrame:
    """CPs with location metadata, county EV density and CPs-per-location."""
    cps = pd.read_parquet(_data_file(cfg, "charge_points"))[["cp_id", "location_id", "power_class"]]
    loc = pd.read_parquet(_data_file(cfg, "locations"))[
        ["location_id", "county_code", "context_type"]
    ]
    cps = cps.merge(loc, on="location_id", how="left")
    cps["cps_at_location"] = cps.groupby("location_id")["cp_id"].transform("count").astype(float)
    ev_key = (cfg.get("data") or {}).get("county_ev")
    ev_map: dict[str, float] = {}
    if ev_key and (data_root() / ev_key).exists():
        ev = pd.read_csv(data_root() / ev_key)
        ev_map = dict(zip(ev["county_code"], ev["ev_per_1000"], strict=False))
    cps["county_ev_per_1000"] = cps["county_code"].map(ev_map).fillna(1.5).astype(float)
    cps["context_type"] = cps["context_type"].fillna("urban")
    return cps.sort_values("cp_id").reset_index(drop=True)


def split_bounds(p: dict) -> dict[str, tuple[np.datetime64, np.datetime64]]:
    far = np.datetime64("2100-01-01")
    return {
        "train": (np.datetime64("1900-01-01"), np.datetime64(p["train_end"])),
        "val": (np.datetime64(p["val_start"]), np.datetime64(p["val_end"])),
        "test": (np.datetime64(p["test_start"]), far),
    }


def draw_sample(cps: pd.DataFrame, active: pd.DataFrame, p: dict) -> pd.DataFrame:
    """Sample active CP-days per split, proportional per power_class. Uses params.sample_seed."""
    seed = int(p["sample_seed"])
    if p.get("max_cps"):
        keep = []
        for ci, pc_ in enumerate(POWER_CLASSES):
            sub = cps[cps["power_class"] == pc_]["cp_id"].to_numpy()
            if len(sub) == 0:
                continue
            n = max(1, round(int(p["max_cps"]) * len(sub) / len(cps)))
            rng = np.random.default_rng([seed, 99, ci])
            keep.extend(rng.choice(sub, size=min(n, len(sub)), replace=False).tolist())
        cps = cps[cps["cp_id"].isin(keep)]
    act = active[active["active"]][["cp_id", "date"]].copy()
    act["date"] = pd.to_datetime(act["date"]).dt.tz_localize(None).dt.normalize()
    act = act.merge(cps[["cp_id", "power_class"]], on="cp_id", how="inner")
    parts = []
    for si, split in enumerate(SPLITS):
        lo, hi = split_bounds(p)[split]
        cand = act[(act["date"] >= pd.Timestamp(lo)) & (act["date"] <= pd.Timestamp(hi))]
        n_want = int(p[f"n_{split}_cpdays"])
        for ci, pc_ in enumerate(POWER_CLASSES):
            sub = cand[cand["power_class"] == pc_]
            if len(sub) == 0:
                continue
            n = max(1, round(n_want * len(sub) / max(len(cand), 1)))
            n = min(n, len(sub))
            rng = np.random.default_rng([seed, si, ci])
            pick = np.sort(rng.choice(len(sub), size=n, replace=False))
            parts.append(sub.iloc[pick].assign(split=split))
    out = pd.concat(parts, ignore_index=True)
    out["split"] = pd.Categorical(out["split"], categories=list(SPLITS), ordered=True)
    out = out.sort_values(["split", "cp_id", "date"]).reset_index(drop=True)
    out["split"] = out["split"].astype(str)
    return out.merge(cps[["cp_id", "location_id"]], on="cp_id", how="left")


def _ts_ns(col: pd.Series) -> np.ndarray:
    return pd.to_datetime(col, utc=True).astype("datetime64[ns, UTC]").astype("int64").to_numpy()


def history_start(sessions: pd.DataFrame) -> int | None:
    """Earliest arrival (ns) in a sessions table, or None when empty."""
    return int(_ts_ns(sessions["arrival_ts"]).min()) if len(sessions) else None


def _sessions_by(sessions: pd.DataFrame, key: str, with_dep: bool) -> dict[str, tuple]:
    out: dict[str, tuple] = {}
    arr = _ts_ns(sessions["arrival_ts"])
    dep = _ts_ns(sessions["departure_ts"]) if with_dep else None
    codes, uniq = pd.factorize(sessions[key])
    order = np.lexsort((arr, codes))
    codes, arr = codes[order], arr[order]
    dep = dep[order] if dep is not None else None
    bounds = np.searchsorted(codes, np.arange(len(uniq) + 1))
    for i, k in enumerate(uniq):
        a, b = bounds[i], bounds[i + 1]
        out[k] = (arr[a:b], dep[a:b] if dep is not None else None)
    return out


def cp_level_features(arr: np.ndarray, t: np.ndarray, history_start: int | None):
    """(rate_7d, rate_28d, mins_since_last) at times t (ns) from sorted arrivals arr (<= t only)."""
    t = np.asarray(t, np.int64)
    idx_t = np.searchsorted(arr, t, side="right")
    hs = history_start
    if hs is None:
        hs = arr[0] if len(arr) else (t.min() if len(t) else 0)
    days = (t - hs) / (86400.0 * NS)
    out = []
    for d in (7, 28):
        cnt = idx_t - np.searchsorted(arr, t - d * 86400 * NS, side="right")
        out.append(cnt / np.clip(days, 1.0, float(d)))
    cap = float(MINS_CAP)
    if len(arr):
        last = (t - arr[(idx_t - 1).clip(0)]) / (60.0 * NS)
        mins = np.where(idx_t > 0, np.minimum(last, cap), cap)
    else:
        mins = np.full(len(t), cap)
    return out[0], out[1], mins


def same_slot_4w(arr: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Mean arrivals in a +-30 min window at the same time of day, 1..4 weeks before t."""
    s4 = np.zeros(len(t))
    for k in range(1, 5):
        c = t - k * 7 * 86400 * NS
        s4 += np.searchsorted(arr, c + 1800 * NS, side="right")
        s4 -= np.searchsorted(arr, c - 1800 * NS, side="right")
    return s4 / 4.0


def charging_at(arr: np.ndarray, dep: np.ndarray | None, t: np.ndarray) -> np.ndarray:
    """1.0 where the last session that started at or before t has not departed yet."""
    if dep is None or not len(arr):
        return np.zeros(len(t), np.float32)
    j = np.searchsorted(arr, t, side="right") - 1
    return ((j >= 0) & (dep[j.clip(0)] > t)).astype(np.float32)


# ---------------------------------------------------------------- signals (streamed)

LOC_SHIFT = 32  # key = loc_idx << 32 | epoch seconds
DAY_SHIFT = 20  # day key = loc_idx << 20 | epoch day


def _dict_codes(col: pa.ChunkedArray | pa.Array) -> tuple[np.ndarray, list]:
    arr = col.combine_chunks() if isinstance(col, pa.ChunkedArray) else col
    if not pa.types.is_dictionary(arr.type):
        arr = pc.dictionary_encode(arr)
        if isinstance(arr, pa.ChunkedArray):
            arr = arr.combine_chunks()
    return arr.indices.to_numpy(zero_copy_only=False), arr.dictionary.to_pylist()


def _to_ns(col) -> np.ndarray:
    arr = col.combine_chunks() if isinstance(col, pa.ChunkedArray) else col
    arr = arr.cast(pa.timestamp("ns", "UTC"))
    return arr.cast(pa.int64()).to_numpy(zero_copy_only=False)


@dataclass
class SignalTables:
    ev_keys: np.ndarray
    ev_vals: np.ndarray
    ice_keys: np.ndarray
    ice_vals: np.ndarray
    tr_keys: np.ndarray
    tr_vals: np.ndarray
    it_keys: np.ndarray
    it_vals: np.ndarray  # (n, 3) counts by lead bucket


def _group_sum(keys: np.ndarray, vals: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if len(keys) == 0:
        return keys, vals
    u, inv = np.unique(keys, return_inverse=True)
    return u, np.bincount(inv, weights=vals, minlength=len(u))


def load_signal_tables(
    path: Path, loc_index: dict[str, int], needed_days: np.ndarray, log: logging.Logger
) -> SignalTables:
    """Stream signals.parquet and keep only rows near the sampled (location, day) pairs."""
    needed = np.unique(needed_days)  # sorted keys loc<<20|day
    ev_k, ev_v, ic_k, ic_v, tr_k, tr_v = [], [], [], [], [], []
    it_ts, it_eta, it_loc = [], [], []
    pf = pq.ParquetFile(path)
    cols = [c for c in ("ts", "location_id", "signal", "value", "eta_ts") if c in pf.schema.names]
    n_in = n_keep = 0
    for batch in pf.iter_batches(batch_size=1_000_000, columns=cols):
        tbl = pa.Table.from_batches([batch])
        loc_codes, loc_names = _dict_codes(tbl["location_id"])
        lut = np.array([loc_index.get(v, -1) for v in loc_names], dtype=np.int64)
        loc = lut[loc_codes]
        ts = _to_ns(tbl["ts"])
        day = ts // (86400 * NS)
        k0 = (loc << DAY_SHIFT) | day
        k1 = (loc << DAY_SHIFT) | (day + 1)

        def has(k, needed=needed):
            pos = np.searchsorted(needed, k).clip(0, max(len(needed) - 1, 0))
            return needed[pos] == k if len(needed) else np.zeros(len(k), bool)

        keep = (loc >= 0) & (has(k0) | has(k1))
        n_in += len(ts)
        n_keep += int(keep.sum())
        if not keep.any():
            continue
        sig_codes, sig_names = _dict_codes(tbl["signal"])
        sig = np.array(sig_names, dtype=object)[sig_codes[keep]]
        loc_k, ts_k = loc[keep], ts[keep]
        val = tbl["value"].combine_chunks().to_numpy(zero_copy_only=False)[keep].astype(float)
        key = (loc_k << LOC_SHIFT) | (ts_k // NS)
        m = sig == "maps_ev_count"
        ev_k.append(key[m])
        ev_v.append(val[m])
        m = sig == "maps_ice_count"
        ic_k.append(key[m])
        ic_v.append(val[m])
        m = sig == "maps_traffic_index"
        tr_k.append(key[m])
        tr_v.append(val[m])
        if "eta_ts" in cols:
            eta = _to_ns(tbl["eta_ts"].fill_null(pa.scalar(0, pa.timestamp("ns", "UTC"))))[keep]
            m = (sig == "ppcblue_route_intent") & (eta > 0)
            it_ts.append(ts_k[m])
            it_eta.append(eta[m])
            it_loc.append(loc_k[m])
    log.info("signals: kept %d of %d rows", n_keep, n_in)

    def cat(xs, dt):
        return np.concatenate(xs) if xs else np.zeros(0, dt)

    evk, evv = _group_sum(cat(ev_k, np.int64), cat(ev_v, float))
    ick, icv = _group_sum(cat(ic_k, np.int64), cat(ic_v, float))
    trk, trv = cat(tr_k, np.int64), cat(tr_v, float)
    order = np.lexsort((trv, trk))  # ties: larger value last, as SignalIndex does
    trk, trv = trk[order], trv[order]
    itk, itv = _intent_table(cat(it_ts, np.int64), cat(it_eta, np.int64), cat(it_loc, np.int64))
    return SignalTables(evk, evv, ick, icv, trk, trv, itk, itv)


def _intent_table(ts: np.ndarray, eta: np.ndarray, loc: np.ndarray):
    """Per (loc, slot) counts of intents received in [t-60m, t] by lead time to eta."""
    if len(ts) == 0:
        return np.zeros(0, np.int64), np.zeros((0, 3))
    s0 = -((-ts) // SLOT_NS)  # first slot at or after the event
    t = (s0[:, None] + np.arange(13)[None, :]) * SLOT_NS
    lead = eta[:, None] - t
    ok = (t <= ts[:, None] + 3600 * NS) & (lead > 0) & (lead <= 3600 * NS)
    cat_ = np.where(lead <= 900 * NS, 0, np.where(lead <= 1800 * NS, 1, 2))
    key = (np.broadcast_to(loc[:, None], t.shape) << LOC_SHIFT) | (t // NS)
    comp = key[ok] * 4 + cat_[ok]
    u, cnt = np.unique(comp, return_counts=True)
    keys = u // 4
    uk, inv = np.unique(keys, return_inverse=True)
    out = np.zeros((len(uk), 3))
    np.add.at(out, (inv, u % 4), cnt)
    return uk, out


def _lookup_sum(keys: np.ndarray, vals: np.ndarray, q: np.ndarray) -> np.ndarray:
    if len(keys) == 0:
        return np.zeros(q.shape + vals.shape[1:])
    pos = np.searchsorted(keys, q).clip(0, len(keys) - 1)
    hit = keys[pos] == q
    out = vals[pos]
    return np.where(hit.reshape(hit.shape + (1,) * (out.ndim - 1)), out, 0.0)


# ---------------------------------------------------------------- feature building


class ContextGrid:
    """Dense hourly weather/holiday grid per county."""

    def __init__(self, context: pd.DataFrame) -> None:
        h = _ts_ns(context["ts_hour"]) // (3600 * NS) if len(context) else np.zeros(0, np.int64)
        self.h0 = int(h.min()) if len(h) else 0
        n = int(h.max()) - self.h0 + 1 if len(h) else 1
        self.counties = {c: i for i, c in enumerate(sorted(context["county_code"].unique()))}
        shape = (len(self.counties), n)
        self.temp = np.full(shape, 15.0, np.float32)
        self.precip = np.zeros(shape, np.float32)
        self.wind = np.full(shape, 10.0, np.float32)
        self.holiday = np.zeros(shape, np.float32)
        if not len(context):
            self.counties = {}
            shape = (1, 1)
            self.temp, self.precip = np.full(shape, 15.0, np.float32), np.zeros(shape, np.float32)
            self.wind, self.holiday = np.full(shape, 10.0, np.float32), np.zeros(shape, np.float32)
            return
        ci = context["county_code"].map(self.counties).to_numpy().astype(np.int64)
        hi = h - self.h0
        self.temp[ci, hi] = context["temp_c"].to_numpy()
        self.precip[ci, hi] = context["precip_mm"].to_numpy()
        self.wind[ci, hi] = context["wind_kmh"].to_numpy()
        self.holiday[ci, hi] = context["is_holiday"].astype(float).to_numpy()

    def get(self, county: str, t_ns: np.ndarray):
        n = self.temp.shape[1]
        h = t_ns // (3600 * NS) - self.h0
        ok = (h >= 0) & (h < n) & (county in self.counties)
        ci = self.counties.get(county, 0)
        hc = h.clip(0, n - 1)
        pick = lambda arr, dflt: np.where(ok, arr[ci, hc], dflt)  # noqa: E731
        return (
            pick(self.holiday, 0.0),
            pick(self.temp, 15.0),
            pick(self.precip, 0.0),
            pick(self.wind, 10.0),
        )


def _cp_features(
    t: np.ndarray,
    cp: pd.Series,
    loc_idx: int,
    cp_arr: tuple,
    loc_arr: np.ndarray,
    ctx: ContextGrid,
    sig: SignalTables,
    history_start: int | None = None,
    sibs: list[tuple] | None = None,
):
    """Features (len(t), len(FEATURE_COLUMNS)) and targets for one CP over slot timestamps t (ns).

    sibs: (arrivals, departures) of every CP at the location (this one included), for
    loc_charging_now; defaults to this CP alone.
    """
    n = len(t)
    X = np.zeros((n, len(FEATURE_COLUMNS)), np.float32)
    col = {c: i for i, c in enumerate(FEATURE_COLUMNS)}
    arr, dep = cp_arr
    sec = t // NS
    hour = (sec % 86400) / 3600.0
    ang = 2.0 * np.pi * hour / 24.0
    X[:, col["hour_sin"]], X[:, col["hour_cos"]] = np.sin(ang), np.cos(ang)
    X[:, col["weekday"]] = ((sec // 86400 + 3) % 7).astype(np.float32)  # 1970-01-01 = Thursday
    X[:, col["minute_of_day"]] = (sec % 86400) // 60
    hol, temp, prec, wind = ctx.get(cp["county_code"], t)
    X[:, col["is_holiday"]], X[:, col["temp_c"]] = hol, temp
    X[:, col["precip_mm"]], X[:, col["wind_kmh"]] = prec, wind
    X[:, col["power_class_code"]] = POWER_CLASS_MAP.get(cp["power_class"], 1)
    X[:, col["context_type_code"]] = CONTEXT_TYPE_MAP.get(cp["context_type"], 0)
    X[:, col["county_ev_per_1000"]] = cp["county_ev_per_1000"]
    X[:, col["cps_at_location"]] = cp["cps_at_location"]

    idx_t = np.searchsorted(arr, t, side="right")
    X[:, col["arrivals_cp_1h"]] = idx_t - np.searchsorted(arr, t - 3600 * NS, side="right")
    X[:, col["arrivals_cp_3h"]] = idx_t - np.searchsorted(arr, t - 3 * 3600 * NS, side="right")
    X[:, col["arrivals_same_slot_4w"]] = same_slot_4w(arr, t)
    r7, r28, mins = cp_level_features(arr, t, history_start)
    X[:, col["arrivals_cp_7d"]], X[:, col["arrivals_cp_28d"]] = r7, r28
    X[:, col["mins_since_last_arrival"]] = mins
    X[:, col["is_charging"]] = charging_at(arr, dep, t)
    # site level: arrivals at the location <= t, and sessions active at the siblings
    li = np.searchsorted(loc_arr, t, side="right")
    X[:, col["loc_arrivals_1h"]] = li - np.searchsorted(loc_arr, t - 3600 * NS, side="right")
    X[:, col["loc_arrivals_3h"]] = li - np.searchsorted(loc_arr, t - 3 * 3600 * NS, side="right")
    X[:, col["loc_same_slot_4w"]] = same_slot_4w(loc_arr, t)
    X[:, col["loc_rate_7d"]] = cp_level_features(loc_arr, t, history_start)[0]
    X[:, col["loc_charging_now"]] = sum(charging_at(a, d, t) for a, d in (sibs or [cp_arr]))
    # signals (location level)
    base = np.int64(loc_idx) << LOC_SHIFT
    ev = sum(
        _lookup_sum(sig.ev_keys, sig.ev_vals, base | ((t - o) // NS))
        for o in (0, SLOT_NS, 2 * SLOT_NS)
    )
    ice = sum(
        _lookup_sum(sig.ice_keys, sig.ice_vals, base | ((t - o) // NS))
        for o in (0, SLOT_NS, 2 * SLOT_NS)
    )
    X[:, col["maps_ev_count_15m"]] = ev
    X[:, col["weighted_flow_15m"]] = ev + 0.05 * ice
    if len(sig.tr_keys):
        pos = np.searchsorted(sig.tr_keys, base | sec, side="right") - 1
        ok = (pos >= 0) & ((sig.tr_keys[pos.clip(0)] >> LOC_SHIFT) == loc_idx)
        X[:, col["traffic_index"]] = np.where(ok, sig.tr_vals[pos.clip(0)], 0.0)
    it = _lookup_sum(sig.it_keys, sig.it_vals, base | sec)
    X[:, col["intent_eta_15"]], X[:, col["intent_eta_30"]] = it[:, 0], it[:, 1]
    X[:, col["intent_eta_60"]] = it[:, 2]
    # targets: first arrival strictly after t
    nxt = arr[idx_t.clip(0, max(len(arr) - 1, 0))] if len(arr) else np.zeros(n, np.int64)
    has = idx_t < len(arr)
    delta = np.where(has, nxt - t, np.iinfo(np.int64).max)
    bucket = np.where(delta <= 3600 * NS, -(-delta // SLOT_NS) - 1, N_BUCKETS).astype(np.uint8)
    y = np.stack([delta <= 900 * NS, delta <= 1800 * NS, delta <= 3600 * NS], 1).astype(np.uint8)
    n60 = np.searchsorted(loc_arr, t + 3600 * NS, side="right") - np.searchsorted(
        loc_arr, t, side="right"
    )
    return X, y, bucket, n60.astype(np.float32)


class ForecastData:
    """Handle on the cached sample: cpdays table plus lazily loaded matrices."""

    def __init__(self, root: Path, feat_dir: Path | None, cpdays: pd.DataFrame, meta: dict):
        self.root, self.feat_dir, self.cpdays, self.info = root, feat_dir, cpdays, meta

    def cpdays_of(self, split: str) -> pd.DataFrame:
        return self.cpdays[self.cpdays["split"] == split].reset_index(drop=True)

    def n_rows(self, split: str) -> int:
        return int((self.cpdays["split"] == split).sum()) * SLOTS

    def y(self, split: str) -> np.ndarray:
        return np.load(self.root / f"{split}_y.npy")

    def bucket(self, split: str) -> np.ndarray:
        return np.load(self.root / f"{split}_bucket.npy")

    def n60(self, split: str) -> np.ndarray:
        return np.load(self.root / f"{split}_n60.npy")

    def X_mmap(self, split: str) -> np.ndarray:
        return np.load(self.feat_dir / f"{split}_X.npy", mmap_mode="r")

    def X(self, split: str, names: list[str]) -> np.ndarray:
        """Selected feature columns as an in-RAM float32 array (chunked read)."""
        mm = self.X_mmap(split)
        cols = [FEATURE_COLUMNS.index(c) for c in names]
        out = np.empty((mm.shape[0], len(cols)), np.float32)
        for a in range(0, mm.shape[0], 1_000_000):
            out[a : a + 1_000_000] = mm[a : a + 1_000_000][:, cols]
        return out

    def meta(self, split: str) -> dict:
        cd = self.cpdays_of(split)
        codes = cd["power_class"].map({c: i for i, c in enumerate(POWER_CLASSES)}).to_numpy()
        return {
            "power_class": np.repeat(codes.astype(np.int8), SLOTS),
            "power_class_names": list(POWER_CLASSES),
        }

    def timestamps(self, split: str) -> pd.DatetimeIndex:
        cd = self.cpdays_of(split)
        day = cd["date"].to_numpy().astype("datetime64[s]").astype("int64")
        sec = (day[:, None] + np.arange(SLOTS) * 300).reshape(-1)
        return pd.DatetimeIndex(sec.astype("datetime64[s]").astype("datetime64[ns]"), tz="UTC")

    def cp_ids(self, split: str) -> np.ndarray:
        return np.repeat(self.cpdays_of(split)["cp_id"].to_numpy(), SLOTS)

    def location_ids(self, split: str) -> np.ndarray:
        return np.repeat(self.cpdays_of(split)["location_id"].to_numpy(), SLOTS)


def _cache_root() -> Path:
    return Path(os.environ.get("DATA_DIR", "data")) / "interim" / "forecast_cache"


def sample_key(cfg: dict) -> str:
    p = data_params(cfg)
    keys = ["sample_seed", "n_train_cpdays", "n_val_cpdays", "n_test_cpdays", "max_cps"]
    keys += ["train_end", "val_start", "val_end", "test_start"]
    files = {k: _sha(_data_file(cfg, k)) for k in ("sessions", "charge_points", "locations")}
    files["cp_active_days"] = _sha(_data_file(cfg, "cp_active_days"))
    return _hash({"v": FEATURE_VERSION, "p": {k: p[k] for k in keys}, "files": files})


def feature_key(cfg: dict) -> str:
    ev = (cfg.get("data") or {}).get("county_ev")
    ev_sha = _sha(data_root() / ev) if ev else "none"
    files = [_sha(_data_file(cfg, "signals")), _sha(_data_file(cfg, "context")), ev_sha]
    return _hash({"v": FEATURE_VERSION, "files": files})


def get_dataset(cfg: dict, log: logging.Logger | None = None, features: bool = True):
    """Build (or load from cache) the sample, targets and, if features=True, all features."""
    log = log or logging.getLogger("forecast_data")
    p = data_params(cfg)
    root = _cache_root() / sample_key(cfg)
    root.mkdir(parents=True, exist_ok=True)
    feat_dir = root / f"feat_{feature_key(cfg)}"
    need_targets = not (root / "targets.done").exists()
    need_feats = features and not (feat_dir / "features.done").exists()
    cps = load_cps(cfg)
    if need_targets or need_feats:
        log.info("building forecast cache %s", root)
        _build(cfg, p, cps, root, feat_dir if need_feats else None, need_targets, log)
    cpdays = pd.read_parquet(root / "cpdays.parquet")
    info = json.loads((root / "sample.json").read_text())
    return ForecastData(root, feat_dir if features else None, cpdays, info)


def _build(cfg, p, cps, root: Path, feat_dir: Path | None, do_targets: bool, log) -> None:
    active = pd.read_parquet(_data_file(cfg, "cp_active_days"))
    cpdays = draw_sample(cps, active, p)
    cpdays.to_parquet(root / "cpdays.parquet", index=False)
    (root / "sample.json").write_text(
        json.dumps({k: p[k] for k in DEFAULT_PARAMS if k != "feature_groups"}, default=str)
    )
    sess = pd.read_parquet(
        _data_file(cfg, "sessions"), columns=["cp_id", "location_id", "arrival_ts", "departure_ts"]
    )
    by_cp = _sessions_by(sess, "cp_id", True)
    by_loc = {k: v[0] for k, v in _sessions_by(sess, "location_id", False).items()}
    hist_start = history_start(sess)
    del sess
    cps_i = cps.set_index("cp_id")
    empty = (np.zeros(0, np.int64), np.zeros(0, np.int64))
    sibs = siblings(cps, by_cp)
    n_rows = {s: int((cpdays["split"] == s).sum()) * SLOTS for s in SPLITS}
    mm_X: dict[str, np.ndarray] = {}
    mm_y: dict[str, np.ndarray] = {}
    if feat_dir is not None:
        feat_dir.mkdir(parents=True, exist_ok=True)
        for s in SPLITS:
            mm_X[s] = np.lib.format.open_memmap(
                feat_dir / f"{s}_X.npy", "w+", np.float32, (n_rows[s], len(FEATURE_COLUMNS))
            )
    for s in SPLITS:
        mm_y[s] = (
            np.zeros((n_rows[s], 3), np.uint8),
            np.zeros(n_rows[s], np.uint8),
            np.zeros(n_rows[s], np.float32),
        )
    loc_names = sorted(cps["location_id"].unique())
    loc_index = {k: i for i, k in enumerate(loc_names)}
    sig = ctx = None
    if feat_dir is not None:
        d = cpdays["date"].to_numpy().astype("datetime64[D]").astype("int64")
        li = cpdays["location_id"].map(loc_index).to_numpy().astype(np.int64)
        needed = np.unique((li << DAY_SHIFT) | d)
        sig = load_signal_tables(_data_file(cfg, "signals"), loc_index, needed, log)
        ctx = ContextGrid(pd.read_parquet(_data_file(cfg, "context")))
    # per split row offsets of each (cp) block
    cpdays["_row"] = np.arange(len(cpdays))
    split_first = {s: int((cpdays["split"] == s).idxmax()) if n_rows[s] else 0 for s in SPLITS}
    for cp_id, grp in cpdays.groupby("cp_id", sort=False):
        cp = cps_i.loc[cp_id]
        loc_arr = by_loc.get(cp["location_id"], np.zeros(0, np.int64))
        cp_arr = by_cp.get(cp_id, empty)
        for s, g in grp.groupby("split", sort=False):
            day = g["date"].to_numpy().astype("datetime64[D]").astype("int64") * 86400 * NS
            t = (day[:, None] + np.arange(SLOTS) * SLOT_NS).reshape(-1)
            X, y, b, n60 = _cp_features(
                t,
                cp,
                loc_index[cp["location_id"]],
                cp_arr,
                loc_arr,
                ctx if ctx is not None else ContextGrid(_empty_ctx()),
                sig if sig is not None else _empty_sig(),
                hist_start,
                sibs.get(cp["location_id"]),
            )
            a = (int(g["_row"].iloc[0]) - split_first[s]) * SLOTS
            e = a + len(t)
            if feat_dir is not None:
                mm_X[s][a:e] = X
            mm_y[s][0][a:e], mm_y[s][1][a:e], mm_y[s][2][a:e] = y, b, n60
    if do_targets:
        for s in SPLITS:
            np.save(root / f"{s}_y.npy", mm_y[s][0])
            np.save(root / f"{s}_bucket.npy", mm_y[s][1])
            np.save(root / f"{s}_n60.npy", mm_y[s][2])
        (root / "targets.done").write_text("ok")
    if feat_dir is not None:
        for s in SPLITS:
            mm_X[s].flush()
        (feat_dir / "features.done").write_text("ok")
    cpdays.drop(columns="_row").to_parquet(root / "cpdays.parquet", index=False)


def siblings(cps: pd.DataFrame, by_cp: dict[str, tuple]) -> dict[str, list[tuple]]:
    """location_id -> (arrivals, departures) of each CP there (empty arrays when none)."""
    empty = (np.zeros(0, np.int64), np.zeros(0, np.int64))
    out: dict[str, list[tuple]] = {}
    for cp_id, loc in zip(cps["cp_id"], cps["location_id"], strict=True):
        out.setdefault(loc, []).append(by_cp.get(cp_id, empty))
    return out


def _empty_ctx() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_hour": pd.to_datetime([]).tz_localize("UTC"),
            "county_code": [],
            "temp_c": [],
            "precip_mm": [],
            "wind_kmh": [],
            "is_holiday": [],
        }
    )


def _empty_sig() -> SignalTables:
    z = np.zeros(0, np.int64)
    return SignalTables(
        z, z.astype(float), z, z.astype(float), z, z.astype(float), z, np.zeros((0, 3))
    )
