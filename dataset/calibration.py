"""Compare real AmpWhere charging starts with reconstructed sessions (decision 0044).

Usage: python -m dataset.calibration
"""

from __future__ import annotations

import glob
import json
import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

CLASSES = ["AC_22", "DC_50", "DC_HPC"]


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "./data"))


def real_unit_starts(
    d: Path, ppc_provider_id: int = 5
) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    """Unit-level starts: a CP goes from 0 to >= 1 charging connector."""
    p = d / "processed"
    refs = pd.read_parquet(p / "source_refs.parquet")
    refs = refs[(refs.source == "ampwhere") & refs.cp_id.notna()]
    conn2cp = dict(zip(refs.source_id, refs.cp_id, strict=True))
    state: dict = {}
    unit_n: dict = {}
    starts = []
    t_first = t_last = None
    for f in sorted(glob.glob(str(d / "raw" / "live" / "statuses-*.jsonl"))):
        for ln in open(f, encoding="utf-8"):
            r = json.loads(ln)
            if r.get("provider_id") != ppc_provider_id:
                continue
            cp = conn2cp.get(f"{r['source_location_id']}|{r['source_cp_id']}")
            if cp is None:
                continue
            ts = pd.Timestamp(r["ts"])
            t_first = t_first or ts
            t_last = ts
            k = (r["source_location_id"], r["source_cp_id"])
            prev, on = state.get(k), r["status"] == "CHARGING"
            state[k] = on
            if prev is None:
                unit_n[cp] = unit_n.get(cp, 0) + on
                continue
            if prev == on:
                continue
            before = unit_n.get(cp, 0)
            unit_n[cp] = before + (1 if on else -1)
            if before == 0 and unit_n[cp] > 0:
                starts.append((ts, cp))
    real = pd.DataFrame(starts, columns=["ts", "cp_id"])
    # skip the first poll window: its "starts" are only the initial state
    start = t_first + pd.Timedelta(minutes=5)
    return real[real.ts >= start], start, t_last


def compare(d: Path) -> dict:
    p = d / "processed"
    cps = pd.read_parquet(p / "charge_points.parquet")[["cp_id", "power_class"]]
    cls = dict(zip(cps.cp_id, cps.power_class, strict=True))
    real, t0, t1 = real_unit_starts(d)
    real["cls"] = real.cp_id.map(cls)
    l0, l1 = t0.tz_convert("Europe/Bucharest"), t1.tz_convert("Europe/Bucharest")
    m0, m1 = l0.hour * 60 + l0.minute, l1.hour * 60 + l1.minute
    s = pd.read_parquet(p / "sessions.parquet", columns=["cp_id", "arrival_ts"])
    act = pd.read_parquet(p / "cp_active_days.parquet")
    act["date"] = pd.to_datetime(act["date"]).dt.date
    act = act[act.active]
    lt = s.arrival_ts.dt.tz_convert("Europe/Bucharest")
    s["date"], s["m"] = lt.dt.date, lt.dt.hour * 60 + lt.dt.minute
    same = (lt.dt.day_of_week == l0.day_of_week) & (lt.dt.month == l0.month)
    s = s[same & (s.m >= m0) & (s.m < m1)]
    s["cls"] = s.cp_id.map(cls)
    n_all = cps.power_class.value_counts()
    out = {"window_local": f"{l0:%Y-%m-%d %a %H:%M}-{l1:%H:%M}", "classes": {}}
    for c in CLASSES:
        per_day = []
        for day in sorted(set(s.date)):
            a_c = set(act[(act.date == day) & (act.cp_id.map(cls) == c)].cp_id)
            if a_c:
                n = int(((s.date == day) & (s.cls == c) & s.cp_id.isin(a_c)).sum())
                per_day.append(n * n_all[c] / len(a_c))
        syn = pd.Series(per_day, dtype=float)
        r = int((real.cls == c).sum())
        out["classes"][c] = {
            "real_starts": r,
            "synthetic_mean": round(float(syn.mean()), 1),
            "synthetic_p10": round(float(syn.quantile(0.1)), 1),
            "synthetic_p90": round(float(syn.quantile(0.9)), 1),
            "n_days": len(syn),
            "real_over_synthetic": round(r / syn.mean(), 2) if syn.mean() else None,
        }
    return out


if __name__ == "__main__":
    load_dotenv()
    d = data_dir()
    rep = compare(d)
    (d / "processed" / "reports").mkdir(parents=True, exist_ok=True)
    (d / "processed" / "reports" / "calibration_ampwhere.json").write_text(
        json.dumps(rep, indent=2), encoding="utf-8"
    )
    print(json.dumps(rep, indent=2))
