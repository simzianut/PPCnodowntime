"""MaskablePPO agent for the one-CP sleep environment (decisions 0017, 0051)."""

from __future__ import annotations

import sys
import time
from functools import partial

import torch
from sb3_contrib import MaskablePPO
from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv

from model.experiments.registry import register
from model.experiments.runner import RunContext
from model.rl_common import ValidationCallback, build_eval_setup, finalize, make_env, perf_info
from sim.config import sim_config


def _act_fn(model: MaskablePPO):
    def act(obs, mask) -> int:
        return int(model.predict(obs, action_masks=mask, deterministic=True)[0])

    return act


@register(
    "rl_maskppo",
    kind="rl",
    promote={
        "model/maskppo_policy.zip": "maskppo_policy.zip",
        "model/maskppo_meta.json": "maskppo_meta.json",
    },
)
def train(ctx: RunContext) -> dict:
    p = ctx.config.get("params", {})
    torch.set_num_threads(int(p.get("torch_threads", 1)))
    setup = build_eval_setup(ctx)
    n_envs = int(p.get("n_envs", 16))
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
        for rank in range(n_envs)
    ]
    if p.get("vec_env", "dummy") == "subproc":
        method = "fork" if sys.platform.startswith("linux") else "spawn"
        vec = SubprocVecEnv(fns, start_method=method)
    else:
        vec = DummyVecEnv(fns)
    net = [int(x) for x in p.get("net_arch", [64, 64])]
    model = MaskablePPO(
        "MlpPolicy",
        vec,
        policy_kwargs={"net_arch": {"pi": net, "vf": net}},
        n_steps=int(p.get("n_steps", 256)),
        batch_size=int(p.get("batch_size", 256)),
        n_epochs=int(p.get("n_epochs", 10)),
        gamma=float(p.get("gamma", 0.99)),
        gae_lambda=float(p.get("gae_lambda", 0.95)),
        ent_coef=float(p.get("ent_coef", 0.01)),
        learning_rate=float(p.get("learning_rate", 3e-4)),
        seed=ctx.seed,
        device=ctx.device,
        verbose=0,
    )
    total = int(ctx.config.get("total_timesteps", 1_000_000))
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
        "model": "rl_maskppo",
        "lambda_wait": setup.lambda_wait,
        "default_lambda": sim_config().default_lambda,
        "total_timesteps": int(model.num_timesteps),
        "seed": ctx.seed,
    }
    return finalize(
        ctx,
        "maskppo_policy.zip",
        "maskppo_meta.json",
        lambda path: MaskablePPO.load(path, device="cpu"),
        _act_fn,
        cb,
        perf,
        extra,
    )
