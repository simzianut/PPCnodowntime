# 0029: AmpWhere snapshot is the main PPC Blue station list (local only)

- Date: 2026-11-07
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: — (extends 0014)
- Affects: spec 03 §1–2 (T4 importer), spec 01 §8

## Context
- The open-data station list (0014) has 155 PPC Blue locations.
- The AmpWhere snapshot the live collector downloads lists **445 PPC Blue locations (provider_id 5) with 1,364 charge points**. Each has coordinates, power, current type, connector type, EVSE id and live status.
- Live statuses can only be matched to a `cp_id` if the charge point exists in M3's DB.

## Decision
- The T4 importer reads the latest AmpWhere snapshot from `data/raw/live/ampwhere_snapshot_latest.json.gz` (written by the collector, gitignored) as the **primary** PPC Blue list. It falls back to the committed `data/seed/stations_open.csv` when the snapshot is absent.
- `source_refs` stores (`ampwhere`, location `uid`, charger id) so live statuses match a `cp_id`.
- AmpWhere-derived data is never committed (0003). The committed seed stays the open-data list.

## Alternatives considered
- Keep the 155-location open list: about 290 PPC locations would get live statuses that match no charge point.

## Consequences
- Anyone without a local snapshot gets the smaller open-data network. Results must state which list they were built on.
- In AmpWhere, 663 PPC charge points are tagged "DC 22 kW". These are probably AC 22 kW points mislabelled; T4 should treat ≤ 22 kW as `AC_22`.
