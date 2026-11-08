# 0075: Demo mode runs a small deterministic day simulation

- Date: 2026-11-08
- Who: tudor + tudor-claude
- Status: accepted
- Affects: spec 05, frontend demo mode (`frontend/src/api/demoSim.ts`, `mock.ts`, `useDashboard.ts`)

## Context
Without a backend the app falls back to demo data. The old mock flipped one random CP per slot through all six states and drew KPIs from fixed formulas, so the agent never visibly acted, the ticker stayed empty, KPIs did not match the map, seeking did not change the map, and Review mode paused the run at 02:50.

## Decision
- `simulateDay(locations)` simulates one day (288 five-minute slots) once, deterministically (hashed randomness):
  - arrivals follow an hourly weekday curve, ~4 sessions per CP per day, scaled by site context;
  - DC sessions last 20–45 min, AC 60–180 min; 2% of CPs are offline all day.
- The demo agent is a simple forecast rule, a stand-in for the real one:
  - It sleeps a CP idle for at least 10 min when P(arrival in the next 30 min) < 6%, choosing deep sleep below 2.5% (DC only).
  - It wakes the CP when that probability rises above 10%.
  - An arrival at a sleeping CP waits 0.5 min (standby) or 2 min (deep sleep).
- KPIs come from those states with the `energy.ts` power profiles, at 1.15 RON/kWh.
- The always-on baseline uses the same charging sessions with every free CP idle.
- Ticks emit the real state diffs, so the map ticker and pulses show sleeps and wakes.
- Seeking jumps the map and KPIs to that slot.
- Demo autonomy starts in Act.

## Alternatives considered
- Replaying a recorded backend run as a fixture: more realistic, but large and tied to one backend version.

## Consequences
Demo numbers (about 33% less idle energy, ~0.2 min mean wait over the day) are illustrative only and are not model results.
