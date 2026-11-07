# 0059: Forecaster training implementation: shared sample, vectorised features, F0/F1/F2 settings

- Date: 2026-11-07
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: —
- Affects: spec 02 §2, model/forecast_data.py, model/forecast_eval.py, model/experiments/f0_seasonal.py, f1_lgbm.py, f2_gru.py, config/train/

## Context
Decision 0052 chose three forecasters and an ablation. To compare them fairly they need the same rows, and to train at full scale the feature builder must be fast. Several data quirks also showed up while building it.

## Decision
- **One shared sample** of active CP-days only (inactive days are never sampled), proportional by power_class.
  - It is drawn with `params.sample_seed` (20260101), never the run seed, so every model and seed sees the same rows.
  - Date splits: train ≤ 2023-09-30, validation 2023-10-01..2023-12-31, test ≥ 2024-01-01.
  - Default sizes: 40k / 10k / 20k CP-days (about 11.5M / 2.9M / 5.8M slot rows).
  - A CP-day is a UTC day of 288 slots (no DST handling).
- **Vectorised feature builder** (`model/forecast_data.py`) with the same 22 feature definitions as `model/features.py`. Edy's per-slot Python loop would take over an hour at this scale. A test checks the new builder against `build_training_dataset` on synthetic data, and another test checks there is no lookahead.
- **Cache** under `DATA_DIR/interim/forecast_cache/<sample key>/feat_<signals hash>/`, keyed by the sample parameters and the input file hashes. All 22 columns are cached and the ablation groups are selected at load time, so one cache serves every ablation step for a given signals file. Size is about 1.9 GB per signals variant at full scale.
- **`arrivals_same_slot_4w`** is defined as the mean count in a ±30 min window at the same time of day, 1–4 weeks back. Edy's training had it at 0.
- **Monotone horizons:** a cumulative max enforces p15 ≤ p30 ≤ p60 after calibration. Before the fix, F1 violated it on 13–33% of rows. The violation count is recorded in metrics.json.
- **Fast isotonic calibration:** quantile-binned PAVA with at most 4,000 knots, written in edy's knot format. Edy's O(n²) PAVA cannot handle millions of rows.
- **F0:** per-CP local hour-of-week rates from all active train-period CP-days. They are shrunk towards the (power_class, context_type) rate (`group_strength` 24, `prior_strength` 100 h), and p_H integrates the rate over (t, t+H].
- **F1:** learning rate 0.08, 63 leaves, `min_data_in_leaf` 200, feature and bagging fraction 0.8, up to 600 rounds with early stopping after 30, `max_bin` 127, `deterministic=True`, fixed `num_threads`. Early stopping and calibration both use the validation slice, so validation metrics are slightly optimistic; test metrics are not affected. It saves in edy's artifact format, so `promote` replaces `forecaster.lgb` and `forecaster_meta.json`.
- **F2:**
  - GRU with hidden size 64 and 1 layer, a 36-slot window (zero-padded and masked before the day start), `log1p` + standardised inputs.
  - Embeddings are one-hot × Linear, with no gather/scatter, for CUDA determinism.
  - Twelve hazards, with horizons computed as explicit logsigmoid sums.
  - Poisson weight 0.2, AdamW lr 2e-3, batch 2048, 15 epochs of 2M random windows, patience 3.
  - Per-horizon isotonic calibration on by default; metrics are reported with and without it.
- **Timings go in `timing.json`**, not `metrics.json`, so the same config and seed give a byte-identical `metrics.json`.

## Alternatives considered
- Reusing edy's `build_training_dataset`: correct, but too slow at full scale.
- Training each model on its own sample: comparisons would mix model differences with sampling noise.

## Consequences
- **Known issue, inherited:** `weighted_flow_15m` only matches combustion counts at exact 5-minute slots, but those counts are hourly. In practice it is mostly EV counts. It is kept for parity with edy's model; fixing it needs a new feature version and a retrain.
- **Live mode gap:** `arrivals_cp_1h`, `arrivals_cp_3h` and `arrivals_same_slot_4w` are 0 in live inference until a per-CP history tracker exists, so a promoted F1 sees different inputs live than in training.
- The F1 feature importance is dominated by the intent features, as expected from how intents are mocked (0052 framing).
