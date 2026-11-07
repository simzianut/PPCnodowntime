# 0047: Stations DB schema, store API, and engineering-grounded power profiles

- Date: 2026-11-07
- Who: vlad + vlad-antigravity
- Status: accepted
- Supersedes: 0005 (placeholder power draw numbers in `config/power_profiles.yaml`)
- Affects: spec 03 §2–§7, `config/power_profiles.yaml`, `db/migrations/0001_stations_schema.sql`, `stations/state.py`, `stations/store.py`

## Context
- Spec 03 §5 and decision 0005 used placeholder wattages (`AC_22: 10/3 W`, `DC_50 & DC_HPC: 150/30/5 W`). The electrical engineering analysis in `engineering/engineering_plan.md` establishes an irreducible auxiliary power floor ($5.5\text{--}9.0\text{ W}$ on AC, $19.0\text{--}32.0\text{ W}$ on DC) required to keep the 4G LTE / OCPP controller, main 24V PPC blue logo LED, and safety watchdog energized, and provides idle/sleep draws across 13 observed capacities ($11\text{--}300\text{ kW}$) and November 2026 electricity tariffs ($1.15\text{ RON/kWh}$ Net B2B, $1.39\text{ RON/kWh}$ Gross Retail).
- T5 also needed to resolve schema requests from T9 (`live_status` source columns, decision 0016), `Recommendation.expires_at` in `CONTRACTS.md`, transition logging for `CHARGING` vs dropped commands (decision 0018), and recommendation approval status (decision 0025).

## Decision
1. **Engineering power profiles (`config/power_profiles.yaml`):**
   - `AC_22` (22 kW reference, 0-CAPEX OTA LED/RFID sleep = `STANDBY`): `idle_w: 25, standby_w: 9, deep_sleep_w: null, standby_wake_s: 10, deep_sleep_wake_s: null`.
   - `DC_50` (50–85 kW mid-range DC reference): `idle_w: 105, standby_w: 35, deep_sleep_w: 20, standby_wake_s: 10, deep_sleep_wake_s: 180`.
   - `DC_HPC` (100–300 kW ultra-fast/HPC reference): `idle_w: 165, standby_w: 50, deep_sleep_w: 25.5, standby_wake_s: 10, deep_sleep_wake_s: 180`.
   - `standby_wake_s: 10` and `deep_sleep_wake_s: 180` remain in `config/power_profiles.yaml` so M2 (`cost_rule`) and M4 (`sim`) wait trade-offs stay calibrated, while hardware contactor pre-charge latency (`precharge_wake_s`: $0.5\text{ s}$ AC, $5\text{--}8\text{ s}$ DC), the 13-capacity lookup table (`max_kw`), and RON tariffs (`1.15` net / `1.39` gross) are exposed in `stations.store`.
   - The cabinet humidity/dew-point guard ($\ge 85\%\text{ RH}$) is treated strictly as an autonomous local PLC hardware loop within the $0\text{--}5\text{ W}$ thermal allowance.
2. **Database schema (`db/migrations/0001_stations_schema.sql`):**
   - Added nullable `source_location_id text, source_cp_id text, connector text` to `live_status` (per T9 / decision 0016).
   - Added `expires_at timestamptz` to `commands` to match `CONTRACTS.md` `Recommendation`.
   - `upsert_db` seeds initial `cp_state` rows as `IDLE`, and migration 0001 seeds `settings('autonomy_mode' = '"ACT"')`.
3. **Transition logging and store behaviour (`stations/state.py`, `stations/store.py`):**
   - Transition rules match decision 0018 (`sim/transitions.py`).
   - In `stations.store.apply_command`, commands to a `CHARGING` CP are persisted in `commands` with `status = 'rejected'` and `reason = 'busy'` (spec 03 §4), while `same`, `waking`, and `offline` commands are dropped silently and return `None`.
   - `decide_recommendation` sets `status = 'approved'` (applied at the start of the next slot per decision 0025) or `'rejected'`, raising `KeyError` (404) when missing and `ValueError` (409) when already decided or expired.
   - `list_locations` and `get_location` fall back to `data/processed/*.parquet` (or `data/seed/stations_open.csv`) when Postgres is unavailable.

## Alternatives considered
- Keep `5 W` DC deep sleep: physically impossible while keeping the 4G OCPP SBC and PPC logo backlight energized.
- Change `deep_sleep_wake_s` to `8 s` in `config/power_profiles.yaml`: flattens the unannounced walk-up wait penalty in the simulator and breaks M2/M4 calibration.

## Consequences
- `stations.store` works both with Postgres + PostGIS (`make db && make seed`) and in headless/Parquet environments.
