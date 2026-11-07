# Spec 01: Dataset curation

Owner folder: `dataset/`. Produces the ground-truth charging sessions for Romania, the mocked EV signals, and the context features. Also runs the live-status collector.

## 1. Outputs (defined in CONTRACTS.md)

| Output | Path / table | Used by |
|---|---|---|
| Donor sessions (Dundee, cleaned) | `data/processed/donor_sessions.parquet` | M1 mapping, M2 (weather effect) |
| Donor chargers | `data/processed/donor_chargers.parquet` | M1 mapping |
| **Romanian sessions** (ground truth) | `data/processed/sessions.parquet` | M2, M4 |
| **Signals** (mocked PPC Blue / Maps) | `data/processed/signals.parquet` | M2, M4 |
| Context features (weather, holidays) | `data/processed/context.parquet` | M2, M4 |
| County EV stats | `data/seed/county_ev_stats.csv` (committed) | M1, M2, M3 |
| Live statuses | DB table `live_status` | M2 (validation), M4 (live mode) |

Fixtures: `fixtures/sessions_sample.parquet` and `fixtures/signals_sample.parquet` (2 days × 5 locations), committed early.

## 2. Pipeline

```
Dundee CSVs ──► clean ──► donor_sessions + donor_chargers
                                   │  + Open-Meteo weather at Dundee, GB-SCT holidays
                                   ▼
RO charge points (M3) + county EV stats ──► donor matching + scaling + calendar shift
                                   ▼
                          sessions.parquet (RO ground truth)
                                   ▼
                    signal generator ──► signals.parquet
RO weather (Open-Meteo per county centroid) + RO holidays ──► context.parquet
```

Run it all with `make data`. Every step is a CLI under `python -m dataset.<step>`, takes a fixed seed, and gives the same output each time it's run.

## 3. Donor data: Dundee (must-have)

Decision: [0027](../decisions/0027-dundee-cleaning-and-lanes.md).

- **Source:** Dundee City Council EV charging sessions, Jul 2021 – Aug 2025, one CSV per year.
  - `python -m dataset.dundee download` fetches them from the public ArcGIS item URLs listed in `config/dataset.yaml` into `data/raw/dundee/`.
  - The licence isn't stated, so the raw files are never committed.
- **Cleaning:**
  - parse timestamps in Europe/London
  - departure = Start + Duration, falling back to End
  - drop End < Start and departure ≤ arrival
  - drop sessions under 2 minutes or over 24 hours
  - drop sessions with blank or ≤ 0 kWh
  - drop duplicate session IDs

  The counts per rule go to `data/processed/reports/dundee_cleaning.json`.
- **Lanes:** the source has no connector column and sessions on one charger overlap. Each charger ID is split greedily into at most `max_lanes = 2` lanes (`<cp>#1`, `<cp>#2`). Sessions that overflow both lanes are dropped. Each lane is one donor charger.
- **Chargers:** one row per lane in `donor_chargers.parquet`.
  - Map the source's tag to `power_class`: ac → `AC_22`, rapid → `DC_50`, ultra_rapid → `DC_HPC`.
  - Utilisation is computed on the training period: `sessions_per_day` and `util_quantile` within class. Chargers that only exist in the test period use the test period (`util_basis = test`).
  - Occupancy share by local hour of the week goes to `donor_occupancy.parquet`.
- **Weather at Dundee:** Open-Meteo historical archive, hourly temperature, precipitation and wind, attached to each session's arrival hour.
- **Holidays:** `holidays.country_holidays("GB", subdiv="SCT")`.
- **Split:** the training period ends 2023-12-31. The test period starts 2024-01-01. The split is written into the outputs as a `split` column.

## 4. Mapping donors onto Romanian charge points (must-have)

Decision: [0002](../decisions/0002-dundee-donor-sessions.md). Method: **donor matching + scaling**.

1. **Demand index per RO charge point:**
   `demand = county_ev_density(county) × context_multiplier(context_type) / cps_at_location`
   - `county_ev_density`: electric M1 cars per 1,000 people, from DRPCIV end-2025 and INS population.
   - `context_multiplier` comes from `config/signals.yaml`. Placeholders: urban 1.0, retail 1.2, highway 0.6, airport 0.8, other 0.8.
2. **Pick a donor:** among donor chargers with the same `power_class`, choose the one whose utilisation quantile is closest to the CP's demand quantile among RO CPs of that class.
   - Ties are broken at random, using the seed.
   - Neighbouring CPs at the same location must get **different** donors where possible, to avoid cloned timelines.
3. **Scale:** `ratio = target_rate / donor_rate`.
   - `target_rate` is the class's median donor rate × the CP's demand relative to the class median.
   - If `ratio < 1`, drop each donor session independently with probability `1 − ratio`.
   - If `ratio > 1`, add sessions from a second donor of the same class with probability `ratio − 1`, skipping any that overlap an existing session.
