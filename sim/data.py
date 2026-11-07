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
    """Load M1/M2 outputs from data/processed and CPs from the station seed (until T4/T5)."""
    from sim.stations_seed import load_seed_stations

    p = (base or data_dir()) / "processed"
    locations, cps = load_seed_stations()
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
    """Real processed data when present, else the seeded synthetic bundle."""
    if processed_available():
        return load_processed()
    from sim.synthetic import generate

    return generate(seed=seed)
