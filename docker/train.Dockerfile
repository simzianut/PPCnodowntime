# syntax=docker/dockerfile:1
ARG CUDA_IMAGE=nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

FROM ${CUDA_IMAGE} AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /uvx /bin/
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# Low open-file limits on shared hosts (decision 0063): no stdlib precompile, few parallel jobs.
ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_LINK_MODE=copy \
    UV_CONCURRENT_INSTALLS=4 \
    UV_CONCURRENT_DOWNLOADS=8 \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN ulimit -n "$(ulimit -Hn)" 2>/dev/null || true; \
    env -u UV_COMPILE_BYTECODE uv python install 3.12
RUN ulimit -n "$(ulimit -Hn)" 2>/dev/null || true; \
    uv sync --frozen --no-dev --extra rl --extra train --no-install-project
COPY . .
RUN ulimit -n "$(ulimit -Hn)" 2>/dev/null || true; \
    uv sync --frozen --no-dev --extra rl --extra train

ARG GIT_SHA=unknown
ARG GIT_DIRTY=false
ENV PYTHONHASHSEED=0 \
    CUBLAS_WORKSPACE_CONFIG=:4096:8 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    RUNS_DIR=/app/runs \
    ARTIFACTS_DIR=/app/artifacts \
    GIT_SHA=${GIT_SHA} \
    GIT_DIRTY=${GIT_DIRTY}
ENTRYPOINT ["uv", "run", "--frozen", "--no-sync", "python", "-m", "model.experiments"]
CMD ["run", "config/train/smoke.yaml"]

# Build-time check without a GPU: docker build --target verify (dev deps only here).
FROM runtime AS verify
ENV UV_CONCURRENT_INSTALLS=2
RUN ulimit -n "$(ulimit -Hn)" 2>/dev/null || true; \
    uv sync --frozen --extra rl --extra train
RUN uv run --frozen --no-sync python -c "import torch, lightgbm, stable_baselines3, sb3_contrib, model.experiments; print(torch.__version__, torch.version.cuda); assert torch.version.cuda.startswith('12.4')"
RUN RUNS_DIR=/tmp/runs ARTIFACTS_DIR=/tmp/artifacts uv run --frozen --no-sync pytest model/tests/test_experiments.py -q
