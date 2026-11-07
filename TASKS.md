# Task board

Rules: see `AGENTS.md`. Claim = move the task to **In progress** with your name and branch, then commit and push to `main` *before* coding.
Task IDs only go up. The new ID is the highest existing ID + 1.

Format:
`- [T<id>] <title> | owner: <person>-<agent> | branch: <branch> | depends: T<id>`
Add notes as indented sub-bullets.

## In progress
- [T16] M5 frontend scaffold + Romania map with CP states, against fixtures/MSW (spec 05 §3.1) | owner: teodora | branch: teodora/frontend-spec05 | depends: T2
- [T17] M5 KPI panel + energy-vs-wait curve + replay controls + autonomy switch + recommendations feed (spec 05 §3.2–3.3) | owner: teodora | branch: teodora/frontend-spec05 | depends: T16, T12
- [T18] M5 station detail panel (spec 05 §3.4) | owner: teodora | branch: teodora/frontend-spec05 | depends: T17
- [T9] M1 live-status collector: adapter interface, AmpWhere (human-enabled) + file adapters, live_status table writes; start ASAP and keep running (spec 01 §8) | owner: vlad-claude | branch: vlad-claude/T9-live-collector | depends: T5
  - Starts before T5: writes JSONL under data/raw/live/ now, DB table once T5 lands. A human (vlad) runs the AmpWhere poller, per decision 0003.
- [T20] M1 real traffic: TomTom live traffic index + Mapbox typical hour-of-week traffic per location → traffic_typical.parquet (decision 0028) | owner: vlad-claude | branch: vlad-claude/T20-traffic | depends: T4
  - Needs TOMTOM_API_KEY and MAPBOX_TOKEN in a local .env (vlad). Can start on stations_open.csv / fixture locations before T4.

- [T7] M1 donor mapping onto RO charge points → sessions.parquet, plus RO context.parquet (spec 01 §4, §6) | owner: vlad-claude | branch: vlad-claude/T7-donor-mapping | depends: T4, T6

- [T8] M1 mocked signal generator → signals.parquet, config/signals.yaml (spec 01 §5) | owner: vlad-claude | branch: vlad-claude/T7-donor-mapping | depends: T7
## To do
- [T5] M3 DB schema + migrations, state model + transition tests, power_profiles.yaml, stations.store API, autonomy setting (spec 03 §3–7) | owner: - | branch: - | depends: T2
  - Request from T9 (vlad-claude): please add nullable `source_location_id text, source_cp_id text, connector text` to `live_status`, so collected rows can be matched to `cp_id` later (decision 0016).
  - Note from T10 (tudor-claude): sim/transitions.py implements decision 0018 (spec 03 §4 gaps). The T5 transition test should check the same rules.
- [T11] M4 evaluator → artifacts/eval_results.json (spec 04 §5) | owner: - | branch: - | depends: T10, T13
- [T12] M4 FastAPI (fixture mode is T19): REST + WS replay runner, recommendations, autonomy, live mode (spec 04 §6) | owner: - | branch: - | depends: T5, T10, T19
- [T14] M2 PPO agent training on ChargePointEnv, λ sweep if time allows, artifacts/ppo_policy.zip (spec 02 §4) | owner: - | branch: - | depends: T10, T13
- [T15] M2 live-mode signal sampling + live validation report (spec 02 §5, §2) | owner: - | branch: - | depends: T9, T13

## Blocked

## Done
- [T19] M4 API fixture mode (split from T12): API_FIXTURES=1 serves every route from fixtures/api/, incl. a playable recorded day over WS (spec 04 §6) | owner: tudor-claude | branch: tudor-claude/T19-api-fixtures | depends: T2
  - Merged in #2. api/: fixture mode for every CONTRACTS route + playable recorded day over WS (fixtures/api, `python -m api.fixtures_gen`); decision 0036. Test: `API_FIXTURES=1 make api`, `uv run pytest api`. T12 regenerates fixtures from the engine.
