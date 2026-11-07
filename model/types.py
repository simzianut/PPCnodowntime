"""Shared simulation types and protocols for model/.
Imports from sim.types if available (decision 0017); falls back to CONTRACTS.
"""

from __future__ import annotations

try:
    from sim.types import (
        Command,
        CpView,
        Forecaster,
        Policy,
        PolicyName,
        PowerClass,
        SimState,
        State,
        TargetState,
    )
except ImportError:
    from dataclasses import dataclass
    from datetime import datetime
    from enum import StrEnum
    from typing import Protocol

    import pandas as pd

    class State(StrEnum):
        CHARGING = "CHARGING"
        IDLE = "IDLE"
        WAKING = "WAKING"
        STANDBY = "STANDBY"
        DEEP_SLEEP = "DEEP_SLEEP"
        OFFLINE = "OFFLINE"

    class TargetState(StrEnum):
        IDLE = "IDLE"
        STANDBY = "STANDBY"
        DEEP_SLEEP = "DEEP_SLEEP"

    class PowerClass(StrEnum):
        AC_22 = "AC_22"
        DC_50 = "DC_50"
        DC_HPC = "DC_HPC"

    class PolicyName(StrEnum):
        ALWAYS_ON = "always_on"
        NAIVE_QUIET_HOURS = "naive_quiet_hours"
        COST_RULE = "cost_rule"
        RL_PPO = "rl_ppo"

    @dataclass
    class CpView:
        cp_id: str
        location_id: str
        power_class: PowerClass
        connectors: list[str]
        state: State
        seconds_in_state: int

    @dataclass
    class SimState:
        t: datetime
        cps: list[CpView]
        signals: pd.DataFrame
        context: dict[str, dict]
        forecast: pd.DataFrame | None = None

    @dataclass
    class Command:
        cp_id: str
        target_state: TargetState
        reason: str

    class Policy(Protocol):
        name: PolicyName

        def decide(self, state: SimState) -> list[Command]: ...

    class Forecaster(Protocol):
        def predict(self, state: SimState) -> pd.DataFrame: ...


__all__ = [
    "Command",
    "CpView",
    "Forecaster",
    "Policy",
    "PolicyName",
    "PowerClass",
    "SimState",
    "State",
    "TargetState",
]
