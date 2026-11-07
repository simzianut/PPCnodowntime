# 0062: Forecaster training is shipped to the GPU server as one tar with code, data and a GPU-selecting .env

- Date: 2026-11-07
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: —
- Affects: docker-compose.train.yml, docker/train_forecasters.sh, .env.train.example, docs/TRAINING_GPU.md

## Context
The forecasters (0052, 0059) are trained on a shared A100 server reached over ssh. Three problems:
- The server has no git checkout and no copy of the private data.
- `docker-compose.train.yml` mounted all of `./data` read-only, but the forecaster feature cache writes to `data/interim/`, so a full run would have failed.
- Containers run as root, so the outputs would end up owned by root.

## Decision
- **One tar per code version.** It holds `git archive` of the commit, the 8 data parquet files needed, `DATA_SHA256SUMS`, and a `.env` with `GPU_ID` (the `nvidia-smi` index), `GIT_SHA`, `GIT_DIRTY` and `COMPOSE_PROJECT_NAME`. The tar is built locally and never committed (the data is private). `.env.train.example` is the committed template.
- **Mounts:** `data/interim` is mounted writable on top of the read-only `data` mount.
- **`docker/train_forecasters.sh` runs everything in stages:** preflight (GPU, driver ≥ 550, data checksums), build + verify, GPU smoke, real-data smoke, `main` (F0/F1/F2 × 3 seeds), `ablation`, `status`, `chown`, `pack`. The long stages are resumable, because suites skip finished runs.
- **Ownership:** outputs are handed back to the user with a `chown` container, instead of running the container as a non-root user. A non-root user would need extra uv and home setup in the image.

## Alternatives considered
- Copy the image instead of building on the server (`docker save`): the laptop cannot build it (RAM), and the server has internet.
- rsync the repo + data separately: more steps and easier to get out of sync with the code version.

## Consequences
- The server must have internet for the first build.
- Anyone with the tar has the private data, so keep it on the team server only.
