"""Double DQN with action masking on top of stable_baselines3.DQN (decision 0017 mask rules)."""

from __future__ import annotations

import numpy as np
import torch as th
from stable_baselines3 import DQN
from torch.nn import functional as F

from sim.obs import OBS_FIELDS

_NO_SLEEP = [OBS_FIELDS.index(f"state_{s}") for s in ("charging", "waking", "offline")]
_AC = OBS_FIELDS.index("power_ac_22")
_NEG = -1e9


def mask_from_obs(obs: th.Tensor) -> th.Tensor:
    """Valid actions (IDLE, STANDBY, DEEP_SLEEP) as a bool tensor, from observation columns."""
    busy = obs[:, _NO_SLEEP].sum(dim=1) > 0.5
    ac = obs[:, _AC] > 0.5
    return th.stack([th.ones_like(busy), ~busy, ~busy & ~ac], dim=1)


class MaskedDoubleDQN(DQN):
    """DQN whose greedy, exploratory and bootstrap actions are all restricted to valid ones."""

    def _masked_q(self, net, obs: th.Tensor) -> th.Tensor:
        return net(obs).masked_fill(~mask_from_obs(obs), _NEG)

    def _act(self, observation, epsilon: float) -> np.ndarray:
        self.policy.set_training_mode(False)
        obs, vectorized = self.policy.obs_to_tensor(observation)
        with th.no_grad():
            mask = mask_from_obs(obs)
            actions = self._masked_q(self.q_net, obs).argmax(dim=1).cpu().numpy()
        if epsilon > 0:
            rng = self._explore_rng
            valid = mask.cpu().numpy()
            rand = rng.random(len(actions)) < epsilon
            # uniform over valid actions: argmax of random scores restricted to the mask
            pick = np.where(valid, rng.random(valid.shape), -1.0).argmax(axis=1)
            actions = np.where(rand, pick, actions)
        return actions if vectorized else actions.squeeze(0)

    @property
    def _explore_rng(self) -> np.random.Generator:
        if getattr(self, "_rng", None) is None:
            self._rng = np.random.default_rng(0 if self.seed is None else int(self.seed))
        return self._rng

    def _excluded_save_params(self) -> list[str]:
        return [*super()._excluded_save_params(), "_rng"]

    def predict(self, observation, state=None, episode_start=None, deterministic=False):
        eps = 0.0 if deterministic else float(self.exploration_rate)
        return self._act(observation, eps), state

    def _sample_action(self, learning_starts, action_noise=None, n_envs=1):
        eps = 1.0 if self.num_timesteps < learning_starts else float(self.exploration_rate)
        actions = self._act(self._last_obs, eps)
        return actions, actions

    def train(self, gradient_steps: int, batch_size: int = 100) -> None:
        self.policy.set_training_mode(True)
        self._update_learning_rate(self.policy.optimizer)
        losses = []
        for _ in range(gradient_steps):
            data = self.replay_buffer.sample(batch_size, env=self._vec_normalize_env)
            discounts = data.discounts if data.discounts is not None else self.gamma
            with th.no_grad():
                # Double DQN: online net picks the valid action, target net values it.
                nxt = data.next_observations
                best = self._masked_q(self.q_net, nxt).argmax(dim=1, keepdim=True)
                q_next = self.q_net_target(nxt).gather(1, best)
                target = data.rewards + (1 - data.dones) * discounts * q_next
            current = self.q_net(data.observations).gather(1, data.actions.long())
            loss = F.smooth_l1_loss(current, target)
            losses.append(loss.item())
            self.policy.optimizer.zero_grad()
            loss.backward()
            th.nn.utils.clip_grad_norm_(self.policy.parameters(), self.max_grad_norm)
            self.policy.optimizer.step()
        self._n_updates += gradient_steps
        self.logger.record("train/n_updates", self._n_updates, exclude="tensorboard")
        self.logger.record("train/loss", float(np.mean(losses)))


__all__ = ["MaskedDoubleDQN", "mask_from_obs"]
