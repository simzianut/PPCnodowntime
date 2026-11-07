"""Run one training config: resolve, hash, seed, dispatch, write manifest and metrics."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from model import repro
from model.experiments.registry import get_trainer

DEFAULTS = {"seed": 0, "device": "auto", "deterministic": True}


@dataclass
class RunContext:
    run_dir: Path
    config: dict
    seed: int
    device: str
    log: logging.Logger

    def path(self, *parts: str) -> Path:
        return self.run_dir.joinpath(*parts)

    def data_path(self, key: str) -> Path:
        return data_root() / self.config["data"][key]

    def save_metrics(self, metrics: dict) -> None:
        self.path("metrics.json").write_text(json.dumps(metrics, indent=2, sort_keys=True))


def data_root() -> Path:
    return Path(os.environ.get("DATA_DIR", "data")) / "processed"


def parse_override(item: str) -> tuple[str, Any]:
    key, sep, val = item.partition("=")
    if not sep or not key:
        raise ValueError(f"--set expects key.path=value, got {item!r}")
    return key.strip(), yaml.safe_load(val)


def apply_overrides(config: dict, overrides: dict[str, Any]) -> dict:
    cfg = copy.deepcopy(config)
    for key, val in overrides.items():
        node = cfg
        *parents, leaf = key.split(".")
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = val
    return cfg


def resolve_config(path, seed: int | None = None, overrides: dict[str, Any] | None = None) -> dict:
    cfg = yaml.safe_load(Path(path).read_text()) or {}
    cfg = apply_overrides(cfg, overrides or {})
    for k, v in DEFAULTS.items():
        cfg.setdefault(k, v)
    if seed is not None:
        cfg["seed"] = int(seed)
    for req in ("name", "model"):
        if req not in cfg:
            raise ValueError(f"config {path} is missing {req!r}")
    return cfg


def config_sha256(cfg: dict) -> str:
    blob = json.dumps(cfg, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def make_run_id(cfg: dict) -> str:
    return f"{cfg['name']}-{config_sha256(cfg)[:8]}-s{cfg['seed']}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _data_files(cfg: dict) -> list[str]:
    out: list[str] = []
    for v in (cfg.get("data") or {}).values():
        out.extend(v if isinstance(v, list) else [v])
    return out


def run_status(run_id: str) -> str | None:
    m = repro.runs_dir() / run_id / "manifest.json"
    try:
        return json.loads(m.read_text()).get("status")
    except (OSError, ValueError):
        return None


def run_config(cfg: dict, overwrite: bool = False) -> Path:
    """Execute a resolved config. Returns the run dir; raises on trainer failure."""
    sha, run_id = config_sha256(cfg), make_run_id(cfg)
    run_dir = repro.runs_dir() / run_id
    if run_status(run_id) == "ok" and not overwrite:
        raise FileExistsError(f"{run_id} already done (use --overwrite to rerun)")
    spec = get_trainer(cfg["model"])
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=True))

    env = repro.environment_info()
    manifest: dict[str, Any] = {
        "run_id": run_id,
        "config_sha256": sha,
        "seed": cfg["seed"],
        "deterministic": cfg["deterministic"],
        **repro.git_info(),
        "data": {},
        "packages": env.pop("packages"),
        **env,
        "started_at": _now(),
        "finished_at": None,
        "duration_s": None,
        "status": "running",
    }
    manifest_path = run_dir / "manifest.json"

    def write_manifest() -> None:
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))

    write_manifest()

    log = logging.getLogger(f"run.{run_id}")
    log.setLevel(logging.INFO)
    log.propagate = False
    handlers = [
        logging.FileHandler(run_dir / "train.log", mode="w"),
        logging.StreamHandler(sys.stdout),
    ]
    for h in handlers:
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
    t0 = time.monotonic()
    try:
        for rel in _data_files(cfg):
            manifest["data"][rel] = repro.sha256_file(data_root() / rel)
        repro.seed_everything(cfg["seed"], cfg["deterministic"])
        device = repro.resolve_device(cfg["device"])
        log.info("run %s device=%s seed=%s", run_id, device, cfg["seed"])
        ctx = RunContext(run_dir, cfg, int(cfg["seed"]), device, log)
        metrics = spec.fn(ctx)
        ctx.save_metrics(metrics)
        manifest["status"] = "ok"
    except BaseException as exc:
        manifest["status"] = "failed"
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        log.exception("run failed")
        raise
    finally:
        manifest["finished_at"] = _now()
        manifest["duration_s"] = round(time.monotonic() - t0, 3)
        write_manifest()
        for h in handlers:
            log.removeHandler(h)
            h.close()
    return run_dir
