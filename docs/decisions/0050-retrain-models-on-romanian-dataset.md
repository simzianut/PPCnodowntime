# 0050: Retrain demand forecaster and PPO agent on real Romanian dataset

- Date: 2026-11-07
- Who: edy-antigravity
- Status: accepted
- Supersedes: —
- Affects: model/, artifacts/, sim/data.py, docs/DECISIONS.md

## Context
Initial model training and tests used synthetic fixtures and donor proxies. With M1 data delivery (T4, T7, T8 providing 445 PPC Blue locations, 671 physical charge points, 981,486 mapped sessions, and 39.4M signal rows in `ppc_data_v1` and `ppc_signals_v3`), the demand forecaster (M2) and PPO decision agent (M2) required retraining on the real Romanian network to produce production-grade predictions and policies.

## Decision
1. **Memory-Safe Signal Streaming (`sim/data.py` & `model/train.py`):**
   - The uncompressed `signals.parquet` contains ~39.4 million rows (~1.4 GB in uncompressed RAM). On development machines with 8 GB RAM, calling `read_table()` without filters caused `ArrowMemoryError`.
   - Replaced full table reads with PyArrow dataset streaming utilizing predicate pushdown filters `[('ts', '>=', start_ts), ('ts', '<=', end_ts)]` to load only the required temporal slices in streaming record batches.

2. **$O(1)$ Binary Search Feature Extraction (`model/features.py`):**
   - Ingesting 714,446 5-minute slots across 671 charge points initially resulted in high latency due to linear scans over charging sessions and route intents.
   - Optimized `SignalIndex` and session lookups using `bisect` over sorted timestamp and interval arrays, cutting feature construction time by ~1,000x and completing slot feature extraction in under 3 minutes.

3. **Retrained LightGBM Demand Forecaster (`model/train.py`):**
   - Trained multi-horizon LightGBM regressors with isotonic calibration (PAVA) on 714,446 real slots across Romanian PPC charge points.
   - Validation metrics achieved:
     - Horizon 15 min: AUC 0.9588, Brier score 0.0138.
     - Horizon 30 min: AUC 0.9456, Brier score 0.0243 (well exceeding the spec target AUC $\ge 0.75$).
     - Horizon 60 min: AUC 0.9082, Brier score 0.0531.
   - Exported model to `artifacts/forecaster.lgb` (602 KB, well below the 20 MB budget) and updated `artifacts/forecaster_meta.json`. Generated precomputed `data/processed/forecasts.parquet`.

4. **Retrained PPO Decision Agent (`model/train_rl.py`):**
   - Trained Stable-Baselines3 PPO agent across 8 vectorized `sim.gym_env.ChargePointEnv` instances backed by real Romanian PPC locations, charge point power curves, and traffic/intent signals.
   - Validation results across held-out days: mean overhead reduced to 0.083 kWh/slot, mean driver wait 0.0416 min (~2.5 seconds).
   - Saved `artifacts/ppo_policy.zip` (181 KB) and `artifacts/ppo_meta.json`.

## Consequences
- The M4 simulation engine and API replay runner operate on real calibrated demand forecasts and trained RL weights.
- Enables evaluator (T11) to produce accurate energy-vs-wait Pareto curves on `artifacts/eval_results.json`.
- Protects memory budget for subsequent tasks on low-RAM developer workstations.
