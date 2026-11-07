# Spec 03: PPC station network + states

Owner folder: `stations/`, `db/migrations/`, `config/power_profiles.yaml`. Builds the database of PPC locations and charge points across Romania, and defines the charge-point state model: the states, their power draw, the transitions, and the command log.

Decisions: [0014](../decisions/0014-station-list-from-open-sources.md) (supersedes 0004), [0005](../decisions/0005-charge-point-states-and-power.md), [0009](../decisions/0009-global-autonomy-switch.md), [0047](../decisions/0047-stations-db-store-and-engineering-power-profiles.md) (supersedes 0005 power numbers).

## 1. Sources: what T3 collected (2026-11-07)

There is no PPC feed and no paid data. Everything comes from free online sources, collected in T3.

**Result: 155 locations in 34 counties.**

| Confidence | Locations | Meaning |
|---|---|---|
| `high` | 94 | A source names PPC, Enel X or Hermes as the operator |
| `medium` | 5 | A partner-store site, confirmed by press |
| `low` | 56 | An OSM charger within 150 m of a PPC partner store, with no operator tag. Chargers tagged with other operators (E.ON, Eldrive, Renovatio, Tesla, …) are left out. |

**Detail coverage:**
- Charge-point count: 117 locations. They add up to 421 CPs. For Hermes rows the count is connectors, not CPs.
- Connectors: 94 locations.
- Max kW: 85 locations.
- Current type: 94 locations (55 AC+DC, 22 AC only, 17 DC only).

**Biggest counties:** București 33, Ilfov 14, Cluj 12, Constanța 11, Satu Mare 9, Timiș 9.

**Partner sites:** Penny 31 (only 4 confirmed, against 75 in press), Romstal 19, malls 15, Carrefour 14, Mega Image 12, Altex 10, Selgros 6, Dedeman 4, Otopeni airport 1 (14 points × 200 kW).

The press figure is 450+ locations and 1,400+ CPs, so we have roughly a third of the network.

| Source | Locations it contributes to | Licence | Committed? |
|---|---|---|---|
| OpenStreetMap (Overpass, re-run 2026-11-07, 761 RO chargers): operator/brand/network/name variants of PPC, Enel X, Hermes, plus chargers near partner stores | 90 | ODbL 1.0 (attribution, share-alike) | Yes |
| OpenChargeMap keyless bulk export (`github.com/openchargemap/ocm-export`, `data/RO`, snapshot 2026-04-22), Enel X / PPC POIs. Most were last updated 2021–23. | 43 | CC BY 4.0, a few CC0 (Voltrelli) | Yes |
| Hermes Flow network page `/retea/`, Wayback snapshot of 2026-02-16 | 34 | No licence; factual data from an archived public page | No (local only) |
| Press articles (revistabiz, startupcafe): Selgros Sibiu, Craiova, Agigea, Constanța, Otopeni airport. 12 *planned* sites are kept apart in `press_named_sites.csv`. | 5 | Facts only | No (local only) |

**Sources tried that gave nothing usable:**
- Romania's National Access Point (pna.cestrin.ro): 30 datasets, none about charging or AFIR.
- Archived PPC Blue map pages: they load stations in the browser from PPC's private API, so there is no data in the HTML. We don't call that API.
- GoingElectric: 598 RO locations, none PPC, and its API terms only allow use inside apps.
- Chargefinder: no Romania coverage.
- Electromaps, Plugsurfing, Shell Recharge, Nextcharge: maps served only through private APIs.
- EVmapa: Czech/Slovak only.
- Penny store locator: 467 stores. Its EV-charging filter exists, but no store has the flag set yet.

**Not used, on purpose:** AmpWhere, ro-evmap.ro and Chargemap (their `robots.txt` blocks AI agents), the ppcblue.ro bot wall, and PPC's private API.

**Files:**
- `data/seed/stations_open.csv`: **committed**. The 116 locations whose sources are only OSM and/or OCM: 60 high confidence, 56 low.
- `data/raw/stations/ppc_stations_merged.csv`: all 155 locations, gitignored, **local only**. Also local: `press_named_sites.csv`.
- `data/raw/stations/wayback_hermesflow_retea.csv`: the Hermes rows, local only.

