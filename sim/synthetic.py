"""Seeded synthetic sessions, signals, context and oracle forecasts (decision 0024)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

import holidays
import numpy as np
import pandas as pd

from sim.config import TZ, signals_config
from sim.data import SimData

SLOT_S = 300
SLOTS_PER_DAY = 86400 // SLOT_S
_NS = 1_000_000_000
_MIN_NS = 60 * _NS
_SLOT_NS = SLOT_S * _NS

SESSION_COLUMNS = [
    "session_id", "cp_id", "location_id", "arrival_ts", "departure_ts", "energy_kwh",
    "donor_charger_id", "split",
]  # fmt: skip
SIGNAL_COLUMNS = ["ts", "location_id", "signal", "value", "eta_ts", "session_id"]
CONTEXT_COLUMNS = ["ts_hour", "county_code", "temp_c", "precip_mm", "wind_kmh", "is_holiday"]
FORECAST_TABLE_COLUMNS = [
    "ts", "cp_id", "p_arrival_15", "p_arrival_30", "p_arrival_60", "expected_arrivals_60",
]  # fmt: skip

BACKGROUND_EV_FALLBACK = 2.0
ICE_BASE_FALLBACK = 40.0
QUIET_SHARE = 0.2

# Sessions per day before context scaling, duration minutes and charging kW per power class.
_RATE = {"AC_22": (2.0, 5.0), "DC_50": (4.0, 10.0), "DC_HPC": (4.0, 10.0)}
_DURATION_MIN = {"AC_22": (60.0, 240.0), "DC_50": (20.0, 50.0), "DC_HPC": (15.0, 35.0)}
_POWER_KW = {"AC_22": (7.0, 11.0), "DC_50": (30.0, 45.0), "DC_HPC": (80.0, 150.0)}

# Day factor by context: Mon..Sun, then the public-holiday multiplier.
_WEEKDAY_FACTOR = {
    "urban": ([1, 1, 1, 1, 1, 0.75, 0.65], 0.6),
    "retail": ([0.9, 0.9, 0.9, 0.95, 1.05, 1.35, 1.0], 0.7),
    "highway": ([0.95, 0.9, 0.9, 0.95, 1.2, 1.0, 1.2], 0.8),
    "airport": ([1, 1, 1, 1, 1.1, 0.9, 1.05], 0.8),
    "other": ([1, 1, 1, 1, 1, 0.85, 0.8], 0.6),
}


def _gauss(h: np.ndarray, mu: float, sd: float) -> np.ndarray:
    return np.exp(-0.5 * ((h - mu) / sd) ** 2)


def _profiles() -> dict[str, np.ndarray]:
    """Hourly arrival weights (local time) per context type, normalised."""
    h = np.arange(24) + 0.5
    day = (h >= 6) & (h < 23)
    raw = {
        "urban": _gauss(h, 8, 1.5) + 1.2 * _gauss(h, 18, 2.0) + 0.25 * day + 0.02,
        "retail": np.where((h >= 10) & (h < 20), 1.0, 0.0)
        + np.where((h >= 8) & (h < 10) | (h >= 20) & (h < 22), 0.3, 0.0)
        + 0.01,
        "highway": np.where((h >= 6) & (h < 22), 1.0, 0.15),
        "airport": 0.25 + _gauss(h, 6, 1.5) + _gauss(h, 14, 2.0) + _gauss(h, 20, 2.0) - 0.2 * ~day,
        "other": _gauss(h, 13, 3.0) + 0.05,
    }
    return {k: v / v.sum() for k, v in raw.items()}


PROFILES = _profiles()


def _traffic_curve(hour: np.ndarray, highway: bool) -> np.ndarray:
    if highway:
        return 0.25 + 0.6 * _gauss(hour, 14, 5.0)
    return (
        0.1
        + 0.6 * _gauss(hour, 8, 1.5)
        + 0.7 * _gauss(hour, 17.5, 2.0)
        + 0.25 * _gauss(hour, 13, 3.0)
    )


def _local_midnights_ns(days: Sequence[date]) -> np.ndarray:
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in days]).tz_localize(TZ).tz_convert("UTC")
    return idx.as_unit("ns").asi8


def slot_starts(days: Sequence[date]) -> pd.DatetimeIndex:
    """UTC slot starts from each day's local midnight to the next (276/288/300 on DST days)."""
    mid = _local_midnights_ns(days)
    nxt = _local_midnights_ns([d + timedelta(days=1) for d in days])
    parts = [np.arange(a, b, _SLOT_NS, dtype=np.int64) for a, b in zip(mid, nxt, strict=True)]
    ns = np.concatenate(parts) if parts else np.empty(0, dtype=np.int64)
    return pd.DatetimeIndex(ns.astype("datetime64[ns]")).tz_localize("UTC")


