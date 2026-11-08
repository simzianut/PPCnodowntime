# PPCnodowntime

Energy-saving sleep scheduling for PPC Blue charge points. An agent decides every 5 minutes which idle chargers to put to sleep and which to wake up. The dashboard compares the network with and without the agent on real AmpWhere charger statuses.

What and why: `docs/PRD.md`. How the team works: `AGENTS.md`. Interfaces: `docs/CONTRACTS.md`.

## Reproduce the demo

You need only this repo, Docker and the AmpWhere archive. Everything else is committed or is built when the stack starts. This was checked from a clean clone on 2026-11-08: the station files it builds are byte-identical to the reference ([docs/data-manifest.md](docs/data-manifest.md), decision 0094).

### Requirements

| What | Version / value | Check |
|---|---|---|
| Docker Desktop (or Docker Engine + Compose v2) | Docker 24+, Compose 2.20+ | `docker --version`, `docker compose version` |
| Memory for Docker | at least **4 GB** (Windows: `memory=4GB` under `[wsl2]` in `%USERPROFILE%\.wslconfig`, then `wsl --shutdown`) | Docker Desktop → Settings → Resources |
| Free disk | about 6 GB (images + data) | |
| Network on first start | yes: Docker images and Python/npm packages | |
| AmpWhere archive | `ampwhere-live-20261107T2232Z-20261108T0925Z.tar.gz` (7.1 MB), held privately by Tudor. It is **not** in the repo (decision 0003). | SHA-256 below |

The archive's SHA-256 must be `6c285de9ad5b39a8d5ea5a09b806951d313a0fc185bb9f0b6b7da8b7b5d2f9cb`. Full manifest: [docs/live-runs/2026-11-07-overnight.md](docs/live-runs/2026-11-07-overnight.md).

### Steps

```bash
git clone https://github.com/simzianut/PPCnodowntime.git
cd PPCnodowntime

# 1. Verify and unpack the AmpWhere statuses into data/raw/live/ (gitignored)
shasum -a 256 ~/Desktop/ampwhere-live-20261107T2232Z-20261108T0925Z.tar.gz   # Linux: sha256sum
mkdir -p data/raw
tar -xzf ~/Desktop/ampwhere-live-20261107T2232Z-20261108T0925Z.tar.gz -C data/raw/
ls data/raw/live        # statuses-20261107.jsonl  statuses-20261108.jsonl  ampwhere_snapshot_latest.json.gz  raw/  imputed/ ...

# 2. Configure: copy the example and set a fixed access key
cp .env.example .env
#    edit .env: PPC_ACCESS_KEY=<any string you choose>   (leave DATA_HOST_DIR=./data)

# 3. Build and start (first build takes several minutes)
docker compose up -d --build
docker compose ps       # ppc-api "healthy", ppc-web "Up"; ppc-migrate and ppc-seed exit 0
```

Open **http://localhost:8080** and enter the `PPC_ACCESS_KEY` from `.env`.

### What you should see

- The replay starts on **Sun 8 Nov 2026**. Its timeline covers the collected window, **00:35 to 11:25** Romanian time (22:35 to 09:25 UTC).
- The clock advances **one minute per tick**. The agent decides at every 5-minute boundary, because the forecaster and policies were trained on 5-minute slots (decision 0091).
- Speeds are **1×, 15×, 60×, 300×** of real time. At 60× one simulated minute takes one second, and the whole window takes about 11 minutes. At 300× it takes about 2 minutes.
- On the left, the network without the agent. On the right, with the agent. The "Agent actions" list shows each sleep or wake command.

### Check that it is the real thing

```bash
docker compose logs ppc-api | grep "collected replay"
```
The line for each run should contain `'source': 'collected'`, `'forecaster': 'f2_gru_live'`, `'signals': 'off'`, `'cps_in_run': 671`, `'start_slot': 7` and `'end_slot': 137`.

