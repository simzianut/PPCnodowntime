"""Loaders for config/sim.yaml and config/power_profiles.yaml."""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path

import pandas as pd
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


def cp_power(pc: PowerClass | str, max_kw: float | None = None) -> PowerProfile:
    """Per-CP watts from stations.store (decision 0049); wake times stay from the class profile."""
    kw = None if max_kw is None or pd.isna(max_kw) else float(max_kw)
    return _cp_power(PowerClass(pc), kw)


@cache
def _cp_power(pc: PowerClass, kw: float | None) -> PowerProfile:
    base = power_profiles()[pc]
    if kw is None:
        return base
    from stations.store import get_power_profile

    p = get_power_profile(pc.value, kw)
    deep = float(p["deep_sleep_w"]) if base.has_deep_sleep else None
    return replace(
        base, idle_w=float(p["idle_w"]), standby_w=float(p["standby_w"]), deep_sleep_w=deep
    )


@cache
def net_tariff_ron_per_kwh() -> float:
    from stations.store import get_energy_tariffs

    return float(get_energy_tariffs()["net_b2b_ron_per_kwh"])


@cache
def signals_config(config_dir: Path = CONFIG_DIR) -> dict:
    return _load("signals.yaml", config_dir)
