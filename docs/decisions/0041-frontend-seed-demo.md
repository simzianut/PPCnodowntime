# 0041: Seed-data demo adapter for frontend work before the API exists

- Date: 2026-11-07
- Who: teodora
- Status: accepted
- Supersedes: —
- Affects: frontend module

## Context
The frontend is expected to run end to end before the M4 API and its fixtures exist. The repository has an open station seed file, but no shared API fixtures yet.

## Decision
The frontend attempts the documented API by default. When required startup endpoints are unavailable, it switches to a deterministic in-browser demo generated from `data/seed/stations_open.csv`. Missing charge-point counts use two demo points, and initial states and replay metrics are illustrative only.

## Alternatives considered
- Wait for the M4 fixture API: this would prevent frontend work from being previewed before the backend is ready.
- Add a new frontend mode environment variable: this would require a shared contract change and offers little value for the development-only fallback.

## Consequences
The app can be previewed without a running backend and automatically uses the real API when it is available. Demo charge-point counts, state transitions, recommendations, and metrics are not real operational data and are not used when the API succeeds.
