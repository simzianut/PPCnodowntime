# 0076: Agent actions are staggered within each 5-minute slot

- Date: 2026-11-08
- Who: tudor + tudor-claude
- Status: accepted
- Affects: spec 05, `frontend/src/state/useDashboard.ts`, `NetworkMap` (`actionClock` prop)

## Context
The engine steps in 5-minute slots, so every tick carries all CP changes of that slot at once. On the map this read as bursts every 5 minutes instead of decisions happening live. Tudor asked to see them as they happen.

## Decision
- The frontend spreads a tick's changes over 90% of the real time until the next tick, which is 5 min / replay speed.
- Each CP gets a stable offset from a hash of `cp_id` and slot, so a replay looks the same each time; offsets are grouped into 40 buckets to keep timers few.
- Each change is stamped with a minute inside its slot. The ticker shows that minute, passed to the map as `actionClock`.
- A new tick first applies any changes still pending. A seek or a new run drops them.
- If a tick changes more than half the CPs (a seek or a policy switch), it is applied at once, not staggered.
- KPIs and the replay clock still update once per tick.

## Alternatives considered
- Engine sub-slot timestamps per command: truer timing, but it changes the engine and the WS contract.

## Consequences
- The minute shown inside a slot is cosmetic. The real decision time is the slot boundary.
- The map lags the engine by up to one slot.
