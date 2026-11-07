# 0009: One global autonomy switch: ACT or RECOMMEND

- Date: 2026-11-07
- Who: tudor
- Status: accepted
- Supersedes: —
- Affects: spec 03, spec 04, spec 05

## Context
The agent puts charge points into standby itself if it has permission. Otherwise it only recommends.

## Decision
- A single system-wide setting, `settings.autonomy_mode`:
  - **ACT:** the agent applies its commands.
  - **RECOMMEND:** commands become pending recommendations that an operator approves or rejects. They expire after a time limit.

## Alternatives considered
- A mode flag per station: more flexible, but more UI and more state.

## Consequences
- Per-station permissions can be added later as a new decision, without breaking the commands table.
