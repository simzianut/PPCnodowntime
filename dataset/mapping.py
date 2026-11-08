"""Map Dundee donor sessions onto Romanian PPC charge points (spec 01 §4).

CLI: python -m dataset.mapping [--donors dundee,cps]   ($DATASET_DONORS does the same)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import zlib
from pathlib import Path

import holidays
import numpy as np
import pandas as pd
import yaml
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO / "config" / "dataset.yaml"
SIGNALS_PATH = REPO / "config" / "signals.yaml"
COUNTY_STATS_PATH = REPO / "data" / "seed" / "county_ev_stats.csv"
GB_TZ, RO_TZ = "Europe/London", "Europe/Bucharest"
CLASSES = ["AC_22", "DC_50", "DC_HPC"]
NS_PER_DAY = 86_400 * 10**9

SESSION_COLS = [
    "session_id",
    "cp_id",
    "location_id",
    "arrival_ts",
    "departure_ts",
    "energy_kwh",
    "donor_charger_id",
    "split",
]
MAP_COLS = [
    "cp_id",
    "power_class",
    "demand",
    "demand_quantile",
    "donor_charger_id",
    "donor_fallback",
    "second_donor_id",
    "target_rate",
    "donor_rate",
    "ratio",
    "capped",
    "active_from",
    "active_to",
]


def load_config() -> dict:
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    sig = yaml.safe_load(SIGNALS_PATH.read_text(encoding="utf-8"))
    d = cfg["dundee"]
    out = {
        **cfg["mapping"],
        "start": d["start"],
        "end": d["end"],
        "split_date": d["split_date"],
        "context_multiplier": sig["context_multiplier"],
    }
    env = donor_list(os.environ.get("DATASET_DONORS"))
    if env:
        out["donors"] = env
    return out


def donor_list(raw: str | None) -> list[str]:
    """'dundee,cps' -> ['dundee', 'cps']; empty for None or blank."""
    return [d.strip() for d in (raw or "").split(",") if d.strip()]


def data_dir() -> Path:
    load_dotenv(REPO / ".env")
    return Path(os.environ.get("DATA_DIR", REPO / "data"))


def _ns(ts: pd.Series) -> np.ndarray:
    """tz-aware timestamps -> int64 ns since epoch (UTC)."""
    naive = ts.dt.tz_convert("UTC").dt.tz_localize(None)
    return naive.to_numpy().astype("datetime64[ns]").astype(np.int64)


def _from_ns(v: np.ndarray) -> pd.Series:
    return pd.Series(pd.to_datetime(v, unit="ns", utc=True)).astype("datetime64[ns, UTC]")


# ---------- demand and donor choice ----------


def demand_index(
    cps: pd.DataFrame, locations: pd.DataFrame, ev_per_1000: pd.Series, multipliers: dict
) -> pd.DataFrame:
    """demand = ev_per_1000(county) x context_multiplier(context) / CPs at the location."""
    loc = locations.set_index("location_id")[["county_code", "context_type"]]
    df = cps[["cp_id", "location_id", "power_class"]].join(loc, on="location_id")
    df["n_cps_at_location"] = df.groupby("location_id")["cp_id"].transform("size")
    ev = df["county_code"].map(ev_per_1000)
    mult = df["context_type"].map(multipliers)
    if ev.isna().any() or mult.isna().any():
        bad = df.loc[ev.isna() | mult.isna(), ["cp_id", "county_code", "context_type"]]
        raise ValueError(f"missing county EV rate or context multiplier:\n{bad.head()}")
    df["demand"] = ev.astype(float) * mult.astype(float) / df["n_cps_at_location"]
    return df.reset_index(drop=True)


def share_at_or_below(values: np.ndarray) -> np.ndarray:
    """Quantile as the share of values <= each value (same rule as donor util_quantile)."""
    v = np.asarray(values, dtype=float)
    return np.searchsorted(np.sort(v), v, side="right") / len(v)


def pooled_util_quantile(donors: pd.DataFrame) -> np.ndarray:
    """Donor util_quantile rule over a pooled table: share of train-basis chargers of the class
    at or below each charger's rate (same rule as dundee.build_chargers)."""
    q = np.full(len(donors), 0.5)
    rate = donors["sessions_per_day"].to_numpy(dtype=float)
    train = (donors["util_basis"] == "train").to_numpy()
    for idx in donors.groupby("power_class").indices.values():
        ref = np.sort(rate[idx][train[idx]])
        if len(ref):
            q[idx] = np.searchsorted(ref, rate[idx], side="right") / len(ref)
    return q


