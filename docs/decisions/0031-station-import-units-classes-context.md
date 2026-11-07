# 0031: Station import: one CP per physical unit, HPC at 100 kW and above, simplified context rules, other operators as a context layer

- Date: 2026-11-07
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: — (changes spec 03 §2.4 and §2.5; builds on 0029)
- Affects: spec 03 §2, CONTRACTS (processed station files), T7, T9

## Context
- The AmpWhere snapshot (0029) lists PPC Blue as 445 locations with 1,364 connectors. Each connector is an EVSE with its own id, e.g. `RO*PPC*E21BZ1859A*1`.
- Grouped by EVSE prefix, the connectors form **671 physical units**:
  - a typical DC unit is CCS 75 kW + CHAdeMO 50 kW + Type2 22 kW
  - a typical AC unit is 2 × Type2 22 kW
  - Type2 sockets are often tagged "DC 22 kW"
- The Dundee donor classes follow the UK scale, where ultra-rapid means 100 kW and above.
- The station DB (T5) doesn't exist yet.

## Decision
Decided with the user:
- **One `cp_id` per physical unit.** `connectors` lists the unit's connector types and `max_kw` is the max connector power. The live status of a CP is CHARGING if any connector is in use.
- **Power class by unit max kW:** ≤ 22 → `AC_22`; < 100 → `DC_50`; ≥ 100 → `DC_HPC`. This replaces spec 03's "> 60 kW". Result: AC_22 332, DC_50 213, DC_HPC 126.
- **Other operators** (5,563 non-PPC locations) go into `other_chargers.parquet` as a context layer for neighbour features and the map. They are not simulated.

Implementation choices (agent, reviewed):
- **IDs:** `location_id` is `L` + 5 digits, kept stable through a local `uid → location_id` map (`data/raw/stations/location_id_map.csv`). The first run assigns ids by (county, name, uid). `cp_id = <location_id>-<n>`, numbered by EVSE prefix.
- **`source_refs`:** one `ampwhere` row per location (uid), and one per connector (`uid|charger id`, with its `cp_id`). Open-data rows within 50 m add osm/ocm refs; the 62 unmatched open-data rows are not added.
- **County:** point-in-polygon on the committed `data/seed/ro_counties.geojson` (geoBoundaries ADM1, CC BY 4.0), with the nearest polygon as a fallback.
- **`context_type`**, checked in this order:
  1. `airport`, by name: Henri Coandă, aeroport…
  2. `retail`, by store keyword
  3. `highway`, if within 500 m of an OSM motorway, or a fuel brand more than 3 km from any city or town
  4. `urban`, if within 3 km of an OSM city or town
  5. `other`

  The keyword lists and distances are in `config/stations.yaml`. OSM data is cached from one Overpass query each.
- **Output:** Parquet files under `data/processed/` until T5's tables exist. The DB upsert runs only when `DATABASE_URL` is set and the tables exist; it is untested.
- If the snapshot is missing, the importer falls back to `stations_open.csv`, with sequential ids and 2 estimated CPs per location.

## Alternatives considered
- One CP per connector (1,364): the agent would put to sleep connectors that share one cabinet.
- Keep the 60 kW HPC cut-off: 35 more HPC units with no matching donor class.
- Simulate every operator: it's not PPC's network.

## Consequences
- 671 CPs is fewer than the press figure of 1,400+, which counts connectors. Results must say "units".
- A unit serves one session at a time in the sim, while real 2-socket AC units can serve two cars.
- Station IDs depend on the local AmpWhere snapshot. A teammate without one gets the open-data network with different IDs.
