# Spec 04: Simulator backend

Owner folders: `sim/` (engine, Gymnasium environment, baselines, evaluator), `api/` (FastAPI) and `config/sim.yaml`. One engine serves three uses:
- **RL training:** headless and fast.
- **Replay:** a test day played back at any speed, with all policies side by side.
- **Live mode:** the agent acting on the real statuses collected today.

Decision: [0011](../decisions/0011-shared-slot-engine.md).

## 1. Engine (`sim/engine.py`, must-have)

- **Inputs:**
  - the charge points from M3 (`stations.store`)
  - the power profiles
  - sessions, signals and context for the chosen date range, from M1's Parquet files or the fixtures
  - one or more `Policy` objects from M2
  - an autonomy mode
- **Time:** 5-minute slots in `Europe/Bucharest`. Each slot runs in this order:
  1. **Observe:** build a `SimState`: the time, each CP's state and time in state, signals visible up to `t`, and the forecaster's output.
  2. **Decide:** each policy's `decide(SimState)` returns `Command`s. In RECOMMEND mode, commands become pending recommendations. They are applied only once approved, through the API; replay auto-expires them after the TTL.
  3. **Apply:** apply the commands, following the transition rules in spec 03 §4.
  4. **Events:** process the slot's arrivals and departures in time order, at second precision:
     - **Arrival at a CP:** if the CP is IDLE, it starts CHARGING. Otherwise, if a **compatible sibling** at the same location is IDLE and free, the driver moves to it and waits 0. Compatible means same `current_type` and a shared connector.
     - Otherwise the driver waits for the sleeping CP. Wait = remaining wake time (from STANDBY, DEEP_SLEEP or partway through WAKING). The CP goes to WAKING, then CHARGING when ready. The departure time moves later by the wait.
     - If every compatible CP is busy charging, it's the same queueing as always-on, so it counts as **not** added wait.
  5. **Account:** add overhead energy per CP, `power(state) × seconds in state`, inside the slot, plus `wake_extra_wh` per wake.
- **Policies run side by side:** every policy gets its own copy of the CP states, driven by the **same** sessions and signals. Results are directly comparable.
- **Determinism:** seeded. The same inputs produce the same results.
- **Speed target:** one day, all CPs (~1,500), 4 policies in under 20 s headless. Use vectorised per-CP arrays where possible.

## 2. Metrics (`sim/metrics.py`)

Per policy, as running totals and per slot:

| Field | Definition |
|---|---|
| `overhead_kwh` | Overhead energy consumed |
| `saved_kwh`, `saved_pct` | Compared with `always_on` in the same run |
| `wait_min_total`, `wait_min_mean`, `wait_min_p95` | Added driver wait (sessions with wait 0 are included in the mean; p95 is over all sessions) |
| `sessions`, `waited_sessions` | Helper counts |
| `sleep_share` | Share of CP time spent in STANDBY or DEEP_SLEEP |

Drivers are never lost (decision 0006).

## 3. Baselines (`sim/baselines.py`, must-have)

- `always_on`: never sends a command, so every CP is IDLE when not charging.
- `naive_quiet_hours`:
  - From the **training period**, compute each CP's arrival rate by hour of the week.
  - Sleep the CP during its `naive_quiet_hours_per_day` quietest hours each day (default 3, in `config/sim.yaml`). Use the deepest state available, wake at the end of the window, and never wake early.
  - This is the "Ampwhere usage → switch off when least used" baseline from the original brief.

## 4. Gymnasium environment (`sim/gym_env.py`, must-have, needed early by M2)

`ChargePointEnv`:
- Episode = one CP × one day, sampled from a split.
- The CP's siblings follow a policy you pass in (default `cost_rule`).
- Observation and action spaces are exactly those in spec 02 §4, with action masking.
- Reward is as in spec 02 §4. Pass `lambda_wait` in, so the training loop can sweep it.

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

**Replay runner:**
- An asyncio task advances the engine `speed` slots per second (1–288).
- After each step it sends one `tick` message: sim time, the KPIs of every policy, and **only the CPs whose state changed** under `focus_policy`.
- `seek` re-runs the engine from midnight up to the target slot, without sending anything.

**Live mode:**
- Every 5 minutes, read the latest `live_status` into CP states. AVAILABLE becomes IDLE unless the agent has put the CP to sleep.
- Build signals with `model.live_signals`, call the policy, and apply or recommend its commands through `stations.store`.
- Send ticks over the same WebSocket.

**Fixture mode:** `API_FIXTURES=1` serves every route from `fixtures/`, so M5 can integrate before the engine is ready.

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
- A replay at speed 288 finishes one day in under 10 s and streams ticks without blocking the API.
- Every route matches CONTRACTS.md. A test checks the `fixtures/` responses against the Pydantic models.
