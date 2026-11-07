"""Open-Meteo historical archive client with a parquet cache."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
USER_AGENT = "PPCnodowntime-hackathon"
COLUMNS = ["ts_hour", "temp_c", "precip_mm", "wind_kmh"]


def _cache_path(name: str, start: str, end: str) -> Path:
    data_dir = Path(os.environ.get("DATA_DIR", "./data"))
    return data_dir / "raw" / "weather" / f"{name}_{start}_{end}.parquet"


def fetch_hourly(lat: float, lon: float, start: str, end: str, name: str) -> pd.DataFrame:
    """Hourly weather, UTC. Returns ts_hour, temp_c, precip_mm, wind_kmh. Cached on disk."""
    path = _cache_path(name, start, end)
    if path.exists():
        return pd.read_parquet(path)
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start,
        "end_date": end,
        "hourly": "temperature_2m,precipitation,wind_speed_10m",
        "wind_speed_unit": "kmh",
        "timezone": "UTC",
    }
    req = urllib.request.Request(
        f"{ARCHIVE_URL}?{urllib.parse.urlencode(params)}", headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        h = json.load(resp)["hourly"]
    df = pd.DataFrame(
        {
            "ts_hour": pd.to_datetime(h["time"], utc=True),
            "temp_c": h["temperature_2m"],
            "precip_mm": h["precipitation"],
            "wind_kmh": h["wind_speed_10m"],
        }
    )
    df["ts_hour"] = df["ts_hour"].astype("datetime64[ns, UTC]")
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return df
