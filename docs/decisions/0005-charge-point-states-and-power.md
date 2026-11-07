# 0005: Agent acts per charge point; 6 states with placeholder power numbers

- Date: 2026-11-07
- Who: tudor
- Status: accepted
- Superseded by: 0047 (power draw numbers)
- Supersedes: —
- Affects: spec 03, spec 02, spec 04, CONTRACTS

## Context
Each location has several charge points. We need sleep states, how much power each draws, and how long waking up takes. Real datasheet numbers aren't available yet.

## Decision
- The agent controls each **charge point** separately. It can keep one awake and put the rest of a location to sleep.
- States: CHARGING, IDLE, WAKING, STANDBY, DEEP_SLEEP, OFFLINE. WAKING is an extra transition state, added so the wake-up delay can be modelled.
- Placeholder numbers, in `config/power_profiles.yaml`:

  | Charger | Idle | Standby | Deep sleep | Standby wake | Deep-sleep wake |
  |---|---|---|---|---|---|
  | AC | 10 W | 3 W | not supported | 10 s | — |
  | DC | 150 W | 30 W | 5 W | 10 s | 180 s |

- Power classes: AC_22, DC_50 and DC_HPC. DC_HPC uses the DC numbers for now.

## Alternatives considered
- Per-location control: coarser, and it gives up the "keep one awake" lever.
- 3 states: loses the trade-off between standby and deep sleep.

## Consequences
- The team expects to bring real numbers later. Each change goes in a new decision file and updates the config.
