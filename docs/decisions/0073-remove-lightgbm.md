# 0073: LightGBM is removed from the project

- Date: 2026-11-08
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: 0032 (LightGBM forecaster), the F1 part of 0052 and 0059
- Affects: pyproject.toml, uv.lock, model/, sim/evaluate.py, api/live_mode.py, config/train/, artifacts/, docker/

## Context
The agent is F2 GRU + cost rule (0069). LightGBM (F1) was still a core dependency because `model/__init__.py` imported it eagerly, the evaluator and live mode used it as a fallback, and the F1 trainer and edy's legacy pipeline used it. The team will not use it again.

## Decision
- **Dependency:** `lightgbm` is gone from `pyproject.toml` and `uv.lock`, which also drops `scipy` and `narwhals`.
- **Deleted:**
  - `model/forecaster.py` (DemandForecaster) and `model/train.py` (edy's LightGBM pipeline)
  - `model/experiments/f1_lgbm.py`, `config/train/f1_lgbm.yaml`, the F1 ablation suite
  - `artifacts/forecaster.lgb` and `forecaster_meta.json`
  - the F1 tests
- **Fallbacks:**
  - The evaluator without `forecasts.parquet` runs the F2 GRU online.
  - Live mode uses the F2 GRU, or a constant stub (with a warning) if the model can't load.
- **`python -m model` / `make train`** now trains F2 through `model.experiments`.
- `model/__init__.py` exports only the cost rule, the stubs and the shared types, so importing `model` no longer loads any ML library.

## Alternatives considered
- Move LightGBM to an optional extra: keeps F1 reproducible from main, but the team will not use it.

## Consequences
- The F1 results (y30 PR-AUC 0.582 vs F2 0.623, server run `forecasters-b2e500c1`) stay recorded in 0069 and in git history. Rerunning F1 means checking out a commit before this decision.
- The feature-group ablation, if rerun, uses F2 (`config/train/suites/ablation_f2.yaml`).
