# PPCnodowntime

Energy-saving sleep scheduling for PPC Blue charge points. What and why: `docs/PRD.md`. How we work: `AGENTS.md`.

## Run locally
Needs Docker, [uv](https://docs.astral.sh/uv/) and Node 20+.

```
cp .env.example .env
make db      # postgres+postgis on :5432, apply db/migrations
make seed    # M3: load station seed into DB
make data    # M1: build processed datasets (needs data/raw/)
make train   # M2: train forecaster + PPO, write artifacts/
make api     # M4: FastAPI on :8000
make web     # M5: Vite on :5173
make test    # pytest + frontend tests
make lint    # ruff
```

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
- Add a trainer: write `train(ctx: RunContext) -> dict` with `@register("f2_gru", kind="forecaster", promote={...})` and add one line `"f2_gru": "model.experiments.f2_gru:train"` to `MODULES` in `model/experiments/registry.py`.