- **Statuses:** every charger state change comes from the AmpWhere archive (`data/raw/live/statuses-*.jsonl`). Nothing is generated. The 03:30–04:16 UTC gap (laptop asleep) is replayed as a single step. Imputed rows are never used.
- **Agent:** the forecaster is the trained GRU `artifacts/f2_gru_live.pt`, with the calibration fitted on real AmpWhere arrivals (`artifacts/f2_live_calibration.json`, decision 0084). Its forecasts drive the cost rule (`RL_AGENT=auto`, decision 0069). If the model can't load, the run fails; there is no stub (decision 0093).
- **Signals:** none. PPC Blue route intents and Maps traffic counts have no real feed, so `LIVE_SIGNALS=off` (decision 0093). `LIVE_SIGNALS=mock` turns the old sampled signals back on.
- **Frontend:** it never falls back to fake data. If the API is down, it says "Backend unreachable" (decision 0092). The simulated demo exists only behind `?demo` in the URL or a `VITE_DEMO=1` build.

### How the pieces fit

| Service | Role |
|---|---|
| `ppc-web` | nginx on `:8080`. Serves the React app and proxies `/api` and `/ws` to `ppc-api`. |
| `ppc-api` | FastAPI and the simulation engine. Reads `data/` and `artifacts/` read-only. |
| `ppc-db` | Postgres + PostGIS. |
| `ppc-migrate` | Runs once: database migrations. |
| `ppc-seed` | Runs once on every `up`: `python -m stations` builds `data/processed/{locations,charge_points,source_refs,other_chargers}.parquet` from `data/raw/live/ampwhere_snapshot_latest.json.gz` and `data/seed/`. |
| `ppc-collector` | Profile `live` only: polls AmpWhere every 5 minutes into `data/raw/live/`. |

