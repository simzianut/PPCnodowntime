"""RL plumbing shared by rl_maskppo and rl_ddqn: episode sets, env, evaluation, validation."""

from __future__ import annotations

import json
import logging
import shutil
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import gymnasium
import numpy as np
import pandas as pd
from stable_baselines3.common.callbacks import BaseCallback

from model.cost_rule import CostRulePolicy
from model.oracle import HindsightOraclePolicy
from sim.baselines import AlwaysOn
from sim.config import sim_config
from sim.data import SimData
from sim.engine import ALWAYS_ON, Engine
from sim.forecast import ZeroForecaster
from sim.gym_env import (
    _EXO,
    _PC_INDEX,
    _POWER,
    _SIB_TOTAL,
    AGENT,
    ChargePointEnv,
    _AgentPolicy,
    _CachedForecaster,
    default_data,
)
from sim.metrics import PolicyMetrics
from sim.obs import OBS_DIM, OBS_FIELDS
from sim.types import ACTIONS, POWER_CLASSES, Command

log = logging.getLogger("model.rl_common")
COST_RULE, ORACLE = "cost_rule", "oracle"
_DATA_OVERRIDE: SimData | None = None  # tests inject a small synthetic bundle
_DATA_CACHE: dict[str, SimData] = {}


def get_data(forecasts: str | Path | None = None) -> SimData:
    """The shared SimData, with optional forecasts.parquet loaded into it (else Zero)."""
    key = str(forecasts)
    if _DATA_OVERRIDE is None and key in _DATA_CACHE:
        return _DATA_CACHE[key]
    base = _DATA_OVERRIDE if _DATA_OVERRIDE is not None else default_data(0)
    if forecasts:
        base = replace(base, forecasts=pd.read_parquet(forecasts))
    if _DATA_OVERRIDE is None:
        _DATA_CACHE[key] = base
    return base


def split_days(data: SimData, n_val_days: int) -> tuple[list[date], list[date]]:
    """Train-split days minus the last n_val_days (validation). Test days are never used."""
    days = data.days("train")
    if len(days) <= n_val_days:
        raise ValueError(f"{len(days)} train days, need more than n_val_days={n_val_days}")
    return days[:-n_val_days], days[-n_val_days:]


class EpisodeSampler:
    """(cp, day) pairs over active CP-days, with optional DC oversampling."""

    def __init__(
        self,
        data: SimData,
        days: Sequence[date],
        active: pd.DataFrame | None = None,
        dc_oversample: float = 1.0,
    ) -> None:
        cps = data.cps.drop_duplicates("cp_id")
        ids = [str(c) for c in cps["cp_id"]]
        pc = dict(zip(ids, cps["power_class"].astype(str), strict=True))
        if active is None:
            grid = pd.MultiIndex.from_product([ids, list(days)], names=["cp_id", "date"])
            pairs = grid.to_frame(index=False)
        else:
            pairs = active[active["active"] & active["date"].isin(set(days))]
            pairs = pairs[pairs["cp_id"].astype(str).isin(set(ids))][["cp_id", "date"]]
        if pairs.empty:
            raise ValueError("no active (cp, day) pairs to sample")
        pairs = pairs.sort_values(["date", "cp_id"], kind="mergesort").reset_index(drop=True)
        self.cp_ids = pairs["cp_id"].astype(str).to_numpy()
        self.dates = pairs["date"].to_numpy()
        is_dc = np.array([pc[c] != "AC_22" for c in self.cp_ids])
        self.power_class = np.array([pc[c] for c in self.cp_ids])
        self.weights = np.where(is_dc, float(dc_oversample), 1.0)
        self._cum = np.cumsum(self.weights)

    def __len__(self) -> int:
        return len(self.cp_ids)

    def sample(self, rng: np.random.Generator) -> tuple[str, date]:
        i = int(np.searchsorted(self._cum, rng.random() * self._cum[-1], side="right"))
        i = min(i, len(self) - 1)
        return str(self.cp_ids[i]), self.dates[i]

    def fixed_episodes(self, n: int, seed: int) -> list[tuple[str, date]]:
        """n distinct episodes drawn with `seed` (independent of the run seed), sorted."""
        n = min(int(n), len(self))
        rng = np.random.default_rng(seed)
        idx = rng.choice(len(self), size=n, replace=False, p=self.weights / self.weights.sum())
        return sorted((str(self.cp_ids[i]), self.dates[i]) for i in idx)


