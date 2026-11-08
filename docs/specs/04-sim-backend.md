# Spec 04: Simulator backend

Owner folders: `sim/` (engine, Gymnasium environment, baselines, evaluator), `api/` (FastAPI) and `config/sim.yaml`. One engine serves three uses:
- **RL training:** headless and fast.
- **Replay:** a test day played back at any speed, with all policies side by side. A collected AmpWhere day replays the same way on its observed statuses ([0090](../decisions/0090-replay-collected-days.md), `api/collected_replay.py`).
- **Live mode:** the agent acting on the real statuses collected today.

Decisions: [0011](../decisions/0011-shared-slot-engine.md), [0017](../decisions/0017-shared-sim-types.md) (types in `sim/types.py`, observation builder `sim/obs.py`), [0018](../decisions/0018-engine-transition-rules.md) (transitions), [0019](../decisions/0019-arrival-handling-and-wait.md) (arrivals), [0020](../decisions/0020-lambda-unit-and-reward.md) (λ unit), [0021](../decisions/0021-precomputed-forecasts.md) (forecast table), [0022](../decisions/0022-naive-baseline-window.md) (naive window), [0023](../decisions/0023-gym-env-defaults.md) (gym env), [0024](../decisions/0024-synthetic-sim-data.md) (synthetic data), [0025](../decisions/0025-recommend-in-replay.md) (RECOMMEND in replay), [0026](../decisions/0026-engine-run-defaults.md) (run defaults).

## 1. Engine (`sim/engine.py`, must-have)

- **Inputs:**
  - the charge points from M3 (`stations.store`)
  - the power profiles
  - sessions, signals and context for the chosen date range, from M1's Parquet files or the fixtures
  - one or more `Policy` objects from M2
  - an autonomy mode
- **Time:** 5-minute slots in `Europe/Bucharest`, from local midnight to the next local midnight (288 slots; 276 or 300 on DST-change days, decision [0034](../decisions/0034-engine-implementation-details.md)). Each slot runs in this order:
  1. **Observe:** build a `SimState`: the time, each CP's state and time in state, signals with `ts` in `(t − 60 min, t]`, and the forecaster's output (from the precomputed `forecasts.parquet` in training and replay, decision 0021).
  2. **Decide:** each policy's `decide(SimState)` returns `Command`s. In RECOMMEND mode, `focus_policy`'s commands become pending recommendations; other policies act. They are applied at the next slot once approved, through the API, and expire after the TTL in sim time. A replay pauses itself when new recommendations appear (decision 0025).
  3. **Apply:** apply the commands, following the transition rules in spec 03 §4 and decision 0018 (`sim/transitions.py`).
  4. **Events:** process the slot's arrivals and departures in time order, at second precision:
     - **Arrival at a CP:** if the CP is IDLE, it starts CHARGING. Otherwise, if a **compatible sibling** at the same location is IDLE, the driver moves to it and waits 0 (lowest CP index first). Compatible means same `current_type` and a shared connector; when connectors are unknown, same `current_type` and `power_class`.
     - Otherwise the driver uses the compatible CP (own or sibling) that is asleep or WAKING and ready soonest. Wait = remaining wake time (from STANDBY, DEEP_SLEEP or partway through WAKING), attributed to that CP. The CP goes to WAKING, then CHARGING when ready. The departure time moves later by the wait.
     - If every compatible CP is busy charging, the driver queues for the first to free up. It's the same queueing as always-on, so it counts as **not** added wait (decision 0019).
  5. **Account:** add overhead energy per CP, `power(state) × seconds in state`, inside the slot, plus `wake_extra_wh` per wake.
- **Minute ticks** ([0091](../decisions/0091-minute-ticks-real-time-speeds.md)): `Engine.step()` simulates a whole slot (training, evaluation, gym env). `Engine.step_minute()` advances the clock one minute instead. The slot's first minute runs steps 1–3, and each minute processes only the events inside it (step 4). The energy of step 5 is still added to the metrics at the slot end, so minute-ticked and slot-stepped days end with identical KPIs; mid-slot KPIs include the slot's open part. `run_to_minute(m)` runs whole slots, then the remaining minutes. The live engine applies each observed status change on its own minute.
- **Policies run side by side:** every policy gets its own copy of the CP states, driven by the **same** sessions and signals. Results are directly comparable.
- **Determinism:** seeded. The same inputs produce the same results.
- **Speed target:** one day, all real CPs (~500, decision 0026), 4 policies in under 20 s headless. Use vectorised per-CP arrays where possible.

