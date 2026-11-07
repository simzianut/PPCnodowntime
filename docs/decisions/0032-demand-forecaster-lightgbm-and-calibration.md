# 0032: LightGBM EV demand forecaster, PAVA isotonic calibration, and precomputed forecasts

- Date: 2026-11-07
- Who: edy-antigravity
- Status: accepted
- Supersedes: —
- Affects: spec 02 §2, CONTRACTS, model/, artifacts/

## Context
Spec 02 §2 requires an EV demand forecaster predicting 3 binary arrival horizons ($y_{15}, y_{30}, y_{60}$) and Poisson expected arrivals at the location ($n_{60}$), with calibrated probabilities and $y_{30}$ AUC $\ge 0.75$. Decision 0021 also requires generating a precomputed batch table `data/processed/forecasts.parquet` so the simulator and Gymnasium environment run headless without per-slot LightGBM inference latency.

## Decision
1. **Native LightGBM Engine (`model/forecaster.py`):**
   - Uses native `lgb.train` with `lgb.Dataset` for 3 binary classifiers and 1 Poisson regressor. This avoids adding `scikit-learn` to project dependencies while running significantly faster.
2. **PAVA Isotonic Calibration (`model/calibration.py`):**
   - Implements Pool Adjacent Violators Algorithm (PAVA) directly in NumPy to monotonically calibrate raw LightGBM probability outputs on held-out validation time slices.
3. **High-Performance Feature Extraction (`model/features.py`):**
   - `SignalIndex` pre-indexes discrete signal events (`maps_ev_count`, `maps_ice_count`, `maps_traffic_index`, `ppcblue_route_intent`) for $O(1)$ lookups per slot.
   - Historical arrival counts and target horizons use binary search (`bisect`) over sorted timestamps, generating thousands of slot-CP rows in seconds.
   - Strict leakage guard: `session_id` is never used as a feature; only timestamps $ts \le t$ are visible.
4. **Artifacts & Precomputed Forecasts:**
   - Saved `artifacts/forecaster.lgb` (578 KB) and `artifacts/forecaster_meta.json` (107 KB) with feature list, calibrator knots, and metrics (under the 20 MB limit).
   - Generated `data/processed/forecasts.parquet` and `fixtures/forecasts_sample.parquet` matching the CONTRACTS.md schema (`ts`, `cp_id`, `p_arrival_15`, `p_arrival_30`, `p_arrival_60`, `expected_arrivals_60`).
   - Achieved $y_{30}$ AUC of **0.9103** (exceeding the $\ge 0.75$ criterion) and Brier score of **0.0488**.

## Alternatives considered
- Adding `scikit-learn` to dependencies: unnecessary when native LightGBM and NumPy PAVA provide full capability with zero dependency footprint.
- Online-only predictions during simulation: rejected by decision 0021 due to RL episode throughput requirements.

## Consequences
- M4 evaluator (T11) and Gym environment (T14) can immediately load precomputed forecasts.
- `DemandForecaster.predict(SimState)` is available for live mode inference.
