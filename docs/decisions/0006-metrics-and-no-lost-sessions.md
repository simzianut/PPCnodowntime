# 0006: Headline metrics are energy saved and added wait; drivers are never lost

- Date: 2026-11-07
- Who: tudor
- Status: accepted
- Supersedes: —
- Affects: PRD, spec 02, spec 04, spec 05

## Context
We need metrics that are honest and comparable between policies. The PPC Blue app is unchanged, so a sleeping charge point still shows as available, and the driver arrives and waits for it to wake.

## Decision
- Headline metrics:
  - energy saved compared with always-on, in kWh and %
  - added wait minutes, as total, mean and p95
- No "lost session" metric: a driver always waits.
- The RL reward is overhead energy minus λ × wait minutes.

## Alternatives considered
- A disruption count: still logged as the helper field `waited_sessions`, but not a headline metric.
- Lost sessions with a patience threshold: rejected.

## Consequences
- With a 3-minute deep-sleep wake, the cost per disruption is small. The trade-off is driven by λ.
