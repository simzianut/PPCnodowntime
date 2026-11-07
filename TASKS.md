# Task board

Rules: see `AGENTS.md`. Claim = move the task to **In progress** with your name and branch, then commit and push to `main` *before* coding.
Task IDs only go up. The new ID is the highest existing ID + 1.

Format:
`- [T<id>] <title> | owner: <person>-<agent> | branch: <branch> | depends: T<id>`
Add notes as indented sub-bullets.

## In progress
- [T6] M1 Dundee ingest + Open-Meteo weather at Dundee + GB-SCT holidays + county_ev_stats.csv; commit fixtures/sessions_sample + signals_sample early | owner: vlad-claude | branch: vlad-claude/T6-dundee-ingest | depends: T2
- [T16] M5 frontend scaffold + Romania map with CP states, against fixtures/MSW (spec 05 §3.1) | owner: teodora | branch: teodora/frontend-spec05 | depends: T2
- [T17] M5 KPI panel + energy-vs-wait curve + replay controls + autonomy switch + recommendations feed (spec 05 §3.2–3.3) | owner: teodora | branch: teodora/frontend-spec05 | depends: T16, T12
- [T18] M5 station detail panel (spec 05 §3.4) | owner: teodora | branch: teodora/frontend-spec05 | depends: T17

## To do
- [T4] M3 station importer: load data/seed/stations_open.csv (or the local merged file), dedupe, counties, context, CP expansion, coverage report (spec 03 §1–2) | owner: - | branch: - | depends: T2
- [T5] M3 DB schema + migrations, state model + transition tests, power_profiles.yaml, stations.store API, autonomy setting (spec 03 §3–7) | owner: - | branch: - | depends: T2
- [T7] M1 donor mapping onto RO charge points → sessions.parquet, plus RO context.parquet (spec 01 §4, §6) | owner: - | branch: - | depends: T4, T6
- [T8] M1 mocked signal generator → signals.parquet, config/signals.yaml (spec 01 §5) | owner: - | branch: - | depends: T7
- [T9] M1 live-status collector: adapter interface, AmpWhere (human-enabled) + file adapters, live_status table writes; start ASAP and keep running (spec 01 §8) | owner: - | branch: - | depends: T5
- [T10] M4 sim engine core on fixtures: slots, state transitions, sibling handover, wait + energy accounting, metrics, always_on + naive baselines; ChargePointEnv gym wrapper ASAP for M2 (spec 04 §1–4) | owner: - | branch: - | depends: T2
- [T11] M4 evaluator → artifacts/eval_results.json (spec 04 §5) | owner: - | branch: - | depends: T10, T13
- [T12] M4 FastAPI: fixture mode first, then REST + WS replay runner, recommendations, autonomy, live mode (spec 04 §6) | owner: - | branch: - | depends: T5, T10
- [T13] M2 demand forecaster (LightGBM, calibrated) + RandomPolicy/ConstantForecaster stubs on day one + cost_rule policy (spec 02 §1–3) | owner: - | branch: - | depends: T8
- [T14] M2 PPO agent training on ChargePointEnv, λ sweep if time allows, artifacts/ppo_policy.zip (spec 02 §4) | owner: - | branch: - | depends: T10, T13
- [T15] M2 live-mode signal sampling + live validation report (spec 02 §5, §2) | owner: - | branch: - | depends: T9, T13

## Blocked

## Done
- [T3] Collect the full PPC Blue station list from open online sources (no PPC feed, no Geolocet); output a merged station file for the M3 importer | owner: tudor-claude | branch: tudor-claude/T3-station-list | depends: -
  - 155 PPC locations found online (94 high / 5 medium / 56 low, 34 counties); 116 open-licence rows committed to data/seed/stations_open.csv, full merge local-only; spec 03 §1 + decision 0014. Test: `wc -l data/seed/stations_open.csv`.
- [T2] Repo scaffold: folders from spec 00 §1, pyproject (uv, ruff, pytest), docker-compose postgres+postgis, Makefile targets, .gitignore (data/raw, data/interim, data/processed, .env), .env.example, empty config/*.yaml with the keys from CONTRACTS | owner: tudor-claude | branch: tudor-claude/T2-scaffold | depends: T1
  - Folders/packages per spec 00 §1, pyproject+uv.lock, docker-compose postgis, Makefile (entry points + migration runner), .gitignore, .env.example, config/*.yaml with spec placeholders; decision 0013. Test: `make db && make test && make lint`. Note: `background_ev`/`ice_base` in config/signals.yaml are null until T8.
- [T1] Write PRD, spec 0 (foundation) and module specs 01-05; seed tasks/contracts/decisions | owner: tudor-claude | branch: tudor-claude/T1-specs | depends: -
  - Added docs/PRD.md, docs/specs/00-05, decisions 0001-0012, contracts v0, updated AGENTS.md, seeded T2-T18. Test: read docs/PRD.md, then pick a task.
