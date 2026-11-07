"""Training and evaluation pipeline for Module 2 demand forecaster (spec 02 §2).

Loads data (processed or fixtures fallback), extracts features, trains
LightGBM models, calibrates probabilities, writes artifacts/ and forecasts.parquet.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

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

    is_processed = (d_dir / "sessions.parquet").exists() and (d_dir / "locations.parquet").exists()

    if is_processed:
        sessions_df = pd.read_parquet(d_dir / "sessions.parquet")
        locations_df = pd.read_parquet(d_dir / "locations.parquet")
        charge_points_df = pd.read_parquet(d_dir / "charge_points.parquet")
        cp_active_days_df = (
            pd.read_parquet(d_dir / "cp_active_days.parquet")
            if (d_dir / "cp_active_days.parquet").exists()
            else None
        )
        context_df = pd.read_parquet(d_dir / "context.parquet")

        # Load county EV statistics if available
        stats_path = Path("data/seed/county_ev_stats.csv")
        if stats_path.exists():
            ev_stats = pd.read_csv(stats_path)
            ev_map = dict(zip(ev_stats["county_code"], ev_stats["ev_per_1000"], strict=False))
            locations_df["county_ev_per_1000"] = locations_df["county_code"].map(ev_map).fillna(1.5)

        # Merge CPs with location metadata
        merged_cps = charge_points_df.merge(
            locations_df[["location_id", "county_code", "context_type", "county_ev_per_1000"]],
            on="location_id",
            how="left",
        )

        # Stratified sampling of 60 CPs across power classes
        sampled_cps: list[str] = []
        for pc in ("AC_22", "DC_50", "DC_HPC"):
            sub = merged_cps[merged_cps["power_class"] == pc]
            n = 15 if pc == "DC_HPC" else 25
            sampled_cps.extend(sub.sample(n=min(n, len(sub)), random_state=42)["cp_id"].tolist())

        sampled_locs = list(set(merged_cps[merged_cps["cp_id"].isin(sampled_cps)]["location_id"]))
        sub_cps = merged_cps[merged_cps["cp_id"].isin(sampled_cps)].reset_index(drop=True)

        # Target window: Q4 2023 (peak autumn/winter demand with full active coverage)
        t_start = pd.Timestamp("2023-10-01", tz="UTC")
        t_end = pd.Timestamp("2023-11-20 23:59:59", tz="UTC")

        # Memory-safe pyarrow reading of signals strictly for sampled locations & window
        sig_tbl = pq.read_table(
            d_dir / "signals.parquet",
            filters=[
                ("location_id", "in", sampled_locs),
                ("ts", ">=", t_start),
                ("ts", "<=", t_end),
            ],
            columns=["ts", "location_id", "signal", "value", "eta_ts"],
        )
        signals_df = sig_tbl.to_pandas()

        sub_sess = sessions_df[
            sessions_df["cp_id"].isin(sampled_cps)
            & (sessions_df["arrival_ts"] >= t_start)
            & (sessions_df["departure_ts"] <= t_end)
        ]
        sub_ctx = context_df[(context_df["ts_hour"] >= t_start) & (context_df["ts_hour"] <= t_end)]
        slots = pd.date_range(t_start, t_end, freq="5min", tz="UTC")

        X_df, y_df = build_training_dataset(
            sessions_df=sub_sess,
            signals_df=signals_df,
            context_df=sub_ctx,
            locations_df=locations_df,
            charge_points_df=sub_cps,
            cp_active_days_df=cp_active_days_df,
            slots=slots,
            slot_min=5,
        )
    else:
        sessions_df = pd.read_parquet(f_dir / "sessions_sample.parquet")
        signals_df = pd.read_parquet(f_dir / "signals_sample.parquet")
        context_df = pd.read_parquet(f_dir / "context_sample.parquet")
        locations_df = pd.read_csv(f_dir / "locations_sample_m1.csv")

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

    d_dir.mkdir(parents=True, exist_ok=True)
    forecasts_df.to_parquet(d_dir / "forecasts.parquet", index=False)
    forecasts_df.head(288 * 5).to_parquet(f_dir / "forecasts_sample.parquet", index=False)

    return forecaster
