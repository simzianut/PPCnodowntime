"""App-facing Policy adapters for the promoted MaskablePPO and DDQN artifacts."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from sb3_contrib import MaskablePPO

from model.ddqn import MaskedDoubleDQN, mask_from_obs
from sim.obs import build_obs
from sim.types import ACTIONS, Command, SimState, State

_SKIP = {State.CHARGING, State.WAKING, State.OFFLINE}


class _BatchPolicy:
    """decide(): one batched forward pass over all CPs, masks applied, no-op commands dropped."""

    name = ""

    def _actions(self, obs: np.ndarray, mask: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def decide(self, state: SimState) -> list[Command]:
        if not state.cps:
            return []
        obs = build_obs(state, [cp.cp_id for cp in state.cps])
        mask = mask_from_obs(torch.as_tensor(obs)).numpy()
        actions = self._actions(obs, mask)
        out: list[Command] = []
        for cp, a in zip(state.cps, actions.tolist(), strict=True):
            target = ACTIONS[a]
            if cp.state not in _SKIP and cp.state.value != target.value:
                out.append(Command(cp.cp_id, target, f"{self.name}({target.value})"))
        return out


class MaskablePPOPolicy(_BatchPolicy):
    name = "rl_maskppo"

    def __init__(self, model: MaskablePPO) -> None:
        self.model = model

    @classmethod
    def load(cls, path: str | Path = "artifacts/maskppo_policy.zip") -> MaskablePPOPolicy:
        return cls(MaskablePPO.load(str(path), device="cpu"))

    def _actions(self, obs, mask):
        return self.model.predict(obs, action_masks=mask, deterministic=True)[0]


class DDQNPolicy(_BatchPolicy):
    name = "rl_ddqn"

    def __init__(self, model: MaskedDoubleDQN) -> None:
        self.model = model

    @classmethod
    def load(cls, path: str | Path = "artifacts/ddqn_policy.zip") -> DDQNPolicy:
        return cls(MaskedDoubleDQN.load(str(path), device="cpu"))

    def _actions(self, obs, mask):
        return self.model.predict(obs, deterministic=True)[0]
