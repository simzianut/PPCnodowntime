# 0017: Dundee cleaning rules, 2-lane split of chargers, and donor utilisation

- Date: 2026-11-07
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: —
- Affects: spec 01 §3, CONTRACTS (donor_chargers extra columns, donor_occupancy)

## Context
Facts from the Dundee CSVs (2021H2–2025, 387,843 rows):
- There is no connector column. Sessions on the same `CP ID` overlap in time.
- In 1–4% of rows, End ≠ Start + Duration. Start and End have minute precision; Duration has second precision.
- Only 3 ultra-rapid chargers exist, none before 2022.
- The download page is disabled, but the public ArcGIS item URLs work without auth.

## Decision
Decided with the user:
- **Departure = Start + Duration.** Fall back to End when Duration is blank. Drop the row if End < Start or departure ≤ arrival.
- **Lanes:** each `CP ID` is split greedily into at most **2 lanes** (`<cp>#1`, `<cp>#2`). A session that finds both lanes busy is dropped (1,640 rows). Each lane is one donor charger and never overlaps itself.
  - Lanes 3–7 held about 1% of AC and 0.3% of DC sessions, but made up 91 near-empty donors that distorted `util_quantile`.
- **DC_HPC donors** are the 3 ultra-rapid chargers (6 lanes). T7 falls back to a DC_50 donor when an HPC point can't get a distinct one.
- **Automated download:** `python -m dataset.dundee download` fetches the 5 item IDs listed in `config/dataset.yaml`, skips existing files, and waits 5 s between requests.

Implementation choices (agent, reviewed):
- Times are parsed as Europe/London (`nonexistent=shift_forward`, `ambiguous=True`), then converted to UTC.
- Rows are dropped in this order:
  1. unparseable start
  2. duplicate SDR ID
  3. kWh blank or ≤ 0
  4. End < Start
  5. no usable departure
  6. departure ≤ arrival
  7. < 2 min
  8. > 24 h
  9. lane overflow

  The counts are in `data/processed/reports/dundee_cleaning.json`.
- `sessions_per_day` = training sessions ÷ the CP ID's active training days (first to last training session). `util_quantile` is its percentile within `power_class` among train-basis chargers.
- 6 chargers that only exist in the test period get `util_basis = test`. They are rated on test data and placed against the train distribution.
- Site and power class are the mode per CP ID; 3 CP IDs appear under more than one site.
- `donor_occupancy.parquet` holds the share of each local hour-of-week that is occupied, averaged over training weeks.

## Alternatives considered
- Drop all overlapping sessions: loses about 5%, mostly at the busiest chargers.
- Keep all 7 lanes: near-empty donors.
- Use the End column: minute precision only.

## Consequences
- Result: 371,228 sessions (train 233,833, test 137,395) and 178 donors (AC_22 114, DC_50 58, DC_HPC 6). 27 donors have < 0.05 sessions/day.
- UK behaviour stands in for Romanian behaviour (0002).
