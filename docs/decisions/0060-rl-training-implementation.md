# 0060: RL training implementation: episode sampling, validation, references, hyperparameters

- Date: 2026-11-07
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: —
- Affects: spec 02 §4, model/rl_common.py, model/ddqn.py, model/oracle.py, model/rl_policies.py, model/experiments/rl_maskppo.py, rl_ddqn.py, oracle.py, config/train/

## Context
Decision 0053 chose MaskablePPO, Double DQN and a hindsight oracle. Training them on real data forced choices about which episodes to sample, how to validate, and what to compare against.

## Decision
- **Episodes:** `EpisodeEnv` subclasses `ChargePointEnv` (no changes to `sim/`).
  - It samples only active (CP, day) pairs from `cp_active_days`.
  - DC CPs are oversampled ×3 (`params.dc_oversample`), because DC is where the savings are.
- **Validation:**
  - It uses the last 60 days of the train split. The test split is left for the evaluator.
  - It runs a fixed list of 400 episodes drawn with `eval_seed`, so every agent, λ and seed is scored on the same episodes.
  - Validation runs every 250k steps. `checkpoints/best.zip` (best validation reward) becomes the run's model.
- **References in the same engine:** each validation episode runs the agent, always_on, the full `CostRulePolicy` and the oracle side by side on one `Engine`, and reads the numbers at the agent CP. `metrics.json` reports saved %, wait (total, mean, p95), waited sessions and reward for each, plus `pct_of_oracle_savings`.
- **No observation or reward normalisation:** observations are already about [0, 1].
- **Double DQN:** a `MaskedDoubleDQN` subclass of SB3 DQN.
  - Exploration, greedy actions and the bootstrap argmax use only valid actions.
  - The mask comes from the observation columns; a test checks it equals `sim.obs.action_mask` on more than 500 real states.
- **Oracle:**
  - It uses only its own CP's true arrivals (no sibling routing).
  - It picks the deepest state whose wake time + one slot fits before the next arrival and whose saving exceeds `wake_extra_wh`, and wakes one slot early.
  - Agents that accept waits can therefore save slightly more than it, so `pct_of_oracle_savings` can exceed 100%. It is a zero-wait reference, not a strict upper bound.
- **Forecasts:** `data.forecasts` is an optional path to a T26 run's `forecasts.parquet`. Without it the run warns loudly and uses `ZeroForecaster`. The key must be omitted, not set to null.
- **Hyperparameters:**
  - MaskablePPO: 1M steps, 16 subproc envs, `n_steps` 256, batch 512, net [64, 64].
  - DDQN: 1M steps, 4 envs, buffer 200k, `learning_starts` 10k, `train_freq` 4, target update every 2,000 steps, ε from 1.0 to 0.05 over the first 30%.
  - Both on CPU (0051).
- **App adapters:** `MaskablePPOPolicy` and `DDQNPolicy` (`model/rl_policies.py`) implement the sim `Policy` protocol in batch over all CPs.
- **Timings** go in metrics.json under `perf` and are excluded from the determinism check.

## Alternatives considered
- Uniform CP sampling: most episodes would be AC, where sleeping saves almost nothing.
- A strict oracle that also uses sibling routing: more complex, and not needed for a zero-wait reference.

## Consequences
- RL quality depends on the forecaster. On the server, train F1 first and pass its `forecasts.parquet` to the RL runs.
- Validation costs about 1.2 s per episode, so about 8 min per validation at 400 episodes.
- Smoke-scale throughput is about 160 env steps/s per process. DDQN with 4 envs needs about 1.7 h per 1M steps.
