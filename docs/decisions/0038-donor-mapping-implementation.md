# 0038: Donor mapping implementation details: holiday swaps, top-ups, de-cloning, active spans

- Date: 2026-11-07
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: —
- Affects: spec 01 §4 and §6, CONTRACTS (`cp_donor_map.parquet`, `cp_active_days.parquet`, `config/dataset.yaml` `mapping:`/`context:`)

## Context
Spec 01 §4 fixes the method (0002), but leaves details open. Building it on 671 CPs and 178 donors also exposed two problems:
- **Clones:** CPs with ratio ≥ 2 got identical timelines.
- **Missing history:** HPC donors only start in 2022–23, so many CPs have no history for the early period.

## Decision
- **Demand and donor choice** follow spec 01 §4 exactly. HPC points borrow a DC_50 donor when no distinct HPC donor is free at the location; this happened for 1 CP.
- **Scaling:**
  - `max_scale: 3.0`. With one second donor taken at probability ≤ 1, the effective cap is about 2×; 148 CPs hit the cap.
  - The second donor is the same class with the closest rate, preferring one not used at the location. Top-up sessions that overlap are skipped (20,179).
- **Holidays:**
  - A Romanian holiday takes the donor's Sunday of the same ISO week.
  - A Scottish-only holiday takes the same weekday of the previous week, else the next week, if that day is inside the donor's active span and isn't itself a holiday. Otherwise the day stays as it is.
  - Overlaps created by the swap keep the earliest session (225 dropped).
- **Clock time:** donor London wall-clock time becomes Bucharest wall-clock time (`shift_forward` / `ambiguous=True`).
- **De-cloning:** every session is shifted by a per-CP, per-day offset drawn from U(−20, +20) min (`declone_jitter_min`). This leaves 0 identical timelines; overlaps from the jitter keep the earliest session (1,454 dropped).
- **Active spans:**
  - `cp_donor_map.parquet` has `active_from` and `active_to`. `cp_active_days.parquet` (`cp_id, date, active`) marks the days when the primary donor existed.
  - **Inactive days are unknown, not zero demand.** Consumers must exclude them.
  - Inactive share: AC 20%, DC_50 18%, HPC 51%.
- **Context:** Open-Meteo hourly weather at each county's centroid (from `ro_counties.geojson`), plus Romanian national holidays. It uses `county_code`, as in CONTRACTS, not the spec's `county`.

## Alternatives considered
- Fill inactive spans with another donor: more data, but it mixes timelines. No time for it.
- Ship clones: the model would see identical CPs in different counties.

## Consequences
- 981,486 sessions in total. Test-period median sessions per CP per day: AC 0.18, DC_50 0.77, HPC 0.74.
- Mapped arrivals keep the donor hour-of-week shape (correlation 0.98–0.99).
- `min_donor_sessions_per_day` exists in the config and is 0 (spec default). 114 CPs have a donor below 0.05 sessions/day.
