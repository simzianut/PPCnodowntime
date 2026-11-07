# 0071: F2 agent implementation: promoted model, UTC forecast window, online GRU forecaster

- Date: 2026-11-08
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: —
- Affects: artifacts/, model/gru_forecaster.py, model/rl_select.py, sim/data.py, sim/forecast.py, sim/evaluate.py, api/engine_mode.py

## Context
Decision 0069 made F2 GRU + cost rule the agent. Building it showed details that 0069 did not fix.

## Decision
- **Promoted models:** `artifacts/f2_gru.pt` is `f2_gru-c75aafc1-s2`, trained on the A100 with git 99c58d6: test y30 AUC 0.951, PR-AUC 0.624, Brier 0.0104, 21,997 parameters. `artifacts/f0_rates.parquet` is `f0_seasonal-607c1c1f-s0`, used for live signals (0070). Both have provenance files.
- **The forecast window is given in UTC days.** `forecasts.parquet` timestamps are UTC, so the 60-day window is generated with `--start 2025-02-28 --end 2025-04-29` to fully cover the local days 2025-03-01..2025-04-29. On the laptop that is 9.8M rows, 158 MB, 5 min, 1.6 GB peak.
- **Signals follow the forecasts.** When `forecasts.parquet` exists, `sim.data.load_processed` loads signals for its time range instead of the fixed 2023 window (`SIM_SIGNALS_START`/`SIM_SIGNALS_END` override it). Before this, replays of 2024–25 test days had no signals at all.
- **Covered days only:** with `forecasts.parquet` present, API replay days and the evaluator's default days are the local days it fully covers.
- **Online `GRUForecaster`** (`model/gru_forecaster.py`):
  - It keeps a rolling 36-slot history per CP, built only from `SimState`.
  - It matches the batch `predict_days` to under 1e-4 from slot 24 of a day; the first slots differ because training used UTC-day windows.
  - In live mode it infers arrivals from CHARGING transitions.
- **Agent selection:** `RL_AGENT=gru`, and `auto` picks `gru` when `artifacts/f2_gru.pt` exists. `rl_ppo` is then a `CostRulePolicy` on the engine's F2 forecasts.

## Consequences
- **First results** on 2 real test days with real F2 forecasts (2025-03-12, 2025-04-20, 1,096 sessions):

  | Policy | Saved | Mean wait | Waited sessions |
  |---|---|---|---|
  | agent (λ 0.1) | 55.1% | 0.04 min | 27 |
  | naive | 9.5% | — | 63 |
  | oracle | 78.5% | 0 | 0 |

- The API loads 2025 signals when forecasts are present, which uses more RAM than the 2023 window.
