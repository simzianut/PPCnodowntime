# 0040: Traffic build details: Mapbox 2-hourly sampling with interpolation, free-flow baseline, TomTom quality filter

- Date: 2026-11-07
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: — (implements 0028)
- Affects: spec 01 §5, CONTRACTS (`config/traffic.yaml` keys)

## Context
- 0028 chose Mapbox typical travel times for training and TomTom for live traffic.
- One request at a time, hourly sampling of 445 locations would have taken 5–9 h. The team had under 2 h.
- The first TomTom test point (central Bucharest) returned a degenerate segment: free-flow 2 km/h.

## Decision
Decided with the user:
- **Sampling:** 4 workers at ≤ 4 requests/s globally. Hours 00, 02, …, 22 are sampled for a weekday (next Tuesday) and a weekend day (next Saturday), via `depart_at` on `driving-traffic`.
- **Odd hours** are filled by circular linear interpolation. The 10 pilot locations keep their full hourly samples.
- **Expansion:** Mon–Fri use the weekday profile and Sat–Sun the weekend profile, giving 168 hours of the week.

Implementation choices (agent, reviewed):
- **Route:** about 600 m through the site, east–west, with north–south as the fallback if the ends snap together. One-way streets can lengthen it; the index is a ratio, so that's acceptable.
- **Index:**
  - sample = `duration_typical`, falling back to `duration`
  - free-flow = min(all samples, one `driving` call with no traffic)
  - `typical_index = clip(sample / free_flow − 1, 0, 1)`
- **Fallback:** locations with no usable route get `fallback_synthetic` with index 0. None occurred in the final build.
- **TomTom live:** `live_index` returns None when FRC is worse than FRC5, confidence < 0.5, or free-flow < 15 km/h, so the caller falls back to the typical index. It raises on 401/403/429.
- **Robustness:** network errors are retried up to 4 times. The build resumes from cached raw responses (`data/raw/traffic/mapbox/`).

## Consequences
- Result: 445/445 locations from Mapbox, 10,875 requests, 46 min, 74,760 rows. Country-wide mean index: Tue 03h 0.10, Tue 08h 0.31, Tue 17h 0.34, Sat 08h 0.11.
- Typical traffic was sampled for November 2026 dates and is applied to every year 2021–2025. Seasonal and long-term traffic changes are ignored.
