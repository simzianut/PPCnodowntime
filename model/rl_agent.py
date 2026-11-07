"""PPO reinforcement learning decision agent for charge point sleep scheduling (spec 02 §4)."""

from __future__ import annotations

from pathlib import Path

import gymnasium as gym
import numpy as np
import torch
from stable_baselines3 import PPO

from model.types import (
    Command,
    Forecaster,
    Policy,
    PolicyName,
    SimState,
    State,
)
from sim.obs import OBS_DIM, build_obs
from sim.types import ACTIONS

DEFAULT_PPO_PATH = Path("artifacts/ppo_policy.zip")
_SKIP_STATES = {State.CHARGING, State.WAKING, State.OFFLINE}
_AC_COL = 28


class _DummyObsEnv(gym.Env):
    def __init__(self) -> None:
        super().__init__()
        self.observation_space = gym.spaces.Box(-np.inf, np.inf, shape=(OBS_DIM,), dtype=np.float32)
        self.action_space = gym.spaces.Discrete(len(ACTIONS))


class RLPolicy(Policy):
    """PPO-based sleep scheduling policy for charge points (spec 02 §4)."""

    name = PolicyName.RL_PPO.value

    def __init__(
        self,
        model_path: Path | str | None = None,
        model: PPO | None = None,
        forecaster: Forecaster | None = None,
        lambda_wait: float = 0.1,
    ) -> None:
        self.lambda_wait = float(lambda_wait)
        self.forecaster = forecaster
        if model is not None:
            self.model = model
        elif model_path is not None and Path(model_path).exists():
            self.model = PPO.load(str(model_path))
        elif DEFAULT_PPO_PATH.exists():
            self.model = PPO.load(str(DEFAULT_PPO_PATH))
        else:
            dummy = _DummyObsEnv()
            arch = dict(net_arch=dict(pi=[64, 64], vf=[64, 64]))
            self.model = PPO("MlpPolicy", dummy, policy_kwargs=arch, verbose=0)

    def decide(self, state: SimState) -> list[Command]:
        if not state.cps:
            return []

        cp_ids = [cp.cp_id for cp in state.cps]
        eval_state = state
        if eval_state.forecast is None and self.forecaster is not None:
            fc_df = self.forecaster.predict(state)
            eval_state = SimState(
                t=state.t,
                cps=state.cps,
                signals=state.signals,
                context=state.context,
                forecast=fc_df,
            )

        obs = build_obs(eval_state, cp_ids)
        obs_tensor = torch.as_tensor(obs, dtype=torch.float32)

        with torch.no_grad():
            latent_pi, _ = self.model.policy.mlp_extractor(obs_tensor)
            logits = self.model.policy.action_net(latent_pi)
            is_ac = obs_tensor[:, _AC_COL] > 0.5
            logits[is_ac, 2] = -1e9
            actions = logits.argmax(dim=-1).cpu().numpy()

        commands: list[Command] = []
        for i, cp in enumerate(state.cps):
            if cp.state in _SKIP_STATES:
                continue
            target = ACTIONS[int(actions[i])]
            if cp.state.value != target.value:
                reason = f"rl_ppo(action={target.value})"
                commands.append(Command(cp_id=cp.cp_id, target_state=target, reason=reason))

        return commands