## 2. Metrics (`sim/metrics.py`)

Per policy, as running totals and per slot:

| Field | Definition |
|---|---|
| `overhead_kwh` | Overhead energy consumed |
| `saved_kwh`, `saved_pct` | Compared with `always_on` in the same run |
| `wait_min_total`, `wait_min_mean`, `wait_min_p95` | Added driver wait (sessions with wait 0 are included in the mean; p95 is over all sessions) |
| `sessions`, `waited_sessions` | Helper counts |
| `sleep_share` | Share of CP time spent in STANDBY or DEEP_SLEEP |
| `overhead_ron`, `saved_ron` | The matching kWh × the net B2B tariff from `stations.store.get_energy_tariffs()` (1.15 RON/kWh, decision [0049](../decisions/0049-t5-data-in-sim-ron-kpis-run-history.md)) |

Each CP's watts come from `stations.store.get_power_profile(power_class, max_kw)` (the capacity table of decision 0048) unless the engine is given explicit profiles (decision 0049).

Drivers are never lost (decision 0006).

## 3. Baselines (`sim/baselines.py`, must-have)

- `always_on`: never sends a command, so every CP is IDLE when not charging.
- `naive_quiet_hours`:
  - From the **training period**, compute each CP's arrival rate by hour of the week.
  - Sleep the CP during the one contiguous window of `naive_quiet_hours_per_day` hours (default 3, in `config/sim.yaml`) with the lowest summed rate for that weekday; ties go to the window centred nearest 03:00 (decision 0022). Use the deepest state available, wake at the end of the window, and never wake early.
  - This is the "Ampwhere usage → switch off when least used" baseline from the original brief.

## 4. Gymnasium environment (`sim/gym_env.py`, must-have, needed early by M2)

`ChargePointEnv`:
- Episode = one CP × one day, sampled from a split.
- The CP's siblings follow a policy you pass in (default `naive_quiet_hours` until `cost_rule` exists, decision 0023). `days=` restricts sampling to given dates.
- If `model.cost_rule` is importable it is the default sibling policy; `env.sibling_policy_name` says which one is used. Implementation: [0037](../decisions/0037-gym-env-implementation.md).
- Data comes from `sim.data.SimData`: `data/processed/` when present, else the seeded generator in `sim/synthetic.py` (decision 0024).
- Observation and action spaces are exactly those in spec 02 §4, with action masking.
- Reward is as in spec 02 §4, with λ in kWh per wait-minute (decision 0020). Pass `lambda_wait` in, so the training loop can sweep it.

Ship a version on fixtures in the **first few hours**, so M2 can start PPO training.

## 5. Evaluator (`python -m sim.evaluate`, must-have)

- Runs all policies over every test day (or a sample set in `config/sim.yaml`), and over the λ grid for `cost_rule` and `rl_ppo`.
- Writes `artifacts/eval_results.json`: metrics per policy and λ, and the energy-vs-wait curve points. The schema is in CONTRACTS.md.

## 6. API (`api/`, FastAPI on :8000)

Full request and response schemas are in `docs/CONTRACTS.md`. Summary:

| Route | Purpose |
|---|---|
| `GET /api/health` | Liveness check |
| `GET /api/stations` | Locations + CPs (+ live state in live mode). Query: `bbox`, `county` |
| `GET /api/stations/{location_id}` | One location with its CPs |
| `GET /api/policies` | Available policies + their λ values |
| `GET /api/settings/autonomy` / `PUT` | Global autonomy switch, `ACT` or `RECOMMEND` |
| `POST /api/runs` | Start a run: `{mode: replay\|live, date, policies[], focus_policy, lambda, speed}` → `run_id` |
| `GET /api/runs/{run_id}` | Status + KPIs so far |
| `POST /api/runs/{run_id}/control` | `{action: play\|pause\|speed\|seek\|focus, value}` |
| `WS /ws/runs/{run_id}` | Stream of tick messages |
| `GET /api/runs/{run_id}/cp/{cp_id}/timeline` | States, arrivals, waits, forecast vs actual, commands for one CP |
| `GET /api/recommendations?run_id&status=pending` | Recommendation feed |
| `POST /api/recommendations/{id}` | `{decision: approve\|reject}` |
| `GET /api/eval` | Contents of `artifacts/eval_results.json` |

