"""Loaders for config/sim.yaml and config/power_profiles.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

from sim.types import PowerClass

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
TZ = "Europe/Bucharest"


@dataclass(frozen=True)
class PowerProfile:
    idle_w: float
    standby_w: float
    deep_sleep_w: float | None
    standby_wake_s: float
    deep_sleep_wake_s: float | None

    @property
    def has_deep_sleep(self) -> bool:
        return self.deep_sleep_w is not None


@dataclass(frozen=True)
class PowerProfiles:
    by_class: dict[PowerClass, PowerProfile]
    wake_extra_wh: float

    def __getitem__(self, pc: PowerClass | str) -> PowerProfile:
        return self.by_class[PowerClass(pc)]


@dataclass(frozen=True)
class SimConfig:
    slot_s: int
    lambda_grid: list[float]
    default_lambda: float
    naive_quiet_hours_per_day: int
    min_awake_per_location: int
    recommendation_ttl_slots: int
    eval_days: str | list[str]

    @property
    def slots_per_day(self) -> int:
        return 86400 // self.slot_s


def _load(name: str, config_dir: Path) -> dict:
    return yaml.safe_load((config_dir / name).read_text())


@cache
def sim_config(config_dir: Path = CONFIG_DIR) -> SimConfig:
    return SimConfig(**_load("sim.yaml", config_dir))


@cache
def power_profiles(config_dir: Path = CONFIG_DIR) -> PowerProfiles:
    raw = _load("power_profiles.yaml", config_dir)
    extra = float(raw.pop("wake_extra_wh") or 0)
    return PowerProfiles({PowerClass(k): PowerProfile(**v) for k, v in raw.items()}, extra)


@cache
def signals_config(config_dir: Path = CONFIG_DIR) -> dict:
    return _load("signals.yaml", config_dir)
