# 0069: RL is dropped; the agent is the F2 GRU forecaster plus the cost rule, served in the rl_ppo slot

- Date: 2026-11-08
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: 0001 (RL as the main product), 0061 (only its `auto` rule for RL_AGENT)
- Affects: PRD, spec 02, CONTRACTS (RL_AGENT, forecasts coverage, replay days), model/, sim/evaluate.py, api/engine_mode.py

## Context
On the GPU server (`runs/suites/forecasters-b2e500c1`), F2 GRU beat F1 LightGBM and F0 on every metric and every seed (0052, 0059). Mean over 3 seeds on the 2024–25 test sample:
- y30 AUC: F0 0.797 / F1 0.940 / F2 0.951
- y30 PR-AUC: 0.072 / 0.582 / 0.623 (base rate about 0.019)
- y15 PR-AUC: F1 0.542 / F2 0.642

The RL agents were never trained at full scale, and the only RL artifact on main (PPO, 25k steps) made 94% of drivers wait in a replay. The team drops RL and makes the GRU the core of the agent.

## Decision
- **The agent** is `CostRulePolicy` driven by F2 GRU forecasts: GRU arrival probabilities → cost rule → IDLE / STANDBY / DEEP_SLEEP, with wake on PPC Blue intent (spec 02 §3).
- It is served under the existing PolicyName **`rl_ppo`**, so the frontend (sidebar "agent vs always on", 0065) works unchanged. `RL_AGENT` gains the value `gru`, and `auto` now means `gru` when `artifacts/f2_gru.pt` exists. The other values are kept for the record.
- The engine-level forecaster is F2 everywhere, so `cost_rule` also uses F2. At the same λ, `cost_rule` and `rl_ppo` therefore behave the same; the λ sweep of `cost_rule` is the agent's energy-vs-wait curve. `rl_maskppo` and `rl_ddqn` stay in the code but are no longer part of the app.
- **Model:** F2 seed 2 (`f2_gru-c75aafc1-s2`), the best seed on y30 Brier and PR-AUC by a hair, promoted to `artifacts/f2_gru.pt` with a provenance file.
- **Replay and evaluation** use precomputed F2 forecasts for a **60-day demo window** of test days, 2025-03-01..2025-04-29 (about 11M rows, includes Orthodox Easter 2025-04-20). The API and the evaluator offer only the days the forecasts cover. A `predict` command builds the file from the model.
- **Live mode** uses the same F2 model online (0070).

## Alternatives considered
- A new PolicyName `agent`: clearer, but needs a frontend change that its owner has not scheduled.
- F1 for live and F2 only for replay: two models to explain. F2 is better and small enough for CPU inference.
- All 608 test days: about 117M rows and about 3 GB of extra RAM in the API on every laptop.

## Consequences
- The name `rl_ppo` now means "the agent", not PPO. The pitch must say "trained forecaster + decision rule", not RL.
- `data/processed/forecasts.parquet` is private data and is shared like the other data zips.
- The ablation (0052) is postponed.
