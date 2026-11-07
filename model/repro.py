"""Reproducibility helpers: seeding, device choice, environment and file hashing."""

from __future__ import annotations

import hashlib
import importlib.metadata as md
import json
import os
import platform
import random
import socket
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

try:
    import torch
except ImportError:  # LightGBM-only runs work without torch
    torch = None


def seed_everything(seed: int, deterministic: bool = True) -> None:
    """Seed python, numpy and torch. PYTHONHASHSEED only works if set before start (Docker does)."""
    os.environ.setdefault("PYTHONHASHSEED", str(seed))
    if deterministic:
        os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    random.seed(seed)
    np.random.seed(seed)
    if torch is None:
        return
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(deterministic, warn_only=False)
    torch.backends.cudnn.deterministic = deterministic
    torch.backends.cudnn.benchmark = False


def resolve_device(device: str = "auto") -> str:
    """Map auto|cpu|cuda to a concrete torch device string."""
    cuda_ok = torch is not None and torch.cuda.is_available()
    if device == "auto":
        return "cuda" if cuda_ok else "cpu"
    if device == "cuda" and not cuda_ok:
        raise RuntimeError("device=cuda requested but CUDA is not available")
    if device not in ("cpu", "cuda"):
        raise ValueError(f"unknown device {device!r}")
    return device


def seed_worker(worker_id: int) -> None:
    """DataLoader worker_init_fn: derive python/numpy seeds from the torch worker seed."""
    s = torch.initial_seed() % 2**32
    np.random.seed(s)
    random.seed(s)


def torch_generator(seed: int):
    g = torch.Generator()
    g.manual_seed(seed)
    return g


def _driver() -> str | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        line = out.stdout.strip().splitlines()
        return line[0].strip() if out.returncode == 0 and line else None
    except (OSError, subprocess.SubprocessError):
        return None


def environment_info() -> dict[str, Any]:
    info: dict[str, Any] = {
        "python": platform.python_version(),
        "torch": None,
        "cuda": None,
        "cudnn": None,
        "gpu_name": None,
        "driver": _driver(),
        "host": socket.gethostname(),
        "packages": {},
    }
    if torch is not None:
        info["torch"] = torch.__version__
        info["cuda"] = torch.version.cuda
        cudnn = torch.backends.cudnn.version() if torch.backends.cudnn.is_available() else None
        info["cudnn"] = str(cudnn) if cudnn else None
        if torch.cuda.is_available():
            info["gpu_name"] = torch.cuda.get_device_name(0)
    info["packages"] = {
        d.metadata["Name"]: d.version for d in md.distributions() if d.metadata["Name"]
    }
    return info


def git_info() -> dict[str, Any]:
    """Git sha and dirty flag; env GIT_SHA/GIT_DIRTY win (no .git inside Docker)."""
    sha, dirty = os.environ.get("GIT_SHA") or None, os.environ.get("GIT_DIRTY")
    if sha:
        return {"git_sha": sha, "git_dirty": str(dirty).lower() in ("1", "true", "yes")}
    try:
        root = Path(__file__).resolve().parent.parent
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=10
        ).stdout.strip()
        st = subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, timeout=10
        ).stdout.strip()
        return {"git_sha": sha or None, "git_dirty": bool(st) if sha else None}
    except (OSError, subprocess.SubprocessError):
        return {"git_sha": None, "git_dirty": None}


def runs_dir() -> Path:
    return Path(os.environ.get("RUNS_DIR", "runs"))


def sha256_file(path: str | Path) -> dict[str, Any]:
    """sha256 + size, cached in RUNS_DIR/.hashcache.json by (abs path, size, mtime_ns)."""
    p = Path(path).resolve()
    st = p.stat()
    key = f"{p}|{st.st_size}|{st.st_mtime_ns}"
    cache_file = runs_dir() / ".hashcache.json"
    cache: dict[str, str] = {}
    try:
        cache = json.loads(cache_file.read_text())
    except (OSError, ValueError):
        pass
    if key not in cache:
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        cache[key] = h.hexdigest()
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(cache))
        except OSError:
            pass
    return {"sha256": cache[key], "bytes": st.st_size}
