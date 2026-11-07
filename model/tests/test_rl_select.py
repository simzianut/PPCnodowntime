"""RL_AGENT resolution and PolicyName loaders, with tiny untrained models in a tmp dir."""

import gymnasium as gym
import numpy as np
import pytest

from model import rl_select
from sim.obs import OBS_DIM
from sim.types import PolicyName


class _Env(gym.Env):
    observation_space = gym.spaces.Box(-np.inf, np.inf, shape=(OBS_DIM,), dtype=np.float32)
    action_space = gym.spaces.Discrete(3)


def make_artifacts(art_dir, which=("maskppo", "ddqn")):
    """Write untrained maskppo/ddqn zips (and a ddqn meta with lambda 0.3)."""
    from sb3_contrib import MaskablePPO
    from stable_baselines3.common.vec_env import DummyVecEnv

    from model.ddqn import MaskedDoubleDQN

    art_dir.mkdir(parents=True, exist_ok=True)
    venv = DummyVecEnv([_Env])
    if "maskppo" in which:
        MaskablePPO("MlpPolicy", venv, n_steps=32, batch_size=32).save(art_dir / "maskppo_policy")
    if "ddqn" in which:
        MaskedDoubleDQN("MlpPolicy", venv).save(art_dir / "ddqn_policy")
        (art_dir / "ddqn_meta.json").write_text('{"lambda_wait": 0.3}')


def test_policy_name_values():
    for v in ("rl_maskppo", "rl_ddqn", "oracle"):
        assert PolicyName(v).value == v
    assert PolicyName.RL_PPO is PolicyName.rl_ppo
    assert len(list(PolicyName)) == 7


def test_resolve(tmp_path):
    assert rl_select.resolve_rl_agent(tmp_path, env="auto") == "ppo"
    assert rl_select.resolve_rl_agent(tmp_path, env="") == "ppo"
    make_artifacts(tmp_path, ("maskppo",))
    assert rl_select.resolve_rl_agent(tmp_path, env="auto") == "maskppo"
    for v in ("ppo", "maskppo", "ddqn"):
        assert rl_select.resolve_rl_agent(tmp_path, env=v) == v
    with pytest.raises(ValueError, match="RL_AGENT"):
        rl_select.resolve_rl_agent(tmp_path, env="nope")


def test_resolve_reads_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("RL_AGENT", "ddqn")
    assert rl_select.resolve_rl_agent(tmp_path) == "ddqn"


def test_load_and_names(tmp_path):
    make_artifacts(tmp_path)
    assert rl_select.load_policy("rl_maskppo", tmp_path).name == "rl_maskppo"
    assert rl_select.load_policy("rl_ddqn", tmp_path).name == "rl_ddqn"
    # auto picks maskppo for the rl_ppo slot, but the recorded name stays rl_ppo
    assert rl_select.load_policy("rl_ppo", tmp_path).name == "rl_ppo"
    assert rl_select.lambdas("rl_ddqn", tmp_path) == [0.3]
    assert len(rl_select.lambdas("rl_maskppo", tmp_path)) == 1  # default_lambda


def test_missing_artifact(tmp_path):
    for n in ("rl_ppo", "rl_maskppo", "rl_ddqn"):
        with pytest.raises(rl_select.PolicyUnavailable, match="missing"):
            rl_select.check(n, tmp_path)
