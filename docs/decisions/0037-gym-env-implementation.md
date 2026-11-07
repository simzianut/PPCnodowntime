# 0037: Gym env implementation: location-day engine, cached slices, cost_rule auto-detect

- Date: 2026-11-07
- Who: tudor-claude
- Status: accepted
- Supersedes: —
- Affects: spec 04 §4, spec 02 §4, sim/gym_env.py, M2

## Context
Decision 0023 fixes the env's interface. M2's PPO needs about 2 M steps in about 2 hours, roughly 280 steps/s across 8 envs.

## Decision
- Each episode runs the full `Engine` over the agent CP's whole location for one day, with a single internal policy `"agent"`: siblings follow the sibling policy, the agent CP gets only the env's command (`reason="rl"`). Masked actions send no command.
- Default sibling policy: `model.cost_rule.CostRulePolicy` if it can be imported (T13), otherwise `NaiveQuietHours`. `env.sibling_policy_name` shows which one is in use; a broken `model.cost_rule` import also falls back silently, so check it.
- Data slices per (location, day) are cached (up to 1,024 per process, about 200 KB each). Observation columns 0–18 don't depend on the policy, so they are computed once per location-day and reused; columns 19–30 are computed each step and tested against `build_obs`.
- The default data bundle is loaded once per process and shared.
- Terminal observation: built at the end-of-day time like any other, with the real final state and a zero forecast (there is no forecast row for the next midnight).
- Measured on synthetic data, one process: about 650–830 steps/s on a first visit to a location-day, about 2,100 when cached.

## Alternatives considered
- A separate single-CP simulator: would drift from the engine (decision 0011).
- Always naive siblings: biases training towards sleeping when `cost_rule` exists (decision 0023).

## Consequences
- The synthetic bundle uses about 760 MB per process. Prefer 8 envs in one process (`DummyVecEnv`) over 8 subprocesses, or pass a smaller `data`.
- Possible speed-ups, if needed: a whole-day vectorised observation builder, a numpy-backed forecast instead of a DataFrame per slot, and an engine option to skip the hidden always_on run.
