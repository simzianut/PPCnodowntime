#!/usr/bin/env bash
# Forecaster training on the GPU server (decisions 0051, 0052, 0059). See docs/TRAINING_GPU.md.
# Usage: docker/train_forecasters.sh <preflight|build|gpu-check|smoke|main|ablation|ablation-f2|status|pack|chown|all>
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE="docker compose -f docker-compose.train.yml"
[ -f .env ] || { echo "missing .env (copy .env.train.example to .env and set GPU_ID)"; exit 1; }
set -a; . ./.env; set +a
GPU_ID="${GPU_ID:?set GPU_ID in .env}"
mkdir -p runs logs artifacts data/interim

DATA_FILES="sessions charge_points locations cp_active_days context signals signals_evtag01 signals_papp03"
SMOKE="--set params.max_cps=20 --set params.n_train_cpdays=300 --set params.n_val_cpdays=100 --set params.n_test_cpdays=150"

need_image() { docker image inspect ppc-train:latest >/dev/null 2>&1 || { echo "image ppc-train:latest missing: run build first"; exit 1; }; }
run() { need_image; $COMPOSE run --rm train "$@"; }
log() { echo "[$(date '+%F %T')] $*"; }

preflight() {
  log "docker: $(docker --version)"; $COMPOSE version
  command -v nvidia-smi >/dev/null || { echo "nvidia-smi not found"; exit 1; }
  log "selected GPU_ID=$GPU_ID:"
  nvidia-smi -i "$GPU_ID" --query-gpu=index,name,pci.bus_id,memory.used,memory.total,driver_version \
    --format=csv
  drv=$(nvidia-smi -i "$GPU_ID" --query-gpu=driver_version --format=csv,noheader | cut -d. -f1)
  [ "$drv" -ge 550 ] || { echo "driver $drv < 550: too old for CUDA 12.4 images"; exit 1; }
  docker info 2>/dev/null | grep -qi nvidia || echo "WARN: docker info shows no nvidia runtime"
  for f in $DATA_FILES; do [ -s "data/processed/$f.parquet" ] || { echo "missing $f"; exit 1; }; done
  [ -s data/seed/county_ev_stats.csv ] || { echo "missing data/seed/county_ev_stats.csv"; exit 1; }
  if [ -f DATA_SHA256SUMS ]; then sha256sum --quiet -c DATA_SHA256SUMS && log "data checksums OK"; fi
  log "preflight OK"
}

build() {
  log "building runtime image (GIT_SHA=${GIT_SHA:-unknown})"
  $COMPOSE build train
  log "building verify stage (imports, CUDA 12.4 torch, pipeline tests; no container is started)"
  docker build -f docker/train.Dockerfile --target verify \
    --build-arg GIT_SHA="${GIT_SHA:-unknown}" --build-arg GIT_DIRTY="${GIT_DIRTY:-false}" \
    -t ppc-train:verify .
}

gpu_check() {
  log "smoke model on cuda (proves the container sees GPU $GPU_ID, deterministic kernels work)"
  run run config/train/smoke.yaml --set device=cuda --overwrite
  grep -h '"gpu_name"\|"cuda"\|"status"' runs/smoke-*/manifest.json | sort -u
}

smoke() {
  log "small F0/F1/F2 runs on real data (catches data/cache/CUDA problems in minutes)"
  run run config/train/f0_seasonal.yaml $SMOKE --overwrite
  run run config/train/f1_lgbm.yaml $SMOKE --overwrite
  run run config/train/f2_gru.yaml $SMOKE --set device=cuda --set params.epochs=2 \
    --set params.samples_per_epoch=100000 --overwrite
}

main_suite() { run suite config/train/suites/forecasters.yaml --continue-on-error; }
ablation() { run suite config/train/suites/ablation.yaml --continue-on-error; }
ablation_f2() { run suite config/train/suites/ablation_f2.yaml --continue-on-error; }

status() {
  for m in runs/*/manifest.json; do
    [ -f "$m" ] || continue
    python3 -c "import json,sys; m=json.load(open(sys.argv[1])); \
print(f\"{m['run_id']:<40} {m['status']:<8} {m.get('duration_s') or '':>8} {m.get('gpu_name') or 'cpu'}\")" "$m"
  done
  ls runs/suites/*/summary.md 2>/dev/null || true
}

chown_back() {
  # containers run as root; give the outputs back to the current user
  $COMPOSE run --rm --entrypoint chown train -R "$(id -u):$(id -g)" /app/runs /app/data/interim \
    /app/artifacts
}

pack() {
  out="forecaster_results_$(date +%Y%m%d_%H%M).tar.gz"
  tar --exclude='runs/.hashcache.json' -czf "$out" runs logs
  log "wrote $out ($(du -h "$out" | cut -f1)); copy it back with scp"
}

case "${1:-}" in
  preflight) preflight ;;
  build) build ;;
  gpu-check) gpu_check ;;
  smoke) smoke ;;
  main) main_suite ;;
  ablation) ablation ;;
  ablation-f2) ablation_f2 ;;
  status) status ;;
  chown) chown_back ;;
  pack) pack ;;
  all) preflight; build; gpu_check; smoke; main_suite; ablation; chown_back; pack ;;
  *) sed -n '2,3p' "$0"; exit 1 ;;
esac
