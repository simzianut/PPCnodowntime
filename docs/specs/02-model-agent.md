# Spec 02: EV demand model + decision agent

Owner folder: `model/`, `artifacts/`. Two parts: a **demand forecaster** that predicts EV arrivals, and a **decision agent** that chooses each charge point's power state. Both live in this module. The simulator (M4) only calls them.

Decisions: [0001](../decisions/0001-rl-agent-with-forecaster.md), [0010](../decisions/0010-weather-donor-train-ro-run.md).

## 1. Interfaces (defined in CONTRACTS.md)

```python
class Forecaster:
    def predict(self, obs: ForecastInput) -> ForecastOutput: ...
    # per cp_id: p_arrival_15, p_arrival_30, p_arrival_60, expected_arrivals_60

class Policy:
    name: str
    def decide(self, state: SimState) -> list[Command]: ...
    # Command(cp_id, target_state ∈ {IDLE, STANDBY, DEEP_SLEEP}, reason: str)
```

Policies to ship: `always_on` and `naive_quiet_hours` (owned by M4, in `sim/baselines.py`), plus `cost_rule` and `rl_ppo` (owned here). Day-one stubs: `RandomPolicy` and `ConstantForecaster`, so M4 is never blocked.

Artifacts in `artifacts/`:
- `forecaster.lgb` and `forecaster_meta.json` (feature list, training period, metrics)
- `ppo_policy.zip` and `ppo_meta.json` (λ, observation spec, training steps)

Each must be under 20 MB.

## 2. Demand forecaster (must-have)

- **Unit:** one row per (`cp_id`, slot start `t`).
- **Targets:**
  - `y15`, `y30`, `y60`: whether a session arrives at this CP in `(t, t+H]`. Binary.
  - `n60`: number of arrivals at the **location** in the next 60 min. A count, used to sample signals in live mode.
- **Model:** LightGBM. Three binary classifiers plus one Poisson regressor. Use calibrated probabilities (isotonic, fitted on a time-split slice of the training period).
- **Features:**
  - Calendar: hour (sin and cos), weekday, `is_holiday`, minutes since midnight.
  - Weather: `temp_c`, `precip_mm`, `wind_kmh`.
    - **Training:** Dundee weather and GB-SCT holidays, matching each donor session's real conditions.
    - **Run time:** Romanian weather and holidays for the CP's county.
  - History, with no lookahead:
    - arrivals at this CP in the last 1h and 3h
    - arrivals in the same slot ±30 min over the last 4 same weekdays
    - whether the CP is currently charging
  - Station: `power_class`, `context_type`, `county_ev_per_1000`, `cps_at_location`.
  - Charge-point level (`cp_level`, decision 0079): arrivals per day over the last 7 and 28 days, minutes since the last arrival.
  - Site level (`site_level`, decision 0084): arrivals at the location in the last 1 h and 3 h, the location's same-slot mean over the last 4 weeks, its 7-day rate, charge points at the location charging now.
  - Signals at the location:
    - count of `ppcblue_route_intent` events with `eta` in `(t, t+15]`, `(t+15, t+30]` and `(t+30, t+60]`
    - `maps_ev_count` over the last 15 min
    - weighted flow = `ev_count + 0.05 × ice_count`
    - `traffic_index`
- **Leakage guard:** the `session_id` column in signals is never a feature. All features use only data at or before `t`.
- **Evaluation**, on the test split (2024–2025): AUC and Brier score per horizon and per `power_class`, plus reliability plots. Written to `forecaster_meta.json` and to `model/reports/`.
- **Validation against live data:** compare predicted occupancy by hour with occupancy observed in `live_status` for the hackathon hours. Report the gap; it is not a training target.
- **Two F2 models (decisions 0084, 0085):** `artifacts/f2_gru.pt` (since 0085 the three-seed `f2_gru_v2` ensemble trained on a Colab A100) (all feature groups, used for replay and the evaluator) and `artifacts/f2_gru_live.pt` (`calendar_history + cp_level + site_level`, no mocked app/maps signals and no traffic), which live mode serves. The live model's hazard logits get an affine calibration fitted on the real AmpWhere arrivals (`python -m model.live_calibration`, `artifacts/f2_live_calibration.json`). Training uses cold-start augmentation so the model has seen the empty history a live run starts with.

## 3. Cost-rule policy (must-have, fallback and comparison)

For each CP that is not CHARGING, at each slot:

```
E_save(s)  = (P_idle − P_s) × slot_seconds / 3.6e6             # kWh saved by sleeping in state s
C_wait(s)  = p_arrival_H(s) × wake_time(s) / 60 × λ            # λ in kWh per wait-minute (decision 0020)
choose s ∈ {IDLE, STANDBY, DEEP_SLEEP} maximising E_save(s) − C_wait(s)
```

- If there is a route intent with `eta` within `wake_time(s) + 5 min`, force the CP to IDLE (wake it).
- At least `min_awake_per_location` CPs (from `config/sim.yaml`, default 1) of each power class stay IDLE at each location, unless the forecast says no arrival in 60 min with p > 0.98.
- AC charge points have no DEEP_SLEEP, so choose only between IDLE and STANDBY for them.
- The λ sweep gives the energy-vs-wait curve.

