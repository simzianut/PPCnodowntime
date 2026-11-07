# 0047: One station loader for sim and API; the API runs replays on sim.Engine in memory

- Date: 2026-11-07
- Who: tudor + tudor-claude
- Status: accepted
- Supersedes: — (replaces the provisional-ID part of 0024 when real station files exist; splits T12)
- Affects: CONTRACTS v0.3, spec 04 §6, `sim/stations_seed.py`, `sim/data.py`, `api/engine_mode.py`, `api/main.py`, `sim/engine.py`, `frontend/src/state/useDashboard.ts`, `frontend/src/App.tsx`

## Context
- Dataset, sim and frontend were all on `main` but used different station lists:
  - The sim read the 116-row open seed with provisional IDs `L1nnnn` (0024).
  - `load_processed()` paired those CPs with real `sessions.parquet`, whose IDs are `L00001-x`. Nothing matched.
  - The frontend saw only fixture data or its own demo.
- The API returned 503 outside fixture mode (T19). T12 (the real API) depends on T5 (DB), which isn't merged yet.
- The real PPC Blue list (445 locations, 671 CPs) is AmpWhere-derived, so it must stay local (0003, 0029). Tudor has it as a CSV export: `ppc_locations.csv`, `ppc_charge_points.csv`.

## Decision
- **One loader:** `sim.stations_seed.load_stations()` is the only station source for the sim, gym env and API. It checks `DATA_DIR/processed/` in this order:
  1. `locations.parquet` + `charge_points.parquet` (the T4 importer output)
  2. `ppc_locations.csv` + `ppc_charge_points.csv` (the export format, `connectors` joined with `+`)
  3. the committed open seed
- **Loader details:**
  - It logs which list it used, since 0029 requires results to say which list they were built on.
  - CSV rows without `primary_source` or `confidence` get `ampwhere` and `high`.
- **Connector spelling:** connectors are normalised to the seed's spelling, `Type2`, `CCS2`, `CHAdeMO`, matched case-insensitively.
  - A bare `CCS` becomes `CCS2` (the European standard).
  - Repeats collapse to a sorted set, because `sim.engine.compatible()` only compares sets.
- **Synthetic sessions on real stations:** `load_default()` uses real processed sessions when they exist. Otherwise it generates synthetic sessions on `load_stations()`.
- **What stays on the open seed:** `generate()`'s own default, `api/fixtures_gen.py` and the committed `fixtures/api/` all keep the open seed, so nothing AmpWhere-derived gets committed.
- **Test isolation:** a root `conftest.py` points `DATA_DIR` at an empty dir, so tests never see a developer's local data.
- **API real mode** (`API_FIXTURES=0`, `api/engine_mode.py`) runs in memory, with no DB:
  - Each run is one `sim.Engine` day.
  - Steps run in a worker thread, `speed` slots per second.
  - REST and WS shapes are the ones in CONTRACTS.
  - Live mode returns 400 until T12. `StationRunSummary` fields are null.
  - `GET /api/eval` returns 404 until T11 writes `artifacts/eval_results.json`.
- **Policies:**
  - A policy is `available: false` if it can't be built: `model` fails to import, or `artifacts/ppo_policy.zip` is missing for `rl_ppo` (otherwise `RLPolicy` would silently use an untrained network). Asking for an unavailable policy returns 400.
  - `cost_rule` lists `lambda_grid`. `rl_ppo` lists the λ it was trained with (`ppo_meta.json`).
  - Any λ > 0 is accepted.
- **Runs:**
  - Replay `date: null` means the first test day of the loaded data.
  - Seeking forward steps the engine. Seeking backward builds a fresh engine and re-runs from midnight, in the run's current autonomy mode.
  - Seek and focus changes expire every pending recommendation (spec 04 §6, same as fixture mode).
  - Recommendation ids are global integers, unique across runs and engine rebuilds.
  - In ACT mode the focus policy's applied commands are sent with status `applied` for the activity log. This uses the new `Engine.applied_commands()`; `Engine.expire_pending()` was also added.
  - At most 10 runs are kept, since each engine holds a full day of history.
  - Station `state` comes from the latest run's focus policy as soon as any run exists, not only in live mode.
  - If an engine step fails, the error is logged and the run pauses.
- **Frontend:**
  - A missing eval shows "Evaluation not run yet" instead of dropping to demo mode. The replay date picker falls back to the run's date.
  - Runs request only the policies `GET /api/policies` marks available. The focus is `rl_ppo` if available, else the last available policy.
  - The recommendation feed keeps only `pending` items.

## Alternatives considered
- Regenerate the fixture day locally from the real stations: cheap, but the policies stay faked and the engine stays unconnected.
- Wait for T5 and build T12 on the DB: blocks every integration on an unmerged task.
- Commit the real station list: not allowed (0003, 0029).

## Consequences
- **Same command, different networks:** `make api` and `make web` show the real network on a laptop that has the station files, and the open seed elsewhere.
- **Cost:** with 671 CPs the synthetic bundle takes about 8 s and 1.4 GB to build, almost all of it signals. An engine step costs about 5 ms with the baselines.
- **Policies that won't load here:** `cost_rule` and `rl_ppo` need lightgbm to load (on macOS: `brew install libomp`) and `stable-baselines3` (`uv sync --extra rl`; `make api` now runs with `--extra rl`). Without them, only `always_on` and `naive_quiet_hours` run.
- **T12 scope:** T12 keeps DB persistence and live mode. T11 still has to produce `eval_results.json`.
- **Gym env cache:** `sim.gym_env.default_data` caches by seed only, not by station source.
