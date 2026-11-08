#!/usr/bin/env bash
# F2 v2 training on a rented cloud GPU box without Docker (decision 0084). See docs/TRAINING_GPU.md.
# Laptop:  docker/pack_cloud.sh        -> ppc_cloud_<sha>.tar.gz (code + private data; never publish)
# Box:     tar xzf ppc_cloud_*.tar.gz && cd ppc_cloud && docker/train_cloud.sh all
# Usage:   docker/train_cloud.sh <setup|check|smoke|suite|ensemble|pack|all>
set -euo pipefail
cd "$(dirname "$0")/.."
export DATA_DIR="${DATA_DIR:-$PWD/data}" RUNS_DIR="${RUNS_DIR:-$PWD/runs}" PYTHONHASHSEED=0
export CUBLAS_WORKSPACE_CONFIG=:4096:8 UV_LINK_MODE=copy
PY="uv run --frozen --no-sync --extra rl --extra train python"
log() { echo "[$(date '+%F %T')] $*"; }

setup() {
  if ! command -v uv >/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  fi
  uv sync --frozen --no-dev --extra rl --extra train
  mkdir -p runs logs
}
check() {
  $PY -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"
  for f in sessions charge_points locations cp_active_days context signals source_refs; do
    [ -s "data/processed/$f.parquet" ] || { echo "missing data/processed/$f.parquet"; exit 1; }
  done
  [ -s data/seed/county_ev_stats.csv ] || { echo "missing data/seed/county_ev_stats.csv"; exit 1; }
  log "check OK"
}
smoke() {  # a few minutes: tiny sample, proves the cache build and both configs on this GPU
  S="--set params.n_train_cpdays=600 --set params.n_val_cpdays=200 --set params.n_test_cpdays=200"
  S="$S --set params.epochs=1 --set params.samples_per_epoch=100000"
  $PY -m model.experiments run config/train/f2_gru_live.yaml $S --overwrite
  $PY -m model.experiments run config/train/f2_gru_v2.yaml $S --overwrite
}
suite() { $PY -m model.experiments suite config/train/suites/f2_v2.yaml --continue-on-error; }
ensemble() {
  for name in f2_gru_v2 f2_gru_live; do
    dirs=$(ls -d runs/${name}-*-s[0-9] 2>/dev/null || true)
    [ -n "$dirs" ] || { log "no runs for $name"; continue; }
    $PY -m model.experiments ensemble $dirs --name "${name}_ens"
  done
}
pack() {
  out="f2_v2_results_$(date +%Y%m%d_%H%M).tar.gz"
  tar --exclude='*/forecasts.parquet' -czf "$out" runs logs
  log "wrote $out ($(du -h "$out" | cut -f1)); copy it back, then promote on the laptop"
}
case "${1:-}" in
  setup) setup ;; check) check ;; smoke) smoke ;; suite) suite ;; ensemble) ensemble ;; pack) pack ;;
  all) setup; check; smoke; suite; ensemble; pack ;;
  *) echo "usage: $0 <setup|check|smoke|suite|ensemble|pack|all>"; exit 1 ;;
esac
