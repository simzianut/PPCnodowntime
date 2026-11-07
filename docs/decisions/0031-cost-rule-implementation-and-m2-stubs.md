# 0031: M2 day-one stubs (ConstantForecaster, RandomPolicy), contract type fallback, and cost_rule implementation

- Date: 2026-11-07
- Who: edy-antigravity
- Status: accepted
- Supersedes: —
- Affects: spec 02, CONTRACTS, model/

## Context
M4 (simulator engine and gym environment) requires day-one stubs (`ConstantForecaster`, `RandomPolicy`) to run end-to-end without waiting for ML training. Furthermore, decision 0017 assigned shared simulation types to `sim/types.py` (owned by M4 in T10), which is currently in progress. Spec 02 §3 also requires an analytical `cost_rule` policy that trades energy savings against driver wait, respecting route intent overrides and `min_awake_per_location` constraints.

## Decision
1. **Fallback types in `model/types.py`:** Imports from `sim.types` if available; if not yet present on the current branch, provides identical dataclasses and enums matching `docs/CONTRACTS.md`.
2. **Stubs in `model/stubs.py`:**
   - `ConstantForecaster`: produces constant calibrated probability outputs matching the forecast schema (`cp_id`, `p_arrival_15`, `p_arrival_30`, `p_arrival_60`, `expected_arrivals_60`).
   - `RandomPolicy`: produces valid state commands, respecting AC constraints (no DEEP_SLEEP) and skipping CHARGING or OFFLINE charge points.
3. **`CostRulePolicy` in `model/cost_rule.py`:**
   - Evaluates $E_{save}(s) = \frac{(P_{idle} - P_s) \times slot\_s}{3.6 \times 10^6}$ (in kWh) and $C_{wait}(s) = p_{arrival\_H}(s) \times \frac{wake\_time(s)}{60} \times \lambda$ (in kWh, decision 0020).
   - $H$ is chosen as the smallest horizon $\ge wake\_time(s) + slot\_s$.
   - Any imminent `ppcblue_route_intent` signal with $eta \le wake\_time(s) + 300\text{ s}$ forces the charge point to `IDLE` (wake up).
   - Enforces `min_awake_per_location` per power class unless the forecast guarantees no arrival in 60 min with $p > 0.98$ ($p\_arrival\_60 < 0.02$).

## Alternatives considered
- Waiting for `sim.types` to land from T10: rejected because it would delay M2 stubs and block M4 integration.
- Enforcing `min_awake` in the engine: rejected because decision 0023 established that min_awake is a policy-specific rule.

## Consequences
- M4 can immediately run and test with `from model import ConstantForecaster, RandomPolicy, CostRulePolicy`.
- Unit tests in `model/tests/` verify stub behavior and analytical optimization rules independently of training data.
