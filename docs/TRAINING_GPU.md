# Training the forecasters on the GPU server

Covers F0 seasonal, F1 LightGBM, F2 GRU and the feature ablation (decisions 0051, 0052, 0058, 0059). RL training is separate.

## Requirements on the server
- Docker with Compose v2 and the NVIDIA Container Toolkit (`docker info` lists an `nvidia` runtime).
- NVIDIA driver ≥ 550, needed by the CUDA 12.4 image.
- Internet access for the first image build: CUDA base image, uv, PyPI, the PyTorch wheel index.
- Disk: about 15 GB for the image, 0.7 GB for data, about 6 GB for the feature cache (3 signals files), plus runs.
- RAM: 16 GB is enough. One run peaks at about 6 GB.

## Steps
```bash
# on your laptop
scp ppc_forecasters_<sha>.tar user@server:~/
# on the server
tar -xf ppc_forecasters_<sha>.tar && cd ppc_forecasters
cat .env                                # GPU_ID is the nvidia-smi index; check it
docker/train_forecasters.sh preflight   # shows the chosen GPU, checks the driver and data checksums
docker/train_forecasters.sh build       # image + verify stage (imports, CUDA 12.4 torch, tests)
docker/train_forecasters.sh gpu-check   # 1-minute smoke model on the GPU
docker/train_forecasters.sh smoke       # small F0/F1/F2 runs on real data, about 5 min
# long stages: run inside tmux/screen or with nohup
nohup docker/train_forecasters.sh main     > logs/main.log 2>&1 &   # F0/F1/F2 × 3 seeds, about 3–4 h
nohup docker/train_forecasters.sh ablation > logs/ablation.log 2>&1 &   # F1 ladder + sensitivity, about 6–9 h
docker/train_forecasters.sh status      # one line per run: status, duration, GPU
docker/train_forecasters.sh chown       # outputs back to your user (containers run as root)
docker/train_forecasters.sh pack        # forecaster_results_<date>.tar.gz, copy back with scp
```
Run `main` and `ablation` one after the other, not at the same time. They build the same feature cache.
The stage times above are estimates from smoke runs, not measurements.

## What it writes
- `runs/<name>-<sha8>-s<seed>/`: `config.yaml`, `manifest.json`, `metrics.json`, `timing.json`, `model/`, `forecasts.parquet` (test sample), `train.log`.
- `runs/suites/<suite>-<sha8>/summary.md` and `summary.csv`: one row per run with AUC, PR-AUC, Brier per horizon.
- `data/interim/forecast_cache/`: the shared feature cache. Safe to delete; it is rebuilt when needed.

## If something fails
- **A suite stops on an error:** fix it and rerun the same command. Finished runs (status `ok`) are skipped.
- **F2 raises a determinism error on CUDA:** rerun with `--set deterministic=false`. The manifest records it. Report the op, so a decision file can cover it.
- **Out of GPU memory** (the A100 is shared): lower `params.batch_size` for F2, e.g. `--set params.batch_size=1024`. F2 needs well under 4 GB at the default.
- **Wrong GPU:** `GPU_ID` is the `nvidia-smi` index. `preflight` prints the bus id; compare it with `nvidia-smi`. Compose also accepts the GPU UUID (`nvidia-smi -L`) as `GPU_ID`.

## After training
Copy the results back. The best F1 run's `forecasts.parquet` feeds the RL runs (`--set data.forecasts=<abs path>`).
Promote a forecaster into `artifacts/` only by decision: `python -m model.experiments promote runs/<run_id>`.

## Without the team server: any CUDA box (decision 0084)
No Docker needed. On the laptop `docker/pack_cloud.sh` writes `ppc_cloud_<sha>.tar.gz` (code of HEAD plus the seven private data files; never publish it). On the box, with a PyTorch CUDA image or plain Ubuntu with an NVIDIA driver:
```bash
tar xzf ppc_cloud_<sha>.tar.gz && cd ppc_cloud
tmux new -s train
docker/train_cloud.sh all      # setup (uv), check, 2-min smoke, suite f2_v2, ensembles, pack
```
Stages can be run one by one (`setup|check|smoke|suite|ensemble|pack`). A run of the F2 configs takes about 5 minutes on an A100. The result tarball `f2_v2_results_*.tar.gz` holds `runs/` (without the big forecasts files); unpack it into the repo root on the laptop and promote with `python -m model.experiments promote runs/<id> [--as f2_gru_live.pt]`. Apple-silicon fallback: `--set device=mps --set deterministic=false` (slower, not bit-reproducible).

### Google Colab Pro (A100), what worked on 2026-11-08 (decision 0085)
Drag `ppc_cloud_<sha>.tar.gz` into the Files panel by hand (the browser extension cannot upload files over 10 MB), wait until `gzip -t` passes, then in a `%%bash` cell `tar xzf`, `bash docker/train_cloud.sh setup && bash docker/train_cloud.sh check`. Run the seeds from the Colab terminal (Pro) with `nohup`, three in parallel: one seed alone keeps the A100 a third busy (36 s per epoch), three together take about 45 s per epoch, so the whole `f2_gru_v2` triple finishes in about 15 minutes. Build the ensemble, `promote`, `predict` and `sim.evaluate` on the box too (the data files are in the tarball), then download the results tarball and `forecasts.parquet` with `google.colab.files.download`. Keep `%%bash` cells short: their output appears only when the cell ends.
