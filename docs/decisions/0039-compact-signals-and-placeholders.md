# 0039: Compact signals.parquet (events + hourly maps signals + non-zero EV counts) and placeholder signal parameters

- Date: 2026-11-07
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: — (refines spec 01 §5; the traffic source is 0028)
- Affects: spec 01 §5, CONTRACTS (`signals.parquet` resolution), `config/signals.yaml`

## Context
- At full 5-minute resolution, all signals for 445 locations over 4 years would be about 580M rows. The forecaster's `SignalIndex` reads signals row by row.
- `background_ev` and `ice_base` were null in the config.
- The team needs training data within the hour.

## Decision
Format, decided with the user:
- `ppcblue_route_intent` is stored as events (648k), with `eta_ts`. `session_id` is set only for evaluation.
- `maps_traffic_index` and `maps_ice_count` are **hourly** per location. Readers use the latest value at or before t.
- `maps_ev_count` is per 5-minute slot, and **only non-zero rows** are written; a missing slot means 0.
- The output is one zstd file sorted by ts, with 1M-row groups. Result: 43.9M rows, 218 MB, about 30 s to build.

Formulas (spec 01 §5):
- traffic = `clip(typical_index[loc, hour_of_week] × (1 + rain_boost × min(precip, 5)/5) × lognormal(0, traffic_noise_sd), 0, 1)`
- EV rate per slot = `ev_tag_rate × (arrivals in the next 30 min + background_ev × traffic)`
- ICE = `Poisson(ice_base × traffic)`
- False-positive intents are placed near the location's real arrival times, shifted by ±3 h.

Placeholders (guesses, tune only through a new decision):
- `background_ev: 0.5`
- `ice_base: 40`
- `rain_boost: 0.3`
- `traffic_noise_sd: 0.1`
- `seed: 42`

Traffic source per location: Mapbox typical (`traffic_typical.parquet`), then the partial build, then a synthetic urban or highway curve. Signals are regenerated once the full Mapbox build finishes.

## Alternatives considered
- Full 5-minute rows: too big.
- A sample of days only: the simulator couldn't replay every test day.

## Consequences
- Acceptance holds: the intent share is 0.600 against `p_app` 0.6, and every intent comes before its arrival.
- Data shared with teammates as v2 had real traffic for only 50 locations; v3 will replace it.
