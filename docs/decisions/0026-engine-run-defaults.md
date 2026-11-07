# 0026: Engine run defaults: always_on runs in every run, day boundaries, sleep_share, speed target

- Date: 2026-11-07
- Who: tudor-claude
- Status: accepted
- Supersedes: —
- Affects: spec 04 §1–2

## Context
Several small engine choices are not in the spec.

## Decision
- `always_on` runs inside every run, even if not requested, because `saved_*` is measured against it. It is reported only if requested.
- At 00:00, CPs mid-session are CHARGING and all others IDLE. Waits and delayed departures past midnight are cut at the end of the day.
- `sleep_share` = seconds in STANDBY or DEEP_SLEEP / all CP seconds.
- Power profiles are read straight from `config/power_profiles.yaml`.
- Speed target: one day, all **real** CPs (about 500, decision 0014), 4 policies, under 20 s headless.

## Alternatives considered
- Spec's 1,500 CPs: no longer matches the station list, which is not padded.

## Consequences
Multi-day runs (stretch) would need to carry state across midnight.
