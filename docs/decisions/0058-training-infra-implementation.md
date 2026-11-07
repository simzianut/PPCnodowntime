# 0058: Training infrastructure implementation details

- Date: 2026-11-07
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: —
- Affects: CONTRACTS (Training runs), docker/train.Dockerfile, pyproject, uv.lock, model/experiments/, model/repro.py

## Context
Decision 0051 set the goals: Docker on a chosen GPU, reproducible runs, a runs/ directory, and explicit promote. Building it forced several smaller choices.

## Decision
- **Pins:**
  - base `nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04`, uv `0.11.26` (ghcr image), Python 3.12 installed by uv
  - torch `2.6.0` (the `+cu124` wheel on Linux from the PyTorch index; the PyPI wheel on Windows/macOS)
  - `sb3-contrib` 2.8.0. To satisfy it, `stable-baselines3` is locked at 2.8.0 (it was 2.9.2). The existing PPO tests pass on 2.8.0.
- **The venv lives at `/opt/venv`** (`UV_PROJECT_ENVIRONMENT`), not `/app/.venv`, so bind mounts under `/app` can never hide it.
- **`verify` build stage** (`make train-image-verify`). It needs no GPU and starts no container. It installs the dev group, checks that torch/lightgbm/sb3/sb3-contrib/model.experiments import, asserts `torch.version.cuda` starts with `12.4`, and runs `model/tests/test_experiments.py` on CPU. The runtime image has no dev dependencies. The image has not been built on a laptop yet: the Docker VM there has under 1 GB of RAM. The first build is on the GPU server.
- **`deterministic: true` is strict.** `torch.use_deterministic_algorithms(True)` raises on a non-deterministic op instead of warning, so a run cannot silently become irreproducible. A trainer that needs such an op sets `deterministic: false`, and the manifest records it.
- **Thread counts are set by each trainer**, not by the runner: LightGBM `num_threads` + `deterministic: true`, torch `set_num_threads`, and SB3 env counts all come from the model's config `params`.
- **Trainer registry:** one line in `model.experiments.registry.MODULES` plus `@register(name, kind=..., promote={...})` on the train function. Modules are imported only when their model runs, so LightGBM-only runs don't need torch.
- **Suites** skip runs already finished with status ok and still list them in `summary.csv`/`summary.md`. The suite folder name uses the sha of the suite YAML.
- The manifest has an extra `error` key on failed runs. The torch/cuda/gpu/driver fields are null when not available.

## Alternatives considered
- `warn_only=True` for deterministic mode: runs would look reproducible when they aren't.
- Pinning stable-baselines3 2.9.2 and skipping sb3-contrib: we would have to write action-masked PPO ourselves.

## Consequences
- The GPU server needs the NVIDIA Container Toolkit for `device_ids` to work.
- Containers run as root, so files in `runs/` on the host are owned by root unless the server maps users.