def _ro_holidays(days: Sequence[date]) -> set[date]:
    years = sorted({d.year for d in days})
    return set(holidays.country_holidays("RO", years=years)) if years else set()


def _str(values) -> pd.Series:
    return pd.Series(np.asarray(values, dtype=object), dtype="str")


def _ts(ns: np.ndarray) -> pd.Series:
    """UTC timestamps floored to whole seconds; int64 min stays NaT."""
    ns = np.where(ns == np.iinfo(np.int64).min, ns, ns - ns % _NS)
    return pd.Series(pd.DatetimeIndex(ns.astype("datetime64[ns]")).tz_localize("UTC"))


def _rngs(seed: int, n: int) -> list[np.random.Generator]:
    return [np.random.default_rng(s) for s in np.random.SeedSequence(seed).spawn(n)]


def _sessions(
    rng: np.random.Generator,
    cps: pd.DataFrame,
    days: list[date],
    splits: list[str],
    hol: set[date],
) -> pd.DataFrame:
    mult = signals_config()["context_multiplier"]
    n_days = len(days)
    weekday = np.array([d.weekday() for d in days])
    is_hol = np.array([d in hol for d in days])
    day_off = (np.array([d.toordinal() for d in days]) - days[0].toordinal()) * 1440.0
    loc_mid = np.array([pd.Timestamp(d) for d in days], dtype="datetime64[ns]").astype(np.int64)

    parts = []
    for i, cp in enumerate(cps.itertuples(index=False)):
        pc, ctx = str(cp.power_class), str(cp.context_type)
        lo, hi = _RATE[pc]
        rate = rng.uniform(lo, hi) * float(mult.get(ctx, 1.0))
        if rng.random() < QUIET_SHARE:
            rate = rng.uniform(0.2, 1.0)
        wk, hol_f = _WEEKDAY_FACTOR.get(ctx, _WEEKDAY_FACTOR["other"])
        fac = np.asarray(wk)[weekday] * np.where(is_hol, hol_f, 1.0)
        counts = rng.poisson(rate * fac)
        n = int(counts.sum())
        if n == 0:
            continue
        day_idx = np.repeat(np.arange(n_days), counts)
        hours = rng.choice(24, size=n, p=PROFILES.get(ctx, PROFILES["other"]))
        minute = hours * 60.0 + rng.uniform(0, 60, n)
        dur = rng.uniform(*_DURATION_MIN[pc], n)
        kw = rng.uniform(*_POWER_KW[pc], n)
        order = np.argsort(day_off[day_idx] + minute, kind="stable")
        day_idx, minute, dur, kw = day_idx[order], minute[order], dur[order], kw[order]
        # Drop sessions that would overlap the previous kept one on this CP.
        t = day_off[day_idx] + minute
        keep = np.zeros(n, dtype=bool)
        free_at = -np.inf
        for k in range(n):
            if t[k] >= free_at:
                keep[k] = True
                free_at = t[k] + dur[k]
        parts.append(
            pd.DataFrame(
                {
                    "cp": i,
                    "day": day_idx[keep],
                    "local_ns": loc_mid[day_idx[keep]] + (minute[keep] * _MIN_NS).astype(np.int64),
                    "dur_min": dur[keep],
                    "kw": kw[keep],
                }
            )
        )
    empty = {"cp": [0], "day": [0], "local_ns": [0], "dur_min": [0.0], "kw": [0.0]}
    df = pd.concat(parts or [pd.DataFrame(empty).iloc[:0]], ignore_index=True)
    local = pd.DatetimeIndex(df["local_ns"].to_numpy().astype("datetime64[ns]"))
    arr = local.tz_localize(
        TZ, ambiguous=np.zeros(len(df), dtype=bool), nonexistent="shift_forward"
    ).tz_convert("UTC")
    arr_ns = arr.as_unit("ns").asi8
    dep_ns = arr_ns + (df["dur_min"].to_numpy() * _MIN_NS).astype(np.int64)
    order = np.lexsort((df["cp"].to_numpy(), arr_ns))
    cp_ids = cps["cp_id"].to_numpy(dtype=object)
    loc_ids = cps["location_id"].to_numpy(dtype=object)
    cpi = df["cp"].to_numpy()[order]
    energy = np.round(df["kw"].to_numpy() * df["dur_min"].to_numpy() / 60.0, 2)[order]
    split_arr = np.asarray(splits, dtype=object)[df["day"].to_numpy()[order]]
    return pd.DataFrame(
        {
            "session_id": _str([f"S{k:07d}" for k in range(1, len(df) + 1)]),
            "cp_id": _str(cp_ids[cpi]),
            "location_id": _str(loc_ids[cpi]),
            "arrival_ts": _ts(arr_ns[order]),
            "departure_ts": _ts(dep_ns[order]),
            "energy_kwh": energy.astype("float64"),
            "donor_charger_id": _str(np.full(len(df), "synthetic", dtype=object)),
            "split": _str(split_arr),
        }
    )