Columns in both station files:
- `name, partner, address, city, county, lat, lon`
- `n_charge_points, connectors, max_kw, current_types`
- `sources, source_ids, confidence, licence_notes, status, notes`

Attribution (shown in the frontend and the README): "© OpenStreetMap contributors (ODbL)" and "Open Charge Map contributors (CC BY 4.0)".

**Priority when sources disagree:** OCM, then Hermes archive, then OSM, then press. A higher source wins field by field. Lower sources only fill fields that are empty.

Stations at Kaufland (Renovatio) and Lidl (self-operated) are excluded: they are not PPC.

**No synthetic padding.** Only these real locations are simulated. The PRD scales results up to 1,400+ CPs and states the method.

**Ways to close the gap:**
- **Penny locator:** check again later. Once its EV flag is filled in, it confirms about 75 sites at once.
- **AFIR static data:** ask RAR or the transport ministry where it is published. It is free, but someone has to ask, and they aren't PPC.
- **Google Places API, free quota:** can *check* our list. Only place IDs may be stored, so it can't build the database.
- **AmpWhere collector** (decision 0003, run by a human): if it yields a station list, add it as a local-only source.
- Check the `low`-confidence sites by hand.

## 2. Import pipeline (`make seed`)

Decisions: [0029](../decisions/0029-ampwhere-ppc-station-list.md), [0033](../decisions/0033-station-import-units-classes-context.md). Built by `python -m stations` (`stations/importer.py`, settings in `config/stations.yaml`).

0. **Primary source:** if `data/raw/live/ampwhere_snapshot_latest.json.gz` exists (written by the live collector, local only), use its PPC Blue locations (`provider_id` 5). Open-data rows within 50 m only add osm/ocm `source_refs`; unmatched ones are not added. Other operators go to `data/processed/other_chargers.parquet` as a context layer. Steps 1–2 below apply only when the snapshot is absent.

1. Load `data/seed/stations_open.csv`. If `data/raw/stations/ppc_stations_merged.csv` exists, load that instead. Both are already deduplicated within 50 m. Map each row to a common `RawStation` shape.
2. Deduplicate again, to cover any extra local source: same site if the sites are less than 50 m apart, or less than 150 m apart with name similarity ≥ 0.6.
3. Assign the county by point-in-polygon on `data/seed/ro_counties.geojson` (geoBoundaries ADM1, CC BY 4.0).
4. Assign `context_type`, first match wins (0033):
   - `airport` if the name matches an airport
   - `retail` if the name or address matches a store keyword
   - `highway` if within 500 m of a motorway (OSM `highway=motorway`), or a fuel brand more than 3 km from any city or town
   - `urban` if within 3 km of an OSM city or town, else `other`
5. Expand to charge points:
   - AmpWhere: one CP per physical unit, i.e. connectors grouped by EVSE id prefix (`RO*PPC*<unit>*<n>`). This gives 671 CPs and 1,364 connectors.
   - Open data: use the source's connector and point counts where they exist. Otherwise default to 2 CPs, with `cp_count_estimated = true`.
   - Each CP gets a `power_class` from its maximum connector power (0033): `AC_22` up to 22 kW, `DC_50` below 100 kW, `DC_HPC` at 100 kW and above. Type2 sockets tagged "DC 22 kW" count as AC.
   - IDs stay stable across reruns through `data/raw/stations/location_id_map.csv` (local).
6. Write `data/processed/locations.parquet`, `charge_points.parquet`, `source_refs.parquet` and `other_chargers.parquet`. Also upsert into the DB tables when `DATABASE_URL` is set and T5's tables exist. Then write `data/processed/reports/stations_coverage.json`: counts by source, county, power class and context type.

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
  expires_at timestamptz null, decided_by text null, decided_at timestamptz null)

settings(key text pk, value jsonb)            -- 'autonomy_mode' → "ACT" | "RECOMMEND"

live_status(id bigserial pk, ts timestamptz, cp_id text null, source text,
  source_location_id text null, source_cp_id text null, connector text null,
  raw_status text, status text)  -- written by M1 (decision 0016, 0047)