| Input | In git? | Where it comes from |
|---|---|---|
| `artifacts/` (models, calibration, eval results) | yes | committed; provenance in `artifacts/*_provenance.json` |
| `data/seed/` | yes | committed |
| `data/raw/live/` | **no** | the AmpWhere archive (step 1) or the collector |
| `data/seed/osm/*.json.gz` | yes | OpenStreetMap motorways and towns (© OpenStreetMap contributors, ODbL), used by `ppc-seed` (decision 0094) |
| `data/processed/` stations and `source_refs` | no | built by `ppc-seed` at start |
| `data/processed/` sessions, signals, forecasts | no | optional. Without them the replay day list holds only the collected day, plus synthetic days as a fallback. With them you also get the 60 Dundee-derived test days and `GET /api/eval`. See [Full data](#full-data-optional). |

## Live mode

Live mode runs the same agent on statuses that are coming in now. An AI agent must not start the AmpWhere collector; a person does (decision 0003, `docs/LIVE_DATA.md` §1).

```bash
echo "AMPWHERE_ENABLED=1" >> .env
echo "COMPOSE_PROFILES=live" >> .env
docker compose up -d ppc-collector
tail -f data/raw/live/collector.log      # one line per poll, every 5 minutes
```

After the first poll, click **Switch to live** in the dashboard or open http://localhost:8080/?mode=live. A live run is refused with "live feed is stale … is ppc-collector running?" when the newest status is more than 30 minutes old (decision 0080). The collector appends to the same `data/raw/live/` folder, so today's statuses also become a replayable day.

Stop the collector with `docker compose stop ppc-collector`.

## Everyday commands

```bash
docker compose up -d --build        # after changing code; then reload the page
docker compose logs -f ppc-api      # API logs
docker compose down                 # stop everything (data/ stays)
```

- An API restart drops in-memory runs. Reload the page to start a new one.
- With `PPC_ACCESS_KEY` empty, the API generates a key at every start: `docker compose logs ppc-api | grep "access key"`. `PPC_AUTH=off` turns the key off locally.
- After changing `.env`, recreate the API: `docker compose up -d ppc-api`.
- CPU and memory caps (`API_CPUS`, `API_MEM_LIMIT`, …) are in `.env.example`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| The day picker has no 8 Nov 2026 | `data/raw/live/statuses-*.jsonl` is missing. Redo step 1, then `docker compose restart ppc-api`. |
| `POST /api/runs` 400 on 8 Nov | `data/processed/source_refs.parquet` is missing. `docker compose logs ppc-seed` shows why; rerun `docker compose up -d ppc-seed` then `docker compose restart ppc-api`. |
| Run creation fails with "live forecaster … is missing" | `artifacts/f2_gru_live.pt` or `data/processed/charge_points.parquet` is missing. Check `git status artifacts/` and the seed logs. |
| "Backend unreachable" in the header | `ppc-api` isn't healthy yet (start-up takes up to 2 minutes) or crashed: `docker compose ps`, `docker compose logs ppc-api`. |
| Containers killed or very slow | Docker has less than 4 GB of memory. |
| Port 8080 taken | Set `HTTP_PORT=8081` in `.env`. |
| `Bind for 127.0.0.1:5432 failed` | Another Postgres uses port 5432. Stop it, or remove the `ports:` line of `ppc-db` locally. |

## Full data (optional)

The Dundee-derived sessions, signals, context and replay forecasts in `data/processed/` come from third-party data with restrictive licences, so they are never committed (AGENTS.md hard rules). To rebuild them you need the raw inputs in `data/raw/` (Dundee, weather, OSM, stations), [uv](https://docs.astral.sh/uv/) and Python 3.12:

```bash
uv sync
make data                                    # python -m dataset: data/raw → data/processed
uv run python -m model.experiments predict artifacts/f2_gru.pt \
  --start 2025-02-28 --end 2025-04-29 --out data/processed/forecasts.parquet
```

The SHA-256 of every file in the reference `data/processed/` is in [docs/data-manifest.md](docs/data-manifest.md), so you can check that a rebuilt or copied folder is identical.

## Development

Needs [uv](https://docs.astral.sh/uv/) with Python 3.12 and Node 20+.

```bash
uv sync && (cd frontend && npm ci)
make test    # pytest (api, sim, model) + frontend vitest
make lint    # ruff
```

`make api` / `make web` start a single module for debugging. Demos, reviews and "does it work" checks use Docker.

Repo layout and conventions: `docs/specs/00-foundation.md`. Live data format: `docs/LIVE_DATA.md`. Decisions: `docs/DECISIONS.md`.

## Server

Behind Traefik (decisions 0066, 0088): `cp .env.example .env`, fill the server block (`COMPOSE_FILE`, `TRAEFIK_NETWORK`, `PPC_HOST`), set a strong `POSTGRES_PASSWORD` and a fixed `PPC_ACCESS_KEY`, put the same `data/` (including `data/raw/live/`) and `artifacts/` on the server, then `docker compose up -d --build`. Nothing is published on the host; Traefik routes to `ppc-web`.

## Training on the GPU server

Reproducible runs in Docker (decision 0051). Needs Docker with the NVIDIA container toolkit.

```bash
make train-image                                   # build ppc-train:latest (stamps git sha)
make train-docker CONFIG=config/train/smoke.yaml SEED=0 GPU_ID=1   # one run on host GPU 1
make train-suite-docker SUITE=config/train/suites/smoke_seeds.yaml # several runs + summary table
docker compose -f docker-compose.train.yml run --rm train promote runs/<run_id>   # into artifacts/
make train-local CONFIG=config/train/smoke.yaml    # no Docker (CPU or local GPU)
```

- Data is mounted read-only from `./data`. Runs go to `./runs/<run_id>/` (gitignored): config, manifest, metrics, log, model.
- Nothing reaches `artifacts/` until `promote`, which also writes `<name>_provenance.json`.
- The same config, seed, data and image give the same `metrics.json` on the same GPU model and driver. The manifest records both.
- To add a trainer, write `train(ctx: RunContext) -> dict` with `@register(...)` and add one line to `MODULES` in `model/experiments/registry.py`.
