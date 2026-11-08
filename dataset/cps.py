"""ChargePlace Scotland donor ingest (Zenodo record 19645933, CC BY 4.0).

Second donor city next to Dundee. Writes donor_sessions_cps / donor_chargers_cps /
donor_occupancy_cps in the Dundee schema, with every id prefixed `cps:`.
CLI: python -m dataset.cps download | build
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import time
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from dataset import dundee
from dataset.dundee import CHARGER_COLS, SESSION_COLS, TZ

PREFIX = "cps:"
RAW_COLS = ["CPID", "Consumed(kWh)", "Duration", "Start", "Time"]
SHARED_KEYS = ("lat", "lon", "start", "end", "split_date")


def load_config() -> dict:
    cfg = yaml.safe_load(dundee.CONFIG_PATH.read_text(encoding="utf-8"))
    shared = {k: cfg["dundee"][k] for k in SHARED_KEYS}
    return {**shared, **cfg["cps"]}


def download(cfg: dict, raw_dir: Path) -> list[Path]:
    """Fetch each zip that is not already present and unpack it into raw_dir."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for i, f in enumerate(cfg["files"]):
        dest = raw_dir / f["name"]
        if dest.exists() and dest.stat().st_size > 0:
            print(f"skip {dest.name} (exists)")
        else:
            if i:
                time.sleep(cfg["download_delay_s"])
            req = urllib.request.Request(f["url"], headers={"User-Agent": cfg["user_agent"]})
            with urllib.request.urlopen(req, timeout=600) as resp, open(dest, "wb") as fh:
                shutil.copyfileobj(resp, fh)
            print(f"downloaded {dest.name} ({dest.stat().st_size} bytes)")
        with zipfile.ZipFile(dest) as z:
            safe = [n for n in z.namelist() if not (n.startswith("/") or ".." in n)]
            z.extractall(raw_dir, members=safe)
        out.append(dest)
    return out


# ---------- raw parsing ----------


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, dtype=str, skipinitialspace=True)
    else:
        df = pd.read_excel(path, engine="calamine")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def load_raw(raw_dir: Path, cfg: dict) -> pd.DataFrame:
    """All monthly files, by column name (the column order differs between files)."""
    folder = raw_dir / cfg["sessions_dir"]
    paths = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".xlsx", ".csv"))
    if not paths:
        raise FileNotFoundError(f"no session files in {folder}; run python -m dataset.cps download")
    frames = []
    for p in paths:
        d = _read_table(p)[RAW_COLS]
        d.insert(0, "sdr_id", [f"{PREFIX}{p.stem}:{i}" for i in range(len(d))])
        frames.append(d)
    return pd.concat(frames, ignore_index=True)


def _norm_cpid(x) -> str | None:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    if isinstance(x, (int, float)):
        return str(int(x))
    s = re.sub(r"\.0$", "", str(x).strip())
    return s or None


def _strip_prefix(cpid: str) -> str:
    """'APT51423' and '51423' are the same charger under two back-office prefixes."""
    return re.sub(r"^[A-Za-z]+", "", cpid)


def _clock_str(x) -> str:
    if isinstance(x, dt.time):
        return x.strftime("%H:%M:%S")
    s = str(x).strip()
    return s + ":00" if len(s) == 5 else s


def _naive_local(start: pd.Series, clock: pd.Series) -> pd.Series:
    date = pd.to_datetime(start.astype(str).str.slice(0, 10), format="%Y-%m-%d", errors="coerce")
    return date + pd.to_timedelta(clock.map(_clock_str), errors="coerce")


def _localize_utc(naive: pd.Series) -> pd.Series:
    ok = naive.notna()
    out = pd.Series(pd.NaT, index=naive.index, dtype="datetime64[ns, UTC]")
    loc = naive[ok].dt.tz_localize(
        TZ, nonexistent="shift_forward", ambiguous=np.ones(int(ok.sum()), dtype=bool)
    )
    out[ok] = loc.dt.tz_convert("UTC")
    return out


def _duration_s(dur: pd.Series) -> pd.Series:
    """Seconds from Excel time/timedelta cells, 'HH:MM:SS' / 'N days, HH:MM:SS' strings and
    bare numbers (seconds, as in Sep24). Anything else becomes NaN."""
    out = pd.Series(np.nan, index=dur.index, dtype=float)
    is_cell = dur.map(lambda x: isinstance(x, (dt.time, dt.timedelta)))
    out[is_cell] = dur[is_cell].map(
        lambda x: (
            x.total_seconds()
            if isinstance(x, dt.timedelta)
            else x.hour * 3600 + x.minute * 60 + x.second
        )
    )
    is_str = dur.map(lambda x: isinstance(x, str))
    text = dur[is_str].str.strip()
    num = pd.to_numeric(text, errors="coerce")
    out[is_str] = num.where(num.notna(), pd.to_timedelta(text, errors="coerce").dt.total_seconds())
    rest = ~is_cell & ~is_str
    out[rest] = pd.to_numeric(dur[rest], errors="coerce")
    return out


