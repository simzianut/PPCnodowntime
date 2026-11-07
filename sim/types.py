"""Shared simulation types (CONTRACTS: Python interfaces, decision 0017)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

import pandas as pd


class State(StrEnum):
    CHARGING = "CHARGING"
    IDLE = "IDLE"
    WAKING = "WAKING"
    STANDBY = "STANDBY"
    DEEP_SLEEP = "DEEP_SLEEP"
    OFFLINE = "OFFLINE"


# Integer codes used in per-CP numpy arrays; order matches the observation one-hot.
STATES: list[State] = list(State)
STATE_INDEX: dict[State, int] = {s: i for i, s in enumerate(STATES)}
CHARGING, IDLE, WAKING, STANDBY, DEEP_SLEEP, OFFLINE = range(6)


class TargetState(StrEnum):
    IDLE = "IDLE"
    STANDBY = "STANDBY"
    DEEP_SLEEP = "DEEP_SLEEP"


# Gym action index -> target state.
ACTIONS: list[TargetState] = [TargetState.IDLE, TargetState.STANDBY, TargetState.DEEP_SLEEP]


class PowerClass(StrEnum):
    AC_22 = "AC_22"
    DC_50 = "DC_50"
    DC_HPC = "DC_HPC"


POWER_CLASSES: list[PowerClass] = list(PowerClass)


class ContextType(StrEnum):
    urban = "urban"
    retail = "retail"
    highway = "highway"
    airport = "airport"
    other = "other"


class Autonomy(StrEnum):
    ACT = "ACT"
    RECOMMEND = "RECOMMEND"


class CommandStatus(StrEnum):
    applied = "applied"
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    expired = "expired"


class PolicyName(StrEnum):
    always_on = "always_on"
    naive_quiet_hours = "naive_quiet_hours"
    cost_rule = "cost_rule"
    rl_ppo = "rl_ppo"
    rl_maskppo = "rl_maskppo"
    rl_ddqn = "rl_ddqn"
    oracle = "oracle"
    # Uppercase aliases, used by model/ (T13).
    ALWAYS_ON = "always_on"
    NAIVE_QUIET_HOURS = "naive_quiet_hours"
    COST_RULE = "cost_rule"
    RL_PPO = "rl_ppo"


class Signal(StrEnum):
    ppcblue_route_intent = "ppcblue_route_intent"
    maps_ev_count = "maps_ev_count"
    maps_ice_count = "maps_ice_count"
    maps_traffic_index = "maps_traffic_index"


FORECAST_COLUMNS = [
    "cp_id",
    "p_arrival_15",
    "p_arrival_30",
    "p_arrival_60",
    "expected_arrivals_60",
]


@dataclass
class CpView:
    cp_id: str
    location_id: str
    power_class: PowerClass
    connectors: list[str]
    state: State
    seconds_in_state: int
    max_kw: float | None = None


@dataclass
class SimState:
    t: datetime  # slot start, tz-aware Europe/Bucharest
    cps: list[CpView]
    signals: pd.DataFrame  # signals.parquet schema, t - 60 min < ts <= t
    context: dict[str, dict]  # county_code -> {temp_c, precip_mm, wind_kmh, is_holiday}
    forecast: pd.DataFrame | None  # FORECAST_COLUMNS, one row per CP


@dataclass
class Command:
    cp_id: str
    target_state: TargetState
    reason: str


@runtime_checkable
class Policy(Protocol):
    name: str

    def decide(self, state: SimState) -> list[Command]: ...


@runtime_checkable
class Forecaster(Protocol):
    def predict(self, state: SimState) -> pd.DataFrame: ...