def load_active(path: str | Path | None) -> pd.DataFrame | None:
    if path is None:
        return None
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


class EpisodeEnv(ChargePointEnv):
    """ChargePointEnv sampling only active CP-days; can also run reference policies.

    track: "none" (training), "basic" (+always_on) or "refs" (+always_on, cost_rule, oracle).
    All policies share one Engine, so their numbers are for the same episode.
    """

    def __init__(
        self,
        sampler: EpisodeSampler,
        days: Sequence[date],
        data: SimData,
        lambda_wait: float | None = None,
        seed: int = 0,
        track: str = "none",
    ) -> None:
        super().__init__("train", lambda_wait, None, seed, days, data)
        self.sampler = sampler
        self.track = track
        self._cost = None

    def reset(self, *, seed=None, options=None):
        gymnasium.Env.reset(self, seed=seed)
        if options and "cp_id" in options:
            cp_id, day = str(options["cp_id"]), options["day"]
        else:
            cp_id, day = self.sampler.sample(self.np_random)
        self.cp_id, self.day = cp_id, day
        sl = self._idx.slice(self._idx.cp_loc[cp_id], day)
        inner = sl.forecasts if sl.forecasts is not None else ZeroForecaster()
        self._agent = _AgentPolicy(self.sibling_policy, cp_id)
        self._slice = sl
        policies = [self._agent]
        if self.track != "none":
            policies.append(AlwaysOn())
        if self.track == "refs":
            if self._cost is None:
                mapl = sim_config().min_awake_per_location
                self._cost = CostRulePolicy(
                    lambda_wait=self.lambda_wait, min_awake_per_location=mapl
                )
            policies += [self._cost, HindsightOraclePolicy(sl.data.sessions)]
        self.engine = Engine(
            sl.data, day, policies, forecaster=_CachedForecaster(inner), cp_ids=sl.cp_ids
        )
        self._i = sl.cp_ids.index(cp_id)
        self._static = np.zeros(OBS_DIM - _EXO, dtype=np.float32)
        self._static[_SIB_TOTAL - _EXO] = min((len(sl.cp_ids) - 1) / 10, 1)
        self._static[_POWER + _PC_INDEX[self._idx.cp_pc[cp_id]] - _EXO] = 1
        self._norm_cp = float(self.engine.idle_w[self._i]) * self.slot_s / 3.6e6
        self._observe()
        return self._obs, self._info(0)

    def step(self, action):
        if self.engine is None or self.engine.done:
            raise RuntimeError("call reset() first")
        a = int(action)
        if self.action_masks()[a]:
            self._agent.pending = Command(self.cp_id, ACTIONS[a], reason="rl")
        res = self.engine.step()
        i = self._i
        e, w = float(res.overhead_kwh[AGENT][i]), float(res.wait_min[AGENT][i])
        reward = -(e + self.lambda_wait * w) / self._norm_cp
        self._observe()
        info = self._info(res.slot)
        info["overhead_kwh"], info["wait_min"] = e, w
        if self.track != "none":
            info["policies"] = {
                name: {
                    "overhead_kwh": float(res.overhead_kwh[name][i]),
                    "waits": [
                        ev["wait_min"]
                        for ev in res.events[name]
                        if ev["type"] == "arrival" and ev["cp_id"] == self.cp_id
                    ],
                }
                for name in res.overhead_kwh
            }
        return self._obs, reward, self.engine.done, False, info


def make_env(
    forecasts: str | None,
    active_path: str | None,
    n_val_days: int,
    dc_oversample: float,
    lambda_wait: float,
    seed: int,
) -> EpisodeEnv:
    """Training env factory (picklable args only, so subprocess envs can rebuild it)."""
    data = get_data(forecasts)
    train_days, _ = split_days(data, n_val_days)
    sampler = EpisodeSampler(data, train_days, load_active(active_path), dc_oversample)
    return EpisodeEnv(sampler, train_days, data, lambda_wait, seed)


