# 0004: Station list from layered sources, real stations only

- Date: 2026-11-07
- Who: tudor
- Status: superseded by 0014
- Supersedes: —
- Affects: spec 03, PRD

## Context
- Scope is all of Romania. PPC reports 450+ locations and 1,400+ points.
- Open data (OpenStreetMap) confirms only 49 sites. OpenChargeMap has about 26 Enel X sites, and they are stale.
- Geolocet sells 318 sites with connector and kW data for €40, for internal use only.
- Only PPC's AFIR Art. 20 feed would have all of them.

## Decision
Merge the sources in priority order:
1. PPC AFIR feed (requested)
2. Geolocet
3. AmpWhere scrape
4. OSM PPC/Enel X/Hermes
5. OSM chargers near partner stores (low confidence)
6. Press releases

Deduplicate within 50 m and keep each row's source and confidence. Do **not** pad the network with synthetic stations. Scale results up to the full network in the PRD instead.

Kaufland (Renovatio) and Lidl stations are excluded: they are not PPC.

## Alternatives considered
- Synthetic padding to 450 sites, spread by county EV density: looks complete, but is dishonest in front of PPC.
- OSM only: too sparse for "all of Romania".

## Consequences
- The Geolocet and AmpWhere files are never committed. Teammates without them get the OSM + press seed.
- The demo network covers between ~60 and 450+ sites, depending on which sources are available.
