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
