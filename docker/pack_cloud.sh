#!/usr/bin/env bash
# Package code (HEAD) + the private data the F2 trainers read for a cloud GPU box (decision 0084).
# Output: ppc_cloud_<sha>.tar.gz in the repo root (gitignored). Contains private data: never publish.
set -euo pipefail
cd "$(dirname "$0")/.."
stage="$(mktemp -d)/ppc_cloud"
mkdir -p "$stage/data/processed" "$stage/data/seed"
git archive --format=tar HEAD | tar -x -C "$stage"
for f in sessions charge_points locations cp_active_days context signals source_refs; do
  cp "data/processed/$f.parquet" "$stage/data/processed/"
done
cp data/seed/county_ev_stats.csv "$stage/data/seed/"
out="ppc_cloud_$(git rev-parse --short HEAD).tar.gz"
tar -C "$(dirname "$stage")" -czf "$out" ppc_cloud
ls -lh "$out"
