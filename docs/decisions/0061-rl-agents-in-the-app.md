# 0061: New RL agents reach the app as new policy names, plus an RL_AGENT switch for rl_ppo

- Date: 2026-11-07
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: —
- Affects: CONTRACTS (PolicyName, GET /api/policies, env vars), sim/types.py, sim/evaluate.py, api/engine_mode.py, frontend (later)

## Context
MaskablePPO and Double DQN (0053, 0060) have `Policy` adapters, but the simulator, evaluator and API only know `rl_ppo` (edy's plain PPO, 0044). The frontend hardcodes the four original policy names, and its owner is still working on it.

## Decision
- Add `rl_maskppo`, `rl_ddqn` and `oracle` to `PolicyName`. The evaluator and the API real mode run them when available:
  - `rl_maskppo` needs `artifacts/maskppo_policy.zip`
  - `rl_ddqn` needs `artifacts/ddqn_policy.zip`
  - when an artifact is missing, `GET /api/policies` reports that policy with `available: false`
- `oracle` uses the true future arrivals, so it runs in **replay and evaluation only**. A live run that requests it gets 400.
- The env var `RL_AGENT` (`ppo | maskppo | ddqn | auto`, default `auto`) picks the agent behind the existing `rl_ppo` slot. `auto` uses MaskablePPO when its artifact exists, else edy's PPO. The frontend's "RL agent" therefore shows the best promoted agent with no frontend change. The slot keeps the name `rl_ppo` in every payload.
- Teodora gets a TASKS note to add the new names and labels to the UI when she has time.

## Alternatives considered
- New names only: nothing new would be visible in the demo until the frontend changes.
- Replace `rl_ppo` with MaskablePPO everywhere: simplest, but loses the PPO vs MaskablePPO comparison.

## Consequences
- The name `rl_ppo` no longer always means plain PPO. Logs and the evaluator record which implementation served it.
- Until a full MaskablePPO run is promoted, `auto` keeps serving edy's PPO.
