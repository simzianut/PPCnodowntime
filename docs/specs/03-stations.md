# Spec 03: PPC station network + states

Owner folder: `stations/`, `db/migrations/`, `config/power_profiles.yaml`. Builds the database of PPC locations and charge points across Romania, and defines the charge-point state model: the states, their power draw, the transitions, and the command log.

Decisions: [0004](../decisions/0004-station-list-layered-sources.md), [0005](../decisions/0005-charge-point-states-and-power.md), [0009](../decisions/0009-global-autonomy-switch.md).

## 1. Sources and priority

The importer merges sources in this priority order. When two rows are the same site, the higher-priority source wins field by field, and lower sources only fill fields that are empty.

| Priority | Source | Expected | Licence handling |
|---|---|---|---|
| 1 | **PPC AFIR Art. 20 static feed** (DATEX II / OCPI). Ask the organisers now. | 450+ locations, all CPs | Use as PPC allows |
| 2 | **Geolocet PPC Blue dataset** (€40, dated 2026-08-28) | 318 sites with address, county, connectors, max kW (86% filled) | Internal use only: file stays in `data/raw/geolocet/`, never committed |
| 3 | **AmpWhere scrape** (team-run, [0003](../decisions/0003-live-status-collection.md)) | unknown | `data/raw/ampwhere/`, never committed |
| 4 | **OpenStreetMap**: operator/brand/network matching `ppc`, `enel`, `hermes` | 49 sites | ODbL, committed as `data/seed/stations_osm.geojson` with attribution |
| 5 | **OSM chargers within 150 m of PPC partner stores** (Penny, Selgros, Dedeman, Altex, Mega Image, Carrefour, Romstal), with no operator tag | ~57 candidates | ODbL. Imported with `confidence = low` until another source confirms them |
| 6 | **Press releases** (Selgros, Penny, Otopeni airport, A7) | ~10 named sites | Typed by hand into `data/seed/stations_press.csv` |

Exclude stations at Kaufland (Renovatio) and Lidl (self-operated); they are not PPC.

**No synthetic padding.** Only stations with real coordinates are simulated. The PRD scales results up to 1,400+ CPs and states the method.

## 2. Import pipeline (`make seed`)

1. Load each available source into a common `RawStation` shape. Missing sources are skipped with a warning, so teammates without the Geolocet file still get the OSM seed.
2. Deduplicate: same site if the sites are less than 50 m apart, or less than 150 m apart with name similarity ≥ 0.6.
3. Assign the county by point-in-polygon on `data/seed/ro_counties.geojson` (geoBoundaries ADM1, CC BY 4.0).
4. Assign `context_type`:
   - `airport` if the name matches an airport
   - `highway` if within 500 m of a motorway (OSM `highway=motorway`)
   - `retail` if near a partner store
   - otherwise `urban` inside a city boundary, else `other`
5. Expand to charge points:
   - Use the source's connector and point counts where they exist.
   - Otherwise default to 2 CPs, with `cp_count_estimated = true`.
   - Each CP gets a `power_class`: `AC_22` for AC up to 22 kW, `DC_50` for DC up to 60 kW, `DC_HPC` for DC above 60 kW.
6. Write the `locations`, `charge_points` and `source_refs` tables, then print a coverage report: counts by source, county and power class.

## 3. Database schema (Postgres + PostGIS)

```sql
locations(
  location_id text pk, name text, operator text default 'PPC Blue', partner text,
  address text, county_code text, geom geography(Point,4326),
  context_type text check in ('urban','retail','highway','airport','other'),
  primary_source text, confidence text check in ('high','medium','low'),
  cp_count_estimated bool, created_at timestamptz)

charge_points(
  cp_id text pk, location_id text fk, power_class text check in ('AC_22','DC_50','DC_HPC'),
  max_kw numeric, connectors text[],          -- e.g. {'TYPE2'} or {'CCS2','CHADEMO'}
  current_type text check in ('AC','DC'))

source_refs(source text, source_id text, location_id text fk, cp_id text null, pk(source, source_id))

cp_state(                                     -- current state in LIVE mode only
  cp_id text pk fk, state text, since_ts timestamptz, last_command_id bigint null)

state_events(id bigserial, ts timestamptz, cp_id text, from_state text, to_state text,
  cause text check in ('agent','operator','arrival','departure','fault','live_status'),
  run_id uuid null)

commands(
  id bigserial pk, ts timestamptz, run_id uuid null, cp_id text, policy text,
  from_state text, target_state text, reason text,
  status text check in ('applied','pending','approved','rejected','expired'),
  decided_by text null, decided_at timestamptz null)

settings(key text pk, value jsonb)            -- 'autonomy_mode' → "ACT" | "RECOMMEND"

live_status(ts timestamptz, cp_id text null, source text, raw_status text, status text)  -- written by M1
```

