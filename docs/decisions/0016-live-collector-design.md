# 0016: Live collector writes JSONL first, DB when T5 lands; AmpWhere via snapshot + delta endpoints

- Date: 2026-11-07
- Who: vlad-claude
- Status: accepted
- Supersedes: —
- Affects: spec 01 §8, CONTRACTS (config/live.yaml, live JSONL)

## Context
- The collector must start now (spec 01 §8), but the `live_status` table belongs to T5, which isn't done.
- Agents may not fetch AmpWhere themselves (0003). A human captured a HAR; the endpoints below come from it.
- PPC can't provide a feed during the hackathon, so AmpWhere is the only live source.

## Decision
- **Sinks:**
  - Every poll appends normalised rows to `data/raw/live/statuses-YYYYMMDD.jsonl` (UTC date).
  - `DbSink` inserts into `live_status` only when `DATABASE_URL` is set and the table exists. Until then it is a no-op.
  - The JSONL keeps `source_location_id`, `source_cp_id` and `connector`, which the current `live_status` DDL lacks. That lets rows be matched to `cp_id` later.
- **AmpWhere:**
  - URLs, `resync_s` and an honest User-Agent live in `config/live.yaml` (`ampwhere:`). No cookies or auth are needed.
  - First poll and every `resync_s` (6 h) fetch the full snapshot (~5.8 MB); other polls fetch the small status delta.
  - A delta with a missing `latest_status_updated_at`, an unparsable body or an unknown location forces a snapshot.
  - Rows are emitted for every charger after a snapshot, and only for changed locations after a delta.
- **Politeness:**
  - one request at a time, ≥ 2 s apart, polled every 300 s
  - stop on HTTP 403 or 429
  - exponential backoff on 5xx and timeouts
  - exit after 5 consecutive failed polls
- Status words map to `LiveStatus` through `config/live.yaml` (`status_map`); unknown words become `UNKNOWN`.
- The heartbeat line per poll goes to `data/raw/live/collector.log` so runs can be supervised.

## Alternatives considered
- Wait for T5's table: loses hours of live data.
- Snapshot every poll: 5.8 MB per 5 min is needlessly heavy.

## Consequences
- T5 should add `source_location_id`, `source_cp_id` and `connector` to `live_status`, or M2/M4 read the JSONL for matching.
- If AmpWhere changes its API or blocks us, the collector stops cleanly and we keep whatever was collected.
