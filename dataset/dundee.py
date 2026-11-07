"""Dundee donor ingest: download, clean, lanes, chargers, weather, holidays (spec 01 §3)."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from pathlib import Path

import holidays
import numpy as np
import pandas as pd
import yaml

from dataset import weather

REPO = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO / "config" / "dataset.yaml"
TZ = "Europe/London"

SESSION_COLS = [
    "donor_session_id",
    "donor_charger_id",
    "arrival_ts",
    "departure_ts",
    "energy_kwh",
    "temp_c",
    "precip_mm",
    "wind_kmh",
    "is_holiday_gb",
    "split",
]
CHARGER_COLS = [
    "donor_charger_id",
    "site",
    "power_class",
    "sessions_per_day",
    "util_quantile",
    "base_cp_id",
    "lane",
    "n_sessions_train",
    "active_days_train",
    "first_ts",
    "last_ts",
    "util_basis",
]


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))["dundee"]


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "./data"))


def download(cfg: dict, raw_dir: Path) -> list[Path]:
    """Fetch each CSV that is not already present, pausing between requests."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    out, fetched = [], 0
    for f in cfg["files"]:
        dest = raw_dir / f["name"]
        out.append(dest)
        if dest.exists() and dest.stat().st_size > 0:
            print(f"skip {dest.name} (exists)")
            continue
        if fetched:
            time.sleep(cfg["download_delay_s"])
        req = urllib.request.Request(
            cfg["base_url"].format(item_id=f["item_id"]),
            headers={"User-Agent": cfg["user_agent"]},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read()
        dest.write_bytes(body)
        fetched += 1
        print(f"downloaded {dest.name} ({len(body)} bytes)")
    return out


def load_raw(raw_dir: Path, cfg: dict) -> pd.DataFrame:
    frames = [
        pd.read_csv(raw_dir / f["name"], dtype=str, skipinitialspace=True) for f in cfg["files"]
    ]
    df = pd.concat(frames, ignore_index=True)
    df.columns = [c.strip() for c in df.columns]
    return df


def _local_to_utc(s: pd.Series) -> pd.Series:
    naive = pd.to_datetime(s, format="%d/%m/%Y %H:%M", errors="coerce")
    ok = naive.notna()
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns, UTC]")
    loc = naive[ok].dt.tz_localize(
        TZ, nonexistent="shift_forward", ambiguous=np.ones(int(ok.sum()), dtype=bool)
    )
    out[ok] = loc.dt.tz_convert("UTC")
    return out


def clean(raw: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, dict]:
    """Apply the cleaning rules in order. Returns the clean frame and per-rule drop counts."""
    dropped: dict[str, int] = {}
    df = pd.DataFrame(
        {
            "sdr_id": raw["SDR ID"].str.strip(),
            "site": raw["Site"].str.strip(),
            "cp_id": raw["CP ID"].str.strip(),
            "connector": raw["Connector Type"].str.strip().str.lower(),
            "kwh": pd.to_numeric(raw["Consum(kWh)"], errors="coerce"),
            "dur": pd.to_timedelta(raw["Duration"].str.strip(), errors="coerce"),
            "arrival_ts": _local_to_utc(raw["Start"]),
            "end_ts": _local_to_utc(raw["End"]),
        }
    )
    n = len(df)

    def drop(mask: pd.Series, rule: str) -> None:
        nonlocal df
        dropped[rule] = int(mask.sum())
        df = df[~mask]

    drop(df["arrival_ts"].isna() | df["cp_id"].isna() | df["sdr_id"].isna(), "unparseable_start")
    drop(df.duplicated("sdr_id", keep="first"), "duplicate_sdr_id")
    drop(~(df["kwh"] > 0), "kwh_blank_or_nonpositive")
    drop(df["end_ts"].notna() & (df["end_ts"] < df["arrival_ts"]), "end_before_start")
    dep = df["arrival_ts"] + df["dur"]
    dep = dep.where(df["dur"].notna(), df["end_ts"])
    df = df.assign(departure_ts=dep)
    drop(df["departure_ts"].isna(), "no_usable_departure")
    drop(df["departure_ts"] <= df["arrival_ts"], "departure_not_after_arrival")
    secs = (df["departure_ts"] - df["arrival_ts"]).dt.total_seconds()
    drop(secs < cfg["min_session_s"], "duration_too_short")
    secs = (df["departure_ts"] - df["arrival_ts"]).dt.total_seconds()
    drop(secs > cfg["max_session_s"], "duration_too_long")

    df = df.sort_values(["cp_id", "arrival_ts", "sdr_id"]).reset_index(drop=True)
    report = {"rows_in": n, "dropped": dropped, "rows_out": len(df)}
    return df, report