**Replay runner** ([0091](../decisions/0091-minute-ticks-real-time-speeds.md)):
- An asyncio task advances the engine one simulated minute per tick and sleeps `60 / speed` wall seconds minus the time the tick took. `speed` is simulated seconds per wall second: 1, 15, 60 or 300.
- After each tick it sends one `tick` message: sim time, `minute`, `slot`, the KPIs of every policy, and **only the CPs whose state changed** under `focus_policy` during that minute.
- `seek` takes a minute of the day, clamped to the run's `window`. It runs the engine forward to that minute, or re-runs it from midnight (from the window start for a collected day) when going back, without sending anything. Past operator decisions are not replayed; their recommendations count as expired.

**Live mode:**
- The run ticks minute m once the clock passes m + one slot + `LIVE_LAG_S`. At each slot start it reads the statuses collected so far and feeds the slot's observed events, which then apply on their own minute (decision 0091). AVAILABLE becomes IDLE unless the agent has put the CP to sleep.
- Build signals with `model.live_signals`, call the policy, and apply or recommend its commands through `stations.store`.
- Send ticks over the same WebSocket.

**Engine mode (T23, default):** with `API_FIXTURES` unset, `api/engine_mode.py` runs each replay on `sim.Engine` in memory, with no DB. Stations come from `sim.stations_seed.load_stations()`: the real list when `data/processed/` has it, else the open seed. Sessions are real processed data or synthetic sessions on those stations. Live mode returns 400 until T12. Details in [0047](../decisions/0047-engine-backed-api-and-station-loader.md). Autonomy is stored through `stations.store`, and when the DB is up each run writes its focus policy's commands and state changes with its `run_id` ([0049](../decisions/0049-t5-data-in-sim-ron-kpis-run-history.md)).

**Fixture mode (T19):** `API_FIXTURES=1` serves every route from `fixtures/api/`, so M5 can integrate before the engine is ready. Stations are the 116 seed locations with provisional IDs (decision 0024). Runs play a recorded day of 288 slots, one tick per minute with each slot's changes on its first minute, and honour play, pause, speed, seek, focus and recommendations. Regenerate with `python -m api.fixtures_gen`; details in [0036](../decisions/0036-api-fixture-mode.md).

## 7. Config: `config/sim.yaml`

```yaml
slot_s: 300
lambda_grid: [0.01, 0.03, 0.1, 0.3, 1.0]
default_lambda: 0.1
naive_quiet_hours_per_day: 3
min_awake_per_location: 1
recommendation_ttl_slots: 3
eval_days: all      # or a list of dates
```

## 8. Must-have vs stretch

| Must-have | Stretch |
|---|---|
| Engine, metrics, baselines, gym env, evaluator | Multi-day replay runs |
| REST + WS replay, recommendations, autonomy, fixture mode | Live mode backed by a real PPC feed |
| Live mode backed by `live_status` | Per-run export (CSV) |

## 9. Acceptance criteria
- With `always_on`, `saved_kwh = 0` and wait = 0. A test checks this.
- A hand-built 1-CP scenario (arrival during DEEP_SLEEP) gives exactly `deep_sleep_wake_s / 60` minutes of wait.
- A replay at speed 300 plays 5 simulated minutes per wall second (a day in 4.8 min) and streams ticks without blocking the API. With the wall clock scaled down (`api.replay_clock.WALL_SCALE`), a whole fixture day replays in under 10 s.
- Every route matches CONTRACTS.md. A test checks the `fixtures/` responses against the Pydantic models.