def _hours(days: Sequence[date]) -> tuple[np.ndarray, np.ndarray]:
    """UTC hour starts of each local day (23/24/25 on DST days) and their day index."""
    mid = _local_midnights_ns(days)
    nxt = _local_midnights_ns([d + timedelta(days=1) for d in days])
    parts = [np.arange(a, b, 3600 * _NS, dtype=np.int64) for a, b in zip(mid, nxt, strict=True)]
    if not parts:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=int)
    return np.concatenate(parts), np.repeat(np.arange(len(days)), [len(x) for x in parts])


def _local_hour(ts_ns: np.ndarray) -> np.ndarray:
    local = pd.DatetimeIndex(ts_ns.astype("datetime64[ns]")).tz_localize("UTC").tz_convert(TZ)
    return (local.hour + local.minute / 60.0).to_numpy(dtype=float)


def _context(
    rng: np.random.Generator, counties: list[str], days: list[date], hol: set[date]
) -> pd.DataFrame:
    n_c, n_d = len(counties), len(days)
    ts, d_of = _hours(days)
    n_h = ts.size
    hours = _local_hour(ts)
    doy = np.array([d.timetuple().tm_yday for d in days], dtype=float)
    season = 11.0 - 13.0 * np.cos(2 * np.pi * (doy - 20) / 365.25)
    diurnal = 4.5 * np.sin(2 * np.pi * (hours - 9) / 24)
    day_noise = rng.normal(0, 3.0, (n_c, n_d))[:, d_of]
    temp = season[d_of][None, :] + diurnal[None, :] + day_noise
    temp = temp + rng.normal(0, 0.8, temp.shape)
    wet_day = (rng.random((n_c, n_d)) < 0.3)[:, d_of]
    rain = (rng.random((n_c, n_h)) < 0.35) & wet_day
    precip = np.where(rain, rng.exponential(1.2, rain.shape), 0.0)
    wind = rng.gamma(2.5, 4.0, (n_c, n_d))[:, d_of] + rng.gamma(1.5, 2.0, (n_c, n_h))
    is_hol = np.array([d in hol for d in days], dtype=bool)[d_of]
    return pd.DataFrame(
        {
            "ts_hour": _ts(np.tile(ts, n_c)),
            "county_code": _str(np.repeat(np.asarray(counties, dtype=object), n_h)),
            "temp_c": np.round(temp.ravel(), 1),
            "precip_mm": np.round(precip.ravel(), 1),
            "wind_kmh": np.round(wind.ravel(), 1),
            "is_holiday": np.tile(is_hol, n_c).astype(bool),
        }
    )