# ---------- charger metadata ----------


def _lookup(raw_dir: Path, cfg: dict, key: str, col: str) -> pd.Series:
    d = _read_table(raw_dir / cfg["infos_dir"] / cfg[key])
    s = pd.Series(d[col].astype(str).str.strip().to_numpy(), index=d["CPID"].map(_norm_cpid))
    return s[~s.index.duplicated() & s.index.notna()]


def load_cp_info(raw_dir: Path, cfg: dict) -> pd.DataFrame:
    """cp_id -> connector (ac | rapid | ultra-rapid), site, is_private."""
    conn = _lookup(raw_dir, cfg, "speed_file", "Connector_Type").str.lower()
    la = _lookup(raw_dir, cfg, "local_authority_file", "local_authority")
    pc = _lookup(raw_dir, cfg, "postcode_file", "Postcode")
    pp = _lookup(raw_dir, cfg, "public_private_file", "public/prive").str.lower()
    idx = conn.index.union(la.index).union(pc.index).union(pp.index)
    site = la.reindex(idx).fillna("ChargePlace Scotland")
    site = site.where(pc.reindex(idx).isna(), site + " " + pc.reindex(idx))
    return pd.DataFrame(
        {"connector": conn.reindex(idx), "site": site, "is_private": pp.reindex(idx) == "private"}
    )


def dundee_cp_ids(root: Path) -> set[str]:
    """Dundee donor CP IDs (prefix-stripped), from its raw CSVs or else its chargers table."""
    raw = sorted((root / "raw" / "dundee").glob("*.csv"))
    if raw:
        frames = [pd.read_csv(p, dtype=str, skipinitialspace=True) for p in raw]
        for f in frames:
            f.columns = [c.strip() for c in f.columns]
        ids = pd.concat([f["CP ID"] for f in frames])
    elif (root / "processed" / "donor_chargers.parquet").exists():
        ids = pd.read_parquet(root / "processed" / "donor_chargers.parquet")["base_cp_id"]
    else:
        print("warning: no Dundee data found, CPS keeps Dundee-operated CPIDs")
        return set()
    return {_strip_prefix(i.strip()) for i in ids.dropna()}