def pool_donors(
    proc: Path, donors: pd.DataFrame, donor_sessions: pd.DataFrame, extra: list[str]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Append donor_{chargers,sessions}_<name>.parquet per extra donor city and re-rank
    util_quantile per class over the union."""
    for name in extra:
        more = pd.read_parquet(proc / f"donor_chargers_{name}.parquet")
        donors = pd.concat([donors, more], ignore_index=True)
        more = pd.read_parquet(
            proc / f"donor_sessions_{name}.parquet", columns=list(donor_sessions.columns)
        )
        donor_sessions = pd.concat([donor_sessions, more], ignore_index=True)
    donors["util_quantile"] = pooled_util_quantile(donors)
    return donors, donor_sessions


def _closest(q: float, pool: pd.DataFrame, exclude: set, rng: np.random.Generator) -> str | None:
    cand = pool[~pool["donor_charger_id"].isin(exclude)]
    if cand.empty:
        return None
    dist = np.round(np.abs(cand["util_quantile"].to_numpy() - q), 12)
    key = rng.random(len(cand))
    return str(cand["donor_charger_id"].to_numpy()[np.lexsort((key, dist))[0]])


def choose_donors(dem: pd.DataFrame, donors: pd.DataFrame, seed: int) -> pd.DataFrame:
    """Closest util_quantile in class; distinct per location; HPC borrows DC_50 when exhausted."""
    rng = np.random.default_rng([seed, 1])
    dem = dem.copy()
    dem["demand_quantile"] = dem.groupby("power_class")["demand"].transform(
        lambda s: share_at_or_below(s.to_numpy())
    )
    pools = {c: donors[donors["power_class"] == c] for c in CLASSES}
    chosen: dict[str, tuple[str, bool]] = {}
    for _, grp in dem.sort_values(["location_id", "cp_id"]).groupby("location_id", sort=True):
        used: set[str] = set()
        for r in grp.itertuples(index=False):
            fallback = False
            d = _closest(r.demand_quantile, pools[r.power_class], used, rng)
            if d is None and r.power_class == "DC_HPC":
                d = _closest(r.demand_quantile, pools["DC_50"], used, rng)
                fallback = d is not None
            if d is None:
                d = _closest(r.demand_quantile, pools[r.power_class], set(), rng)
            if d is None:
                raise ValueError(f"no eligible donor for class {r.power_class}")
            used.add(d)
            chosen[r.cp_id] = (d, fallback)
    dem["donor_charger_id"] = dem["cp_id"].map(lambda c: chosen[c][0])
    dem["donor_fallback"] = dem["cp_id"].map(lambda c: chosen[c][1]).astype(bool)
    return dem


def scaling(m: pd.DataFrame, donors: pd.DataFrame, max_scale: float) -> pd.DataFrame:
    """target_rate = class median donor rate x demand / class median demand; ratio capped."""
    rate = donors.set_index("donor_charger_id")["sessions_per_day"]
    med_rate = donors.groupby("power_class")["sessions_per_day"].median().reindex(CLASSES)
    med_rate = med_rate.fillna(med_rate.get("DC_50"))
    med_dem = m.groupby("power_class")["demand"].median()
    m = m.copy()
    m["target_rate"] = m["power_class"].map(med_rate) * m["demand"] / m["power_class"].map(med_dem)
    m["donor_rate"] = m["donor_charger_id"].map(rate).astype(float)
    raw = m["target_rate"] / m["donor_rate"]
    m["capped"] = (raw > max_scale).astype(bool)
    m["ratio"] = raw.clip(upper=max_scale)
    return m


def second_donors(m: pd.DataFrame, donors: pd.DataFrame) -> pd.Series:
    """For ratio > 1: same class as the primary, rate closest to the primary's, not used at the
    location if possible; ties by id."""
    cls = donors.set_index("donor_charger_id")["power_class"]
    used_at = m.groupby("location_id")["donor_charger_id"].agg(set)
    out = pd.Series(pd.NA, index=m.index, dtype="string")
    for i, r in m[m["ratio"] > 1].iterrows():
        pool = donors[
            (donors["power_class"] == cls[r["donor_charger_id"]])
            & (donors["donor_charger_id"] != r["donor_charger_id"])
        ]
        fresh = pool[~pool["donor_charger_id"].isin(used_at[r["location_id"]])]
        pool = fresh if not fresh.empty else pool
        if pool.empty:
            continue
        dist = np.round(np.abs(pool["sessions_per_day"].to_numpy() - r["donor_rate"]), 12)
        ids = pool["donor_charger_id"].to_numpy()
        out[i] = ids[np.lexsort((ids, dist))[0]]
    return out


# ---------- calendar ----------


def wall_clock(ts_utc: pd.Series) -> pd.Series:
    """UTC -> naive Europe/London wall-clock time."""
    return ts_utc.dt.tz_convert(GB_TZ).dt.tz_localize(None)


def wall_to_ro_utc(wall: pd.Series) -> pd.Series:
    """Same wall-clock time localized in Europe/Bucharest, returned in UTC."""
    loc = wall.dt.tz_localize(
        RO_TZ, nonexistent="shift_forward", ambiguous=np.ones(len(wall), dtype=bool)
    )
    return loc.dt.tz_convert("UTC").astype("datetime64[ns, UTC]")


def holiday_calendars(start: str, end: str) -> tuple[set, set]:
    years = range(pd.Timestamp(start).year, pd.Timestamp(end).year + 1)
    ro = {pd.Timestamp(d) for d in holidays.country_holidays("RO", years=years)}
    sct = {pd.Timestamp(d) for d in holidays.country_holidays("GB", subdiv="SCT", years=years)}
    return ro, sct


def holiday_sources(lo: pd.Timestamp, hi: pd.Timestamp, ro: set, sct: set) -> dict:
    """target day -> donor day whose timeline replaces it, within the donor span [lo, hi]."""
    out = {}
    for d in sorted(ro):
        if lo <= d <= hi:
            sunday = d + pd.Timedelta(days=6 - d.weekday())
            if sunday != d:
                out[d] = sunday
    special = ro | sct
    for d in sorted(sct - ro):
        if lo <= d <= hi:
            for s in (d - pd.Timedelta(days=7), d + pd.Timedelta(days=7)):
                if lo <= s <= hi and s not in special:
                    out[d] = s
                    break
    return out


def keep_non_overlapping(arr: np.ndarray, dep: np.ndarray) -> np.ndarray:
    """Greedy in arrival order: keep a session only if it starts after the last kept one ends."""
    keep = np.zeros(len(arr), dtype=bool)
    last = np.iinfo(np.int64).min
    for i in range(len(arr)):
        if arr[i] >= last:
            keep[i] = True
            last = dep[i]
    return keep


def donor_timeline(sess: pd.DataFrame, ro: set, sct: set) -> tuple[dict, dict]:
    """One donor's sessions on the RO calendar: holiday swaps in London wall-clock time, then
    the same wall clock in Bucharest. Returns int64-ns arrays and stats."""
    wall = wall_clock(sess["arrival_ts"]).reset_index(drop=True)
    dur = _ns(sess["departure_ts"]) - _ns(sess["arrival_ts"])
    f = pd.DataFrame({"wall": wall, "dur": dur, "energy": sess["energy_kwh"].to_numpy()})
    f["day"] = f["wall"].dt.normalize()
    src = holiday_sources(f["day"].min(), f["day"].max(), ro, sct) if len(f) else {}
    is_tgt = f["day"].isin(list(src))
    parts = [f[~is_tgt]]
    if src:
        sm = pd.DataFrame({"src": list(src.values()), "tgt": list(src.keys())})
        cp = f.merge(sm, left_on="day", right_on="src")
        cp["wall"] = cp["wall"] + (cp["tgt"] - cp["src"])
        parts.append(cp[f.columns])
    tl = pd.concat(parts, ignore_index=True)
    arr = _ns(wall_to_ro_utc(tl["wall"]))
    dep = arr + tl["dur"].to_numpy(dtype=np.int64)
    order = np.argsort(arr, kind="stable")
    arr, dep, energy = arr[order], dep[order], tl["energy"].to_numpy(dtype=float)[order]
    keep = keep_non_overlapping(arr, dep)
    stats = {
        "days_swapped": len(src),
        "sessions_removed_on_swapped_days": int(is_tgt.sum()),
        "sessions_copied_in": int(len(tl) - (~is_tgt).sum()),
        "overlap_dropped": int((~keep).sum()),
    }
    return {"arr": arr[keep], "dep": dep[keep], "energy": energy[keep]}, stats


# ---------- scaling of a timeline ----------


def thin_mask(n: int, ratio: float, rng: np.random.Generator) -> np.ndarray:
    """Keep each session with probability ratio."""
    return rng.random(n) < ratio


def top_up_mask(
    p_arr: np.ndarray,
    p_dep: np.ndarray,
    c_arr: np.ndarray,
    c_dep: np.ndarray,
    prob: float,
    rng: np.random.Generator,
) -> tuple[np.ndarray, int]:
    """Draw candidates with probability prob; skip those overlapping a primary session.
    Primary and candidates are each sorted and non-overlapping."""
    drawn = rng.random(len(c_arr)) < prob
    if len(p_arr) == 0:
        return drawn, 0
    idx = np.searchsorted(p_arr, c_dep, side="left")
    overlap = (idx > 0) & (p_dep[np.maximum(idx - 1, 0)] > c_arr)
    return drawn & ~overlap, int((drawn & overlap).sum())


def declone(s: pd.DataFrame, seed: int, jitter_min: float, start: str, end: str):
    """Shift each CP's sessions by an offset U(-j, +j) min drawn per CP and Bucharest local day,
    then drop overlaps keeping the earliest. Returns (sessions, n_dropped)."""
    if not jitter_min or s.empty:
        return s, 0
    s = s.sort_values(["cp_id", "arrival_ts"], kind="stable").reset_index(drop=True)
    base = pd.Timestamp(start) - pd.Timedelta(days=2)
    n_days = (pd.Timestamp(end) - base).days + 4
    local = s["arrival_ts"].dt.tz_convert(RO_TZ).dt.tz_localize(None).dt.normalize()
    day = np.clip((local - base).dt.days.to_numpy(), 0, n_days - 1)
    arr, dep = _ns(s["arrival_ts"]), _ns(s["departure_ts"])
    keep = np.zeros(len(s), dtype=bool)
    for cp, idx in s.groupby("cp_id", sort=True).indices.items():
        rng = np.random.default_rng([seed, zlib.crc32(cp.encode()), 3])
        draws = (rng.uniform(-jitter_min, jitter_min, n_days) * 60e9).astype(np.int64)
        off = draws[day[idx]]
        a, d = arr[idx] + off, dep[idx] + off
        order = np.argsort(a, kind="stable")
        k = np.zeros(len(idx), dtype=bool)
        k[order] = keep_non_overlapping(a[order], d[order])
        arr[idx], dep[idx], keep[idx] = a, d, k
    s = s.assign(arrival_ts=_from_ns(arr), departure_ts=_from_ns(dep))[keep]
    return s.reset_index(drop=True), int((~keep).sum())


def clone_groups(s: pd.DataFrame) -> int:
    """Groups of 2+ CPs with identical arrival timelines."""
    h = s.groupby("cp_id")["arrival_ts"].agg(
        lambda x: hashlib.sha1(np.sort(_ns(x)).tobytes()).hexdigest()
    )
    sizes = h.value_counts()
    return int((sizes > 1).sum())


def active_spans(m: pd.DataFrame, donors: pd.DataFrame) -> pd.DataFrame:
    """Primary donor's first_ts..last_ts on the RO calendar (same wall clock, UTC)."""
    span = donors.set_index("donor_charger_id")
    m = m.copy()
    for col, src in (("active_from", "first_ts"), ("active_to", "last_ts")):
        ts = m["donor_charger_id"].map(span[src]).astype("datetime64[ns, UTC]")
        m[col] = wall_to_ro_utc(wall_clock(ts)).to_numpy()
    return m


def active_days(m: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """cp_id, date (Bucharest local), active: False = donor did not exist (unknown, not zero)."""
    dates = pd.date_range(start, end, freq="D")
    lo = m["active_from"].dt.tz_convert(RO_TZ).dt.tz_localize(None).dt.normalize().to_numpy()
    hi = m["active_to"].dt.tz_convert(RO_TZ).dt.tz_localize(None).dt.normalize().to_numpy()
    d = np.tile(dates.to_numpy(), len(m))
    out = pd.DataFrame(
        {
            "cp_id": np.repeat(m["cp_id"].to_numpy(), len(dates)).astype(str),
            "date": pd.Series(d).dt.date,
            "active": (d >= np.repeat(lo, len(dates))) & (d <= np.repeat(hi, len(dates))),
        }
    )
    return out


def assign_split(arrival_utc: pd.Series, split_date: str) -> np.ndarray:
    local_day = arrival_utc.dt.tz_convert(RO_TZ).dt.tz_localize(None).dt.normalize()
    return np.where(local_day <= pd.Timestamp(split_date), "train", "test")


def validate(sessions: pd.DataFrame, cp_ids) -> None:
    assert (sessions["departure_ts"] > sessions["arrival_ts"]).all(), "departure <= arrival"
    assert sessions["cp_id"].isin(set(cp_ids)).all(), "unknown cp_id"
    s = sessions.sort_values(["cp_id", "arrival_ts"])
    ids = s["cp_id"].to_numpy()
    arr, dep = _ns(s["arrival_ts"]), _ns(s["departure_ts"])
    bad = (ids[1:] == ids[:-1]) & (arr[1:] < dep[:-1])
    assert not bad.any(), f"{int(bad.sum())} overlapping sessions on a CP"


# ---------- orchestration ----------


def map_sessions(
    cps: pd.DataFrame,
    locations: pd.DataFrame,
    ev_per_1000: pd.Series,
    donors: pd.DataFrame,
    donor_sessions: pd.DataFrame,
    cfg: dict,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Returns (sessions, cp_donor_map, stats)."""
    seed = int(cfg["seed"])
    elig = donors[donors["sessions_per_day"] > cfg.get("min_donor_sessions_per_day", 0.0)]
    dem = demand_index(cps, locations, ev_per_1000, cfg["context_multiplier"])
    m = choose_donors(dem, elig, seed)
    m = scaling(m, elig, float(cfg["max_scale"]))
    m["second_donor_id"] = second_donors(m, elig)
    m = active_spans(m, elig)

    ro, sct = holiday_calendars(cfg["start"], cfg["end"])
    needed = set(m["donor_charger_id"]) | set(m["second_donor_id"].dropna())
    ds = donor_sessions[donor_sessions["donor_charger_id"].isin(needed)]
    timelines, cal = {}, {}
    for did, g in ds.sort_values(["donor_charger_id", "arrival_ts"]).groupby("donor_charger_id"):
        timelines[did], cal[did] = donor_timeline(g, ro, sct)
    empty = {"arr": np.array([], np.int64), "dep": np.array([], np.int64), "energy": np.array([])}

    rng = np.random.default_rng([seed, 2])
    cols = {k: [] for k in ("cp", "loc", "arr", "dep", "energy", "donor")}
    topup_skipped, achieved = 0, {}
    for r in m.sort_values("cp_id").itertuples(index=False):
        p = timelines.get(r.donor_charger_id, empty)
        parts = []
        if r.ratio < 1:
            k = thin_mask(len(p["arr"]), r.ratio, rng)
            parts.append((p, k, r.donor_charger_id))
        else:
            parts.append((p, np.ones(len(p["arr"]), bool), r.donor_charger_id))
            if r.ratio > 1 and not pd.isna(r.second_donor_id):
                c = timelines.get(r.second_donor_id, empty)
                k, skipped = top_up_mask(
                    p["arr"], p["dep"], c["arr"], c["dep"], min(r.ratio - 1, 1.0), rng
                )
                topup_skipped += skipped
                parts.append((c, k, r.second_donor_id))
        n = 0
        for tl, k, did in parts:
            cnt = int(k.sum())
            n += cnt
            cols["arr"].append(tl["arr"][k])
            cols["dep"].append(tl["dep"][k])
            cols["energy"].append(tl["energy"][k])
            cols["donor"].append(np.full(cnt, did, dtype=object))
            cols["cp"].append(np.full(cnt, r.cp_id, dtype=object))
            cols["loc"].append(np.full(cnt, r.location_id, dtype=object))
        achieved[r.cp_id] = n / len(p["arr"]) if len(p["arr"]) else np.nan

    s = pd.DataFrame(
        {
            "cp_id": np.concatenate(cols["cp"]).astype(str),
            "location_id": np.concatenate(cols["loc"]).astype(str),
            "arrival_ts": _from_ns(np.concatenate(cols["arr"])),
            "departure_ts": _from_ns(np.concatenate(cols["dep"])),
            "energy_kwh": np.concatenate(cols["energy"]).astype(float),
            "donor_charger_id": np.concatenate(cols["donor"]).astype(str),
        }
    )
    s, jitter_dropped = declone(
        s, seed, float(cfg.get("declone_jitter_min", 0)), cfg["start"], cfg["end"]
    )
    s = s.sort_values(["cp_id", "arrival_ts"], kind="stable").reset_index(drop=True)
    width = int(cfg.get("session_id_width", 8))
    s.insert(0, "session_id", [f"S{i:0{width}d}" for i in range(1, len(s) + 1)])
    s["split"] = assign_split(s["arrival_ts"], cfg["split_date"])
    validate(s, cps["cp_id"])

    m["achieved_ratio"] = m["cp_id"].map(achieved)
    cal_df = pd.DataFrame(cal).T
    stats = {
        "calendar": {k: int(cal_df[k].sum()) for k in cal_df.columns} if len(cal_df) else {},
        "topup_skipped_overlap": topup_skipped,
        "declone_overlap_dropped": jitter_dropped,
        "clone_groups_remaining": clone_groups(s),
    }
    return s[SESSION_COLS], m, stats


def arrival_shapes(
    s: pd.DataFrame, m: pd.DataFrame, donor_sessions: pd.DataFrame, donors: pd.DataFrame, cfg
) -> dict[str, pd.DataFrame]:
    """Mean RO arrivals per local hour-of-week per CP per week vs the class donor shape,
    scaled to the same total."""
    weeks = ((pd.Timestamp(cfg["end"]) - pd.Timestamp(cfg["start"])).days + 1) / 7
    cls_of = m.set_index("cp_id")["power_class"]
    dcls = donors.set_index("donor_charger_id")["power_class"]
    out = {}
    for c in CLASSES:
        n_cps = int((m["power_class"] == c).sum())
        if n_cps == 0:
            continue
        loc = s.loc[s["cp_id"].map(cls_of) == c, "arrival_ts"].dt.tz_convert(RO_TZ)
        ro = np.bincount(loc.dt.day_of_week * 24 + loc.dt.hour, minlength=168) / (n_cps * weeks)
        dl = donor_sessions.loc[donor_sessions["donor_charger_id"].map(dcls) == c, "arrival_ts"]
        dl = dl.dt.tz_convert(GB_TZ)
        dc = np.bincount(dl.dt.day_of_week * 24 + dl.dt.hour, minlength=168).astype(float)
        donor = dc / dc.sum() * ro.sum() if dc.sum() else np.zeros(168)
        out[c] = pd.DataFrame(
            {
                "hour_of_week": np.arange(168),
                "ro_arrivals_per_cp_week": ro,
                "donor_shape_scaled": donor,
            }
        )
    return out


def write_shapes(shapes: dict[str, pd.DataFrame], rep_dir: Path) -> dict:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        plt = None
    res = {}
    for c, df in shapes.items():
        df.to_csv(rep_dir / f"arrivals_by_how_{c}.csv", index=False)
        a, b = df["ro_arrivals_per_cp_week"], df["donor_shape_scaled"]
        corr = float(np.corrcoef(a, b)[0, 1]) if a.std() > 0 and b.std() > 0 else None
        res[c] = {
            "pearson_r": corr,
            "max_abs_diff_share": float((a / a.sum() - b / b.sum()).abs().max())
            if a.sum()
            else None,
            "peak_how_ro": int(a.idxmax()),
            "peak_how_donor": int(b.idxmax()),
            "output": "png+csv" if plt else "csv (matplotlib not installed)",
        }
        if plt:
            fig, ax = plt.subplots(figsize=(12, 3.5))
            ax.plot(df["hour_of_week"], a, label="RO mapped (Bucharest local)")
            ax.plot(df["hour_of_week"], b, label="Dundee donor shape, scaled", alpha=0.7)
            ax.set_xticks(range(0, 168, 24), ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
            ax.set_ylabel("arrivals / CP / week")
            ax.set_title(f"Arrivals by hour of week: {c}")
            ax.legend()
            fig.tight_layout()
            fig.savefig(rep_dir / f"arrivals_by_how_{c}.png", dpi=110)
            plt.close(fig)
    return res


def _per_day(counts: pd.Series, days: int) -> dict:
    v = counts / days
    q = v.quantile([0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0])
    return {f"p{int(k * 100)}": round(float(x), 4) for k, x in q.items()} | {
        "mean": round(float(v.mean()), 4)
    }


def build_report(s: pd.DataFrame, m: pd.DataFrame, stats: dict, cfg: dict) -> dict:
    split_day = pd.Timestamp(cfg["split_date"])
    days_all = (pd.Timestamp(cfg["end"]) - pd.Timestamp(cfg["start"])).days + 1
    days_test = (pd.Timestamp(cfg["end"]) - split_day).days
    n_all = s.groupby("cp_id").size().reindex(m["cp_id"], fill_value=0)
    n_test = s[s["split"] == "test"].groupby("cp_id").size().reindex(m["cp_id"], fill_value=0)
    mm = m.set_index("cp_id").assign(per_day_all=n_all / days_all, per_day_test=n_test / days_test)
    cls = mm["power_class"]
    by_cs = s.assign(power_class=s["cp_id"].map(cls)).groupby(["power_class", "split"]).size()
    county = (
        mm.groupby("county_code")
        .agg(n_cps=("power_class", "size"), sessions_per_cp_per_day=("per_day_test", "mean"))
        .sort_values("sessions_per_cp_per_day")
        .round(4)
    )
    cp_cols = ["power_class", "county_code", "context_type", "n_cps_at_location"]
    cp_cols += ["donor_charger_id", "per_day_test"]
    ranked = mm.sort_values("per_day_test")
    thinned = mm[mm["ratio"] < 1]
    topped = mm[mm["ratio"] > 1]
    first = s.groupby("cp_id")["arrival_ts"].min().reindex(m["cp_id"])
    late = first > pd.Timestamp(cfg["start"], tz=RO_TZ) + pd.Timedelta(days=60)
    return {
        "sessions_total": len(s),
        "sessions_by_class_split": {f"{a}/{b}": int(v) for (a, b), v in by_cs.items()},
        "cps_by_class": cls.value_counts().to_dict(),
        "sessions_per_cp_per_day_full_range": {
            c: _per_day(n_all[cls == c], days_all) for c in CLASSES if (cls == c).any()
        },
        "sessions_per_cp_per_day_test": {
            c: _per_day(n_test[cls == c], days_test) for c in CLASSES if (cls == c).any()
        },
        "days_full_range": days_all,
        "days_test": days_test,
        "donor_fallback_count": int(mm["donor_fallback"].sum()),
        "capped_count": int(mm["capped"].sum()),
        "capped_by_class": mm[mm["capped"]]["power_class"].value_counts().to_dict(),
        "ratio_gt_2_count": int((mm["ratio"] > 2).sum()),
        "thinned_cps": len(thinned),
        "topped_up_cps": len(topped),
        "achieved_over_ratio_median_thinned": float(
            (thinned["achieved_ratio"] / thinned["ratio"]).median()
        ),
        "achieved_over_ratio_median_topped": float(
            (topped["achieved_ratio"] / topped["ratio"]).median()
        ),
        "topup_skipped_overlap": stats["topup_skipped_overlap"],
        "declone_overlap_dropped": stats["declone_overlap_dropped"],
        "clone_groups_remaining": stats["clone_groups_remaining"],
        "calendar": stats["calendar"],
        "distinct_primary_donors_used": int(m["donor_charger_id"].nunique()),
        "cps_without_sessions_in_first_60_days": int(late.sum()),
        "cps_without_sessions_in_first_60_days_by_class": mm[late.to_numpy()]["power_class"]
        .value_counts()
        .to_dict(),
        "cps_without_train_sessions": int(((n_all - n_test) == 0).sum()),
        "cps_without_test_sessions": int((n_test == 0).sum()),
        "top_counties": county.tail(5)[::-1].reset_index().to_dict("records"),
        "bottom_counties": county.head(5).reset_index().to_dict("records"),
        "busiest_cps": ranked.tail(3)[::-1][cp_cols].round(4).reset_index().to_dict("records"),
        "quietest_cps": ranked.head(3)[cp_cols].round(4).reset_index().to_dict("records"),
    }


def build(out_root: Path | None = None, cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    root = out_root or data_dir()
    proc = root / "processed"
    rep_dir = proc / "reports"
    rep_dir.mkdir(parents=True, exist_ok=True)
    cps = pd.read_parquet(proc / "charge_points.parquet")
    locations = pd.read_parquet(proc / "locations.parquet")
    donors = pd.read_parquet(proc / "donor_chargers.parquet")
    donor_sessions = pd.read_parquet(
        proc / "donor_sessions.parquet",
        columns=["donor_charger_id", "arrival_ts", "departure_ts", "energy_kwh"],
    )
    ev = pd.read_csv(COUNTY_STATS_PATH, dtype={"county_code": str}).set_index("county_code")
    extra = [d for d in cfg.get("donors", ["dundee"]) if d != "dundee"]
    if extra:
        donors, donor_sessions = pool_donors(proc, donors, donor_sessions, extra)

    s, m, stats = map_sessions(cps, locations, ev["ev_per_1000"], donors, donor_sessions, cfg)
    s.to_parquet(proc / "sessions.parquet", index=False)
    m[MAP_COLS].to_parquet(proc / "cp_donor_map.parquet", index=False)
    days = active_days(m, cfg["start"], cfg["end"])
    days.to_parquet(proc / "cp_active_days.parquet", index=False)

    report = build_report(s, m, stats, cfg)
    cls = days["cp_id"].map(m.set_index("cp_id")["power_class"])
    report["inactive_cp_day_share"] = (~days["active"]).groupby(cls).mean().round(4).to_dict()
    elig = donors[donors["sessions_per_day"] > cfg.get("min_donor_sessions_per_day", 0.0)]
    report["arrival_shape"] = write_shapes(arrival_shapes(s, m, donor_sessions, elig, cfg), rep_dir)
    if extra:
        report["donor_pool"] = cfg["donors"]
    (rep_dir / "mapping_report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    return report


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m dataset.mapping")
    ap.add_argument(
        "--donors", default=None, help="comma list overriding mapping.donors, e.g. dundee,cps"
    )
    cfg = load_config()
    cli = donor_list(ap.parse_args(argv).donors)
    if cli:
        cfg["donors"] = cli
    print(json.dumps(build(cfg=cfg), indent=2, default=str))


if __name__ == "__main__":
    main()
