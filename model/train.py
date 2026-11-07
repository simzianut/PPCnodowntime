"""Training and evaluation pipeline for Module 2 demand forecaster (spec 02 §2).

Loads data (processed or fixtures fallback), extracts features, trains
LightGBM models, calibrates probabilities, writes artifacts/ and forecasts.parquet.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from model.features import FEATURE_COLUMNS, build_training_dataset
from model.forecaster import DemandForecaster

logger = logging.getLogger(__name__)


def train_pipeline(
    data_dir: Path | str = "data/processed",
    fixtures_dir: Path | str = "fixtures",
    artifacts_dir: Path | str = "artifacts",
) -> DemandForecaster:
    """Run full forecaster training pipeline and save artifacts."""
    d_dir = Path(data_dir)
    f_dir = Path(fixtures_dir)
    art_dir = Path(artifacts_dir)

    # Determine input paths (prefer data/processed, fallback to fixtures)
    if (d_dir / "sessions.parquet").exists():
        sessions_path = d_dir / "sessions.parquet"
        signals_path = d_dir / "signals.parquet"
        context_path = d_dir / "context.parquet"
        locations_path = f_dir / "locations_sample_m1.csv"
    else:
        sessions_path = f_dir / "sessions_sample.parquet"
        signals_path = f_dir / "signals_sample.parquet"
        context_path = f_dir / "context_sample.parquet"
        locations_path = f_dir / "locations_sample_m1.csv"

    sessions_df = pd.read_parquet(sessions_path)
    signals_df = pd.read_parquet(signals_path)
    context_df = pd.read_parquet(context_path)
    locations_df = pd.read_csv(locations_path)

    # Build dataset
    X_df, y_df = build_training_dataset(
        sessions_df=sessions_df,
        signals_df=signals_df,
        context_df=context_df,
        locations_df=locations_df,
        slot_min=5,
    )

    # Split train/val by time (last 20% of slots for validation and calibration)
    unique_slots = X_df["ts"].sort_values().unique()
    split_idx = max(1, int(len(unique_slots) * 0.8))
    split_time = unique_slots[split_idx]

    train_mask = X_df["ts"] < split_time
    val_mask = X_df["ts"] >= split_time

    X_train, y_train = X_df[train_mask], y_df[train_mask]
    X_val, y_val = X_df[val_mask], y_df[val_mask]

    forecaster = DemandForecaster(features=FEATURE_COLUMNS)
    forecaster.fit(X_train=X_train, y_train=y_train, X_val=X_val, y_val=y_val)

    # Save artifacts
    forecaster.save(art_dir)

    # Generate precomputed forecast table (decision 0021)
    all_preds = forecaster.predict_proba(X_df)
    forecasts_df = pd.DataFrame(
        {
            "ts": X_df["ts"],
            "cp_id": X_df["cp_id"],
            "p_arrival_15": all_preds["y15"],
            "p_arrival_30": all_preds["y30"],
            "p_arrival_60": all_preds["y60"],
            "expected_arrivals_60": all_preds["n60"],
        }
    )

    # Save to data/processed if exists, otherwise create it and also save sample to fixtures
    d_dir.mkdir(parents=True, exist_ok=True)
    forecasts_df.to_parquet(d_dir / "forecasts.parquet", index=False)
    forecasts_df.head(288 * 5).to_parquet(f_dir / "forecasts_sample.parquet", index=False)

    return forecaster
