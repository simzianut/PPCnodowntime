# 0022: Naive baseline sleeps one contiguous window per weekday

- Date: 2026-11-07
- Who: tudor-claude
- Status: accepted
- Supersedes: —
- Affects: spec 04 §3

## Context
"The N quietest hours each day" is ambiguous. At low-use CPs many night hours tie at zero arrivals.

## Decision
- Per CP and weekday, from the training period: the arrival rate by hour of the week.
- Sleep during the one **contiguous** window of `naive_quiet_hours_per_day` hours (may cross midnight) with the lowest summed rate. Ties go to the window whose centre is nearest 03:00.
- Deepest available state during the window; command IDLE at the window end; never wake early.

## Alternatives considered
- Any N separate hours: scattered windows, not what an operator would schedule, and tie-breaking is arbitrary.

## Consequences
Deterministic and easy to explain. A 3-hour window is a weak baseline; that is the spec's choice.
