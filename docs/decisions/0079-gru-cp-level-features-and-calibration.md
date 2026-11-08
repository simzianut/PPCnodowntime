# 0079: F2 GRU improvements: cp_level features, calibration choice, f2_improve suite

- Date: 2026-11-08
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: —
- Affects: model/features.py, model/forecast_data.py, model/gru_forecaster.py, model/experiments/f2_gru.py, config/train/suites/f2_improve.yaml, docker/train_forecasters.sh

## Context
On the A100 run `f2_gru-c75aafc1-s2` (the deployed `artifacts/f2_gru.pt`, 0071), the GRU beats F0 by a wide margin, but its metrics show three problems:
- **Level shift.** Val (Q4 2023) has a y15 base rate of 2.08%. Test (2024–25) has 0.94%. The isotonic calibrators are fit on val, so the model overshoots on test: y60 mean prediction 0.047 vs 0.036 true, n60 0.083 vs 0.063. The model has no input that tells it a charge point's current usage level.
- **Broken ordering.** One isotonic calibrator per horizon breaks `p15 ≤ p30 ≤ p60` on about 2% of test rows. The raw survival head has 0% violations.
- **Inflated headline.** `maps_ev_count` and intents are mock signals drawn from future arrivals (0039, 0052). The full-feature AUC overstates real value. Improvements must also be judged on step 2 (`calendar_history + traffic`).

## Decision
- **New feature group `cp_level`.** It holds three features, all built only from arrivals at or before slot time t:
  - `arrivals_cp_7d`, `arrivals_cp_28d`: arrivals per day at this charge point. The count is divided by the days of history actually available (1 to 7 or 1 to 28). Without that, live mode would see near-zero rates for weeks, because it starts with no session history.
  - `mins_since_last_arrival`: capped at 28 days.
  - It is a separate group rather than part of `calendar_history`, so existing configs and ablation steps keep their meaning and the ablation can measure the group alone.
  - The batch (`forecast_data`) and online (`GRUForecaster`) versions are covered by the parity test.
- **Calibration choice:** `params.calibration` is one of `isotonic | temp_bias | auto | none`.
  - `temp_bias` applies one affine map `a·l + b` to all 12 hazard logits, fit on val by survival NLL. Ordering is kept by construction, and 2 parameters fit val's base rate less tightly than isotonic does.
  - `auto` fits both on one half of the val CP-days, picks the one with lower log loss on the other half, then refits it on all of val.
  - Every run also reports test metrics for `none`, `isotonic` and `temp_bias`, so one run compares all three.
  - Old checkpoints without a `calibration` key still load as isotonic.
- **Evaluation:** suite `f2_improve` covers the baseline and `+cp_level`, each on step-2 features and on all features, with 3 seeds each and `calibration: auto`. It runs on the A100 with `docker/train_forecasters.sh f2-improve`. `ablation-f2` is run on main beforehand for the honest step 1–4 ladder of the current model.
- **Promotion rule:** the new model replaces `artifacts/f2_gru.pt` only if it beats the current one on both step 2 and the full feature set. The comparison uses the 3-seed mean of test PR-AUC and Brier for y15, y30 and y60. If it is promoted, `forecasts.parquet` and `eval_results.json` are regenerated.
- `config/train/f2_gru.yaml` is unchanged, so it still reproduces the deployed model.
- **Implementation details:**
  - `DEFAULT_PARAMS["feature_groups"]` lists the four original groups explicitly, so configs that rely on the default keep their 22 features.
  - Batch `history_start` is the earliest arrival in the sessions table.
  - Online, `history_start` comes from the sessions passed in, or from the first `predict()` time when there are none (live mode). A CP with no arrivals gets rates 0 and minutes 40320.
  - The `auto` split permutes the val CP-days with `default_rng([sample_seed, 4242])`. Ties go to isotonic.
  - `temp_bias` is fit by LBFGS (strong Wolfe, 100 iterations) on all val rows.
  - `calibration` overrides the old `calibrate: bool` when a config has both.
  - New checkpoints carry `calibration: {kind, a, b}`. The deployed `f2_gru.pt` loads and gives bit-identical outputs.

## Alternatives considered
- **Add the features to `calendar_history`:** simpler, but old ablation runs would no longer be comparable.
- **Isotonic plus a monotone re-sort:** the smallest change, but it still fits val's doubled base rate.
- **Calibrate on a recent window just before test:** that would shift the val/test boundary from 0052. It was left out.
- **Local CPU training for the comparison:** too slow for 12 runs. The laptop is used only for a smoke run.

## Consequences
- `FEATURE_VERSION` goes up, so the feature cache is rebuilt on the next run.
- In live mode, `cp_level` is built from arrivals inferred since startup, normalised by that short history. Rates are noisy for the first days.
- Still open (not part of this decision): a larger GRU (hidden 128, 2 layers), a cosine LR schedule, more training CP-days, a loss weighted by power class, and evaluation at the agent's sleep threshold.
