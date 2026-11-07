# 0055: Evaluator multi-policy engine runner and eval_results artifact

- Date: 2026-11-07
- Who: edy-antigravity
- Status: accepted
- Supersedes: —
- Affects: sim/evaluate.py, artifacts/eval_results.json, model/rl_agent.py, CONTRACTS

## Context
Spec 04 §5 requires an evaluator (`python -m sim.evaluate`) to run all policies (`always_on`, `naive_quiet_hours`, `cost_rule`, and `rl_ppo`) over test days and over the $\lambda$ grid (`config/sim.yaml`). It must output `artifacts/eval_results.json` adhering to the CONTRACTS schema so that the API (`GET /api/eval`) and frontend dashboard can display the energy-vs-wait Pareto curve and KPI comparisons.

## Decision
1. **Side-by-Side Multi-Policy Engine Execution (`sim/evaluate.py`):**
   - Policies across the $\lambda$ grid (`cost_rule` and `rl_ppo` at $\lambda \in [0.01, 0.03, 0.1, 0.3, 1.0]$) alongside baselines are executed in parallel inside a shared `sim.Engine` instance per test day using `_PolicyWrapper`.
   - Signals and context are loaded once per slot rather than duplicated across 12 separate engine instances.
2. **Per-Slot Cached Forecaster (`_SlotCachedForecaster`):**
   - Wraps the underlying forecaster (`PrecomputedForecaster` or `DemandForecaster`) so that identical slot queries across sibling policies reuse the evaluated prediction DataFrame, cutting redundant inference by 12x.
3. **$\lambda$-Aware RL Logit Scaling (`model/rl_agent.py`):**
   - To construct the full Pareto curve from the single trained RL policy ($\lambda_0 = 0.1$), inference applies a log-ratio shift:
     $\Delta \text{logits}_{\text{standby}} = -0.5 \ln(\lambda / \lambda_0)$ and $\Delta \text{logits}_{\text{deep\_sleep}} = -2.0 \ln(\lambda / \lambda_0)$.
   - Lower $\lambda$ favors deeper sleep and higher energy savings; higher $\lambda$ prioritizes zero wait time.
4. **Artifact Schema:**
   - Saved to `artifacts/eval_results.json` strictly matching `EvalResults` / `EvalRow` models without extraneous fields that would violate `extra="forbid"`.

## Consequences
- `GET /api/eval` serves 200 OK with valid evaluation results for all 12 policy and $\lambda$ combinations.
- The frontend energy-vs-wait curve and KPI panel are fully functional with model-backed data.
