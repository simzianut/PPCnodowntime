# Spec 00: Foundation

Repo layout, stack, conventions and contribution rules every module follows. `AGENTS.md` holds the short, mandatory version of the rules. This spec holds the details.

## 1. Repo layout

```
PPCnodowntime/
├── AGENTS.md  CLAUDE.md  GEMINI.md  TASKS.md  README.md
├── docs/
│   ├── PRD.md
│   ├── CONTRACTS.md        # every interface between modules
│   ├── DECISIONS.md        # index of decision files
│   ├── decisions/          # one file per decision: NNNN-slug.md
│   └── specs/              # 00-foundation … 05-frontend
├── config/                 # shared YAML, read by several modules
│   ├── power_profiles.yaml # power draw + wake times per power class (M3 owns)
│   ├── signals.yaml        # mocked-signal parameters (M1 owns)
│   └── sim.yaml            # slot size, λ grid, naive-baseline params (M4 owns)
├── tests/                  # cross-module tests; module tests live in <package>/tests/
├── dataset/                # M1: Python package
├── model/                  # M2: Python package (forecaster, policies, RL training)
├── stations/               # M3: Python package (importers, DB models, state store)
├── sim/                    # M4: Python package (engine, Gymnasium env, baselines)
├── api/                    # M4: FastAPI app
├── frontend/               # M5: React + Vite app
├── fixtures/               # small sample files for every contract (committed)
├── artifacts/              # trained models < 20 MB (committed)
├── data/
│   ├── raw/                # downloads + scraped data (gitignored)
│   ├── interim/            # intermediate files (gitignored)
│   ├── processed/          # pipeline outputs (gitignored)
│   └── seed/               # small, licence-clean seed data (committed)
├── db/migrations/          # SQL migrations (M3 owns)
├── docker-compose.yml      # postgres+postgis
├── pyproject.toml          # one Python project, packages above (uv.lock committed)
├── .python-version         # 3.12
├── Makefile                # the commands in §4
└── .env.example
```

Module ownership:

| Module | Folders |
|---|---|
| M1 | `dataset/`, `config/signals.yaml` |
| M2 | `model/`, `artifacts/` |
| M3 | `stations/`, `db/migrations/`, `config/power_profiles.yaml`, `data/seed/stations*` |
| M4 | `sim/`, `api/`, `config/sim.yaml` |
| M5 | `frontend/` |

`fixtures/` is shared. Every module adds fixtures for the contracts it produces.

## 2. Stack

| Layer | Choice |
|---|---|
| Python | 3.12, dependencies via `uv`, lint and format with `ruff`, tests with `pytest` |
| Data | pandas, pyarrow (Parquet), geopandas/shapely where needed |
| ML | LightGBM (forecaster), Gymnasium + Stable-Baselines3 PPO (agent) |
| DB | Postgres 16 + PostGIS 3 via `docker-compose`, accessed with SQLAlchemy 2 + psycopg 3 |
| API | FastAPI + uvicorn, Pydantic v2 models, WebSocket for replay |
| Frontend | React 18 + TypeScript + Vite, MapLibre GL JS with OpenFreeMap tiles, Recharts |

See [decision 0008](../decisions/0008-stack.md).

## 3. Shared conventions

- **Time.** Store timestamps as UTC (`timestamptz` / timezone-aware pandas). The simulation clock and every human-facing display use `Europe/Bucharest`. Donor sessions are mapped by **local clock time**: an 08:10 arrival in Dundee becomes an 08:10 arrival in Romania.
- **Slots.** 5 minutes. A slot is identified by its start time. Events inside a slot keep second precision.
- **IDs.**
  - `location_id`: `L` followed by 5 digits.
  - `cp_id`: `<location_id>-<n>`.
  - Runs: UUIDs.
  - IDs never change once they appear in the DB or in fixtures.
