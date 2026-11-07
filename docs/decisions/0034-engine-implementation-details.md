# 0034: Engine implementation details: event order, command handling, recommendations, midnight

- Date: 2026-11-07
- Who: tudor-claude
- Status: accepted
- Supersedes: —
- Affects: spec 04 §1–2, sim/engine.py, sim/transitions.py, sim/metrics.py

## Context
Decisions 0018, 0019, 0025 and 0026 set the engine rules. Building it raised smaller questions those decisions don't answer.

## Decision
- **Event order within a second:** departures, then wake completions, then arrivals. A driver arriving the second a CP frees up or finishes waking gets it with wait 0. Arrivals are ordered by (arrival_ts, session_id); ties between CPs by position in `engine.cp_ids` (the `data.cps` order).
- **Commands:**
  - If a policy sends several commands for one CP in a slot, the last one wins. Commands for CPs outside the run are ignored.
  - Dropped commands (same state, busy, waking, offline) are not recorded: `CommandStatus` has no "dropped" value.
  - An AC DEEP_SLEEP command is recorded with its effective target, STANDBY.
- **Recommendations:**
  - Created only for commands that would apply now. At most one pending per (policy, CP). A different target from the focus policy expires the pending one, even if the new command is itself a no-op.
  - Approved ones apply at the start of the next slot, before the policies observe, and keep status `approved` even if the transition rules drop them then.
  - Approving at or after `expires_at` is refused (expired). Anything still pending at the end of the day expires.
- **Arrivals:** a CP that is WAKING to serve a waiting driver counts as busy. A queued driver is assigned at arrival to the compatible CP that frees up first (ties to their own CP) and starts the moment it frees.
- **Midnight:** sessions spanning midnight start as CHARGING and are not counted in `sessions` (they arrived the day before). Waits are cut at the end of the day: an arrival at 23:59 at a deep-sleeping CP waits 1 minute.
- **Attribution:** an arrival belongs to the CP that served the driver (events also carry `moved_from` and `queued`). Added wait is attributed to the CP the driver waited on.
- **Metrics:** `Engine.metrics` exposes raw per-policy totals so the evaluator can merge days (p95 across days can't be built from daily KPIs). Rounding: kWh 4 decimals, minutes 3, `saved_pct` 2, `sleep_share` 4.
- **Observation data:** `SimState.signals` keeps the full signals schema (including `session_id`, which policies must never use as a feature) but only for the run's locations. Context is the latest `ts_hour` at or before `t`.

## Alternatives considered
- Arrivals before departures within the same second: would create zero-length queues and spurious waits.
- Recording dropped commands with a new status: would change the `CommandStatus` contract for little value.

## Consequences
- The forecaster is called once per policy per slot (about 45% of run time with `PrecomputedForecaster`). Calling it once per slot for state-independent forecasters is a possible speed-up.
- Benchmark: about 1 s per day for 308 CPs × 4 policies on synthetic data.