## 4. RL agent: PPO (must-have, the main product)

- **Structure:** one shared per-charge-point policy, Stable-Baselines3 PPO with an MLP of [64, 64]. The same network decides for every CP independently.
- **Environment:** `sim.gym_env.ChargePointEnv` (owned by M4, see spec 04).
  - One episode = one CP over one day (288 slots), sampled at random from the training period.
  - The CP's siblings at the same location follow `cost_rule` during training (`naive_quiet_hours` until `cost_rule` exists, decision 0023). This is a documented approximation; at inference every CP uses `rl_ppo`.
- **Observation (float vector)**, built by `sim.obs.build_obs` (layout in CONTRACTS, decision 0017):
  - the forecaster outputs `p15`, `p30`, `p60`, `n60`
  - the route-intent counts by eta bucket
  - weighted EV flow, `traffic_index`
  - hour sin and cos, weekday one-hot, `is_holiday`
  - current state one-hot, time in the current state
  - siblings awake / siblings total, `power_class` one-hot
- **Actions:** `Discrete(3)` = {IDLE, STANDBY, DEEP_SLEEP}.
  - Invalid actions (DEEP_SLEEP on AC; anything while CHARGING) are masked by the environment and treated as "keep the current state".
- **Reward per slot:** `− overhead_kwh − λ × wait_min_caused`. `wait_min_caused` counts the driver wait this CP caused in this slot. Both terms are normalised by the always-on overhead per slot (decision 0020).
- **Training:**
  - 8 parallel environments, about 2 M steps, or as many as fit in a 2-hour budget.
  - Train for λ ∈ the grid in `config/sim.yaml`. If time is short, train a single λ.
  - Save the best checkpoint, chosen by validation reward on held-out training-period days.
- **Evaluation:** M4's evaluator runs all four policies on the **test-period** days. The results go to `artifacts/eval_results.json`, which the frontend's curve chart reads.
- **Fallback rule:** if PPO's (energy saved, wait) result is dominated by `cost_rule` at the same λ, the demo still shows both and the PRD claims are based on the better one. Record which one in a decision file.

## 5. Live-mode signal sampling (must-have)

Since decision [0093](../decisions/0093-no-mocked-inputs-in-decision-path.md) live runs and collected-day replays feed no signals by default (`LIVE_SIGNALS=off`); this sampler runs only with the explicit `LIVE_SIGNALS=mock` opt-in.

In live mode there is no future ground truth. `model.live_signals.sample(location, t)` therefore does the following:
- Draws `ppcblue_route_intent` events from `Poisson(p_app × n60_forecast)`, with etas spread over the next 60 min.
- Draws `maps_ev_count` and `maps_ice_count` using the same formulas as spec 01 §5, with the forecast in place of true arrivals.
- When `live_status` shows a CP switching to CHARGING and an intent sampled earlier exists, the intent is marked confirmed. Otherwise a confirmed late intent is added after the fact, so the demo stays consistent.

## 6. Must-have vs stretch

| Must-have | Stretch |
|---|---|
| Forecaster + metrics | Hyperparameter search |
| Cost rule + λ sweep | PPO trained per λ on the full grid |
| PPO at one λ, beats or ties the naive baseline | Siblings also driven by PPO during training (self-play) |
| `eval_results.json` | LLM explainer: turns `Command.reason` + features into a plain-language explanation |

## 7. Acceptance criteria
- Forecaster AUC ≥ 0.75 for `y30` on the test split, overall. If it's lower, write a decision file explaining why.
- Every policy satisfies the `Policy` interface and runs in the engine on the fixture data.
- `rl_ppo` saves more energy than `naive_quiet_hours` at equal or lower total added wait, on the test days.
- `decide()` takes under 50 ms per slot for 1,500 CPs, so the agent can run live.

## 8. Training runs (decisions 0051–0058)

Every model is trained through `python -m model.experiments` (CONTRACTS "Training runs"), on the GPU server in Docker (`docker/train.Dockerfile`, `GPU_ID` selects the host GPU) or locally with `make train-local`.

- **Output:** each run writes `runs/<name>-<sha8>-s<seed>/`, with a manifest for reproducibility.
- **Artifacts:** `promote` is the only way a run's model reaches `artifacts/`.
- **Models compared:**
  - forecasters F0 seasonal, F1 LightGBM, F2 GRU, plus a feature ablation and sensitivity suite (0052)
  - policies MaskablePPO, Double DQN and a hindsight oracle bound (0053)
- **F2 v2 (0084):** suite `config/train/suites/f2_v2.yaml` (baseline rerun, v2 full × 3 seeds, live × 3 seeds), then `python -m model.experiments ensemble` per triple and `promote --as f2_gru_live.pt` for the live model. Without the team server: `docker/pack_cloud.sh` on the laptop, `docker/train_cloud.sh all` on any CUDA box.