- **States.** `CHARGING | IDLE | WAKING | STANDBY | DEEP_SLEEP | OFFLINE`, exactly as written in `docs/CONTRACTS.md`.
- **Units.** Power in W, energy in kWh, durations in seconds inside the code. Minutes appear only in UI and metric names (`wait_min`).
- **Config.** Values that are tuned, or that are numbers we are guessing, go in `config/*.yaml`, never hardcoded. Secrets go only in `.env`, which is never committed. `.env.example` lists every variable.
- **Code comments.** Short, at most 3 lines. Explanations go in the module spec or a decision file.
- **Data licences.** Third-party data with restrictive or unclear terms (Geolocet, AmpWhere scrapes, Dundee raw files) stays under `data/raw/` and is never committed. Only OSM-derived data, public statistics and data we generate may go in `data/seed/` or `fixtures/`.
- **Web access.** No scraping of sites whose `robots.txt` disallows the agent doing it. No bypassing bot protection. AmpWhere collection is run by the team, as recorded in [0003](../decisions/0003-live-status-collection.md).

## 4. Running locally

```
cp .env.example .env
make db            # docker-compose up postgres+postgis, apply migrations
make seed          # M3: load station seed into DB
make data          # M1: build processed datasets (needs data/raw/)
make train         # M2: train forecaster + PPO, write artifacts/
make api           # M4: uvicorn api.main:app on :8000
make web           # M5: vite dev server on :5173
make test          # pytest + frontend tests
make lint          # ruff check + format check
```

Each target calls the module's entry point (`python -m <package>`) and prints "not implemented yet" until it exists. `make train` installs the `rl` extra (Stable-Baselines3). Migrations are plain SQL, applied once each by `make db`. See `docs/CONTRACTS.md` and [decision 0013](../decisions/0013-repo-scaffold.md).

Ports and environment variables are defined in `docs/CONTRACTS.md`.

## 5. Working in parallel under 24h

- **Fixtures first.** Before upstream code exists, each producing module commits a small fixture for its contract to `fixtures/`:
  - sessions and signals: 2 days × 5 locations
  - stations: 20 locations
  - a WebSocket tick
  - KPI results

  Consumers build against these fixtures, so nobody waits for anybody.
- **Stubs.** `model/` ships a `RandomPolicy` and a `ConstantForecaster` on day one, so M4 can run the engine end to end. `api/` serves fixture-backed endpoints before the engine is wired in, so M5 can integrate early.
- **Contracts on main.** Any change to a payload, table, file schema, config key, route, port or environment variable goes into `docs/CONTRACTS.md` on `main` first, as its own small commit. Nobody guesses an interface that isn't written down.
- **Must-have before stretch.** Every module spec lists both tiers. Don't start a stretch item while a must-have item in your module is open.

## 6. Decision logging (mandatory)

**Every agent documents every decision.** A decision is anything a teammate could later ask "why?" about:
- choosing between two or more reasonable options (a library, an algorithm, a parameter value, a data source)
- an assumption made because information was missing
- any departure from a spec or contract
- a placeholder value that will need replacing later
- a shortcut taken for time, and what it costs

How:
1. Create `docs/decisions/NNNN-short-slug.md` from the template in `docs/decisions/README.md`. `NNNN` = highest existing number + 1. If two agents collide on a number, the later one renumbers on rebase.
2. Add one line to `docs/DECISIONS.md`: `- NNNN | YYYY-MM-DD | who | title`.
3. Commit the decision together with the code it explains, or before it. Decisions that change a contract or spec go to `main` straight away.
4. Never edit a decision after it is merged. To change it, write a new decision that says `Supersedes: NNNN`, then add `Superseded by: MMMM` to the old file. That added line is the only edit allowed.
5. If the decision changes a spec, update that spec in the same commit and link the decision from it.

Before you finish a task, check: *"Did I make a choice that isn't written down?"* If yes, write the decision file before moving the task to Done.

## 7. Specs stay true

The specs describe what is actually built. If you implement something differently from the spec, update the spec section and add a decision in the same change. Agents read specs first, and a stale spec misleads them.

## 8. Definition of done (any task)
- The code runs through its `make` target, and the tests for the new logic pass.
- Fixtures and contracts are updated if your outputs changed.
- Decisions are logged (§6).
- The task is moved to Done in `TASKS.md` with a one-line note: what changed and how to test it.
