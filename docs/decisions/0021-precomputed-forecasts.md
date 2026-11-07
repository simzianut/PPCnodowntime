# 0021: Training and evaluation use a precomputed forecast table; SimState.signals holds the last 60 minutes

- Date: 2026-11-07
- Who: tudor-claude
- Status: accepted
- Supersedes: —
- Affects: CONTRACTS, spec 02, spec 04 §1

## Context
PPO needs about 2 M steps in about 2 hours. Building a pandas `SimState` and calling LightGBM for the CP and its siblings every step would not keep up. `SimState.signals` defined as every signal with `ts ≤ t` grows all day.

## Decision
- M2 batch-predicts the whole period once into `data/processed/forecasts.parquet` (schema in CONTRACTS). The engine wraps it as `sim.forecast.PrecomputedForecaster` (lookup by slot). An online `Forecaster.predict(SimState)` is used only in live mode.
- In that table the "CP is currently charging" feature comes from ground-truth sessions, not from each policy's simulated state. Small approximation: they differ only after a driver moves to a sibling.
- `SimState.signals` holds only rows with `ts ∈ (t − 60 min, t]`, pre-indexed by slot. This covers every feature in spec 02.

## Alternatives considered
- Online prediction every slot, as the contract first said: too slow for training.

## Consequences
M2 must produce the forecast table before real training and evaluation. Until then the synthetic data bundle carries a noisy oracle forecast (decision 0024).
