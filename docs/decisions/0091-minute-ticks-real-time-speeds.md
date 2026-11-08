# 0091: Minute ticks, slot-cadence decisions, real-time speed multiples

- Date: 2026-11-08
- Who: tudor + tudor-claude
- Status: accepted
- Supersedes: — (changes the replay pacing of spec 04 §6 and the `speed`/`seek` semantics in CONTRACTS)
- Affects: `sim/engine.py`, `sim/live.py`, `sim/metrics.py`, `api/engine_mode.py`, `api/collected_replay.py`, `api/live_mode.py`, `api/fixture_mode.py`, `api/replay_clock.py` (new), `api/models.py`, CONTRACTS (`POST /api/runs`, `GET /api/runs/{id}`, control, WS), spec 04 §1, §6 and §9, T54

## Context
- Tudor asked for the simulation to tick minute by minute instead of every 5 minutes, with speed multipliers 1x, 15x, 60x and 300x.
- `speed` used to be "5-minute slots per wall second" (1–288). So "1x" ran at 300x real time.
- The F2 GRU forecaster, the cost rule and the RL policies were all trained on 5-minute slots. Retraining was out of scope.

## Decision
- **Policies keep the 5-minute cadence.**
  - Observe, decide, apply, and the application of approved recommendations, happen only at slot starts, exactly as before.
  - The recommendation TTL stays 3 slots (15 min).
- **The clock ticks every simulated minute** (`TICK_S = 60`).
  - `Engine.step_minute()` applies the arrivals, departures, wake ends and observed status events that fall inside that minute.
  - A slot's first tick also runs the slot start (observe/decide/apply). `StepResult` gains `minute` and `decided`.
  - `Engine.step()` still simulates a whole slot, so training, evaluation and the gym env are unchanged.
- **Accounting stays bit-identical to `step()`.**
  - Energy and sleep time are still added to the metrics once, at the slot end.
  - Mid-slot KPIs add the slot's open part (accounted Wh, plus power × time since the last event, up to the clock) without storing it (`PolicyMetrics.kpis(open_part=...)`).
  - So a minute-ticked day and a slot-stepped day end with exactly the same KPIs.
  - The first version pushed every minute into the metrics. Float summation order then flipped a 4th-decimal RON rounding on synthetic data.
- **Live statuses are applied at their own minute.**
  - `LiveEngine.feed()` takes the rest of the current slot's events, called once at the slot start. Each minute tick consumes the events inside it.
  - Event times are the reader's estimate: `source_updated_at` when it is inside the poll window, else the poll `ts`.
  - **Collected-day replay:** at each slot start it reads what a live run would have had when the slot was due (slot end + `LIVE_LAG_S`) and feeds the slot.
  - **Live runs:** tick minute `m` once the wall clock passes `m + slot_s + LIVE_LAG_S`. By then the poll after the slot's end has been read, so the policy at the next slot start sees every event before it, and each event appears on its own minute.
    - The cost is a constant ~6-minute display delay. The old delay varied between 1 and 6 minutes.
- **`speed` = simulated seconds per wall second**, one of `1, 15, 60, 300`. The default stays 60.
  - The runner sleeps `60 / speed − elapsed` per tick. At 300x a whole day takes 4.8 min.
  - Other values return 400. Live mode follows its clock (real time, or `LIVE_CLOCK_SPEED`), and `speed`/`seek` there still return 400.
- **Contract fields.**
  - Ticks, snapshots, status messages and `GET /api/runs/{id}` carry `minute`: minutes since local midnight of the run day. It is 0–1439, or up to 1379/1499 on DST days. `slot` stays and equals `minute // 5`.
  - `GET /api/runs/{id}` (and the control response) also carry `window: {start_minute, end_minute}`, the playable range:
    - a collected day: its slot window in minutes, e.g. 2026-11-08 → 35..685;
    - any other replay and live runs: 0..day minutes.
  - A tick's `minute` is the minute it simulated, `[minute, minute+1)`, and its `sim_time` is that minute's start. This follows the old meaning of `slot`.
    - So the last tick is `end_minute − 1`, and the run is then `done`.
- **Seek takes a minute of the local day.**
  - It is clamped to `[start_minute, end_minute − 1]`, so there is always a minute left to play. A non-integer value returns 400. Out-of-range values no longer return 400; they are clamped.
  - The engine runs whole slots up to the target, then the remaining minutes. A backward seek rebuilds from midnight (or, for a collected day, from the window start).
- **Fixture mode keeps its recorded slots.**
  - It ticks every minute, and a slot's recorded changes all land on its first minute.
  - Fixture live mode ticks one minute per wall second.
- **Tests:** `api.replay_clock.WALL_SCALE` scales the wall time per tick. The API tests set it to 0.001, so replays run fast and the 10 s full-day test passes again.

## Alternatives considered
- **Retrain the forecaster and policies on 1-minute slots:** this means 5× the decisions and new artifacts, and no time to validate them. The 5-minute decision cadence matches the collector's 5-minute poll anyway.
- **Live ticks right after each minute (small lag):** status rows arrive with the next poll, up to 5 min later. Events would bunch at poll times, and a policy at a slot start would decide before seeing the previous slot's events, which is worse than before.
- **Push energy into the metrics every minute:** simpler, but minute-ticked and slot-stepped KPIs would differ in the last digit.
- **Keep `speed` as slots per second and only add minute ticks:** "1x" would still not mean real time, which is what was asked.

## Consequences
- The map and KPIs move every simulated minute, while decisions and recommendations still appear at slot starts.
- A full-day replay can no longer finish in under 10 s at the top speed: at 300x it takes 4.8 min. Spec 04 §9 now states the speed target in terms of the tick rate.
- Seeking far forward still runs whole slots, so it costs about the same as before.
- The live view lags real time by about one slot plus `LIVE_LAG_S` (~6 min).
