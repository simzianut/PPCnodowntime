# 0028: Real traffic index: TomTom for live mode, Mapbox typical travel times for training and replay

- Date: 2026-11-07
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: — (partly replaces the `maps_traffic_index` part of 0012)
- Affects: spec 01 §5, spec 02 §5, CONTRACTS (env vars, traffic_typical.parquet, config/traffic.yaml)

## Context
- 0012 mocked every Maps signal. Real general-traffic data is available for Romania; EV and ICE counts are not available anywhere.
- Options checked on 2026-11-07, from official pricing and terms pages:
  - **Google Routes API:** the traffic-aware SKU is free up to 5,000 requests a month, then $10 per 1,000. The Maps Platform terms §3.2.3(c)(vii) forbid using Maps content "to train, test, validate or fine-tune" ML models, so it can't feed training.
  - **TomTom Flow Segment Data:** free up to 20,000 requests a month, no card needed. Returns `currentSpeed`, `freeFlowSpeed`, `confidence`.
  - **Mapbox Directions `driving-traffic`:** free up to 100,000 requests a month. Returns `duration` (live) and `duration_typical`. Romania is covered.
  - HERE (quota unconfirmed); CNAIR census (no downloadable data found); city open data (none found).

## Decision
- **Live mode:**
  - `maps_traffic_index` = `1 − currentSpeed / freeFlowSpeed` from TomTom, clipped to 0..1, for the road segment nearest each location.
  - Polled every 15 min, only for the locations in the live run, within the free quota.
- **Training and replay:**
  - One-off Mapbox requests: per location, a short route through the site at each hour of the week, about 26k requests in total.
  - `typical_index` = `clip(duration_typical / free_flow_duration − 1, 0, 1)` per location and local hour of the week. It replaces the synthetic time-of-day curve.
  - Weather factor and noise stay as in spec 01 §5.
- `maps_ev_count` and `maps_ice_count` stay mocked (0012), now driven by the real traffic index.
- Google is not used.

## Alternatives considered
- Google live only: small free quota, and its terms block training use.
- TomTom live only: the training curve stays synthetic.
- Stay fully mocked.

## Consequences
- Needs `TOMTOM_API_KEY` and `MAPBOX_TOKEN` in `.env` (a Mapbox account may need a card).
- The terms of TomTom and Mapbox on storing results weren't fully read. We store only derived per-location indices; re-check before any public release.
