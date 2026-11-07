from __future__ import annotations

import os
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "live.yaml"


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "./data"))


def live_dir() -> Path:
    return data_dir() / "raw" / "live"


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)
