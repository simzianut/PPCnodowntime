# 0023: Gym env: naive siblings until cost_rule exists, optional days list; min_awake is a policy rule

- Date: 2026-11-07
- Who: tudor-claude
- Status: accepted
- Supersedes: —
- Affects: CONTRACTS, spec 02 §3–4, spec 04 §4

## Context
The gym env's default sibling policy is `cost_rule`, which does not exist yet (T13 waits on T7/T8). With `always_on` siblings, handover makes sleeping free, so the agent would learn to sleep everything. M2 also needs validation days held out from the training period.

## Decision
- `ChargePointEnv(split, lambda_wait, sibling_policy=None, seed=0, days=None, data=None)`. `sibling_policy=None` means `naive_quiet_hours` until `cost_rule` lands, then `cost_rule`.
- `days` restricts episode sampling to a list of dates. `data` is a `sim.data.SimData` bundle; if omitted, the env loads `data/processed/`.
- `min_awake_per_location` is applied by `cost_rule` only. The engine does not enforce it for other policies.

## Alternatives considered
- `always_on` siblings: biases training.
- An M4-written cost-rule stub: duplicates T13's work.
- An engine-level min-awake guard: caps what RL can save and changes the baselines.

## Consequences
When T13 ships `cost_rule`, switch the default in a small follow-up. PPO and naive may sleep every CP at a location; the metrics show the consequence.