- [T10] M4 sim engine core on fixtures: slots, state transitions, sibling handover, wait + energy accounting, metrics, always_on + naive baselines; ChargePointEnv gym wrapper ASAP for M2 (spec 04 §1–4) | owner: tudor-claude | branch: tudor-claude/T10-sim-engine | depends: T2
  - Merged in #1. sim/: engine (slots, transitions, handover, waits, energy, RECOMMEND, timelines), metrics, always_on + naive baselines, obs builder, PrecomputedForecaster, ChargePointEnv, seeded synthetic data; decisions 0017–0026, 0034, 0035, 0037. Test: `uv run pytest sim tests`.
- [T4] M3 station importer (spec 03 §1–2) | owner: vlad-claude | branch: vlad-claude/T4-station-import | depends: T2
  - AmpWhere snapshot as primary PPC list (0029): 445 locations, 671 CPs (physical units), 1,364 connectors; HPC >= 100 kW; context rules; 5,563 other-operator chargers as context (0033). Writes data/processed/{locations,charge_points,source_refs,other_chargers}.parquet; DB upsert waits for T5. Test: `uv run python -m stations` then `uv run pytest stations`.
- [T13] M2 demand forecaster (LightGBM, calibrated) + RandomPolicy/ConstantForecaster stubs on day one + cost_rule policy (spec 02 §1–3) | owner: edy-antigravity | branch: edy-antigravity/T13-demand-forecaster | depends: T8
  - Shipped stubs (RandomPolicy, ConstantForecaster), CostRulePolicy, LightGBM demand forecaster with PAVA calibration (y30 AUC 0.9103, Brier 0.0488), precomputed forecasts.parquet, artifacts/forecaster.lgb (578KB) + meta.json; decisions 0031, 0032. Test: uv run python -m model && uv run pytest model.
- [T6] M1 Dundee ingest + Open-Meteo weather at Dundee + GB-SCT holidays + county_ev_stats.csv; commit fixtures early | owner: vlad-claude | branch: vlad-claude/T6-dundee-ingest | depends: T2
  - Fixtures (0015); Dundee download + cleaning + 2-lane donors + weather/holidays → donor_sessions/chargers/occupancy (0027); county_ev_stats.csv with B+IF pooled (0030). Test: `uv run python -m dataset` then `uv run pytest dataset`.
- [T3] Collect the full PPC Blue station list from open online sources (no PPC feed, no Geolocet); output a merged station file for the M3 importer | owner: tudor-claude | branch: tudor-claude/T3-station-list | depends: -
  - 155 PPC locations found online (94 high / 5 medium / 56 low, 34 counties); 116 open-licence rows committed to data/seed/stations_open.csv, full merge local-only; spec 03 §1 + decision 0014. Test: `wc -l data/seed/stations_open.csv`.
- [T2] Repo scaffold: folders from spec 00 §1, pyproject (uv, ruff, pytest), docker-compose postgres+postgis, Makefile targets, .gitignore (data/raw, data/interim, data/processed, .env), .env.example, empty config/*.yaml with the keys from CONTRACTS | owner: tudor-claude | branch: tudor-claude/T2-scaffold | depends: T1
  - Folders/packages per spec 00 §1, pyproject+uv.lock, docker-compose postgis, Makefile (entry points + migration runner), .gitignore, .env.example, config/*.yaml with spec placeholders; decision 0013. Test: `make db && make test && make lint`. Note: `background_ev`/`ice_base` in config/signals.yaml are null until T8.
- [T1] Write PRD, spec 0 (foundation) and module specs 01-05; seed tasks/contracts/decisions | owner: tudor-claude | branch: tudor-claude/T1-specs | depends: -
  - Added docs/PRD.md, docs/specs/00-05, decisions 0001-0012, contracts v0, updated AGENTS.md, seeded T2-T18. Test: read docs/PRD.md, then pick a task.
