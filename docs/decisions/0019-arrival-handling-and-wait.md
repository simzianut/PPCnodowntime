# 0019: Arrival handling: compatibility fallback, waking a sleeping sibling, queueing not counted

- Date: 2026-11-07
- Who: tudor-claude
- Status: accepted
- Supersedes: —
- Affects: spec 04 §1, spec 02 §4 (reward)

## Context
Spec 04 §1 leaves open what a driver does when their own CP is busy because a displaced driver is charging there, and how to judge compatibility when connectors are unknown (about 40% of locations).

## Decision
On arrival, in order:
1. Own CP IDLE → start charging, wait 0.
2. Else a compatible sibling is IDLE → move there, wait 0. Ties → lowest CP index.
3. Else a compatible CP (own or sibling) is asleep or WAKING → the driver uses the one ready soonest (own CP wins ties). Wait = remaining wake time, counted as added wait and attributed to that CP. Departure moves later by the wait.
4. Else every compatible CP is charging → the driver queues for the first one to free up. The queue time delays the session but is **not** counted as added wait (spec 04 as written).

Compatible = same `current_type` and a shared connector. When either CP has no connectors listed, same `current_type` and same `power_class`.

## Alternatives considered
- Count queue time as added wait too: more honest (always_on never queues), but rejected by the team to stay with the spec.
- Unknown connectors = never compatible: would make handover vanish at 40% of locations.

## Consequences
Queue waits are reported nowhere in the headline metrics. Policies that cause many displacements look slightly better than they are; this is expected to be rare.
