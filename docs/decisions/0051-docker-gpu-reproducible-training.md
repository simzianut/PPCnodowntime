# 0051: Training runs in Docker on a chosen GPU, reproducible by default

- Date: 2026-11-07
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: —
- Affects: CONTRACTS (Training runs, env vars), spec 02, model/, docker/, Makefile, pyproject (extra `train`)

## Context
The team will train the forecasters and RL agents on a shared NVIDIA server, not on laptops. Results go into the pitch, so anyone must be able to rerun a model and get the same numbers. The server has several GPUs and each run must say which one it uses.

## Decision
- **Image:** `docker/train.Dockerfile` on a pinned NVIDIA CUDA runtime base. Python dependencies are installed with `uv sync --frozen` from `uv.lock`. A new optional extra `train` (torch with a CUDA build on Linux, `sb3-contrib`) keeps CPU laptops on their current dependencies.
- **GPU choice:** `GPU_ID` (host index, as in `nvidia-smi`) is passed to Docker as the only visible device. The code always uses `cuda:0` inside the container.
- **Data** is mounted read-only. It is never baked into the image, because it is private (decision 0003, data READMEs).
- **Reproducibility:**
  - one seed sets Python, NumPy, torch, LightGBM and the envs
  - `torch.use_deterministic_algorithms(True)`, `CUBLAS_WORKSPACE_CONFIG=:4096:8`, cuDNN deterministic, fixed thread counts
  - every run writes a manifest: git SHA + dirty flag, sha256 of every input file, package versions, CUDA/driver/GPU name, resolved config
- **Runs, not artifacts:** training writes to `runs/<run_id>/`. Only an explicit `promote` copies a model into `artifacts/`, so a night of experiments never overwrites the models the app uses.
- **RL device:** the RL policies are about 13k-parameter MLPs and the time goes into the Python simulator, so RL configs default to `device: cpu` even in the GPU container. The GRU forecaster uses the GPU.

## Alternatives considered
- Conda environment on the server: harder to pin, and differs from the laptops' uv setup.
- Selecting the GPU with `CUDA_VISIBLE_DEVICES` only: works, but Docker-level device isolation also stops a run from using memory on other GPUs.
- Writing straight into `artifacts/`: a bad overnight run would replace the demo models.

## Consequences
- Bit-identical results are guaranteed only on the same GPU model, driver and image. The manifest records all three.
- Deterministic kernels are slightly slower. A config can set `deterministic: false` for quick tests, and the manifest records it.
