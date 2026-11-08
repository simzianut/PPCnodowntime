# 0094: Reproducible demo from a clean clone: committed OSM copy, README walkthrough, data manifest

- Date: 2026-11-08
- Who: tudor + tudor-claude
- Status: accepted
- Supersedes: —
- Affects: README, stations/importer.py, data/seed/osm/, docs/data-manifest.md

## Context
A clean clone plus the AmpWhere archive must reproduce the demo. Checked on 2026-11-08 with a fresh clone in a separate compose project: `ppc-seed` fetches the OpenStreetMap motorway and place layers from Overpass when `data/raw/osm/` is missing. Overpass answered 504, the seed failed (non-fatal), `source_refs.parquet` was never built and the collected-day replay could not start.

## Decision
- Commit gzipped copies of the two Overpass results as `data/seed/osm/ro_motorways.json.gz` and `ro_places.json.gz` (OpenStreetMap, ODbL, attribution in the README). `stations/importer.py` uses them when `data/raw/osm/` has no cache, and only queries Overpass when neither exists.
- With them, a fresh `ppc-seed` builds `locations`, `charge_points`, `source_refs` and `other_chargers` byte-identical to the reference (same SHA-256), and the same `location_id_map.csv`, from the archive's snapshot alone. `location_id_map.csv` stays out of git (it holds AmpWhere location ids, decision 0003).
- The README's first section is a step-by-step reproduction with the archive checksum, the expected screen and a log line to check. `docs/data-manifest.md` lists the SHA-256 of the reference `data/processed/`.

## Alternatives considered
- Retry Overpass: still depends on a third-party service at demo time.
- Ship a `data/processed` tarball: it contains Dundee-derived data with a restrictive licence.

## Consequences
- 0.56 MB more in git. The OSM layers are frozen at the 2026-11-08 extract; delete `data/raw/osm/` and the seed copy to refresh.
