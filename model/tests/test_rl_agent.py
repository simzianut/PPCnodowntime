"""Tests for RL PPO decision policy and training pipeline (spec 02 §4)."""

import json
import time
from datetime import UTC, datetime

import pandas as pd
import torch

from model.rl_agent import RLPolicy
from model.train_rl import train_ppo
from model.types import (
    Command,
    CpView,
    Policy,
    PolicyName,
    PowerClass,
    SimState,
    State,
    TargetState,
)


def _make_test_simstate() -> SimState:
    t = datetime(2026, 11, 7, 14, 0, tzinfo=UTC)
    cps = [
        CpView("L1-1", "L1", PowerClass.AC_22, ["Type2"], State.IDLE, 600),
        CpView("L1-2", "L1", PowerClass.DC_50, ["CCS"], State.IDLE, 600),
        CpView("L1-3", "L1", PowerClass.DC_HPC, ["CCS"], State.CHARGING, 1200),
        CpView("L1-4", "L1", PowerClass.DC_HPC, ["CCS"], State.STANDBY, 1800),
    ]
    signals = pd.DataFrame(
        [
            {
                "ts": t,
                "location_id": "L1",
                "signal": "maps_traffic_index",
                "value": 0.4,
                "eta_ts": None,
                "session_id": None,
            }
        ]
    )
    context = {"B": {"temp_c": 16.0, "precip_mm": 0.0, "wind_kmh": 8.0, "is_holiday": False}}
    return SimState(t=t, cps=cps, signals=signals, context=context, forecast=None)


def test_rl_policy_interface():
    policy = RLPolicy()
    assert policy.name == PolicyName.RL_PPO.value
    assert isinstance(policy, Policy)


def test_rl_policy_decide_basic():
    policy = RLPolicy()
    state = _make_test_simstate()
    commands = policy.decide(state)
    assert isinstance(commands, list)
    for cmd in commands:
        assert isinstance(cmd, Command)
        assert cmd.target_state in (TargetState.IDLE, TargetState.STANDBY, TargetState.DEEP_SLEEP)


def test_rl_policy_action_masking_guards():
    policy = RLPolicy()
    # Force logits in policy to strongly prefer action 2 (DEEP_SLEEP)
    with torch.no_grad():
        policy.model.policy.action_net.bias.fill_(-10.0)
        policy.model.policy.action_net.bias[2] = 10.0

    state = _make_test_simstate()
    commands = policy.decide(state)
    cmd_map = {c.cp_id: c.target_state for c in commands}

    # L1-1 is AC_22: must NEVER receive DEEP_SLEEP
    if "L1-1" in cmd_map:
        assert cmd_map["L1-1"] != TargetState.DEEP_SLEEP

    # L1-3 is CHARGING: must NEVER receive any command
    assert "L1-3" not in cmd_map


def test_rl_policy_speed_benchmark():
    policy = RLPolicy()
    t = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    cps = [
        CpView(
            f"L{i // 4}-{i % 4 + 1}",
            f"L{i // 4}",
            PowerClass.DC_50 if i % 2 == 0 else PowerClass.AC_22,
            ["CCS"],
            State.IDLE,
            300,
        )
        for i in range(1500)
    ]
    state = SimState(
        t=t,
        cps=cps,
        signals=pd.DataFrame(columns=["ts", "location_id", "signal", "value", "eta_ts"]),
        context={"B": {"temp_c": 18.0, "precip_mm": 0.0, "wind_kmh": 5.0, "is_holiday": False}},
        forecast=None,
    )
    # Warmup
    policy.decide(state)

    t0 = time.perf_counter()
    commands = policy.decide(state)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert isinstance(commands, list)
    # Must be under 50 ms per spec 02 §7
    assert elapsed_ms < 50.0, f"decide() took {elapsed_ms:.2f} ms (must be < 50 ms)"


def test_train_ppo_smoke(tmp_path):
    model_path = tmp_path / "ppo_test.zip"
    meta_path = tmp_path / "ppo_test_meta.json"

    # Fast 576 timesteps (2 rollouts on 1 env)
    model, meta = train_ppo(
        total_timesteps=576,
        n_envs=1,
        output_model_path=model_path,
        output_meta_path=meta_path,
    )
    assert model_path.exists()
    assert meta_path.exists()
    assert model_path.stat().st_size < 20 * 1024 * 1024

    with open(meta_path, encoding="utf-8") as f:
        meta_loaded = json.load(f)
    assert meta_loaded["policy_name"] == "rl_ppo"
    assert meta_loaded["observation_dim"] == 31
    assert "val_mean_reward" in meta_loaded["metrics"]

    # Verify reloading in RLPolicy
    reloaded_policy = RLPolicy(model_path=model_path)
    state = _make_test_simstate()
    cmds = reloaded_policy.decide(state)
    assert isinstance(cmds, list)