def _intents(
    rng: np.random.Generator, sessions: pd.DataFrame, days: list[date], cfg: dict
) -> pd.DataFrame:
    n = len(sessions)
    arr = sessions["arrival_ts"].dt.as_unit("ns").astype("int64").to_numpy()
    lo, hi = cfg["intent_lead_min"]
    has = rng.random(n) < float(cfg["p_app"])
    idx = np.flatnonzero(has)
    eta = arr[idx] + (rng.normal(0, float(cfg["eta_sd_min"]), idx.size) * _MIN_NS).astype(np.int64)
    ts = arr[idx] - (rng.uniform(lo, hi, idx.size) * _MIN_NS).astype(np.int64)
    loc = sessions["location_id"].to_numpy(dtype=object)[idx]
    sid = sessions["session_id"].to_numpy(dtype=object)[idx]
    # False positives: copy a random true intent's location, shifted by 2-6 h either way.
    n_fp = rng.binomial(idx.size, float(cfg["false_positive_rate"])) if idx.size else 0
    src = rng.integers(0, max(idx.size, 1), n_fp)
    shift = rng.uniform(120, 360, n_fp) * rng.choice([-1, 1], n_fp)
    fp_eta = eta[src] + (shift * _MIN_NS).astype(np.int64) if n_fp else np.zeros(0, np.int64)
    fp_ts = fp_eta - (rng.uniform(lo, hi, n_fp) * _MIN_NS).astype(np.int64)
    mid = _local_midnights_ns(days)
    nxt = _local_midnights_ns([d + timedelta(days=1) for d in days])
    d_idx = np.searchsorted(mid, fp_eta, side="right") - 1
    in_days = (d_idx >= 0) & (fp_eta < nxt[np.maximum(d_idx, 0)])
    fp_eta, fp_ts, fp_loc = fp_eta[in_days], fp_ts[in_days], loc[src][in_days]
    k = fp_eta.size
    return pd.DataFrame(
        {
            "ts": np.concatenate([ts, fp_ts]),
            "loc": np.concatenate([loc, fp_loc]),
            "value": 1.0,
            "eta": np.concatenate([eta, fp_eta]),
            "sid": np.concatenate([sid, np.full(k, None, dtype=object)]),
        }
    )