Simulation runs keep their state in memory (M4) and only write `commands` and `state_events` that carry a `run_id`. In live mode, `cp_state` holds the current truth. This module's Python API is the only code that writes these tables: `stations.store`.

## 4. State model

States: `CHARGING`, `IDLE`, `WAKING`, `STANDBY`, `DEEP_SLEEP`, `OFFLINE`.

```
            agent cmd               agent cmd
   IDLE ───────────────► STANDBY ─────────────► DEEP_SLEEP (DC only)
    ▲  ▲                    │                        │
    │  │ wake done          │ wake cmd / plug-in     │ wake cmd / plug-in
    │  └──── WAKING ◄───────┴────────────────────────┘
    │  plug-in (from IDLE, no wait)
    ▼
 CHARGING ──departure──► IDLE
 any ──fault──► OFFLINE ──recovered──► IDLE
```

- **WAKING** is a transition state. It lasts `wake_s` of the state it is leaving and draws `idle_w`.
- A driver who plugs into a CP that is WAKING, STANDBY or DEEP_SLEEP waits until the CP is ready. That wait is the "added wait".
- CHARGING cannot be interrupted. Commands to a CHARGING CP are rejected with `reason = busy`.
- AC charge points have no DEEP_SLEEP. A DEEP_SLEEP command for an AC CP is applied as STANDBY.

## 5. Power profiles: `config/power_profiles.yaml`

Placeholders (decision 0005). The team will replace them with datasheet or PPC numbers, recording each change in a decision file.

```yaml
AC_22:  {idle_w: 10,  standby_w: 3,  deep_sleep_w: null, standby_wake_s: 10, deep_sleep_wake_s: null}
DC_50:  {idle_w: 150, standby_w: 30, deep_sleep_w: 5,    standby_wake_s: 10, deep_sleep_wake_s: 180}
DC_HPC: {idle_w: 150, standby_w: 30, deep_sleep_w: 5,    standby_wake_s: 10, deep_sleep_wake_s: 180}
wake_extra_wh: 0      # one-off energy per wake, placeholder
```

During CHARGING, only energy delivered to the car is counted, and it is not overhead. OFFLINE draws 0.

## 6. Autonomy

`settings.autonomy_mode` is a single global switch (decision 0009):
- **ACT:** a policy command becomes a `commands` row with `status = applied` and the state changes.
- **RECOMMEND:** the command becomes `status = pending`. An operator approves it (applied) or rejects it, through the API. Pending commands expire after `recommendation_ttl_slots` (from `config/sim.yaml`, default 3).

## 7. Python API (`stations.store`)

`list_locations(bbox=None, county=None)`, `get_location(id)`, `get_power_profile(power_class)`, `apply_command(cmd, mode)`, `decide_recommendation(id, approve, who)`, `get_autonomy()` / `set_autonomy(mode)`, `record_state_event(...)`. M4 calls these. It never writes SQL against these tables directly.

## 8. Must-have vs stretch

| Must-have | Stretch |
|---|---|
| OSM + press seed committed, Geolocet + AmpWhere importers | PPC AFIR / OCPI importer |
| Dedupe, counties, context, CP expansion, coverage report | Manual review UI for `low`-confidence sites |
| Schema, state model, power profiles, autonomy | Per-CP fault simulation |

## 9. Acceptance criteria
- `make seed` works with only the committed seed data, and works with the Geolocet and AmpWhere files present.
- No two locations are less than 50 m apart. Every location has a county and a context type.
- A unit test checks every allowed and forbidden transition in §4.
- The coverage report is committed to `docs/specs/03-coverage.md` after the first full import.