def assign_lanes(df: pd.DataFrame, max_lanes: int | None = None) -> tuple[pd.DataFrame, int]:
    """Greedy interval partitioning per CP; lanes start at 1. Sessions with no free lane drop."""
    df = df.sort_values(["cp_id", "arrival_ts", "sdr_id"]).reset_index(drop=True)
    arr = df["arrival_ts"].astype("int64").to_numpy()
    dep = df["departure_ts"].astype("int64").to_numpy()
    cps = df["cp_id"].to_numpy()
    lanes = np.zeros(len(df), dtype=int)
    start = 0
    for i in range(1, len(df) + 1):
        if i == len(df) or cps[i] != cps[start]:
            free_at: list[int] = []
            for j in range(start, i):
                for k, t in enumerate(free_at):
                    if t <= arr[j]:
                        free_at[k] = dep[j]
                        lanes[j] = k + 1
                        break
                else:
                    if max_lanes is None or len(free_at) < max_lanes:
                        free_at.append(dep[j])
                        lanes[j] = len(free_at)
            start = i
    df["lane"] = lanes
    n_dropped = int((lanes == 0).sum())
    df = df[df["lane"] > 0].reset_index(drop=True)
    df["donor_charger_id"] = df["cp_id"] + "#" + df["lane"].astype(str)
    assert_no_overlap(df)
    return df, n_dropped


def assert_no_overlap(df: pd.DataFrame) -> None:
    d = df.sort_values(["donor_charger_id", "arrival_ts"])
    ids = d["donor_charger_id"].to_numpy()
    arr = d["arrival_ts"].astype("int64").to_numpy()
    dep = d["departure_ts"].astype("int64").to_numpy()
    bad = (ids[1:] == ids[:-1]) & (arr[1:] < dep[:-1])
    assert not bad.any(), "overlapping sessions within a lane"


def add_split(df: pd.DataFrame, split_date: str) -> pd.DataFrame:
    local_date = df["arrival_ts"].dt.tz_convert(TZ).dt.date
    df["local_date"] = local_date
    df["split"] = np.where(local_date <= pd.Timestamp(split_date).date(), "train", "test")
    return df


