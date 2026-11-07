# 0040: Run-scoped station summary with nullable live/model metrics

- Date: 2026-11-07
- Who: teodora
- Status: accepted
- Supersedes: —
- Affects: API contract, M4 API, M5 frontend

## Context
The redesigned station panel needs current output, expected wait, and an anomaly score. The current API only has charger rating, run-level wait metrics, and arrival probabilities; no metered output or per-station model values are available yet.

## Decision
Add `GET /api/runs/{run_id}/locations/{location_id}/summary` with required nullable `current_kw`, `expected_wait_min`, and `anomaly_score` fields. `current_kw` means metered output and must not be inferred from `max_kw`. Wait is expressed in minutes. Anomaly score is unitless and clients apply no thresholds. Producers return null until values are available.

## Alternatives considered
- Put station-level fields on `/api/stations/{location_id}`: the metrics depend on a selected run and should be scoped to that run.
- Treat rated charger capacity as current output: `max_kw` is a limit, not a meter reading.
- Invent client-side estimates: no documented algorithm exists for station wait or anomalies.

## Consequences
The frontend can render the summary consistently while showing unavailable values. M4 must add the route and a fixture; M1/M2/M4 producers can populate fields later without changing the response shape.