```

Simulation runs keep their state in memory (M4) and only write `commands` and `state_events` that carry a `run_id`. In live mode, `cp_state` holds the current truth (seeded as `IDLE` on import). This module's Python API is the only code that writes these tables: `stations.store`.

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

- Going to sleep is instant, including IDLE → DEEP_SLEEP directly. DEEP_SLEEP → STANDBY goes through WAKING (deep-sleep wake time), then STANDBY. A WAKING CP ignores sleep commands until ready. Commands equal to the current state are dropped. See [0018](../decisions/0018-engine-transition-rules.md).
- **WAKING** is a transition state. It lasts `wake_s` of the state it is leaving and draws `idle_w`.
- A driver who plugs into a CP that is WAKING, STANDBY or DEEP_SLEEP waits until the CP is ready. That wait is the "added wait".
- CHARGING cannot be interrupted. Commands to a CHARGING CP are recorded in `commands` as `status = rejected` with `reason = busy` (decision [0047](../decisions/0047-stations-db-store-and-engineering-power-profiles.md)).
- AC charge points have no DEEP_SLEEP. A DEEP_SLEEP command for an AC CP is applied as STANDBY.

## 5. Power profiles: `config/power_profiles.yaml`

Grounded in the electrical engineering analysis in `engineering/engineering_plan.md` ([decision 0047](../decisions/0047-stations-db-store-and-engineering-power-profiles.md), superseding 0005 placeholders):

```yaml
AC_22:  {idle_w: 25,  standby_w: 9,  deep_sleep_w: null, standby_wake_s: 10, deep_sleep_wake_s: null}
DC_50:  {idle_w: 105, standby_w: 35, deep_sleep_w: 20,   standby_wake_s: 10, deep_sleep_wake_s: 180}
DC_HPC: {idle_w: 165, standby_w: 50, deep_sleep_w: 25.5, standby_wake_s: 10, deep_sleep_wake_s: 180}
wake_extra_wh: 0      # one-off energy per wake, placeholder
```

`stations.store.get_power_profile(power_class, max_kw=None)` also supports the exact 13-capacity engineering table ($11\text{--}300\text{ kW}$), hardware contactor pre-charge latency (`precharge_wake_s`: $0.5\text{ s}$ AC, $5\text{--}8\text{ s}$ DC), and `stations.store.get_energy_tariffs()` ($1.15\text{ RON/kWh}$ Net B2B, $1.39\text{ RON/kWh}$ Gross Retail). During CHARGING, only energy delivered to the car is counted, and it is not overhead. OFFLINE draws 0.

## 6. Autonomy

`settings.autonomy_mode` is a single global switch (decision 0009):
- **ACT:** a policy command becomes a `commands` row with `status = applied` and the state changes.
- **RECOMMEND:** the command becomes `status = pending` with `expires_at = ts + recommendation_ttl_slots * slot_s`. An operator approves it (`status = approved`, applied at the start of the next slot per [decision 0025](../decisions/0025-recommend-in-replay.md)) or rejects it (`status = rejected`), through the API. Pending commands expire after `recommendation_ttl_slots` (from `config/sim.yaml`, default 3).

## 7. Python API (`stations.store`)

`list_locations(bbox=None, county=None)`, `get_location(id)`, `get_power_profile(power_class, max_kw=None)`, `get_energy_tariffs()`, `apply_command(cmd, mode)`, `decide_recommendation(id, approve, who)`, `list_recommendations(run_id=None, status='pending')`, `get_autonomy()` / `set_autonomy(mode)`, `record_state_event(...)`, `get_cp_states()`. Read queries (`list_locations`, `get_location`) fall back to `data/processed/*.parquet` (or `data/seed/stations_open.csv`) when Postgres is unavailable. M4 calls these. It never writes SQL against these tables directly.

## 8. Must-have vs stretch

| Must-have | Stretch |
|---|---|
| Import of the open seed + local merged file | AmpWhere station list as an extra local source |
| Dedupe, counties, context, CP expansion, coverage report | Manual review UI for `low`-confidence sites |
| Schema, state model, power profiles, autonomy | Per-CP fault simulation |

## 9. Acceptance criteria
- `make seed` works with only `data/seed/stations_open.csv` (116 locations), and with the local merged file present (155 locations).
- No two locations are less than 50 m apart. Every location has a county and a context type.
- A unit test checks every allowed and forbidden transition in §4.
- The coverage report is committed to `docs/specs/03-coverage.md` after the first full import.
