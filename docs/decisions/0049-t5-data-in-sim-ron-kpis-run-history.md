# 0049: The sim uses T5's per-capacity power and tariffs; the API keeps autonomy and run history in the DB

- Date: 2026-11-07
- Who: tudor + tudor-claude
- Status: accepted
- Supersedes: — (extends 0047 and 0048)
- Affects: CONTRACTS v0.4, spec 03 §5, spec 04 §2 and §6, `sim/config.py`, `sim/engine.py`, `sim/types.py`, `sim/metrics.py`, `sim/gym_env.py`, `model/cost_rule.py`, `stations/store.py`, `api/`, `frontend/src/`

## Context
T5 (0048) added data the simulator ignored:
- a 13-row capacity table with idle and sleep watts per `max_kw`, behind `stations.store.get_power_profile(power_class, max_kw)`
- RON tariffs, from `stations.store.get_energy_tariffs()`
- DB tables meant to hold the autonomy setting and the `commands` and `state_events` of sim runs, keyed by `run_id`

Tudor asked for all of it in the simulator: money KPIs at the net B2B tariff, and autonomy plus run history in the DB.

## Decision
- **Per-CP power:**
  - Each CP's idle, standby and deep-sleep watts come from `get_power_profile(power_class, max_kw)` through `sim.config.cp_power()`, cached per (class, kW). A missing `max_kw` falls back to the class profile.
  - The engine only does this when built with the default profiles. Explicit `profiles=` (tests) are used as given.
  - Wake times stay 10 s / 180 s, as 0048 requires. `precharge_wake_s` is not used.
- **CpView and cost_rule:** `CpView` gains `max_kw`, so `cost_rule` uses the same per-CP watts. A caller-supplied `power_profiles` dict still wins.
- **Gym reward:** the reward is divided by the CP's own idle watts, so an always-idle slot still scores −1. Before, it was the class value. PPO checkpoints trained before this change may be slightly off-calibration until retrained.
- **RON KPIs:**
  - `overhead_ron` and `saved_ron` = the matching kWh × the net B2B tariff (1.15 RON/kWh). PPC buys this energy as a business; the gross retail tariff stays unused.
  - They appear in the engine KPIs, the API `Kpis` (both modes; fixture mode derives them from kWh) and the frontend KPI cards. They're optional in `eval_results.json` rows until T11 writes them.
- **Autonomy:** engine mode reads and writes it through `stations.store` (the `settings` table when the DB is up, else memory). This fixed `get_autonomy()`, which decoded the `jsonb` value twice on Postgres. Fixture mode stays in memory.
- **Run history** goes through `stations.store.record_run_history(run_id, commands, events)`:
  - **Writes:** one transaction per flush, validated first. It never touches `cp_state`. Without a DB it's a no-op, with no in-memory buffering.
  - **Which commands:** only the focus policy's, at their final status. Pending ones are never written.
  - **State events:** one per net state change of a CP in a slot, with `from_state` = the state shown at the slot start.
  - **Cause:** `arrival` or `departure` from that slot's events, `operator` for an approved recommendation, else `agent`.
  - **Flushing:** every 12 ticks, on pause or done, after control or decide calls, and on shutdown. Any DB error turns history off for that run; the run keeps going.
  - **Seek and focus change:** history is append-only. A forward seek leaves a gap. A backward seek writes the replayed range again under the same `run_id`, and only the row order (`id`) tells the passes apart. After a focus change, events belong to the new focus policy, and `state_events` has no policy column.

## Alternatives considered
- Gross retail tariff, or both tariffs: overstates PPC's saving, or adds noise to every card.
- Keep runs fully in memory and leave history to T12: Tudor asked for it now.
- Write every policy's history, or every slot: too much write load for 671 CPs × 4 policies, for no demo value.
- Use `precharge_wake_s` as the wake latency: 0048 keeps 10 s / 180 s for calibration.

## Consequences
- **Bigger capacity spread:** 300 kW chargers now idle at 230 W rather than 165 W, and 50 kW ones at 95 W rather than 105 W. On the default data, a day of `always_on` overhead drops from 1,108.75 to 1,067.61 kWh (1,227.75 RON).
- **Committed fixtures aren't regenerated:** `fixtures/api/` still uses class watts. Its RON values are derived from those kWh.
- **The DB needs migrating:** persisting anything requires `make db` (migration 0001). The local `ppc` database had none applied when this was written.
