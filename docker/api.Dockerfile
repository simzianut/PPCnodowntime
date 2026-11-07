# syntax=docker/dockerfile:1
# API image: CPU-only torch, no dev deps (decision 0066).
FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /uvx /bin/
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       libgomp1 ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*
# No UV_COMPILE_BYTECODE (decision 0063); few parallel installs for low open-file limits.
ENV UV_LINK_MODE=copy \
    UV_CONCURRENT_INSTALLS=4 \
    UV_CONCURRENT_DOWNLOADS=8 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    DATA_DIR=/app/data \n    MPLCONFIGDIR=/tmp/mpl
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv venv /opt/venv --python /usr/local/bin/python3.12
# The lock pins the cu124 torch wheel on Linux: CPU torch first, then the rest without torch/nvidia/triton.
RUN uv pip install --python /opt/venv/bin/python torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu
RUN set -e; ulimit -n "$(ulimit -Hn)" 2>/dev/null || true; \
    uv export --frozen --no-dev --extra rl --extra train --no-hashes --no-emit-project \
        --no-emit-package torch -o /tmp/req.txt; \
    grep -viE '^(nvidia-|triton|torch==)' /tmp/req.txt > /tmp/req.filtered.txt; \
    uv pip install -r /tmp/req.filtered.txt
COPY . .
RUN useradd --system --uid 1000 --home-dir /app app
USER app
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