# ---- evaluation --------------------------------------------------------------------------------

ActFn = Callable[[np.ndarray, np.ndarray], int]
_KPI_KEYS = (
    "overhead_kwh", "saved_kwh", "saved_pct", "wait_min_total", "wait_min_mean",
    "wait_min_p95", "sessions", "waited_sessions",
)  # fmt: skip


def _kpis(acc: PolicyMetrics, base: PolicyMetrics, returns: list[float]) -> dict:
    k = acc.kpis(base.overhead_wh)
    out = {key: k[key] for key in _KPI_KEYS}
    out["always_on_overhead_kwh"] = round(base.overhead_wh / 1000.0, 4)
    out["mean_reward"] = float(np.mean(returns)) if returns else 0.0
    out["episodes"] = len(returns)
    return out


def evaluate_episodes(
    act_fn: ActFn,
    episodes: Sequence[tuple[str, date]],
    env: EpisodeEnv,
    refs: bool = True,
) -> dict:
    """Run the agent (act_fn(obs, mask) -> action) and references on identical episodes."""
    env.track = "refs" if refs else "basic"
    pc_of = env._idx.cp_pc
    groups: dict[str, dict[str, PolicyMetrics]] = {}
    rets: dict[str, dict[str, list[float]]] = {}
    for cp_id, day in episodes:
        obs, _ = env.reset(options={"cp_id": cp_id, "day": day})
        ep: dict[str, float] = {}
        met: dict[str, PolicyMetrics] = {}
        done = False
        while not done:
            a = act_fn(obs, env.action_masks())
            obs, r, done, _, info = env.step(a)
            ep[AGENT] = ep.get(AGENT, 0.0) + r
            for name, v in info["policies"].items():
                m = met.setdefault(name, PolicyMetrics())
                m.add_energy_wh(v["overhead_kwh"] * 1000.0)
                for w in v["waits"]:
                    m.add_session(w)
                if name != AGENT:
                    cost = v["overhead_kwh"] + env.lambda_wait * sum(v["waits"])
                    ep[name] = ep.get(name, 0.0) - cost / env._norm_cp
        for g in ("all", str(pc_of[cp_id])):
            for name, m in met.items():
                groups.setdefault(g, {}).setdefault(name, PolicyMetrics()).merge(m)
                rets.setdefault(g, {}).setdefault(name, []).append(ep[name])
    out: dict = {"episodes": len(episodes)}
    names = [AGENT, ALWAYS_ON] + ([COST_RULE, ORACLE] if refs else [])
    for name in names:
        out[name] = {g: _kpis(groups[g][name], groups[g][ALWAYS_ON], rets[g][name]) for g in groups}
    if refs:
        out["pct_of_oracle_savings"] = {
            g: (
                round(100.0 * out[AGENT][g]["saved_kwh"] / out[ORACLE][g]["saved_kwh"], 2)
                if out[ORACLE][g]["saved_kwh"] > 0
                else None
            )
            for g in groups
        }
    return out


