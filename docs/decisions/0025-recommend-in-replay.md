# 0025: In replay, RECOMMEND applies to the focus policy only, and the run pauses when new recommendations appear

- Date: 2026-11-07
- Who: tudor-claude
- Status: accepted
- Supersedes: —
- Affects: CONTRACTS (API), spec 04 §6, spec 05 §3.3

## Context
A 3-slot TTL expires in about 10 ms at 288×, and overnight a policy may emit hundreds of sleep commands at once. Operators could never act during a replay.

## Decision
- In RECOMMEND mode only `focus_policy`'s commands become recommendations. The other policies act, so the comparison stays clean.
- When new pending recommendations appear, the replay runner pauses (`status: paused`, `paused_reason: recommendations`) and sends a `status` message over the WebSocket. The operator approves or rejects, then presses play. Undecided recommendations expire after `recommendation_ttl_slots` of **sim** time.
- Approved commands are applied at the start of the next slot.

## Alternatives considered
- Wall-clock TTL with sim time running: results depend on how fast the operator clicks; not reproducible.
- Sim-slot TTL only: operators can act only at speed 1 or in live mode.

## Consequences
The focus policy's KPIs in RECOMMEND mode reflect operator choices. Seek re-runs from midnight without the operator's past decisions (they are treated as expired).
