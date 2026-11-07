"""Double DQN with action masking (decisions 0017, 0051)."""

from __future__ import annotations

import time
from functools import partial

import torch
from stable_baselines3.common.vec_env import DummyVecEnv

from model.ddqn import MaskedDoubleDQN
from model.experiments.registry import register
from model.experiments.runner import RunContext
from model.rl_common import ValidationCallback, build_eval_setup, finalize, make_env, perf_info
from sim.config import sim_config


def _act_fn(model: MaskedDoubleDQN):
    def act(obs, mask) -> int:
        return int(model.predict(obs, deterministic=True)[0])

    return act


@register(
    "rl_ddqn",
    kind="rl",
    promote={"model/ddqn_policy.zip": "ddqn_policy.zip", "model/ddqn_meta.json": "ddqn_meta.json"},
)
def train(ctx: RunContext) -> dict:
    p = ctx.config.get("params", {})
    torch.set_num_threads(int(p.get("torch_threads", 1)))
    setup = build_eval_setup(ctx)
    fns = [
        partial(
            make_env,
            setup.forecasts,
            setup.active,
            int(p.get("n_val_days", 60)),
            float(p.get("dc_oversample", 1.0)),
            setup.lambda_wait,
            ctx.seed * 1000 + rank,
        )
        for rank in range(int(p.get("n_envs", 1)))
    ]
    vec = DummyVecEnv(fns)
    total = int(ctx.config.get("total_timesteps", 1_000_000))
    model = MaskedDoubleDQN(
        "MlpPolicy",
        vec,
        policy_kwargs={"net_arch": [int(x) for x in p.get("net_arch", [64, 64])]},
        learning_rate=float(p.get("learning_rate", 1e-3)),
        buffer_size=int(p.get("buffer_size", 200_000)),
        learning_starts=int(p.get("learning_starts", 5_000)),
        batch_size=int(p.get("batch_size", 128)),
        gamma=float(p.get("gamma", 0.99)),
        train_freq=int(p.get("train_freq", 4)),
        gradient_steps=int(p.get("gradient_steps", 1)),
        target_update_interval=int(p.get("target_update_interval", 2_000)),
        exploration_fraction=float(p.get("exploration_fraction", 0.3)),
        exploration_initial_eps=float(p.get("exploration_initial_eps", 1.0)),
        exploration_final_eps=float(p.get("exploration_final_eps", 0.05)),
        seed=ctx.seed,
        device=ctx.device,
        verbose=0,
    )
    cb = ValidationCallback(
        setup.env,
        setup.episodes,
        _act_fn,
        ctx.path("checkpoints"),
        int(p.get("eval_every_steps", 100_000)),
        ctx.log,
    )
    t0 = time.perf_counter()
    try:
        model.learn(total_timesteps=total, callback=cb)
    finally:
        vec.close()
    perf = perf_info(cb, model.num_timesteps, time.perf_counter() - t0)
    ctx.log.info("perf %s", perf)
    extra = {
        "model": "rl_ddqn",
        "lambda_wait": setup.lambda_wait,
        "default_lambda": sim_config().default_lambda,
        "total_timesteps": int(model.num_timesteps),
        "seed": ctx.seed,
    }
    return finalize(
        ctx,
        "ddqn_policy.zip",
        "ddqn_meta.json",
        lambda path: MaskedDoubleDQN.load(path, device="cpu"),
        _act_fn,
        cb,
        perf,
        extra,
    )
