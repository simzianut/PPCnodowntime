"""SimData: the input bundle for the engine, gym env and API (decision 0024)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from sim.config import ROOT, TZ


@dataclass
class SimData:
    """All frames use the CONTRACTS schemas; timestamps are tz-aware UTC.

    locations: location_id, name, partner, address, county_code, lat, lon, context_type,
        primary_source, confidence
    cps: cp_id, location_id, power_class, max_kw, connectors (list[str]), current_type,
        county_code, context_type
    sessions / signals / context: as sessions.parquet / signals.parquet / context.parquet
    forecasts: as forecasts.parquet (ts, cp_id, p_arrival_15/30/60, expected_arrivals_60)
    """

    locations: pd.DataFrame
    cps: pd.DataFrame
    sessions: pd.DataFrame
    signals: pd.DataFrame
    context: pd.DataFrame
    forecasts: pd.DataFrame | None = None

    def days(self, split: str | None = None) -> list[date]:
        """Local (Europe/Bucharest) dates that have sessions in the given split."""
        s = self.sessions if split is None else self.sessions[self.sessions["split"] == split]
        local = s["arrival_ts"].dt.tz_convert(TZ).dt.date
        return sorted(set(local))


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", ROOT / "data"))


def load_processed(base: Path | None = None) -> SimData:
    """Load M1/M2 outputs from data/processed and stations via load_stations."""
    from sim.stations_seed import load_stations

    base = base or data_dir()
    p = base / "processed"
    locations, cps = load_stations(base)
    forecasts = p / "forecasts.parquet"
    return SimData(
        locations=locations,
        cps=cps,
        sessions=pd.read_parquet(p / "sessions.parquet"),
        signals=pd.read_parquet(p / "signals.parquet"),
        context=pd.read_parquet(p / "context.parquet"),
        forecasts=pd.read_parquet(forecasts) if forecasts.exists() else None,
    )


def processed_available(base: Path | None = None) -> bool:
    p = (base or data_dir()) / "processed"
    return all((p / f).exists() for f in ("sessions.parquet", "signals.parquet", "context.parquet"))


def load_default(seed: int = 0) -> SimData:
    """Real processed data when present, else synthetic sessions on load_stations()."""
    if processed_available():
        return load_processed()
    from sim.stations_seed import load_stations
    from sim.synthetic import generate

    locations, cps = load_stations()
    return generate(seed=seed, locations=locations, cps=cps)


def load_m1_fixtures(base: Path | None = None) -> SimData:
    """M1's committed samples (decision 0015) as a small SimData with oracle forecasts."""
    from sim.stations_seed import CP_COLUMNS, LOCATION_COLUMNS
    from sim.synthetic import oracle_forecasts

    f = base or ROOT / "fixtures"
    sessions = pd.read_parquet(f / "sessions_sample.parquet")
    meta = pd.read_csv(f / "locations_sample_m1.csv", dtype=str)
    max_kw = {"AC_22": 22.0, "DC_50": 50.0, "DC_HPC": 150.0}
    cps = pd.DataFrame(
        {
            "cp_id": meta["cp_id"],
            "location_id": meta["location_id"],
            "power_class": meta["power_class"],
            "max_kw": meta["power_class"].map(max_kw).astype("float64"),
            "connectors": [[] for _ in range(len(meta))],
            "current_type": meta["power_class"].map(lambda pc: "AC" if pc == "AC_22" else "DC"),
            "county_code": meta["county_code"],
            "context_type": meta["context_type"],
        },
        columns=CP_COLUMNS,
    )
    loc = meta.drop_duplicates("location_id").reset_index(drop=True)
    locations = pd.DataFrame(
        {
            "location_id": loc["location_id"],
            "name": loc["location_id"],
            "partner": None,
            "address": None,
            "county_code": loc["county_code"],
            "lat": None,
            "lon": None,
            "context_type": loc["context_type"],
            "primary_source": "fixture",
            "confidence": "low",
        },
        columns=LOCATION_COLUMNS,
    )
    data = SimData(
        locations=locations,
        cps=cps,
        sessions=sessions,
        signals=pd.read_parquet(f / "signals_sample.parquet"),
        context=pd.read_parquet(f / "context_sample.parquet"),
    )
    data.forecasts = oracle_forecasts(sessions, cps, data.days())
    return data
