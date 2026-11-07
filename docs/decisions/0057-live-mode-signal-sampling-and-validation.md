# 0057: Live-mode signal sampling and live validation report

- Date: 2026-11-07
- Who: edy-antigravity
- Status: accepted
- Supersedes: —
- Affects: model/live_signals.py, model/reports/, docs/DECISIONS.md

## Context
Spec 02 §5 specifies live-mode signal sampling: in live mode, where no future ground truth exists, signals must be sampled dynamically from the demand forecaster's predicted arrival rates ($n_{60}$). Furthermore, Spec 02 §2 requires comparing predicted occupancy against observed charging statuses from `live_status` to report validation quality.

## Decision
1. **Live Signal Sampler (`model/live_signals.py`):**
   - Implements `LiveSignalSampler` parameterized by `config/signals.yaml`.
   - Draws route intent counts from $\text{Poisson}(p_{\text{app}} \times n_{60})$ with 10% false positive chance and ETAs spread over $t + [10, 60]$ minutes.
   - Generates `maps_ev_count` and `maps_ice_count` matching the Spec 01 §5 formulations driven by expected arrivals ($0.5 \times n_{60}$) and traffic index.
   - Enforces intent confirmation logic: when a charge point transitions to `CHARGING`, pending intents within $[-15, +30]$ minutes are marked confirmed; otherwise, a confirmed late intent is generated to maintain consistency.
2. **Live Validation Reporting (`model/reports/live_validation_report.json`):**
   - Computes hourly mean absolute error (MAE) and root mean squared error (RMSE) between forecast demand and live status occupancy profiles.
   - Generates artifact `model/reports/live_validation_report.json` documenting model accuracy on real-time station statuses.

## Consequences
- Spec 02 is 100% completed across all sections (§1 through §7).
- Live mode (T12) has a ready, tested sampling and intent-confirmation mechanism.
