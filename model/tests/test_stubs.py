from datetime import UTC, datetime

import pandas as pd

from model.stubs import ConstantForecaster, RandomPolicy
from model.types import (
    CpView,
    PowerClass,
    SimState,
    State,
    TargetState,
)


def _make_dummy_state() -> SimState:
    t = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    cps = [
        CpView("L00001-1", "L00001", PowerClass.AC_22, ["Type2"], State.IDLE, 300),
        CpView("L00001-2", "L00001", PowerClass.DC_50, ["CCS"], State.IDLE, 300),
        CpView("L00001-3", "L00001", PowerClass.DC_HPC, ["CCS"], State.CHARGING, 600),
    ]
    signals = pd.DataFrame(columns=["ts", "location_id", "signal", "value", "eta_ts"])
    context = {"B": {"temp_c": 15.0, "precip_mm": 0.0, "wind_kmh": 10.0, "is_holiday": False}}
    return SimState(t=t, cps=cps, signals=signals, context=context)


def test_constant_forecaster():
    forecaster = ConstantForecaster(p_arrival_15=0.12, p_arrival_30=0.25, p_arrival_60=0.40)
    state = _make_dummy_state()
    df = forecaster.predict(state)

    assert len(df) == 3
    assert list(df.columns) == [
        "cp_id",
        "p_arrival_15",
        "p_arrival_30",
        "p_arrival_60",
        "expected_arrivals_60",
    ]
    assert df.loc[df["cp_id"] == "L00001-1", "p_arrival_15"].iloc[0] == 0.12
    assert df.loc[df["cp_id"] == "L00001-2", "p_arrival_30"].iloc[0] == 0.25


def test_random_policy():
    policy = RandomPolicy(seed=42)
    state = _make_dummy_state()
    commands = policy.decide(state)

    # L00001-3 is CHARGING, so should not have a command
    cmd_ids = {c.cp_id for c in commands}
    assert "L00001-3" not in cmd_ids
    assert "L00001-1" in cmd_ids
    assert "L00001-2" in cmd_ids

    # AC CPs must never be commanded to DEEP_SLEEP
    for cmd in commands:
        if cmd.cp_id == "L00001-1":
            assert cmd.target_state in (TargetState.IDLE, TargetState.STANDBY)
