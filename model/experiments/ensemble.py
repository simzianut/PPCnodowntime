"""Seed ensemble of F2 runs: averaged hazards, calibrated on val, saved as a run (decision 0084)."""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import torch
import yaml

from model import forecast_data as fd
from model import repro
from model.experiments import f2_gru
from model.experiments.runner import RunContext, config_sha256, make_run_id


def build(run_dirs: list[str | Path], name: str, calibration: str = "auto") -> Path:
    """Average the members' nets, calibrate on the shared val split, write runs/<name>-.../."""
    t0 = time.monotonic()
    dirs = [Path(d) for d in run_dirs]
    if len(dirs) < 2:
        raise ValueError("an ensemble needs at least two run dirs")
    cks = [
        torch.load(d / "model" / "f2_gru.pt", map_location="cpu", weights_only=False) for d in dirs
    ]
    base = cks[0]
    for ck in cks[1:]:
        if (
            ck["features"] != base["features"]
            or ck["config"]["window_slots"] != base["config"]["window_slots"]
        ):
            raise ValueError("ensemble members must share features and window")
        for k in ("mu", "sd", "static_mu", "static_sd"):
            np.testing.assert_allclose(ck["norm"][k], base["norm"][k], rtol=1e-5)
    ids = [json.loads((d / "manifest.json").read_text())["run_id"] for d in dirs]
    cfg = yaml.safe_load((dirs[0] / "config.yaml").read_text())
    cfg = {
        **cfg,
        "name": name,
        "ensemble": ids,
        "params": {**cfg["params"], "calibration": calibration},
    }
    run_id = make_run_id(cfg)
    run_dir = repro.runs_dir() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=True))
    log = logging.getLogger(f"ensemble.{run_id}")
    log.setLevel(logging.INFO)
    if not log.handlers:
        log.addHandler(logging.StreamHandler(sys.stdout))
    dev = repro.resolve_device(cfg.get("device", "auto"))
    net = f2_gru.Ensemble([f2_gru.net_from_checkpoint(ck) for ck in cks]).to(dev)
    c = {**f2_gru.DEFAULTS, **cfg["params"]}
    ctx = RunContext(run_dir, cfg, int(cfg.get("seed", 0)), dev, log)
    names, dyn_names, norm = base["features"], base["dynamic_features"], base["norm"]
    data = fd.get_dataset(cfg, log)
    ds = {}
    for s in ("val", "test"):
        _, dyn, stat = f2_gru._prep(data.X(s, names), names)
        dyn, stat = f2_gru.apply_norm(dyn, stat, norm)
        ds[s] = f2_gru.Windows(dyn, stat, data.bucket(s), data.n60(s), int(c["window_slots"]))
    raw = {s: f2_gru.predict(net, ds[s], dev) for s in ("val", "test")}
    metrics = f2_gru.finish_run(
        ctx, c, data, names, dyn_names, norm, net, raw, time.monotonic() - t0, {"members": ids}
    )
    ctx.save_metrics(metrics)
    env = repro.environment_info()
    env.pop("packages", None)
    manifest = {
        "run_id": run_id,
        "config_sha256": config_sha256(cfg),
        "seed": cfg.get("seed", 0),
        "deterministic": cfg.get("deterministic", True),
        **repro.git_info(),
        **env,
        "members": ids,
        "status": "ok",
        "duration_s": round(time.monotonic() - t0, 3),
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    log.info("ensemble %s: %d members, %s", run_id, len(ids), metrics["test"]["overall"]["y30"])
    return run_dir
