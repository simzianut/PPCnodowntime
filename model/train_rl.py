"""PPO training pipeline on ChargePointEnv (spec 02 §4)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv

from sim.config import sim_config
from sim.gym_env import ChargePointEnv, default_data
from sim.obs import OBS_DIM, OBS_FIELDS
from sim.types import ACTIONS

ARTIFACTS_DIR = Path("artifacts")
MODEL_PATH = ARTIFACTS_DIR / "ppo_policy.zip"
META_PATH = ARTIFACTS_DIR / "ppo_meta.json"


def train_ppo(
    total_timesteps: int = 50_000,
    lambda_wait: float | None = None,
    n_envs: int = 8,
    seed: int = 42,
    output_model_path: Path | str = MODEL_PATH,
    output_meta_path: Path | str = META_PATH,
) -> tuple[PPO, dict[str, Any]]:
    cfg = sim_config()
    lam = float(cfg.default_lambda if lambda_wait is None else lambda_wait)
    data = default_data(seed=seed)
    all_train_days = sorted(data.days("train"))
    if not data.signals.empty and "ts" in data.signals.columns:
        sig_dates = set(data.signals["ts"].dt.tz_convert("Europe/Bucharest").dt.date)
        matching = [d for d in all_train_days if d in sig_dates]
        if matching:
            all_train_days = matching
    if len(all_train_days) > 3:
        train_days = all_train_days[:-2]
        val_days = all_train_days[-2:]
    else:
        train_days = all_train_days
        val_days = all_train_days

    def make_env(rank: int):
        def _init() -> ChargePointEnv:
            return ChargePointEnv(
                split="train",
                lambda_wait=lam,
                seed=seed + rank,
                days=train_days,
                data=data,
            )

        return _init

    vec_env = DummyVecEnv([make_env(i) for i in range(n_envs)])
    policy_kwargs = dict(net_arch=dict(pi=[64, 64], vf=[64, 64]))
    model = PPO(
        "MlpPolicy",
        vec_env,
        policy_kwargs=policy_kwargs,
        n_steps=288,
        batch_size=288,
        n_epochs=10,
        learning_rate=3e-4,
        gamma=0.99,
        seed=seed,
        verbose=0,
    )

    model.learn(total_timesteps=total_timesteps)

    # Evaluate on held-out validation days
    val_env = ChargePointEnv(split="train", lambda_wait=lam, days=val_days, data=data, seed=seed)
    val_rewards: list[float] = []
    val_overheads: list[float] = []
    val_waits: list[float] = []

    for _ in range(len(val_days) * 2):
        obs, info = val_env.reset()
        done = False
        ep_rew = 0.0
        ep_overhead = 0.0
        ep_wait = 0.0
        while not done:
            action, _ = model.predict(obs, deterministic=True)
            obs, reward, done, truncated, info = val_env.step(action)
            ep_rew += float(reward)
            ep_overhead += float(info.get("overhead_kwh", 0.0))
            ep_wait += float(info.get("wait_min", 0.0))
        val_rewards.append(ep_rew)
        val_overheads.append(ep_overhead)
        val_waits.append(ep_wait)

    mean_rew = float(sum(val_rewards) / max(len(val_rewards), 1))
    mean_oh = float(sum(val_overheads) / max(len(val_overheads), 1))
    mean_w = float(sum(val_waits) / max(len(val_waits), 1))

    output_model_path = Path(output_model_path)
    output_meta_path = Path(output_meta_path)
    output_model_path.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(output_model_path))

    sz = output_model_path.stat().st_size
    meta = {
        "policy_name": "rl_ppo",
        "lambda_wait": lam,
        "total_timesteps": total_timesteps,
        "n_envs": n_envs,
        "network_architecture": policy_kwargs["net_arch"],
        "observation_dim": OBS_DIM,
        "observation_fields": OBS_FIELDS,
        "action_space": [a.value for a in ACTIONS],
        "train_days": [d.isoformat() for d in train_days],
        "val_days": [d.isoformat() for d in val_days],
        "metrics": {
            "val_mean_reward": mean_rew,
            "val_mean_overhead_kwh": mean_oh,
            "val_mean_wait_min": mean_w,
        },
        "model_file": output_model_path.name,
        "file_size_bytes": sz,
        "size_mb": round(sz / (1024 * 1024), 3),
    }

    with open(output_meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    return model, meta
