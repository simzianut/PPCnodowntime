# 0090: Replay collected AmpWhere days in the replay view

- Date: 2026-11-08
- Who: teodora + teodora-claude
- Status: accepted
- Supersedes: —
- Affects: `api/collected_replay.py` (new), `api/engine_mode.py` (`create_run`, `GET /api/eval`), CONTRACTS `POST /api/runs` and `GET /api/eval`, spec 04 §1, `docs/LIVE_DATA.md` §6, T49

## Context
- Teodora wants the replay view (`/`) to show the real AmpWhere statuses that were collected, rather than synthetic sessions. Without Vlad's private zips the engine replays only synthetic 2024 days (decision 0083).
- Before this change, the only way to replay collected statuses was live mode with the demo clock (`LIVE_CLOCK_START`/`LIVE_CLOCK_SPEED`, decision 0072). It plays in wall time: no pause, no seek, no speed change, and every run follows the same `.env` window.
- `LIVE_DATA.md` §6 asks consumers to reuse the live reader and engine rather than build a new pipeline.

## Decision
- **Which dates count as collected days.** A replay `date` can be a collected day: a Europe/Bucharest local day with at least one whole 5-minute slot between the first and last poll in `LIVE_DIR/statuses-*.jsonl`.
  - The window runs from slot `ceil(first poll)` to slot `floor(last poll)`. Only each file's first and last complete lines are read to find it.
- **How a collected day is replayed.** The run (`CollectedRun`) is the live engine and `LiveStatusReader`, with no new parser.
  - Each step reads the rows a live run would have had when that slot was due (slot end + `LIVE_LAG_S`) and feeds the slot's observed events.
  - The run is paced like any replay, so `play`, `pause`, `speed` and `seek` work. `seek` is clamped to the window.
  - A backward seek rebuilds the reader, a **fresh** GRU forecaster and the policies, then steps forward. The online forecaster has per-CP state, so it can't be rewound.
- **Signals are mocked** from the F0 rates, as in live mode (Teodora's call). `LIVE_SIGNALS` (decision 0084) belongs to T47 and isn't wired here.
- **What the day picker offers** (`GET /api/eval` `test_days`):
  - collected days first, oldest first;
  - then the loaded test days, only when they are real sessions;
  - synthetic days only when there is neither a collected day nor real data, so the app always has something to replay.
- **Live view.** `/?mode=live` goes back to real time (`LIVE_CLOCK_START` empty). Collected data is now watched through the replay view.
- **When the scan runs.** Collected days are scanned only when the API loads its own data. Tests that pass `data=` opt in by setting `EngineManager.collected_dir`.

## Alternatives considered
- **Keep using live mode's demo clock:** no controls, one `.env` window for everyone, and a restart is needed to change it.
- **Turn the statuses into sessions and run the normal replay engine:** that would be a second parser of the same files, which `LIVE_DATA.md` forbids. The precomputed forecasts also don't cover November 2026.
- **A new `mode: "collected"`:** it would change the API shape for both sides, while `date` already carries the meaning. The frontend needed no change.

## Consequences
- The day picker opens on the collected day. KPIs are shadow estimates on real statuses (`LIVE_DATA.md` §6), with mocked signals.
- The timeline still spans 00:00–24:00, so a short window (the 1-hour sample is 00:35–01:30) shows as a narrow band. A frontend follow-up could zoom the timeline to the window.
- `oracle` returns 400 on a collected day, because it needs sessions.
- **The local sample data comes from a CSV export, not the collector.**
  - Teodora's `ampwhere-sample-2026-11-07-2232-2332UTC.csv` (22:32–23:32 UTC, 13 polls) was converted outside the repo into `statuses-20261107.jsonl` and `ampwhere_snapshot_latest.json.gz` under the gitignored `data/raw/live/`.
  - The CSV has AmpWhere's provider id only for PPC Blue (5). Other operators get `provider_id: null` in the JSONL, and placeholder ids (1000+) in the snapshot, which only name their rows in `other_chargers.parquet`.
  - The CSV has no addresses, so `context_type` relies on name and position only.
