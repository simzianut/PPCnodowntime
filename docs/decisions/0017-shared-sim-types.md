# 0017: Shared simulation types live in sim/types.py; M4 owns the RL observation builder

- Date: 2026-11-07
- Who: tudor-claude
- Status: accepted
- Supersedes: —
- Affects: CONTRACTS, spec 02, spec 04, model/

## Context
CONTRACTS defines `CpView`, `SimState`, `Command`, `Policy` and `Forecaster` but no module path. Both `model/` and `sim/` need them. The gym env (M4) and `rl_ppo` at inference (M2) must build the same observation vector, or training and inference drift apart.

## Decision
- The types, the `State`/`TargetState`/`PowerClass`/`PolicyName` enums and the protocols live in `sim/types.py`, owned by M4. `model/` imports them.
- Policies and forecasters receive their history (training sessions, quiet-hour rates, models) when they are constructed. `SimState` carries only what is visible at `t`.
- The observation vector is built by `sim.obs.build_obs(state, cp_ids)`, owned by M4. Its layout is in CONTRACTS. `rl_ppo` imports it.

## Alternatives considered
- `model/types.py`: `model/` already imports `sim.gym_env`, so the dependency would go both ways.
- A new shared package: adds a folder nobody owns.
- M2 owns the observation builder: the gym env would wait for it.

## Consequences
M2 must import from `sim`. A change to the observation layout is a contract change and breaks trained checkpoints.