4. **Calendar shift:**
   - Map each donor date to the Romanian date in the same ISO year, week and weekday, keeping the local clock time.
   - A Romanian public holiday takes the session timeline of a donor **Sunday** from the same week.
   - A UK holiday that isn't a Romanian holiday takes the timeline of the same weekday in an adjacent week.
5. **Output:** `sessions.parquet` with:
   - `session_id`
   - `cp_id`, `location_id`
   - `arrival_ts`, `departure_ts` (UTC)
   - `energy_kwh`
   - `donor_charger_id`
   - `split`

   A CP's sessions never overlap.

## 5. Mocked signals (must-have)

Decision: [0012](../decisions/0012-mock-signals.md). In replay, every signal is **derived from the ground-truth sessions** so the signals stay consistent with each other. All parameters live in `config/signals.yaml`. The values below are placeholders.

| Signal | Generation |
|---|---|
| `ppcblue_route_intent` | For each session: with probability `p_app = 0.6`, emit an event at `arrival − U(10, 40) min` with `eta = arrival + N(0, 3 min)`, for that `location_id`. False positives: 10% extra events with no matching arrival. |
| `maps_traffic_index` | Per location and slot: the real typical traffic of that location at that local hour of the week (`traffic_typical.parquet`, from Mapbox, decision [0028](../decisions/0028-real-traffic-tomtom-mapbox.md)) × weather factor × noise. A value from 0 to 1. Represents general traffic, not EVs. In live mode, TomTom gives the real current value. |
| `maps_ev_count` | Per location and slot: `Poisson(ev_tag_rate × (sessions arriving at this location in the next 30 min + background_ev × traffic_index))`. `ev_tag_rate = 0.25`: most users don't set a vehicle type. |
| `maps_ice_count` | Per location and slot: `Poisson(ice_base × traffic_index)`. Consumers apply weight `w_ice = 0.05`. |

Schema: `ts`, `location_id`, `signal`, `value`, `eta_ts` (nullable), `session_id` (nullable, only for evaluation, **never used as a model feature**).

Live mode does not use this generator. M2 samples live signals from the forecast instead (see spec 02).

## 6. Context features (must-have)
- Romanian weather: Open-Meteo, hourly, at each county centroid. The historical archive covers the replay range; the forecast API covers live mode.
- Romanian holidays: `holidays.country_holidays("RO")`.
- `context.parquet`: `ts_hour`, `county`, `temp_c`, `precip_mm`, `wind_kmh`, `is_holiday`, `is_school_holiday` (stretch).

## 7. County EV stats (must-have)
- Source: data.gov.ro "parc-auto-romania", the end-2025 file. Filter `category = M1` and `fuel = ELECTRIC`, then aggregate by county. Population comes from INS.
- Output `data/seed/county_ev_stats.csv`: `county_code`, `county_name`, `ev_count`, `population`, `ev_per_1000`. It is committed, because the source is open (OGL-ROU-1.0).

## 8. Live-status collector (must-have, runs for the whole hackathon)

Decision: [0003](../decisions/0003-live-status-collection.md).

- Process: `python -m dataset.live_collector`. It runs continuously and polls every 5 minutes.
- **Adapters**, each behind the interface `fetch() -> list[LiveStatus]`:
  - `AmpWhereAdapter`: run by the team, at the risk recorded in the decision. Minimum request rate. Back off and stop on HTTP 403 or 429.
  - `PpcOfficialAdapter`: OCPI or DATEX II, if PPC provides a feed.
  - `FileReplayAdapter`: for tests.
  - There is **no** adapter that bypasses bot protection, e.g. the PPC Blue site's Imperva wall.
- Each status is matched to `cp_id` through M3's `source_refs` (location ID in the source + connector). Unmatched statuses are logged and stored with `cp_id = NULL`.
- Write to the DB table `live_status`: `ts`, `cp_id`, `source`, `raw_status`, `status` (normalised to `CHARGING | AVAILABLE | OFFLINE | UNKNOWN`).
- Raw responses go to `data/raw/live/` (gitignored).

Uses:
- **Validation:** compare observed occupancy by hour with the mapped sessions' expected occupancy for the same weekday and hour (M2 reports it).
- **Live mode** in the simulator (M4).

## 9. Must-have vs stretch

| Must-have | Stretch |
|---|---|
| Dundee ingest, mapping, sessions, signals, context, county stats | School-holiday calendar |
| Live collector with AmpWhere + file adapters | `PpcOfficialAdapter` (only if PPC provides a feed) |
| Fixtures committed early | Perth & Kinross as a second donor pool |

## 10. Acceptance criteria
- `make data` builds every output from `data/raw/` in under 10 minutes on a laptop.
- No CP has overlapping sessions. Every `cp_id` exists in M3's DB.
- The share of sessions with a route intent is within ±2 percentage points of `p_app`.
- A per-class plot of mapped RO arrivals by hour of the week looks like the donor shape scaled. The plot is saved to `data/processed/reports/`.
- The live collector has run for 1 hour without crashing and filled `live_status`.

## 11. Open points
- Dundee's licence is unknown (recorded in 0002).
- `context_multiplier` and the signal parameters are guesses. Change them only through a decision file.
