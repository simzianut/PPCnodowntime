# Decisions

Index of decisions. Since 2026-11-07, each decision is one file in [`decisions/`](decisions/) (see [0007](decisions/0007-one-file-per-decision.md) and spec 00 §6).
Append-only. Newest at the bottom. Format: `- NNNN | YYYY-MM-DD | who | title`.

## Earlier entries (before 0007)

- 2026-11-07 | vlad | Coordinate all agents through Git (`AGENTS.md`, `TASKS.md`, `docs/`) | why: works for every agent (Claude Code, Antigravity) on every laptop, with no extra infrastructure

## Decision files
- [0001](decisions/0001-rl-agent-with-forecaster.md) | 2026-11-07 | tudor | RL agent (PPO) driven by a demand forecaster, with a cost-rule fallback
- [0002](decisions/0002-dundee-donor-sessions.md) | 2026-11-07 | tudor | Usage history from Dundee sessions mapped onto Romanian charge points
- [0003](decisions/0003-live-status-collection.md) | 2026-11-07 | tudor | Collect live statuses from AmpWhere; no PPC Blue bot-wall bypass
- [0004](decisions/0004-station-list-layered-sources.md) | 2026-11-07 | tudor | Station list from layered sources, real stations only
- [0005](decisions/0005-charge-point-states-and-power.md) | 2026-11-07 | tudor | Agent acts per charge point; 6 states with placeholder power numbers
- [0006](decisions/0006-metrics-and-no-lost-sessions.md) | 2026-11-07 | tudor | Headline metrics are energy saved and added wait; drivers are never lost
- [0007](decisions/0007-one-file-per-decision.md) | 2026-11-07 | tudor | One file per decision; DECISIONS.md becomes an index
- [0008](decisions/0008-stack.md) | 2026-11-07 | tudor | Stack: Python, Postgres + PostGIS, FastAPI, React + MapLibre
- [0009](decisions/0009-global-autonomy-switch.md) | 2026-11-07 | tudor | One global autonomy switch: ACT or RECOMMEND
- [0010](decisions/0010-weather-donor-train-ro-run.md) | 2026-11-07 | tudor | Train on donor weather and holidays, run on Romanian ones
- [0011](decisions/0011-shared-slot-engine.md) | 2026-11-07 | tudor | One 5-minute-slot engine for RL training, replay and live mode
- [0012](decisions/0012-mock-signals.md) | 2026-11-07 | tudor | Mocked signals from ground truth in replay, from the forecast in live mode
- [0013](decisions/0013-repo-scaffold.md) | 2026-11-07 | tudor-claude | Repo scaffold: packaging, entry points, tests, migrations
- [0014](decisions/0014-station-list-from-open-sources.md) | 2026-11-07 | tudor-claude | Station list from free online sources only; commit the openly licensed subset (supersedes 0004)
- [0015](decisions/0015-synthetic-m1-fixtures.md) | 2026-11-07 | vlad-claude | M1 fixtures are synthetic, on placeholder locations L00001–L00005
- [0016](decisions/0016-live-collector-design.md) | 2026-11-07 | vlad-claude | Live collector writes JSONL first, DB when T5 lands; AmpWhere via snapshot + delta endpoints
- [0017](decisions/0017-shared-sim-types.md) | 2026-11-07 | tudor-claude | Shared simulation types live in sim/types.py; M4 owns the RL observation builder
- [0018](decisions/0018-engine-transition-rules.md) | 2026-11-07 | tudor-claude | Engine transition rules that fill the gaps in spec 03 §4
- [0019](decisions/0019-arrival-handling-and-wait.md) | 2026-11-07 | tudor-claude | Arrival handling: compatibility fallback, waking a sleeping sibling, queueing not counted
- [0020](decisions/0020-lambda-unit-and-reward.md) | 2026-11-07 | tudor-claude | λ is kWh per minute of added wait; the reward is normalised by always-on overhead
- [0021](decisions/0021-precomputed-forecasts.md) | 2026-11-07 | tudor-claude | Training and evaluation use a precomputed forecast table; SimState.signals holds the last 60 minutes
- [0022](decisions/0022-naive-baseline-window.md) | 2026-11-07 | tudor-claude | Naive baseline sleeps one contiguous window per weekday
- [0023](decisions/0023-gym-env-defaults.md) | 2026-11-07 | tudor-claude | Gym env: naive siblings until cost_rule exists, optional days list; min_awake is a policy rule
- [0024](decisions/0024-synthetic-sim-data.md) | 2026-11-07 | tudor-claude | Seeded synthetic data in sim/ until M1 and M3 deliver; provisional location IDs in fixtures
- [0025](decisions/0025-recommend-in-replay.md) | 2026-11-07 | tudor-claude | In replay, RECOMMEND applies to the focus policy only, and the run pauses when new recommendations appear
- [0026](decisions/0026-engine-run-defaults.md) | 2026-11-07 | tudor-claude | Engine run defaults: always_on runs in every run, day boundaries, sleep_share, speed target
- [0027](decisions/0027-dundee-cleaning-and-lanes.md) | 2026-11-07 | vlad + vlad-claude | Dundee cleaning rules, 2-lane split of chargers, donor utilisation
- [0028](decisions/0028-real-traffic-tomtom-mapbox.md) | 2026-11-07 | vlad + vlad-claude | Real traffic index: TomTom live, Mapbox typical travel times for training/replay; Google not used
- [0029](decisions/0029-ampwhere-ppc-station-list.md) | 2026-11-07 | vlad + vlad-claude | AmpWhere snapshot is the main PPC Blue station list (local only)
- [0030](decisions/0030-county-ev-density-bucharest-ilfov-pool.md) | 2026-11-07 | vlad + vlad-claude | County EV density from DRPCIV end-2025 + INS 2026; Bucharest and Ilfov share one pooled rate
- [0031](decisions/0031-cost-rule-implementation-and-m2-stubs.md) | 2026-11-07 | edy-antigravity | M2 day-one stubs (ConstantForecaster, RandomPolicy), contract type fallback, and cost_rule implementation
- [0032](decisions/0032-demand-forecaster-lightgbm-and-calibration.md) | 2026-11-07 | edy-antigravity | LightGBM EV demand forecaster, PAVA isotonic calibration, and precomputed forecasts
- [0033](decisions/0033-station-import-units-classes-context.md) | 2026-11-07 | vlad + vlad-claude | Station import: one CP per physical unit, HPC at 100 kW+, context rules, other operators as context layer
- [0034](decisions/0034-engine-implementation-details.md) | 2026-11-07 | tudor-claude | Engine implementation details: event order, command handling, recommendations, midnight
- [0035](decisions/0035-synthetic-generator-parameters.md) | 2026-11-07 | tudor-claude | Parameters of the synthetic simulation data
- [0036](decisions/0036-api-fixture-mode.md) | 2026-11-07 | tudor-claude | API fixture mode plays a heuristic recorded day
- [0037](decisions/0037-gym-env-implementation.md) | 2026-11-07 | tudor-claude | Gym env implementation: location-day engine, cached slices, cost_rule auto-detect
- [0038](decisions/0038-donor-mapping-implementation.md) | 2026-11-07 | vlad + vlad-claude | Donor mapping details: holiday swaps, top-ups, de-cloning, active spans (inactive days = unknown)
- [0039](decisions/0039-compact-signals-and-placeholders.md) | 2026-11-07 | vlad + vlad-claude | Compact signals.parquet (events + hourly maps + non-zero EV counts); placeholder signal params
- [0040](decisions/0040-station-run-summary-contract.md) | 2026-11-07 | teodora | Run-scoped station summary with nullable live/model metrics
- [0041](decisions/0041-frontend-seed-demo.md) | 2026-11-07 | teodora | Seed-data demo adapter for frontend work before the API exists
- [0042](decisions/0042-frontend-map-first-white-theme.md) | 2026-11-07 | teodora | Map-first split, white-only theme, station summary presentation and status markers
- [0043](decisions/0043-traffic-build-details.md) | 2026-11-07 | vlad + vlad-claude | Traffic build: Mapbox 2-hourly + interpolation, free-flow baseline, TomTom quality filter
- [0044](decisions/0044-ppo-agent-training-and-architecture.md) | 2026-11-07 | edy-antigravity | PPO decision agent architecture, vectorized action masking, and artifact generation
- [0045](decisions/0045-svg-base-map.md) | 2026-11-07 | tudor + tudor-claude | SVG base map of Romania (Natural Earth, Web Mercator) with a swappable station icon and a layer for hand-drawn lines
- [0046](decisions/0046-interactive-svg-map.md) | 2026-11-07 | tudor + tudor-claude | Map view is an interactive inline SVG (pan, zoom, clusters, spiderfy) replacing MapLibre
- [0047](decisions/0047-engine-backed-api-and-station-loader.md) | 2026-11-07 | tudor + tudor-claude | One station loader (real list when local, else open seed); API real mode runs replays on sim.Engine in memory; frontend tolerates missing eval
- [0048](decisions/0048-stations-db-store-and-engineering-power-profiles.md) | 2026-11-07 | vlad + vlad-antigravity | Stations DB schema, store API, and engineering-grounded power profiles (supersedes 0005 power numbers)
- [0049](decisions/0049-t5-data-in-sim-ron-kpis-run-history.md) | 2026-11-07 | tudor + tudor-claude | Sim uses T5 per-capacity watts and the net B2B tariff (RON KPIs); API keeps autonomy and focus-policy run history in the DB via stations.store
- [0050](decisions/0050-retrain-models-on-romanian-dataset.md) | 2026-11-07 | edy-antigravity | Retrain demand forecaster and PPO agent on real Romanian dataset with memory-safe signal streaming
- [0051](decisions/0051-docker-gpu-reproducible-training.md) | 2026-11-07 | vlad + vlad-claude | Training runs in Docker on a chosen GPU (GPU_ID), reproducible by default, written to runs/ and promoted explicitly
- [0052](decisions/0052-forecaster-comparison-and-ablation.md) | 2026-11-07 | vlad + vlad-claude | Compare F0 seasonal, F1 LightGBM, F2 GRU-survival forecasters; feature ablation and signal sensitivity suite
- [0053](decisions/0053-rl-maskableppo-ddqn-oracle.md) | 2026-11-07 | vlad + vlad-claude | RL agents MaskablePPO and Double DQN with masking, plus a hindsight oracle bound
- [0054](decisions/0054-tikz-research-figures.md) | 2026-11-07 | vlad + vlad-claude | Research-paper figures in TikZ, built in Docker to PDF and SVG
- [0055](decisions/0055-evaluator-multi-policy-runner.md) | 2026-11-07 | edy-antigravity | Evaluator multi-policy engine runner and eval_results artifact
- [0056](decisions/0056-ampwhere-calibration-check.md) | 2026-11-07 | vlad + vlad-claude | Calibration vs real PPC activity (3 h, Sat evening): HPC ~2x overstated, all within range; report, no rescale yet
- [0057](decisions/0057-live-mode-signal-sampling-and-validation.md) | 2026-11-07 | edy-antigravity | Live-mode signal sampling and live validation report
- [0058](decisions/0058-training-infra-implementation.md) | 2026-11-07 | vlad + vlad-claude | Training infra details: pins (CUDA 12.4.1, torch 2.6.0, uv 0.11.26, sb3 2.8.0), venv at /opt/venv, GPU-free verify stage, strict deterministic mode, trainer registry
- [0059](decisions/0059-forecaster-training-implementation.md) | 2026-11-07 | vlad + vlad-claude | Forecaster training implementation: shared seeded CP-day sample, vectorised feature builder + cache, monotone horizons, F0/F1/F2 settings
- [0060](decisions/0060-rl-training-implementation.md) | 2026-11-07 | vlad + vlad-claude | RL training implementation: active-only episodes, DC oversampling, fixed validation episodes with in-engine references, masked Double DQN, own-CP oracle, hyperparameters
- [0061](decisions/0061-rl-agents-in-the-app.md) | 2026-11-07 | vlad + vlad-claude | New RL agents reach the app as PolicyNames rl_maskppo / rl_ddqn / oracle (replay only); RL_AGENT picks the agent behind rl_ppo
- [0062](decisions/0062-gpu-server-forecaster-package.md) | 2026-11-07 | vlad + vlad-claude | GPU-server package for forecaster training: tar with code + data + GPU_ID .env, writable data/interim mount, staged train_forecasters.sh
- [0063](decisions/0063-training-image-server-fixes.md) | 2026-11-08 | vlad + vlad-claude | Server build fixes: no uv bytecode precompile (fd limit), training containers with network_mode none (no free docker subnets), stages need the image
- [0064](decisions/0064-map-live-actions-with-swipe.md) | 2026-11-07 | tudor + tudor-claude | Map is a quiet 4-colour base with live agent actions (ripples, ticker) and a without/with-agent swipe slider; replaces state rings and energy blobs
- [0065](decisions/0065-sidebar-two-policies-separate-wait.md) | 2026-11-08 | tudor + tudor-claude | Sidebar shows only Always on and RL agent, map fixed to rl_ppo, trade-off curve replaced by a night/day before/after energy bar chart (chart changed by 0068) and average wait per charge as a number, redesigned replay bar
- [0066](decisions/0066-docker-compose-deploy-behind-traefik.md) | 2026-11-08 | vlad + vlad-claude | Whole stack in Docker Compose (ppc-db/migrate/seed/api/web, collector under profile live); one nginx entry point on Traefik's shared network via docker-compose.server.yml; CPU torch in the API image
- [0067](decisions/0067-cluster-energy-saved-blobs.md) | 2026-11-07 | tudor + tudor-claude | Map clusters are colour blobs showing average energy saved (red → green), replacing numbered bubbles
- [0068](decisions/0068-deslop-control-room-style.md) | 2026-11-08 | tudor + tudor-claude | Frontend restyled as a plain "control room" (Inter, greys + one blue, hairlines, KPI table); energy chart is two total idle-energy bars; supersedes the night/day chart of 0065 and the T16/T17 style
- [0069](decisions/0069-f2-gru-is-the-agent.md) | 2026-11-08 | vlad + vlad-claude | RL dropped; the agent is F2 GRU + cost rule, served as rl_ppo (RL_AGENT=gru); 60-day F2 forecast window for replay/eval
- [0070](decisions/0070-live-mode-shadow-agent.md) | 2026-11-08 | vlad + vlad-claude | Live mode runs the F2 agent in shadow on real AmpWhere statuses; live signals sampled from F0 rates
- [0071](decisions/0071-gru-agent-implementation.md) | 2026-11-08 | vlad + vlad-claude | F2 agent implementation: promoted f2_gru.pt + f0_rates, UTC forecast window (start 2025-02-28), signals follow forecasts, covered replay days, online GRUForecaster
- [0072](decisions/0072-live-mode-implementation.md) | 2026-11-08 | vlad + vlad-claude | Live mode details: JSONL source + connector roll-up, events from status transitions, last complete slot + 60 s lag, LiveEngine reuses Engine accounting, F2 live with inferred arrivals, per-slot intent rate, demo clock env
- [0073](decisions/0073-remove-lightgbm.md) | 2026-11-08 | vlad + vlad-claude | LightGBM removed: dependency, F1 trainer, legacy DemandForecaster/train pipeline, artifacts and fallbacks; F2 GRU only
- [0074](decisions/0074-bigger-legend-trim-station-detail.md) | 2026-11-08 | tudor + tudor-claude | Bigger map key with plain-language hints and a group colour bar; station detail drops current output, expected wait and anomaly score
- [0075](decisions/0075-demo-mode-day-simulation.md) | 2026-11-08 | tudor + tudor-claude | Demo mode runs a deterministic one-day simulation with a simple forecast-rule agent, so the map, ticker and KPIs move consistently without a backend
- [0076](decisions/0076-stagger-actions-within-slot.md) | 2026-11-08 | tudor + tudor-claude | Frontend staggers each tick's CP changes across the slot (stable per-CP offsets) so agent actions appear live with their own minute; cosmetic timing
- [0077](decisions/0077-docker-is-the-standard.md) | 2026-11-08 | vlad + vlad-claude | Docker is the standard way to run, demo and check the app (http://localhost:8080); native make api/web only for debugging
- [0078](decisions/0078-replay-clock-ticks-per-minute.md) | 2026-11-08 | tudor + tudor-claude | Replay clock ticks minute by minute in step with the staggered agent actions (supersedes part of 0076)
