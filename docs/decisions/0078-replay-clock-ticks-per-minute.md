# 0078: Replay clock ticks per minute with the staggered actions

- Date: 2026-11-08
- Who: tudor + tudor-claude
- Status: accepted
- Supersedes: the "replay clock still updates once per tick" part of [0076](0076-stagger-actions-within-slot.md)
- Affects: spec 05, `useDashboard.ts` (`actionClock`), `ReplayBar`

## Context
After 0076 the actions appeared live, but the big replay clock still jumped 5 minutes at a time and ran ahead of them. Tudor asked for the clock to move live too.

## Decision
- `actionClock` advances one minute every 1/5 of a tick's spread, from slot start + 1 min to the tick's `sim_time`.
- Staggered changes are stamped with whatever minute that clock shows.
- The ReplayBar shows `actionClock`, falling back to `run.sim_time`.
- A seek or a new run clears it.
- The scrubber position and KPIs still move once per tick.

## Consequences
- The displayed minute is cosmetic inside a slot.
- When paused, the clock rests on the tick's `sim_time`.
