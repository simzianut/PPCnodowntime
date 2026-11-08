# 0083: /api/eval lists the days the engine can replay

- Date: 2026-11-08
- Who: tudor + tudor-claude
- Status: accepted
- Supersedes: —
- Affects: `api/engine_mode.py` (`GET /api/eval`), CONTRACTS `GET /api/eval`, T48

## Context
The frontend starts its first replay on `eval.test_days[0]` and offers those days in the picker. `eval_results.json` lists the days the evaluation ran on (2025-03-10 …), but the engine only replays the test days it loaded that `forecasts.parquet` covers (decision 0069). Without the private sessions the engine runs on synthetic 2024 days, so every start got 400 and the page fell back to demo data.

## Decision
In real mode `GET /api/eval` keeps the results from `eval_results.json` but replaces `test_days` with the engine's replayable days.

## Alternatives considered
- Frontend sends `date: null` for the first run: fixes the start but the picker still offers days that 400.
- New `/api/days` route: a contract change for both sides when one field already carries this meaning.

## Consequences
- The picker and the first run always match what `POST /api/runs` accepts.
- When the engine runs on synthetic data the eval numbers come from different days than the ones listed; that only happens without the private data.
