"""Module 2: EV demand model + decision agent (spec 02)."""

from model.cost_rule import CostRulePolicy
from model.forecaster import DemandForecaster
from model.stubs import ConstantForecaster, RandomPolicy
from model.train import train_pipeline
from model.types import (
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

try:
    from model.rl_agent import RLPolicy
    from model.train_rl import train_ppo
except ImportError:
    RLPolicy = None  # type: ignore
    train_ppo = None  # type: ignore

__all__ = [
    "Command",
    "ConstantForecaster",
    "CostRulePolicy",
    "CpView",
    "DemandForecaster",
    "Forecaster",
    "Policy",
    "PolicyName",
    "PowerClass",
    "RandomPolicy",
    "RLPolicy",
    "SimState",
    "State",
    "TargetState",
    "train_pipeline",
    "train_ppo",
]