class ValidationCallback(BaseCallback):
    """Every `every` steps: validate on the fixed episodes, keep best.zip and last.zip."""

    def __init__(
        self,
        env: EpisodeEnv,
        episodes: Sequence[tuple[str, date]],
        act_fn_for: Callable[[object], ActFn],
        ckpt_dir: Path,
        every: int,
        logger: logging.Logger,
    ) -> None:
        super().__init__()
        self.env, self.episodes, self.act_fn_for = env, episodes, act_fn_for
        self.ckpt_dir, self.every, self.log = ckpt_dir, every, logger
        self.next_eval = every
        self.best = -np.inf
        self.history: list[dict] = []
        self.eval_s = 0.0
        ckpt_dir.mkdir(parents=True, exist_ok=True)

    def _validate(self) -> None:
        t0 = time.perf_counter()
        res = evaluate_episodes(self.act_fn_for(self.model), self.episodes, self.env, refs=False)
        a = res[AGENT]["all"]
        row = {
            "step": int(self.num_timesteps),
            "val_reward": a["mean_reward"],
            "saved_pct": a["saved_pct"],
            "wait_min_mean": a["wait_min_mean"],
        }
        self.history.append(row)
        self.log.info("validation %s", row)
        self.model.save(self.ckpt_dir / "last.zip")
        if row["val_reward"] > self.best:
            self.best = row["val_reward"]
            self.model.save(self.ckpt_dir / "best.zip")
        self.eval_s += time.perf_counter() - t0

    def _on_step(self) -> bool:
        if self.num_timesteps >= self.next_eval:
            self._validate()
            self.next_eval = (self.num_timesteps // self.every + 1) * self.every
        return True

    def _on_training_end(self) -> None:
        if not self.history or self.history[-1]["step"] != self.num_timesteps:
            self._validate()


def finalize(
    ctx,
    model_name: str,
    meta_name: str,
    load_best: Callable[[Path], object],
    act_fn_for: Callable[[object], ActFn],
    cb: ValidationCallback,
    perf: dict,
    meta_extra: dict,
) -> dict:
    """Evaluate best.zip with references, write model/ files and return metrics."""
    best = ctx.path("checkpoints", "best.zip")
    ctx.path("model").mkdir(exist_ok=True)
    shutil.copy2(best, ctx.path("model", model_name))
    res = evaluate_episodes(act_fn_for(load_best(best)), cb.episodes, cb.env, refs=True)
    metrics = {"val": res, "history": cb.history, "best_val_reward": cb.best, "perf": perf}
    meta = {
        "run_id": ctx.run_dir.name,
        "obs_fields": OBS_FIELDS,
        "obs_normalisation": "none (obs already scaled to ~[0,1])",
        "actions": [a.value for a in ACTIONS],
        "power_classes": [p.value for p in POWER_CLASSES],
        "val_metrics": res[AGENT]["all"],
        "pct_of_oracle_savings": res["pct_of_oracle_savings"]["all"],
        **meta_extra,
    }
    ctx.path("model", meta_name).write_text(json.dumps(meta, indent=2, sort_keys=True))
    return metrics


def perf_info(cb: ValidationCallback, steps: int, wall_s: float) -> dict:
    """Throughput; the only non-deterministic part of metrics.json."""
    train_s = max(wall_s - cb.eval_s, 1e-9)
    return {
        "train_env_steps_per_s": round(steps / train_s, 1),
        "train_s": round(train_s, 1),
        "validation_s": round(cb.eval_s, 1),
    }


@dataclass
class EvalSetup:
    forecasts: str | None
    active: str | None
    lambda_wait: float
    env: EpisodeEnv
    episodes: list[tuple[str, date]]


def build_eval_setup(ctx) -> EvalSetup:
    """Validation env and the fixed episode list (same for every agent and seed)."""
    p = ctx.config.get("params", {})
    d = ctx.config.get("data", {}) or {}
    fc = str(ctx.data_path("forecasts")) if d.get("forecasts") else None
    if fc is None:
        ctx.log.warning("NO forecasts configured: the env uses ZeroForecaster (all p_arrival=0)")
    active = str(ctx.data_path("cp_active_days")) if d.get("cp_active_days") else None
    data = get_data(fc)
    lam = float(p.get("lambda_wait", sim_config().default_lambda))
    _, val_days = split_days(data, int(p.get("n_val_days", 60)))
    dco = float(p.get("dc_oversample", 1.0))
    sampler = EpisodeSampler(data, val_days, load_active(active), dco)
    n, seed = int(p.get("n_val_episodes", 400)), int(p.get("eval_seed", 12345))
    episodes = sampler.fixed_episodes(n, seed)
    env = EpisodeEnv(sampler, val_days, data, lam, seed=0)
    ctx.log.info(
        "val days=%d episodes=%d lambda=%s dc_x=%s", len(val_days), len(episodes), lam, dco
    )
    return EvalSetup(fc, active, lam, env, episodes)
