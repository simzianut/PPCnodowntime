"""Romanian context features: hourly weather per county centroid and RO holidays (spec 01 §6).

CLI: python -m dataset.context
"""

from __future__ import annotations

import json
import time
import urllib.error
from pathlib import Path

import holidays
import numpy as np
import pandas as pd
import yaml
from shapely.geometry import shape

from dataset import weather
from dataset.mapping import RO_TZ, data_dir
from stations.importer import COUNTY_CODES

REPO = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO / "config" / "dataset.yaml"
GEOJSON_PATH = REPO / "data" / "seed" / "ro_counties.geojson"
COLS = ["ts_hour", "county_code", "temp_c", "precip_mm", "wind_kmh", "is_holiday"]


def load_config() -> dict:
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    return {**cfg["context"], "start": cfg["dundee"]["start"], "end": cfg["dundee"]["end"]}


def county_centroids(path: Path = GEOJSON_PATH) -> pd.DataFrame:
    """county_code, lat, lon of each polygon centroid (names mapped as in stations.importer)."""
    feats = json.loads(path.read_text(encoding="utf-8"))["features"]
    rows = []
    for ft in feats:
        name = ft["properties"]["shapeName"]
        if name not in COUNTY_CODES:
            raise ValueError(f"county name without ISO code: {name}")
        c = shape(ft["geometry"]).centroid
        rows.append({"county_code": COUNTY_CODES[name], "lat": c.y, "lon": c.x})
    return pd.DataFrame(rows).sort_values("county_code").reset_index(drop=True)


def _fetch(row, cfg: dict) -> pd.DataFrame:
    name = f"ro_{row.county_code}"
    for attempt in range(cfg["max_retries"] + 1):
        try:
            return weather.fetch_hourly(row.lat, row.lon, cfg["start"], cfg["end"], name)
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == cfg["max_retries"]:
                raise
            print(f"HTTP 429 for {name}; waiting {cfg['retry_wait_s']} s")
            time.sleep(cfg["retry_wait_s"])
    raise RuntimeError("unreachable")


def ro_holiday_flags(ts_hour: pd.Series, start: str, end: str) -> np.ndarray:
    """True when the Bucharest local date is a Romanian public holiday."""
    years = range(pd.Timestamp(start).year, pd.Timestamp(end).year + 2)
    ro = {pd.Timestamp(d) for d in holidays.country_holidays("RO", years=years)}
    local_day = ts_hour.dt.tz_convert(RO_TZ).dt.tz_localize(None).dt.normalize()
    return local_day.isin(list(ro)).to_numpy()


def build(out_root: Path | None = None, cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    root = out_root or data_dir()
    proc = root / "processed"
    proc.mkdir(parents=True, exist_ok=True)
    frames = []
    for i, row in enumerate(county_centroids().itertuples(index=False)):
        cached = weather._cache_path(f"ro_{row.county_code}", cfg["start"], cfg["end"]).exists()
        if i and not cached:
            time.sleep(cfg["request_gap_s"])
        w = _fetch(row, cfg)[["ts_hour", "temp_c", "precip_mm", "wind_kmh"]]
        frames.append(w.drop_duplicates("ts_hour").assign(county_code=row.county_code))
    ctx = pd.concat(frames, ignore_index=True)
    ctx["ts_hour"] = ctx["ts_hour"].astype("datetime64[ns, UTC]")
    for c in ("temp_c", "precip_mm", "wind_kmh"):
        ctx[c] = ctx[c].astype(float)
    ctx["is_holiday"] = ro_holiday_flags(ctx["ts_hour"], cfg["start"], cfg["end"])
    ctx = ctx.sort_values(["county_code", "ts_hour"]).reset_index(drop=True)[COLS]
    ctx.to_parquet(proc / "context.parquet", index=False)
    return {
        "rows": len(ctx),
        "counties": int(ctx["county_code"].nunique()),
        "ts_min": str(ctx["ts_hour"].min()),
        "ts_max": str(ctx["ts_hour"].max()),
        "weather_missing_share": float(
            ctx[["temp_c", "precip_mm", "wind_kmh"]].isna().mean().max()
        ),
        "holiday_hours_share": float(ctx["is_holiday"].mean()),
    }


def main() -> None:
    print(json.dumps(build(), indent=2))


if __name__ == "__main__":
    main()
