# 0018: Engine transition rules that fill the gaps in spec 03 §4

- Date: 2026-11-07
- Who: tudor-claude
- Status: accepted
- Supersedes: —
- Affects: spec 03 §4, spec 04 §1, T5

## Context
Spec 03 §4 does not say whether IDLE can go straight to DEEP_SLEEP, how DEEP_SLEEP reaches STANDBY, or what a command to a WAKING CP does. The engine needs fast, pure rules now; T5 (M3) is unclaimed and DB-bound.

## Decision
Implemented vectorised in `sim/transitions.py`:
- Going to sleep is instant, including IDLE → DEEP_SLEEP directly and STANDBY → DEEP_SLEEP. DEEP_SLEEP on an AC CP is applied as STANDBY.
- DEEP_SLEEP → STANDBY goes through WAKING for `deep_sleep_wake_s` (drawing `idle_w`), then STANDBY.
- A wake command (IDLE) starts WAKING for the wake time of the state being left.
- Commands to a WAKING CP: IDLE is a no-op; sleep commands are ignored until the wake finishes.
- Commands to a CHARGING CP are dropped with reason `busy`. Commands that equal the current state are dropped silently and not recorded.
- OFFLINE CPs ignore commands.

## Alternatives considered
- DEEP_SLEEP → STANDBY instant: an agent could keep CPs in deep sleep and drop to standby one slot early for free, hiding the 180 s wake cost.
- Wait for M3's transition function: blocks the engine.

## Consequences
T5's transition test should check these same rules. If M3 implements them differently, one side follows a new decision.
