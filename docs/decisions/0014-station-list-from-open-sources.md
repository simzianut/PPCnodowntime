# 0014: Station list from free online sources only; commit the openly licensed subset

- Date: 2026-11-07
- Who: tudor-claude
- Status: accepted
- Supersedes: 0004
- Affects: spec 03, PRD, T4

## Context
- We will not get a feed from PPC, and the team chose not to buy Geolocet.
- T3 collected stations from free online sources and found 155 locations:
  - 94 high confidence, 5 medium, 56 low
  - 34 counties
  - 421 charge points known at 117 of those locations
- That is about a third of PPC's 450+ locations.
- Archived PPC Blue map pages hold no station data: the map loads it from PPC's private API.

## Decision
- **Sources:** OpenStreetMap, the OpenChargeMap bulk export, a Wayback snapshot of the Hermes Flow `/retea/` page, and press articles.
- **No synthetic padding** (unchanged from 0004).
- **Commit only rows whose sources are all openly licensed** (OSM ODbL, OCM CC BY/CC0) to `data/seed/stations_open.csv`: 116 locations.
- **The full 155-row merge stays local** in `data/raw/stations/`, because the Hermes and press rows come with no licence.
- **Priority for any later source:** OCM, then Hermes archive, then OSM, then press, filled field by field.

## Alternatives considered
- Commit all 155 rows: more stations for every teammate, but the Hermes rows are factual data scraped from an archive page with no licence.
- Call PPC's private map API (`api.ppcblue.ro`): not public, so rejected.
- AmpWhere, ro-evmap.ro, Chargemap: their `robots.txt` blocks AI agents.

## Consequences
- Teammates without the local file simulate 116 locations, not 155. Share the local file by hand if needed.
- 56 `low`-confidence sites may not be PPC. The frontend marks them with a dashed outline.
- Results must be scaled up to 1,400+ charge points in the PRD.