def add_context(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    w = weather.fetch_hourly(cfg["lat"], cfg["lon"], cfg["start"], cfg["end"], "dundee")
    w = w[["ts_hour", "temp_c", "precip_mm", "wind_kmh"]].drop_duplicates("ts_hour")
    w["ts_hour"] = w["ts_hour"].astype("datetime64[ns, UTC]")
    df["ts_hour"] = df["arrival_ts"].dt.floor("h").astype("datetime64[ns, UTC]")
    df = df.merge(w, on="ts_hour", how="left")
    hol = holidays.country_holidays("GB", subdiv="SCT")
    flags = {d: d in hol for d in df["local_date"].unique()}
    df["is_holiday_gb"] = df["local_date"].map(flags).astype(bool)
    return df


def build_chargers(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    cp = df.groupby("cp_id").agg(
        site=("site", lambda s: s.value_counts().index[0]),
        connector=("connector", lambda s: s.value_counts().index[0]),
    )
    cp["power_class"] = cp["connector"].map(cfg["power_class"])
    lanes = df.drop_duplicates("donor_charger_id")[["donor_charger_id", "cp_id", "lane"]]
    lanes = lanes.set_index("donor_charger_id")
    per = {}
    for basis in ("train", "test"):
        sub = df[df["split"] == basis]
        span = sub.groupby("cp_id")["local_date"].agg(["min", "max"])
        days = pd.Series(
            [(b - a).days + 1 for a, b in zip(span["min"], span["max"], strict=True)], span.index
        )
        per[basis] = (sub.groupby("donor_charger_id").size(), days)
    ch = lanes.copy()
    has_train = ch["cp_id"].isin(per["train"][1].index)
    ch["util_basis"] = np.where(has_train, "train", "test")
    n_tr = per["train"][0].reindex(ch.index).fillna(0).astype(int)
    n_te = per["test"][0].reindex(ch.index).fillna(0).astype(int)
    d_tr = ch["cp_id"].map(per["train"][1])
    d_te = ch["cp_id"].map(per["test"][1])
    is_train = ch["util_basis"] == "train"
    ch["n_sessions_train"] = n_tr
    ch["active_days_train"] = d_tr.where(is_train, np.nan)
    ch["sessions_per_day"] = np.where(is_train, n_tr / d_tr, n_te / d_te)
    ch = ch.join(cp, on="cp_id").reset_index()
    # quantile = share of training-basis chargers in the class at or below this rate
    ref = ch[ch["util_basis"] == "train"]
    q = []
    for pc, v in zip(ch["power_class"], ch["sessions_per_day"], strict=True):
        r = ref.loc[ref["power_class"] == pc, "sessions_per_day"].to_numpy()
        q.append(float((r <= v).mean()) if len(r) else 0.5)
    ch["util_quantile"] = q
    span_all = df.groupby("donor_charger_id")["arrival_ts"].agg(first_ts="min", last_ts="max")
    ch = ch.join(span_all, on="donor_charger_id").rename(columns={"cp_id": "base_cp_id"})
    return ch.sort_values("donor_charger_id").reset_index(drop=True)[CHARGER_COLS]


def build_occupancy(df: pd.DataFrame) -> pd.DataFrame:
    """Fraction of each local hour-of-week occupied, averaged over training weeks."""
    tr = df[df["split"] == "train"]
    cols = ["donor_charger_id", "hour_of_week", "occupancy_share"]
    if tr.empty:
        return pd.DataFrame(columns=cols)
    a = tr["arrival_ts"].astype("int64").to_numpy() // 10**9
    d = tr["departure_ts"].astype("int64").to_numpy() // 10**9
    fb, lb = a // 3600, (d - 1) // 3600
    cnt = lb - fb + 1
    idx = np.repeat(np.arange(len(tr)), cnt)
    bins = np.repeat(fb, cnt) + (np.arange(cnt.sum()) - np.repeat(np.cumsum(cnt) - cnt, cnt))
    sec = np.minimum(d[idx], (bins + 1) * 3600) - np.maximum(a[idx], bins * 3600)
    loc = pd.to_datetime(bins * 3600, unit="s", utc=True).tz_convert(TZ)
    occ = pd.DataFrame(
        {
            "donor_charger_id": tr["donor_charger_id"].to_numpy()[idx],
            "hour_of_week": np.asarray(loc.day_of_week * 24 + loc.hour),
            "sec": sec,
        }
    )
    occ = occ.groupby(["donor_charger_id", "hour_of_week"], as_index=False)["sec"].sum()
    occ["cp_id"] = occ["donor_charger_id"].str.rsplit("#", n=1).str[0]
    # count of each hour-of-week inside the CP's active training span
    span = tr.groupby("cp_id")["arrival_ts"].agg(["min", "max"])
    weeks = {}
    for cp_id, r in span.iterrows():
        hrs = pd.date_range(r["min"].floor("h"), r["max"].ceil("h"), freq="h", inclusive="left")
        hl = hrs.tz_convert(TZ)
        weeks[cp_id] = np.bincount(np.asarray(hl.day_of_week * 24 + hl.hour), minlength=168)
    denom = np.array([weeks[c][h] for c, h in zip(occ["cp_id"], occ["hour_of_week"], strict=True)])
    occ["occupancy_share"] = np.clip(occ["sec"] / (3600 * np.maximum(denom, 1)), 0, 1)
    return occ[cols]


def build(out_root: Path | None = None, cfg: dict | None = None, raw_dir: Path | None = None):
    cfg = cfg or load_config()
    root = out_root or data_dir()
    raw_dir = raw_dir or root / "raw" / "dundee"
    proc = root / "processed"
    (proc / "reports").mkdir(parents=True, exist_ok=True)

    df, report = clean(load_raw(raw_dir, cfg), cfg)
    df, n_lane_drop = assign_lanes(df, cfg.get("max_lanes"))
    report["dropped"]["overlap_beyond_max_lanes"] = n_lane_drop
    report["rows_out"] = len(df)
    df = add_split(df, cfg["split_date"])
    df = add_context(df, cfg)
    df = df.rename(columns={"sdr_id": "donor_session_id", "kwh": "energy_kwh"})

    sessions = df.sort_values(["donor_charger_id", "arrival_ts"]).reset_index(drop=True)
    chargers = build_chargers(sessions, cfg)
    occupancy = build_occupancy(sessions)
    sessions[SESSION_COLS].to_parquet(proc / "donor_sessions.parquet", index=False)
    chargers.to_parquet(proc / "donor_chargers.parquet", index=False)
    occupancy.to_parquet(proc / "donor_occupancy.parquet", index=False)

    lanes = sessions.groupby("cp_id")["lane"].max()
    report.update(
        {
            "lanes_per_cp": {k: int(v) for k, v in lanes.items()},
            "chargers_per_class": chargers["power_class"].value_counts().to_dict(),
            "sessions_per_split": sessions["split"].value_counts().to_dict(),
            "util_basis_test_chargers": int((chargers["util_basis"] == "test").sum()),
            "weather_coverage": float(sessions["temp_c"].notna().mean()),
        }
    )
    (proc / "reports" / "dundee_cleaning.json").write_text(json.dumps(report, indent=2))
    return report


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m dataset.dundee")
    p.add_argument("command", choices=["download", "build"])
    args = p.parse_args(argv)
    cfg = load_config()
    if args.command == "download":
        download(cfg, data_dir() / "raw" / "dundee")
    else:
        rep = build(cfg=cfg)
        print(json.dumps({k: v for k, v in rep.items() if k != "lanes_per_cp"}, indent=2))


if __name__ == "__main__":
    main()
