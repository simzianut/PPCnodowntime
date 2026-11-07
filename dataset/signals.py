"""Signal generator (spec 01 section 5, T8): signals.parquet, compact all-days format.

CLI: python -m dataset.signals
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from dataset.mapping import RO_TZ, data_dir

REPO = Path(__file__).resolve().parent.parent
SIGNALS = [
    "ppcblue_route_intent",
    "maps_ev_count",
    "maps_ice_count",
    "maps_traffic_index",
]
NS_MIN = 60 * 10**9
NS_HOUR = 3600 * 10**9
SLOT_NS = 5 * NS_MIN
SLOTS_PER_HOUR = 12
NAT = np.iinfo(np.int64).min


def load_config() -> dict:
    cfg = yaml.safe_load((REPO / "config" / "signals.yaml").read_text(encoding="utf-8"))
    ds = yaml.safe_load((REPO / "config" / "dataset.yaml").read_text(encoding="utf-8"))["dundee"]
    cfg["start"], cfg["end"] = ds["start"], ds["end"]
    return cfg


def _ns(ts: pd.Series) -> np.ndarray:
    naive = ts.dt.tz_convert("UTC").dt.tz_localize(None)
    return naive.to_numpy().astype("datetime64[ns]").astype(np.int64)


def _synthetic_curve(ctx: str) -> np.ndarray:
    """168-value typical curve (local Mon 00h = 0) by context type."""
    how = np.arange(168)
    hour = how % 24
    weekend = (how // 24) >= 5

    def g(mu: float, sd: float) -> np.ndarray:
        return np.exp(-0.5 * ((hour - mu) / sd) ** 2)

    if ctx in ("highway", "airport"):
        c = 0.25 + 0.5 * g(13, 4) + 0.2 * g(17, 2)
    else:
        c = 0.15 + 0.6 * g(8, 1.5) * np.where(weekend, 0.5, 1) + 0.65 * g(17.5, 2) + 0.2 * g(13, 3)
    return np.clip(c, 0, 1)


def traffic_matrix(locs: pd.DataFrame, proc: Path) -> tuple[np.ndarray, dict]:
    """(n_loc, 168) typical index; source preference mapbox > partial > synthetic."""
    mat = np.full((len(locs), 168), np.nan)
    idx = {loc: i for i, loc in enumerate(locs["location_id"])}
    counts = {"mapbox": 0, "partial": 0, "synthetic": 0}
    for fname, key in (
        ("traffic_typical.parquet", "mapbox"),
        ("traffic_typical.partial.parquet", "partial"),
    ):
        p = proc / fname
        if not p.exists():
            continue
        t = pd.read_parquet(p)
        t = t[t["location_id"].isin(idx)]
        for loc, g in t.groupby("location_id"):
            i = idx[loc]
            if not np.isnan(mat[i]).all() or g["hour_of_week"].nunique() < 168:
                continue
            mat[i, g["hour_of_week"].to_numpy()] = g["typical_index"].to_numpy()
            counts[key] += 1
    for i, ctx in enumerate(locs["context_type"]):
        if np.isnan(mat[i]).any():
            mat[i] = _synthetic_curve(ctx)
            counts["synthetic"] += 1
    return np.clip(mat, 0, 1), counts


def gen_intents(rng, cfg, sess: pd.DataFrame, loc_code: np.ndarray) -> dict:
    """Route intents (one per app session) plus false positives with null session_id."""
    lo, hi = cfg["intent_lead_min"]
    arr = _ns(sess["arrival_ts"])
    take = rng.random(len(sess)) < cfg["p_app"]
    a = arr[take]
    ts = a - (rng.uniform(lo, hi, len(a)) * NS_MIN).astype(np.int64)
    eta = a + (rng.normal(0, cfg["eta_sd_min"], len(a)) * NS_MIN).astype(np.int64)
    loc = loc_code[take]
    sid = sess["session_id"].to_numpy(dtype=object)[take]
    order = np.argsort(loc_code, kind="stable")
    arr_sorted = arr[order]
    n_loc = int(loc_code.max()) + 1 if len(loc_code) else 0
    ic = np.bincount(loc, minlength=n_loc)
    bounds = np.searchsorted(loc_code[order], np.arange(n_loc + 1))
    fts, floc = [], []
    for code in range(n_loc):
        n_fp = int(round(cfg["false_positive_rate"] * ic[code]))
        if n_fp == 0 or bounds[code + 1] == bounds[code]:
            continue
        pool = arr_sorted[bounds[code] : bounds[code + 1]]
        fts.append(rng.choice(pool, n_fp) + (rng.uniform(-3, 3, n_fp) * NS_HOUR).astype(np.int64))
        floc.append(np.full(n_fp, code, dtype=np.int64))
    fts = np.concatenate(fts) if fts else np.empty(0, np.int64)
    floc = np.concatenate(floc) if floc else np.empty(0, np.int64)
    feta = fts + (rng.uniform(lo, hi, len(fts)) * NS_MIN).astype(np.int64)
    return {
        "ts": np.concatenate([ts, fts]),
        "loc": np.concatenate([loc, floc]),
        "eta": np.concatenate([eta, feta]),
        "sid": np.concatenate([sid, np.full(len(fts), None, dtype=object)]),
        "n_real": len(ts),
        "share_before_arrival": float(np.mean(ts < a)) if len(a) else 1.0,
    }


def build_arrays(rng, cfg, locs, sess, ctx, traffic, h0_ns, n_hours) -> dict:
    """All signal rows as parallel numpy arrays (unsorted)."""
    n_loc = len(locs)
    code_of = {loc: i for i, loc in enumerate(locs["location_id"])}
    sess = sess[sess["location_id"].isin(code_of)]
    loc_code = sess["location_id"].map(code_of).to_numpy(dtype=np.int64)
    out = {k: [] for k in ("ts", "loc", "sig", "val", "eta", "sid")}

    def add(sig, ts, loc, val, eta=None, sid=None):
        out["ts"].append(ts)
        out["loc"].append(loc)
        out["sig"].append(np.full(len(ts), SIGNALS.index(sig), dtype=np.int8))
        out["val"].append(np.asarray(val, dtype=np.float64))
        out["eta"].append(eta if eta is not None else np.full(len(ts), NAT, dtype=np.int64))
        out["sid"].append(sid if sid is not None else np.full(len(ts), None, dtype=object))

    intents = gen_intents(rng, cfg, sess, loc_code)
    add(
        "ppcblue_route_intent",
        intents["ts"],
        intents["loc"],
        np.ones(len(intents["ts"])),
        intents["eta"],
        intents["sid"],
    )

    hours_ns = h0_ns + np.arange(n_hours, dtype=np.int64) * NS_HOUR
    local = pd.DatetimeIndex(hours_ns.astype("datetime64[ns]"), tz="UTC").tz_convert(RO_TZ)
    how = (local.day_of_week * 24 + local.hour).to_numpy()
    h = (_ns(ctx["ts_hour"]) - h0_ns) // NS_HOUR
    precip_by_county = {}
    for cc in ctx["county_code"].unique():
        m = (ctx["county_code"] == cc).to_numpy() & (h >= 0) & (h < n_hours)
        v = np.zeros(n_hours)
        v[h[m]] = ctx["precip_mm"].fillna(0).to_numpy()[m]
        precip_by_county[cc] = v

    order = np.argsort(loc_code, kind="stable")
    arr_sorted = _ns(sess["arrival_ts"])[order]
    bounds = np.searchsorted(loc_code[order], np.arange(n_loc + 1))
    n_slots = n_hours * SLOTS_PER_HOUR
    slot_hour = np.arange(n_slots) // SLOTS_PER_HOUR
    slot_ts = h0_ns + np.arange(n_slots, dtype=np.int64) * SLOT_NS
    counties = locs["county_code"].to_numpy()

    for i in range(n_loc):
        precip = precip_by_county.get(counties[i], np.zeros(n_hours))
        t = traffic[i][how] * (1 + cfg["rain_boost"] * np.minimum(precip, 5) / 5)
        t = np.clip(t * rng.lognormal(0, cfg["traffic_noise_sd"], n_hours), 0, 1)
        locv = np.full(n_hours, i, dtype=np.int64)
        add("maps_traffic_index", hours_ns, locv, t)
        add("maps_ice_count", hours_ns, locv, rng.poisson(cfg["ice_base"] * t))
        # arrivals in (slot, slot+30min]: arrival a hits slots j-5..j, j = last slot with t_j < a
        a = arr_sorted[bounds[i] : bounds[i + 1]]
        j = np.ceil((a - h0_ns) / SLOT_NS).astype(np.int64) - 1
        j = j[(j >= 0) & (j < n_slots)]
        cnt = np.bincount(j, minlength=n_slots).astype(np.float64)
        # nxt[s] = sum cnt[s .. s+5]
        cs = np.concatenate([[0.0], np.cumsum(cnt), np.full(6, cnt.sum())])
        s = np.arange(n_slots)
        nxt = cs[s + 6] - cs[s]
        rate = cfg["ev_tag_rate"] * (nxt + cfg["background_ev"] * t[slot_hour])
        c = rng.poisson(rate)
        nz = np.flatnonzero(c)
        add("maps_ev_count", slot_ts[nz], np.full(len(nz), i, dtype=np.int64), c[nz])
    cat = {k: np.concatenate(v) for k, v in out.items()}
    cat["n_real"] = intents["n_real"]
    cat["n_sessions"] = len(sess)
    cat["share_before_arrival"] = intents["share_before_arrival"]
    return cat


def to_table(arrs: dict, loc_ids: np.ndarray) -> pa.Table:
    o = np.argsort(arrs["ts"], kind="stable")
    ts_t = pa.timestamp("ns", tz="UTC")
    eta = arrs["eta"][o]
    return pa.table(
        {
            "ts": pa.array(arrs["ts"][o], type=pa.int64()).cast(ts_t),
            "location_id": pa.DictionaryArray.from_arrays(
                arrs["loc"][o].astype(np.int32), pa.array(loc_ids, type=pa.string())
            ),
            "signal": pa.DictionaryArray.from_arrays(arrs["sig"][o], pa.array(SIGNALS)),
            "value": pa.array(arrs["val"][o]),
            "eta_ts": pa.array(eta, type=pa.int64(), mask=eta == NAT).cast(ts_t),
            "session_id": pa.array(arrs["sid"][o], type=pa.string()),
        }
    )


def build(out_path: Path | None = None) -> dict:
    t0 = time.time()
    cfg = load_config()
    proc = data_dir() / "processed"
    rng = np.random.default_rng(cfg["seed"])
    locs = pd.read_parquet(
        proc / "locations.parquet", columns=["location_id", "county_code", "context_type"]
    )
    sess = pd.read_parquet(
        proc / "sessions.parquet", columns=["session_id", "location_id", "arrival_ts"]
    )
    ctx = pd.read_parquet(proc / "context.parquet", columns=["ts_hour", "county_code", "precip_mm"])
    traffic, tcounts = traffic_matrix(locs, proc)
    h0 = pd.Timestamp(cfg["start"], tz="UTC")
    n_hours = int(
        (pd.Timestamp(cfg["end"], tz="UTC") + pd.Timedelta(days=1) - h0) / pd.Timedelta(hours=1)
    )
    arrs = build_arrays(rng, cfg, locs, sess, ctx, traffic, h0.value, n_hours)
    table = to_table(arrs, locs["location_id"].to_numpy())
    out_path = out_path or proc / "signals.parquet"
    pq.write_table(table, out_path, row_group_size=1_000_000, compression="zstd")
    rows = table.column("signal").to_pandas().value_counts()
    rep = {
        "rows_total": int(table.num_rows),
        "rows_per_signal": {s: int(rows.get(s, 0)) for s in SIGNALS},
        "intent_share_of_sessions": round(arrs["n_real"] / max(arrs["n_sessions"], 1), 4),
        "p_app_config": cfg["p_app"],
        "share_intent_ts_before_arrival": arrs["share_before_arrival"],
        "traffic_source": tcounts,
        "file_size_mb": round(out_path.stat().st_size / 1e6, 1),
        "runtime_s": round(time.time() - t0, 1),
    }
    rdir = proc / "reports"
    rdir.mkdir(exist_ok=True)
    (rdir / "signals_report.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
    return rep


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
