"""Hindsight oracle bound: evaluates the oracle on the fixed validation episodes (no training)."""

from __future__ import annotations

import time

from model.experiments.registry import register
from model.experiments.runner import RunContext
from model.rl_common import build_eval_setup, evaluate_episodes


@register("oracle", kind="bound")
def train(ctx: RunContext) -> dict:
    setup = build_eval_setup(ctx)
    t0 = time.perf_counter()
    res = evaluate_episodes(lambda obs, mask: 0, setup.episodes, setup.env, refs=True)
    return {"val": res, "perf": {"eval_s": round(time.perf_counter() - t0, 1)}}
