"""Day-one stubs for forecaster and policy interfaces (spec 02 §1).
Ensures simulator (M4) can run end-to-end without waiting for trained models.
"""

from __future__ import annotations

import random

import pandas as pd

from model.types import (
    Command,
    Forecaster,
    Policy,
    PolicyName,
    PowerClass,
    SimState,
    State,
    TargetState,
)


class ConstantForecaster(Forecaster):
    """Forecaster stub returning fixed arrival probabilities for every CP."""

    def __init__(
        self,
        p_arrival_15: float = 0.05,
        p_arrival_30: float = 0.10,
        p_arrival_60: float = 0.20,
        expected_arrivals_60: float = 0.50,
    ) -> None:
        self.p_arrival_15 = p_arrival_15
        self.p_arrival_30 = p_arrival_30
        self.p_arrival_60 = p_arrival_60
        self.expected_arrivals_60 = expected_arrivals_60

    def predict(self, state: SimState) -> pd.DataFrame:
        """Return constant predictions dataframe for all charge points in state."""
        rows = [
            {
                "cp_id": cp.cp_id,
                "p_arrival_15": self.p_arrival_15,
                "p_arrival_30": self.p_arrival_30,
                "p_arrival_60": self.p_arrival_60,
                "expected_arrivals_60": self.expected_arrivals_60,
            }
            for cp in state.cps
        ]
        return pd.DataFrame(
            rows,
            columns=[
                "cp_id",
                "p_arrival_15",
                "p_arrival_30",
                "p_arrival_60",
                "expected_arrivals_60",
            ],
        )


class RandomPolicy(Policy):
    """Policy stub issuing random valid power state transitions."""

    def __init__(
        self,
        name: PolicyName = PolicyName.COST_RULE,
        seed: int | None = 42,
    ) -> None:
        self.name = name
        self._rng = random.Random(seed)

    def decide(self, state: SimState) -> list[Command]:
        """Issue random commands respecting power class and state transition rules."""
        commands: list[Command] = []
        for cp in state.cps:
            if cp.state in (State.CHARGING, State.OFFLINE):
                continue
            if cp.power_class == PowerClass.AC_22:
                target = self._rng.choice([TargetState.IDLE, TargetState.STANDBY])
            else:
                target = self._rng.choice(
                    [TargetState.IDLE, TargetState.STANDBY, TargetState.DEEP_SLEEP]
                )
            commands.append(Command(cp_id=cp.cp_id, target_state=target, reason="random_stub"))
        return commands
