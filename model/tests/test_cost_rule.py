from datetime import UTC, datetime, timedelta

import pandas as pd

from model.cost_rule import CostRulePolicy
from model.types import (
    CpView,
    PowerClass,
    SimState,
    State,
    TargetState,
)


def _make_state(
    p15: float = 0.01,
    p30: float = 0.02,
    p60: float = 0.03,
    signals: pd.DataFrame | None = None,
) -> SimState:
    t = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    cps = [
        CpView("L00001-1", "L00001", PowerClass.DC_50, ["CCS"], State.IDLE, 300),
        CpView("L00001-2", "L00001", PowerClass.DC_50, ["CCS"], State.IDLE, 300),
        CpView("L00001-3", "L00001", PowerClass.AC_22, ["Type2"], State.IDLE, 300),
    ]
    if signals is None:
        signals = pd.DataFrame(columns=["ts", "location_id", "signal", "value", "eta_ts"])
    context = {"B": {"temp_c": 15.0, "precip_mm": 0.0, "wind_kmh": 10.0, "is_holiday": False}}
    forecast = pd.DataFrame(
        [
            {
                "cp_id": "L00001-1",
                "p_arrival_15": p15,
                "p_arrival_30": p30,
                "p_arrival_60": p60,
                "expected_arrivals_60": 0.1,
            },
            {
                "cp_id": "L00001-2",
                "p_arrival_15": p15,
                "p_arrival_30": p30,
                "p_arrival_60": p60,
                "expected_arrivals_60": 0.1,
            },
            {
                "cp_id": "L00001-3",
                "p_arrival_15": p15,
                "p_arrival_30": p30,
                "p_arrival_60": p60,
                "expected_arrivals_60": 0.1,
            },
        ]
    )
    return SimState(t=t, cps=cps, signals=signals, context=context, forecast=forecast)


def test_cost_rule_high_arrival_stays_idle():
    policy = CostRulePolicy(lambda_wait=0.1)
    state = _make_state(p15=0.8, p30=0.9, p60=0.95)
    commands = policy.decide(state)

    for cmd in commands:
        assert cmd.target_state == TargetState.IDLE


def test_cost_rule_low_arrival_sleeps_and_respects_min_awake():
    policy = CostRulePolicy(lambda_wait=0.1, min_awake_per_location=1)
    # p60 = 0.05 >= 0.02, so min_awake constraint triggers for DC_50 pair
    state = _make_state(p15=0.001, p30=0.002, p60=0.05)
    commands = policy.decide(state)
    cmd_map = {c.cp_id: c.target_state for c in commands}

    # AC can sleep to STANDBY (1 CP in group, but min_awake applies per power class)
    # DC has 2 CPs: exactly 1 must stay IDLE, and 1 can DEEP_SLEEP
    dc_states = [cmd_map["L00001-1"], cmd_map["L00001-2"]]
    assert TargetState.IDLE in dc_states
    assert TargetState.DEEP_SLEEP in dc_states


def test_cost_rule_low_arrival_exempt_from_min_awake():
    policy = CostRulePolicy(lambda_wait=0.1, min_awake_per_location=1)
    # p60 < 0.02 (exemption): both DC CPs can DEEP_SLEEP
    state = _make_state(p15=0.0001, p30=0.0002, p60=0.0005)
    commands = policy.decide(state)
    cmd_map = {c.cp_id: c.target_state for c in commands}

    assert cmd_map["L00001-1"] == TargetState.DEEP_SLEEP
    assert cmd_map["L00001-2"] == TargetState.DEEP_SLEEP
    assert cmd_map["L00001-3"] == TargetState.STANDBY


def test_cost_rule_route_intent_wakes_cp():
    policy = CostRulePolicy(lambda_wait=0.1)
    t = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    # Route intent arriving in 4 minutes (240s <= 180s + 300s = 480s)
    signals = pd.DataFrame(
        [
            {
                "ts": t - timedelta(minutes=5),
                "location_id": "L00001",
                "signal": "ppcblue_route_intent",
                "value": 1.0,
                "eta_ts": t + timedelta(minutes=4),
            }
        ]
    )
    state = _make_state(p15=0.0001, p30=0.0002, p60=0.0005, signals=signals)
    commands = policy.decide(state)
    cmd_map = {c.cp_id: c.target_state for c in commands}

    # Route intent forces CPs at that location to stay IDLE
    assert cmd_map["L00001-1"] == TargetState.IDLE
    assert cmd_map["L00001-2"] == TargetState.IDLE


def _hpc_state(p15: float) -> SimState:
    t = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    cps = [
        CpView("L1-1", "L1", PowerClass.DC_HPC, ["CCS"], State.IDLE, 300, 300.0),
        CpView("L2-1", "L2", PowerClass.DC_HPC, ["CCS"], State.IDLE, 300, 100.0),
        CpView("L3-1", "L3", PowerClass.DC_HPC, ["CCS"], State.IDLE, 300),
    ]
    fc = pd.DataFrame(
        [
            {"cp_id": c.cp_id, "p_arrival_15": p15, "p_arrival_30": p15, "p_arrival_60": p15,
             "expected_arrivals_60": 0.1}
            for c in cps
        ]
    )  # fmt: skip
    return SimState(t=t, cps=cps, signals=pd.DataFrame(), context={}, forecast=fc)


def test_cost_rule_per_cp_power_from_max_kw():
    policy = CostRulePolicy(lambda_wait=0.1, min_awake_per_location=0, sim_config_path="-")
    state = _hpc_state(p15=0.006)
    p300, p100, pnone = (policy._profile(cp) for cp in state.cps)
    assert (p300["idle_w"], p300["deep_sleep_w"], p300["standby_w"]) == (230.0, 32.0, 50.0)
    assert (p100["idle_w"], p100["deep_sleep_w"]) == (130.0, 23.0)
    assert pnone == policy.power_profiles[PowerClass.DC_HPC]
    assert (p300["standby_wake_s"], p300["deep_sleep_wake_s"]) == (10.0, 180.0)
    # 300 kW: standby saves almost as much as deep sleep, so the 180 s wait isn't worth it
    cmd = {c.cp_id: c.target_state for c in policy.decide(state)}
    assert cmd == {
        "L1-1": TargetState.STANDBY,
        "L2-1": TargetState.DEEP_SLEEP,
        "L3-1": TargetState.DEEP_SLEEP,
    }
