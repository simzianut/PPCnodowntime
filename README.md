# PPCnodowntime

Energy-saving sleep scheduling for PPC Blue charge points. What and why: `docs/PRD.md`. How we work: `AGENTS.md`.

## Run the app: Docker (the team standard, decision 0077)
Everyone runs and demos the app with Docker. Needs Docker with **at least 4 GB of memory** (Windows: `memory=4GB` under `[wsl2]` in `%USERPROFILE%\.wslconfig`, then `wsl --shutdown`).

```
cp .env.example .env          # set DATA_HOST_DIR to the folder with the private data (data/processed, data/raw/live)
docker compose up -d --build  # or: make up
```
Open **http://localhost:8080** and enter the **access key** (decision 0087): `PPC_ACCESS_KEY` from `.env`, or, when that is empty, the key `ppc-api` generates and logs at every start: `docker compose logs ppc-api | grep "access key"`. The browser keeps a login cookie for 30 days, or until the key changes. `PPC_AUTH=off` in `.env` turns the key off locally. Containers have CPU and memory caps (`API_CPUS`, `API_MEM_LIMIT`, …, see `.env.example`).

nginx (`ppc-web`) is the single entry point and proxies `/api` and `/ws` to `ppc-api`; `ppc-db` is Postgres + PostGIS; `ppc-seed`/`ppc-migrate` run once.
- Real mode needs the private data (`data/processed/`, incl. `forecasts.parquet`) and `artifacts/` (committed). Without data set `API_FIXTURES=1`.
- The agent is F2 GRU + cost rule (`RL_AGENT=auto`, decision 0069). Check: `docker compose logs ppc-api | grep "served by"`.
- Replay on collected statuses (decision 0090): every local day with AmpWhere statuses in `data/raw/live/statuses-*.jsonl` is offered first in the replay day picker at `/`, with play, pause, speed and seek. It needs `source_refs.parquet` built from the same snapshot (`python -m stations`). File format and rules: `docs/LIVE_DATA.md`.
- Live demo clock (optional): `LIVE_CLOCK_START=2026-11-07T19:30:00+02:00` and `LIVE_CLOCK_SPEED=30` in `.env` replay collected statuses in live mode. Real-time live needs the collector: `docker compose --profile live up -d ppc-collector` with `AMPWHERE_ENABLED=1` (a human enables it, decision 0003).
- Open **http://localhost:8080/?mode=live** to start on the live network (decision 0080). Live needs `data/processed/source_refs.parquet` (`uv run python -m stations` builds it from the collected AmpWhere snapshot). After changing `.env`, recreate the API (`docker compose up -d ppc-api`) so it picks up `LIVE_CLOCK_*`.
- After changing code: `docker compose up -d --build`, then reload the page (an API restart drops in-memory runs; a stale page falls back to demo data).
- Stop: `docker compose down`. Logs: `docker compose logs -f ppc-api`.

Server (behind Traefik, decision 0066): `cp .env.example .env`, fill the server block (`COMPOSE_FILE`, `TRAEFIK_NETWORK`, `PPC_HOST`), a strong `POSTGRES_PASSWORD` and a fixed `PPC_ACCESS_KEY`, make sure `data/` and `artifacts/` are present, then `docker compose up -d --build`. Nothing is published on the host; Traefik routes to `ppc-web`.

## Development tools (not for running the app)
Needs [uv](https://docs.astral.sh/uv/) and Node 20+.
```
make test    # pytest + frontend tests
make lint    # ruff
make data    # M1: build processed datasets (needs data/raw/)
make train   # M2: train the F2 GRU (model.experiments); GPU training: see below
```
`make api` / `make web` still work for debugging a single module, but demos, reviews and "does it work" checks use Docker.

Repo layout and conventions: `docs/specs/00-foundation.md`. Interfaces: `docs/CONTRACTS.md`.

## Training on the GPU server
Reproducible runs in Docker (decision 0051). Needs Docker with the NVIDIA container toolkit.

```
make train-image                                   # build ppc-train:latest (stamps git sha)
make train-docker CONFIG=config/train/smoke.yaml SEED=0 GPU_ID=1   # one run on host GPU 1
GPU_ID=1 docker compose -f docker-compose.train.yml run --rm train run config/train/smoke.yaml --set params.epochs=10
make train-suite-docker SUITE=config/train/suites/smoke_seeds.yaml # several runs + summary table
docker compose -f docker-compose.train.yml run --rm train promote runs/<run_id>   # into artifacts/
make train-local CONFIG=config/train/smoke.yaml    # no Docker (CPU or local GPU)
```

- Data is mounted read-only from `./data`. Runs go to `./runs/<run_id>/` (gitignored): config, manifest, metrics, log, model. Suites write `runs/suites/<name>-<hash>/summary.{csv,md}`.
- Nothing reaches `artifacts/` until `promote`. Promote also writes `<name>_provenance.json`.
- Same config, seed, data and image give the same `metrics.json` on the same GPU model and driver (the manifest records both).
- Demo forecasts (decision 0069): `python -m model.experiments predict <run_dir_or_f2_gru.pt> --start 2025-02-28 --end 2025-04-29 --out data/processed/forecasts.parquet` (UTC days; 2025-02-28 is needed so local day 2025-03-01 is fully covered; `--data-dir`, `--chunk-days` optional; also `python -m model.gru_forecaster`). When that file exists the API and evaluator offer only the days it fully covers.
- Add a trainer: write `train(ctx: RunContext) -> dict` with `@register("f2_gru", kind="forecaster", promote={...})` and add one line `"f2_gru": "model.experiments.f2_gru:train"` to `MODULES` in `model/experiments/registry.py`.