def _maps(
    rng: np.random.Generator,
    locations: pd.DataFrame,
    sessions: pd.DataFrame,
    context: pd.DataFrame,
    days: list[date],
    cfg: dict,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return (ts_ns, loc_idx, signal_code, value) for the three per-slot map series."""
    slots = slot_starts(days).asi8
    n_l, n_s = len(locations), slots.size
    hour = _local_hour(slots)
    highway = (locations["context_type"].to_numpy(dtype=object) == "highway")[:, None]
    curve = np.where(highway, _traffic_curve(hour, True), _traffic_curve(hour, False))
    counties = list(dict.fromkeys(context["county_code"].tolist()))
    c_idx = np.array([counties.index(c) for c in locations["county_code"]], dtype=int)
    precip = context["precip_mm"].to_numpy().reshape(len(counties), -1)
    h_ts, _ = _hours(days)
    h_idx = np.clip(np.searchsorted(h_ts, slots, side="right") - 1, 0, max(h_ts.size - 1, 0))
    wet = precip[c_idx][:, h_idx] if n_l else np.zeros((0, n_s))
    weather = np.where(wet > 0.5, 0.85, 1.0)
    traffic = np.clip(curve * weather * rng.lognormal(0, 0.12, (n_l, n_s)), 0, 1)

    loc_pos = {loc: i for i, loc in enumerate(locations["location_id"])}
    arr = sessions["arrival_ts"].dt.as_unit("ns").astype("int64").to_numpy()
    li = sessions["location_id"].map(loc_pos).to_numpy()
    nxt30 = np.zeros((n_l, n_s))
    for i in range(n_l):
        a = np.sort(arr[li == i])
        nxt30[i] = np.searchsorted(a, slots + 30 * _MIN_NS, "right") - np.searchsorted(
            a, slots, "right"
        )
    bg = cfg.get("background_ev")
    ice = cfg.get("ice_base")
    bg = BACKGROUND_EV_FALLBACK if bg is None else float(bg)
    ice = ICE_BASE_FALLBACK if ice is None else float(ice)
    ev = rng.poisson(float(cfg["ev_tag_rate"]) * (nxt30 + bg * traffic))
    ice_n = rng.poisson(ice * traffic)
    # Codes: 0 = maps_ev_count, 1 = maps_ice_count, 2 = maps_traffic_index.
    vals = np.stack([ev.astype(float), ice_n.astype(float), traffic])  # (3, L, S)
    ts = np.broadcast_to(slots[None, None, :], vals.shape).ravel()
    loc = np.broadcast_to(np.arange(n_l)[None, :, None], vals.shape).ravel()
    sig = np.broadcast_to(np.arange(3)[:, None, None], vals.shape).ravel()
    return ts, loc, sig, vals.ravel()


_SIGNAL_NAMES = np.array(
    ["maps_ev_count", "maps_ice_count", "maps_traffic_index", "ppcblue_route_intent"], dtype=object
)


def _signals(
    rng: np.random.Generator,
    locations: pd.DataFrame,
    sessions: pd.DataFrame,
    context: pd.DataFrame,
    days: list[date],
) -> pd.DataFrame:
    cfg = signals_config()
    ts_m, loc_m, sig_m, val_m = _maps(rng, locations, sessions, context, days, cfg)
    it = _intents(rng, sessions, days, cfg)
    loc_names = locations["location_id"].to_numpy(dtype=object)
    loc_pos = {loc: i for i, loc in enumerate(loc_names)}
    k = len(it)
    ts = np.concatenate([ts_m, it["ts"].to_numpy(np.int64)])
    loc = np.concatenate([loc_m, it["loc"].map(loc_pos).to_numpy(np.int64)])
    sig = np.concatenate([sig_m, np.full(k, 3)])
    val = np.concatenate([val_m, it["value"].to_numpy(float)])
    nat = np.iinfo(np.int64).min
    eta = np.concatenate([np.full(ts_m.size, nat), it["eta"].to_numpy(np.int64)])
    sid = np.concatenate([np.full(ts_m.size, None, dtype=object), it["sid"].to_numpy(object)])
    order = np.lexsort((sig, loc, ts))
    return pd.DataFrame(
        {
            "ts": _ts(ts[order]),
            "location_id": _str(loc_names[loc[order]]),
            "signal": _str(_SIGNAL_NAMES[sig[order]]),
            "value": val[order].astype("float64"),
            "eta_ts": _ts(eta[order]),
            "session_id": _str(sid[order]),
        }
    )


def oracle_forecasts(
    sessions: pd.DataFrame,
    cps: pd.DataFrame,
    days: Sequence[date],
    seed: int = 0,
    noise: float = 0.3,
) -> pd.DataFrame:
    """Noisy-oracle forecasts.parquet rows for every CP x slot of `days` (decision 0024)."""
    rng = np.random.default_rng([seed, 21])
    days = sorted(days)
    slots = slot_starts(days).asi8
    n_s, n_c = slots.size, len(cps)
    cp_ids = cps["cp_id"].astype(str).tolist()
    cp_loc = cps["location_id"].astype(str).to_numpy()
    arr = sessions["arrival_ts"].dt.as_unit("ns").astype("int64").to_numpy()
    s_cp = sessions["cp_id"].astype(str).to_numpy()
    s_loc = sessions["location_id"].astype(str).to_numpy()
    hours = max(len(days) * 24, 1)
    lo_t, hi_t = (slots[0], slots[-1] + _SLOT_NS) if n_s else (0, 0)
    in_range = (arr >= lo_t) & (arr < hi_t)

    horizons = np.array([15, 30, 60])
    p = np.empty((3, n_s, n_c), dtype=np.float32)
    exp60 = np.empty((n_s, n_c), dtype=np.float32)
    loc_cache: dict[str, tuple[np.ndarray, float]] = {}
    for j, cp in enumerate(cp_ids):
        a = np.sort(arr[s_cp == cp])
        rate_min = in_range[s_cp == cp].sum() / (hours * 60.0)
        gap = np.append(a, 2**62)[np.searchsorted(a, slots, "right")] - slots
        for h, mins in enumerate(horizons):
            truth = (gap <= mins * _MIN_NS).astype(float)
            base = 1.0 - np.exp(-rate_min * mins)
            p[h, :, j] = (1 - noise) * truth + noise * base + rng.normal(0, 0.1 * noise, n_s)
        loc = cp_loc[j]
        if loc not in loc_cache:
            la = np.sort(arr[s_loc == loc])
            loc_rate = (in_range[s_loc == loc]).sum() / hours
            loc_cache[loc] = (la, loc_rate)
        la, loc_rate = loc_cache[loc]
        cnt = np.searchsorted(la, slots + 60 * _MIN_NS, "right") - np.searchsorted(
            la, slots, "right"
        )
        e = (1 - noise) * cnt + noise * loc_rate + rng.normal(0, 0.5 * noise, n_s)
        exp60[:, j] = np.maximum(e, 0.0)
    p = np.clip(p, 0.001, 0.999)
    p = np.maximum.accumulate(p, axis=0)
    return pd.DataFrame(
        {
            "ts": _ts(np.repeat(slots, n_c)),
            "cp_id": pd.Categorical.from_codes(np.tile(np.arange(n_c), n_s), categories=cp_ids),
            "p_arrival_15": p[0].ravel(),
            "p_arrival_30": p[1].ravel(),
            "p_arrival_60": p[2].ravel(),
            "expected_arrivals_60": exp60.ravel(),
        }
    )


def generate(
    seed: int = 0,
    train_days: int = 14,
    test_days: int = 7,
    train_start: date = date(2023, 10, 2),
    test_start: date = date(2024, 3, 11),
    locations: pd.DataFrame | None = None,
    cps: pd.DataFrame | None = None,
) -> SimData:
    """Seeded synthetic SimData in the CONTRACTS schemas."""
    if locations is None or cps is None:
        from sim.stations_seed import load_seed_stations

        seed_locs, seed_cps = load_seed_stations()
        if locations is None and cps is None:
            locations, cps = seed_locs, seed_cps
        elif cps is None:
            cps = seed_cps[seed_cps["location_id"].isin(locations["location_id"])]
        else:
            locations = seed_locs[seed_locs["location_id"].isin(cps["location_id"])]
    locations = locations.reset_index(drop=True)
    cps = cps.reset_index(drop=True)

    train = [train_start + timedelta(days=i) for i in range(train_days)]
    test = [test_start + timedelta(days=i) for i in range(test_days)]
    days = train + test
    splits = ["train"] * len(train) + ["test"] * len(test)
    hol = _ro_holidays(days)
    r_sess, r_ctx, r_sig = _rngs(seed, 3)

    sessions = _sessions(r_sess, cps, days, splits, hol)
    counties = list(dict.fromkeys(locations["county_code"].astype(str).tolist()))
    context = _context(r_ctx, counties, days, hol)
    signals = _signals(r_sig, locations, sessions, context, days)
    forecasts = oracle_forecasts(sessions, cps, days, seed=seed)
    return SimData(
        locations=locations,
        cps=cps,
        sessions=sessions,
        signals=signals,
        context=context,
        forecasts=forecasts,
    )
