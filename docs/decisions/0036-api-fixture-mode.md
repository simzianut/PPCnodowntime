# 0036: API fixture mode plays a heuristic recorded day

- Date: 2026-11-07
- Who: tudor-claude
- Status: accepted
- Supersedes: —
- Affects: spec 04 §6, fixtures/api/, api/, M5

## Context
M5 needs every route and the WebSocket before the engine-backed API (T12) exists. T19 was built on a branch without the engine, so its recorded day can't come from the engine yet.

## Decision
- `python -m api.fixtures_gen` (seeded, byte-identical output) writes `fixtures/api/`: `stations.json` (116 seed locations, provisional IDs from decision 0024), `policies.json`, `eval_results.json` (marked `"fixture": true`, invented but plausible numbers) and `run_day.json` (288 slots, 4 policies, 308 CPs, about 1.3 MB). `api/fixture_store.py` is its only reader.
- The recorded day comes from a simple heuristic, not the engine: no sibling handover (`moved_from` always null, `queued` always false), naive sleeps a fixed 01:00–04:00 window, and `min_awake_per_location` is not applied.
- Replay accepts any test day from `eval_results.json` and plays the recorded day re-dated. Live mode plays it from the current local slot at one slot every 5 s, never auto-pauses, and refuses `speed` and `seek`.
- RECOMMEND: the focus policy's recorded commands become recommendations and the run auto-pauses (decision 0025). A recommended CP holds its state until decided; on approve it moves to the target on the next tick. KPIs and timelines come from the recording and don't reflect operator choices.
- In ACT mode the focus policy's commands are sent in the tick's `recommendations` with status `applied`, for M5's activity log (CONTRACTS).
- `GET /api/stations` includes `state` from the most recently created run, so the map can be tested without a live run.
- Without `API_FIXTURES=1`, every route except `/api/health` returns 503 until T12.
- A client whose 5,000-message queue fills is disconnected with code 1013, so it never blocks a run.

## Alternatives considered
- Static responses per route: M5 couldn't exercise play, pause, seek or recommendations.
- Wait for the engine: blocks M5.

## Consequences
- Fixture numbers say nothing about real savings. T12 regenerates `run_day.json` and `eval_results.json` from the engine through the same `RecordedDay` format.
