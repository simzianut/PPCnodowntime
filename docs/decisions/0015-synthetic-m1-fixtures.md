# 0015: M1 fixtures are synthetic, on placeholder locations L00001–L00005

- Date: 2026-11-07
- Who: vlad-claude
- Status: accepted
- Supersedes: —
- Affects: spec 01, fixtures/

## Context
M2 and M4 need `sessions`, `signals` and `context` samples now (spec 00 §5). M3 has not assigned `location_id`s yet (T4). Dundee-derived rows can't be committed (unclear licence).

## Decision
- `python -m dataset.fixtures_gen` (seed 42) writes fully synthetic fixtures for 2024-03-12 and 2024-03-13 (test split).
- 5 placeholder locations `L00001`–`L00005` (B urban, CJ retail, PH highway, IF airport, TM other), 13 CPs, listed in `fixtures/locations_sample_m1.csv`. They are fixture-only and may not match M3's real IDs.
- Invented parameters (fixture-only, not used by the real pipeline):
  - sessions per CP per day by context: urban 8–12, retail 6–10, highway 4–8, airport 5–9, other 3–6; AC halved, floor 3
  - power: AC 7–11 kW, DC_50 30–45 kW, DC_HPC 80–150 kW; energy = power × duration
  - arrival shapes (local time): urban peaks 08/18, retail 13, highway uniform 06–22, airport mix, other 12
  - `background_ev = 2.0`, `ice_base = 40.0` (config values stay null until T8)
  - synthetic March weather in `context_sample.parquet`

## Alternatives considered
- Wait for M3's IDs: blocks M2/M4.
- Cut a sample from Dundee: licence unclear.

## Consequences
- M3's station fixture should include L00001–L00005 so the samples join, or consumers treat them as standalone.
- Fixture numbers say nothing about real demand.
