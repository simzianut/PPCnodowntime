# Task board

Rules: see `AGENTS.md`. Claim = move the task to **In progress** with your name and branch, then commit and push to `main` *before* coding.
Task IDs only go up. The new ID is the highest existing ID + 1.

Format:
`- [T<id>] <title> | owner: <person>-<agent> | branch: <branch> | depends: T<id>`
Add notes as indented sub-bullets.

## In progress
- [T32] GPU-server package for forecaster training (F0/F1/F2 + ablation): writable data/interim mount in docker-compose.train.yml, docker/train_forecasters.sh (preflight, build+verify, GPU smoke, suites, results pack), .env.train.example, docs/TRAINING_GPU.md | owner: vlad-claude | branch: main (small) | depends: T25, T26
- [T30] M5 map scratchpad: 3–4 simplified map-view variants behind a switcher (`?map=a..d`) so Tudor can pick one; the losers get dropped | owner: tudor-claude | branch: tudor-claude/T30-map-variants | depends: T29
  - Tudor picked D (live actions) + B's swipe slider; PR #7 open (decision 0063). Test: `cd frontend && npm test`, `make api` + `make web`.
- [T29] M5 map: replace numbered cluster bubbles with energy-saved blobs (colour = arithmetic mean of member stations' saved level, red → yellow → green) | owner: tudor-claude | branch: tudor-claude/T29-cluster-blobs | depends: T22
  - Folded into T30 (PR #7); PR #5 closed. Decision renumbered 0055 → 0062.
- [T16] M5 frontend scaffold + Romania map with CP states, against fixtures/MSW (spec 05 §3.1) | owner: teodora | branch: teodora/frontend-spec05 | depends: T2
- [T17] M5 KPI panel + energy-vs-wait curve + replay controls + autonomy switch + recommendations feed (spec 05 §3.2–3.3) | owner: teodora | branch: teodora/frontend-spec05 | depends: T16, T12
  - Note from T23 (tudor-claude): small change in useDashboard.ts and App.tsx so a 404 from GET /api/eval no longer drops the app into demo mode (empty curve, replay starts with date null).
- [T18] M5 station detail panel (spec 05 §3.4) | owner: teodora | branch: teodora/frontend-spec05 | depends: T17



## To do

- [T12] M4 FastAPI (fixture mode is T19): REST + WS replay runner, recommendations, autonomy, live mode (spec 04 §6) | owner: - | branch: - | depends: T5, T10, T19
  - Add `GET /api/runs/{run_id}/locations/{location_id}/summary` per CONTRACTS v0.2 and a fixture response; return null until metered power, wait prediction, and anomaly producers exist.
  - Note from T23 (tudor-claude): T23 delivers the replay REST + WS routes on sim.Engine, in memory (api/engine_mode.py). T12 keeps DB persistence (after T5) and live mode.


## Blocked

## Done
- [T31] Wire the new RL agents into the app: PolicyNames rl_maskppo, rl_ddqn, oracle (replay only) in sim/evaluate.py and the API real mode; RL_AGENT switch picks which agent serves rl_ppo | owner: vlad-claude | branch: vlad-claude/T31-rl-wiring | depends: T27
  - model/rl_select.py (RL_AGENT resolve + loaders), PolicyName rl_maskppo/rl_ddqn/oracle, evaluator + API real mode run them; oracle replay-only (live → 400); decision 0061. API smoke on real data 2024-01-01: oracle 77.4% saved / 0 wait, current rl_ppo (edy's 25k-step PPO) 75.3% / 999.5 wait-min over 1,067 of 1,137 sessions, cost_rule 12.1% / 3.8. Test: `uv run --extra rl --extra train pytest model/tests/test_rl_select.py api/tests/test_engine_routes.py sim/tests/test_evaluate.py`.
  - Note for tudor: api/tests/test_routes.py::test_recommendations_routes fails on main (fixture mode, 29 == 28 applied), unrelated to T31.
  - Note for teodora (T16–T18): add rl_maskppo, rl_ddqn, oracle to frontend/src/api/types.ts PolicyName + POLICY_LABELS to show them.
- [T27] M2 RL agents: MaskablePPO (sb3-contrib) and Double DQN with action masking on ChargePointEnv, hindsight oracle bound policy, env-level evaluation on held-out days for model selection | owner: vlad-claude | branch: vlad-claude/T27-rl-agents | depends: T25
  - model.experiments rl_maskppo / rl_ddqn / oracle; EpisodeEnv (active CP-days, DC ×3), fixed validation episodes with always_on / cost_rule / oracle in the same engine; MaskablePPOPolicy + DDQNPolicy adapters (not yet wired into sim/API); suite rl_sweep; decisions 0053, 0060. Test: `uv run --extra rl --extra train pytest model/tests/test_rl_agents.py`; server: pass `--set data.forecasts=<abs path to T26 run>/forecasts.parquet`.
- [T26] M2 forecaster comparison: F0 seasonal hour-of-week baseline, F1 LightGBM (existing, wrapped for reproducible runs + 2024–25 test split + inactive days excluded), F2 GRU with 12-bucket survival head; shared metrics (AUC, PR-AUC, Brier, log loss, reliability); feature ablation + sensitivity suite (steps 1–4 on v3, step 3 on signals_evtag01, step 4 on signals_papp03) | owner: vlad-claude | branch: vlad-claude/T26-forecasters | depends: T25
  - model.experiments f0_seasonal / f1_lgbm / f2_gru on one seeded active-CP-day sample (train ≤ 2023-09-30, val Q4 2023, test 2024–25), shared eval (AUC, PR-AUC, Brier, log loss, reliability), forecasts.parquet per run; suites forecasters / ablation / ablation_f2; decisions 0052, 0059. Smoke y30 AUC F0 0.86 / F1 0.95 / F2 0.96 (20 CPs, noisy). Test: `uv run --extra rl --extra train pytest model/tests/test_forecasters.py`; server: `python -m model.experiments suite config/train/suites/forecasters.yaml`.
- [T28] Research-paper figures in TikZ → PDF + SVG: system overview, forecaster architectures, RL loop (MaskablePPO + DDQN), reproducible training pipeline, experiment design; docker/tex.Dockerfile + `make figures` | owner: vlad-claude | branch: vlad-claude/T28-figures | depends: -
  - docs/figures/src/fig1–fig5 TikZ + style.tex; built PDF/SVG/PNG committed in docs/figures/build; build.sh (pdflatex + dvisvgm, no Ghostscript), docker/tex.Dockerfile; .gitattributes keeps *.sh LF; decision 0054. Test: `make figures-local` (MiKTeX/TeX Live) or `make figures` (Docker).
- [T25] M2 GPU training infra: docker/train.Dockerfile + docker-compose.train.yml (GPU_ID picks the host GPU), reproducible runs (seeds, deterministic torch/LightGBM, run manifest with git SHA + data hashes + versions), experiment CLI `python -m model.experiments`, runs/<run_id>/ layout, explicit promote to artifacts/ | owner: vlad-claude | branch: vlad-claude/T25-train-infra | depends: T13, T14
  - model.experiments CLI (run/suite/promote), runs/<name>-<sha8>-s<seed>/ with manifest, model/repro.py, docker/train.Dockerfile (CUDA 12.4.1, torch 2.6.0+cu124, GPU-free verify stage), docker-compose.train.yml with GPU_ID; decisions 0051, 0058. Image NOT built yet (laptop RAM): first build on the GPU server. Test: `uv run --extra rl --extra train pytest model/tests/test_experiments.py`; on the server `make train-image-verify`, then `make train-docker CONFIG=config/train/smoke.yaml GPU_ID=0`.
- [T15] M2 live-mode signal sampling + live validation report (spec 02 §5, §2) | owner: edy-antigravity | branch: edy-antigravity/T15-live-signals | depends: T9, T13
  - LiveSignalSampler samples route intents, maps EV and ICE counts with CP charging confirmation; generates model/reports/live_validation_report.json; decision 0056. Test: uv run pytest model/tests/test_live_signals.py.
- [T11] M4 evaluator → artifacts/eval_results.json (spec 04 §5) | owner: edy-antigravity | branch: edy-antigravity/T11-evaluator | depends: T10, T13
  - sim.evaluate runs policies over test days and lambda grid; generates artifacts/eval_results.json for GET /api/eval and the frontend Pareto curve; decision 0055. Test: uv run pytest sim/tests/test_evaluate.py.
- [T24] Integrate T5 station data into the sim: per-CP power from the capacity table, RON KPIs (net B2B tariff), API autonomy + run history through stations.store | owner: tudor-claude | branch: tudor-claude/T24-t5-into-sim | depends: T5, T23
  - Per-CP watts from the T5 capacity table (engine, cost_rule, gym reward), overhead_ron/saved_ron at 1.15 RON/kWh in API + KPI cards, autonomy + focus-policy run history via stations.store (needs `make db`); fixed store.get_autonomy on Postgres; tests trimmed to basics; decision 0049. Test: `uv run pytest`, `make api` + `make web`.
- [T5] M3 DB schema + migrations, state model + transition tests, power_profiles.yaml, stations.store API, autonomy setting (spec 03 §3–7) | owner: vlad-antigravity | branch: vlad-antigravity/T5-stations-store | depends: T2
  - Request from T9 (vlad-claude): please add nullable `source_location_id text, source_cp_id text, connector text` to `live_status`, so collected rows can be matched to `cp_id` later (decision 0016).
  - Note from T10 (tudor-claude): sim/transitions.py implements decision 0018 (spec 03 §4 gaps). The T5 transition test should check the same rules.
  - Merged as PR #4 (by tudor-claude on request): db/migrations/0001_stations_schema.sql, stations.store API, state rules, engineering power profiles, coverage report; decision renumbered 0047 → 0048 (0047 was taken by T23). Test: `make db && uv run pytest stations`.
- [T23] Link stations → sim → API → frontend: shared station loader (real 445-location list when present locally), engine-backed API replay mode in memory, frontend tolerates missing eval | owner: tudor-claude | branch: tudor-claude/T23-link-modules | depends: T4, T10, T13, T14, T19
  - sim.stations_seed.load_stations() feeds sim + API (real 445-location list from data/processed when present, else open seed); API_FIXTURES=0 now runs replays on sim.Engine in memory (api/engine_mode.py); frontend tolerates missing eval and requests only available policies; decision 0047. Test: copy ppc_locations.csv + ppc_charge_points.csv into data/processed/, `make api` + `make web`, open :5173; `uv run pytest api sim`. cost_rule/rl_ppo need libomp (macOS) + `--extra rl`.
- [T22] M5 map view on the T21 SVG map: replace MapLibre with an interactive SVG map (pan, wheel/pinch zoom, clusters, selectable stations) using the app's locations | owner: tudor-claude | branch: tudor-claude/T22-svg-map | depends: T21, T16
  - NetworkMap.tsx now inlines frontend/public/map/romania.svg: drag/wheel/pinch/keyboard zoom, PPC-style clusters with asleep badge, spiderfy, selection; maplibre-gl removed; decision 0046. Same props, App.tsx untouched (Teodora: T16 map replaced). Test: `cd frontend && npm test && npm run dev`, open :5173.
- [T21] M5 map view assets: SVG base map of Romania in PPC Blue map style, swappable station marker symbol, layer for hand-drawn lines | owner: tudor-claude | branch: tudor-claude/T21-map-assets | depends: -
  - frontend/public/map/romania.svg from Natural Earth (Web Mercator), styled after the live PPC map; swap `#station-icon`, draw in `#lines`; decision 0045. Rebuild: put Natural Earth geojson in data/raw/naturalearth/, then `.venv/bin/python frontend/tools/build_map_svg.py`.
- [T14] M2 PPO agent training on ChargePointEnv, λ sweep if time allows, artifacts/ppo_policy.zip (spec 02 §4) | owner: edy-antigravity | branch: edy-antigravity/T14-ppo-agent | depends: T10, T13
  - Shipped RLPolicy, vectorized action masking, sub-15ms 1500-CP decide(), artifacts/ppo_policy.zip (173KB) + meta.json, and train_rl.py; decision 0044. Test: uv run pytest model/tests/test_rl_agent.py.
- [T20] M1 real traffic: Mapbox typical + TomTom live | owner: vlad-claude | branch: vlad-claude/T20-traffic | depends: T4
  - traffic_typical.parquet for all 445 locations from Mapbox (10,875 requests); TomTom live_index with quality filter (0028, 0043). Test: `uv run python -m dataset.traffic live --n 3`, `uv run pytest dataset/tests/test_traffic.py`.
- [T9] M1 live-status collector | owner: vlad-claude | branch: vlad-claude/T9-live-collector | depends: T5
  - AmpWhere snapshot + delta collector running since 17:15Z with 0 failures (0016); JSONL in data/raw/live/, latest snapshot feeds T4 (0029). A human starts it: C:\fac\smarthack\run_ampwhere_collector.cmd. DB writes wait for T5.
- [T8] M1 signal generator → signals.parquet | owner: vlad-claude | branch: vlad-claude/T7-donor-mapping | depends: T7
  - 43.9M rows / 218 MB in ~30 s: intents as events, traffic + ICE hourly, EV counts non-zero only; intent share 0.600 (0039). Rerun after traffic: `uv run python -m dataset.signals`. Data shared privately as zips (not in git).
- [T7] M1 donor mapping → sessions.parquet + context.parquet | owner: vlad-claude | branch: vlad-claude/T7-donor-mapping | depends: T4, T6
  - 981,486 sessions on 671 CPs, de-cloned, cp_active_days marks donor-less days (exclude them!); RO weather per county (0038). Test: `uv run python -m dataset.mapping` / `dataset.context`, `uv run pytest dataset`.
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