def infer_connector(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """CPIDs missing from the speed file get a class from the p95 of session mean kW."""
    unknown = df["connector"].isna()
    df["connector_source"] = np.where(unknown, "inferred", "speed_file")
    if unknown.any():
        u = df[unknown]
        p95 = (u["kwh"] / (u["dur_s"] / 3600)).groupby(u["cp_id"]).quantile(0.95)
        thr = cfg["infer_kw_p95"]
        label = np.where(
            p95 >= thr["ultra-rapid"], "ultra-rapid", np.where(p95 >= thr["rapid"], "rapid", "ac")
        )
        df.loc[unknown, "connector"] = u["cp_id"].map(pd.Series(label, index=p95.index))
    return df


# ---------- cleaning ----------


def clean(
    raw: pd.DataFrame, cfg: dict, info: pd.DataFrame, exclude_ids: set[str]
) -> tuple[pd.DataFrame, dict]:
    """Dundee's drop rules plus CPS-specific ones. Returns the clean frame and drop counts."""
    dropped: dict[str, int] = {}
    df = pd.DataFrame(
        {
            "sdr_id": raw["sdr_id"],
            "cp_id": raw["CPID"].map(_norm_cpid),
            "kwh": pd.to_numeric(raw["Consumed(kWh)"], errors="coerce"),
            "dur_s": _duration_s(raw["Duration"]),
            "arrival_ts": _localize_utc(_naive_local(raw["Start"], raw["Time"])),
        }
    )
    n = len(df)

    def drop(mask: pd.Series, rule: str) -> None:
        nonlocal df
        dropped[rule] = int(mask.sum())
        df = df[~mask]

    drop(df["arrival_ts"].isna() | df["cp_id"].isna(), "unparseable_start_or_cpid")
    drop(df.duplicated(["cp_id", "arrival_ts", "dur_s", "kwh"]), "duplicate_row")
    drop(df["cp_id"].map(_strip_prefix).isin(exclude_ids), "dundee_cp_id")
    if cfg.get("exclude_private", True):
        drop(df["cp_id"].map(info["is_private"]).fillna(False).astype(bool), "private_cp_id")
    drop(~(df["kwh"] > 0), "kwh_blank_or_nonpositive")
    drop(df["dur_s"].isna(), "no_usable_duration")
    drop(df["dur_s"] <= 0, "departure_not_after_arrival")
    drop(df["dur_s"] < cfg["min_session_s"], "duration_too_short")
    drop(df["dur_s"] > cfg["max_session_s"], "duration_too_long")
    lo = pd.Timestamp(cfg["start"], tz=TZ)
    hi = pd.Timestamp(cfg["end"], tz=TZ) + pd.Timedelta(days=1)
    drop((df["arrival_ts"] < lo) | (df["arrival_ts"] >= hi), "outside_global_range")

    df = df.assign(departure_ts=df["arrival_ts"] + pd.to_timedelta(df["dur_s"], unit="s"))
    df = df.join(info[["connector", "site"]], on="cp_id")
    df["site"] = df["site"].fillna("ChargePlace Scotland")
    df = infer_connector(df, cfg)
    df["cp_id"] = PREFIX + df["cp_id"]
    df = df.sort_values(["cp_id", "arrival_ts", "sdr_id"]).reset_index(drop=True)
    report = {"rows_in": n, "dropped": dropped, "rows_out": len(df)}
    return df, report


def _rate_quantiles(ch: pd.DataFrame) -> dict:
    qs = [0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0]
    out = {}
    for pc, g in ch.groupby("power_class"):
        q = g["sessions_per_day"].quantile(qs)
        out[pc] = {f"p{int(k * 100)}": round(float(v), 4) for k, v in q.items()}
        out[pc]["mean"] = round(float(g["sessions_per_day"].mean()), 4)
    return out


def build(out_root: Path | None = None, cfg: dict | None = None, raw_dir: Path | None = None):
    cfg = cfg or load_config()
    root = out_root or dundee.data_dir()
    raw_dir = raw_dir or root / "raw" / "cps"
    proc = root / "processed"
    (proc / "reports").mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    info = load_cp_info(raw_dir, cfg)
    excl = dundee_cp_ids(root) if cfg.get("exclude_dundee", True) else set()
    df, report = clean(load_raw(raw_dir, cfg), cfg, info, excl)
    df, n_lane_drop = dundee.assign_lanes(df, cfg.get("max_lanes"))
    report["dropped"]["overlap_beyond_max_lanes"] = n_lane_drop
    report["rows_out"] = len(df)
    df = dundee.add_split(df, cfg["split_date"])
    df = dundee.add_context(df, cfg)
    df = df.rename(columns={"sdr_id": "donor_session_id", "kwh": "energy_kwh"})

    sessions = df.sort_values(["donor_charger_id", "arrival_ts"]).reset_index(drop=True)
    chargers = dundee.build_chargers(sessions, cfg)
    occupancy = dundee.build_occupancy(sessions)
    sessions[SESSION_COLS].to_parquet(proc / "donor_sessions_cps.parquet", index=False)
    chargers[CHARGER_COLS].to_parquet(proc / "donor_chargers_cps.parquet", index=False)
    occupancy.to_parquet(proc / "donor_occupancy_cps.parquet", index=False)

    src = sessions.drop_duplicates("cp_id").set_index("cp_id")["connector_source"]
    inferred = chargers[chargers["base_cp_id"].map(src) == "inferred"]
    report.update(
        {
            "n_cp_ids": int(sessions["cp_id"].nunique()),
            "cp_ids_with_2_lanes": int((sessions.groupby("cp_id")["lane"].max() == 2).sum()),
            "chargers_per_class": chargers["power_class"].value_counts().to_dict(),
            "chargers_per_class_inferred": inferred["power_class"].value_counts().to_dict(),
            "sessions_per_split": sessions["split"].value_counts().to_dict(),
            "util_basis_test_chargers": int((chargers["util_basis"] == "test").sum()),
            "weather_coverage": float(sessions["temp_c"].notna().mean()),
            "weather_series": "dundee (reused for every CPS charger)",
            "date_range": [str(sessions["arrival_ts"].min()), str(sessions["arrival_ts"].max())],
            "sessions_per_day_by_class": _rate_quantiles(chargers),
            "elapsed_s": round(time.time() - t0, 1),
        }
    )
    (proc / "reports" / "cps_cleaning.json").write_text(json.dumps(report, indent=2, default=str))
    return report


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m dataset.cps")
    p.add_argument("command", choices=["download", "build"])
    args = p.parse_args(argv)
    cfg = load_config()
    if args.command == "download":
        download(cfg, dundee.data_dir() / "raw" / "cps")
    else:
        print(json.dumps(build(cfg=cfg), indent=2, default=str))


if __name__ == "__main__":
    main()
