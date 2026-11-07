# 0053: RL agents MaskablePPO and Double DQN, plus a hindsight oracle bound

- Date: 2026-11-07
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: —
- Affects: spec 02 §4, model/, config/train/

## Context
The current PPO (0044, 0050) was trained for 25k steps, about 87 episodes. It masks invalid actions only at inference, so in training the policy samples actions the env silently ignores. One RL algorithm with no upper bound gives no sense of how good the agent is.

## Decision
- **MaskablePPO** (`sb3-contrib`): uses `ChargePointEnv.action_masks()` during training and inference. Parallel envs with `SubprocVecEnv`.
- **Double DQN:** SB3's DQN with the Double-DQN target (the online net picks the next action, the target net scores it). Invalid actions are masked in exploration, in action selection and in the target `argmax`. The mask is rebuilt from the observation (state and power-class one-hots), matching `sim.obs.action_mask`. Reason to test it: costly events (a car meeting a sleeping CP) are rare, and the replay buffer reuses them.
- **Hindsight oracle** (`kind: bound`): knows the true arrivals of its own CP and sleeps in the deepest valid state whenever the next arrival is far enough away, waking `wake_time` early. It gives the best possible savings at zero added wait, so results can be quoted as "% of the oracle". It is never used in live mode.
- **Model selection:** each run evaluates on held-out training-period days and keeps the best checkpoint. Final comparison on test days stays with the evaluator (T11).
- **Wake cost:** read from `config/power_profiles.yaml` `wake_extra_wh` through the engine, so every policy pays it. M3 will set the value. No separate RL-only penalty.
- Edy's plain PPO (`model/train_rl.py`, `artifacts/ppo_policy.zip`) stays as it is, for comparison.

## Alternatives considered
- Recurrent PPO (LSTM policy): the forecaster already summarises history; it adds tuning time.
- One central agent for all CPs: 3^1364 joint actions.
- An RL-only switching penalty: it would make the comparison with the cost rule unfair.

## Consequences
- While `wake_extra_wh` is 0, each slot's decision barely affects later ones, so RL is expected to tie the cost rule rather than beat it.
- RL runs need several seeds. Results are reported as mean ± spread.
