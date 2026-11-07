"""Pick and load RL agents by PolicyName; RL_AGENT chooses what serves the rl_ppo slot (0060)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from sim.config import ROOT, sim_config

log = logging.getLogger("model.rl_select")
AGENTS = ("ppo", "maskppo", "ddqn", "auto")
ART_FILES = {
    "ppo": "ppo_policy.zip",
    "maskppo": "maskppo_policy.zip",
    "ddqn": "ddqn_policy.zip",
}
META_FILES = {"ppo": "ppo_meta.json", "maskppo": "maskppo_meta.json", "ddqn": "ddqn_meta.json"}
POLICY_AGENT = {"rl_ppo": None, "rl_maskppo": "maskppo", "rl_ddqn": "ddqn"}
_models: dict[tuple[str, float], object] = {}


class PolicyUnavailable(RuntimeError):
    """Raised when a policy's artifact or dependency is missing."""


def _art(art_dir: Path | str | None) -> Path:
    return Path(art_dir) if art_dir is not None else ROOT / "artifacts"


def resolve_rl_agent(art_dir: Path | str | None = None, env: str | None = None) -> str:
    """RL_AGENT -> 'ppo' | 'maskppo' | 'ddqn'; auto = maskppo if its artifact exists else ppo."""
    v = (env if env is not None else os.environ.get("RL_AGENT", "auto")).strip().lower() or "auto"
    if v not in AGENTS:
        raise ValueError(f"RL_AGENT must be one of {list(AGENTS)}, got {v!r}")
    if v == "auto":
        return "maskppo" if (_art(art_dir) / ART_FILES["maskppo"]).exists() else "ppo"
    return v


def agent_for(name: str, art_dir: Path | str | None = None) -> str:
    """Implementation behind a PolicyName (rl_ppo follows RL_AGENT)."""
    if name not in POLICY_AGENT:
        raise KeyError(name)
    return POLICY_AGENT[name] or resolve_rl_agent(art_dir)


def check(name: str, art_dir: Path | str | None = None) -> None:
    """Raise PolicyUnavailable (with a reason) unless `name` can be loaded."""
    try:
        agent = agent_for(name, art_dir)
    except ValueError as e:
        raise PolicyUnavailable(str(e)) from e
    path = _art(art_dir) / ART_FILES[agent]
    if not path.exists():
        raise PolicyUnavailable(f"artifacts/{path.name} is missing")
    try:
        if agent == "ppo":
            import model.rl_agent  # noqa: F401
        else:
            import model.rl_policies  # noqa: F401
    except Exception as e:
        raise PolicyUnavailable(f"{agent} deps failed to import ({type(e).__name__})") from e


def lambdas(name: str, art_dir: Path | str | None = None) -> list[float]:
    """Lambda list for a policy: meta lambda_wait when present, else default_lambda."""
    agent = agent_for(name, art_dir)
    meta = _art(art_dir) / META_FILES[agent]
    try:
        return [float(json.loads(meta.read_text())["lambda_wait"])]
    except (OSError, KeyError, TypeError, ValueError):
        return [sim_config().default_lambda]


def load_policy(name: str, art_dir: Path | str | None = None, forecaster=None,
                lam: float | None = None):  # fmt: skip
    """Policy whose `name` is the requested PolicyName, even if another agent serves it."""
    check(name, art_dir)
    agent = agent_for(name, art_dir)
    path = _art(art_dir) / ART_FILES[agent]
    key = (str(path), path.stat().st_mtime)
    if agent == "ppo":
        from model.rl_agent import RLPolicy

        lam = sim_config().default_lambda if lam is None else lam
        if key in _models:
            pol = RLPolicy(model=_models[key], forecaster=forecaster, lambda_wait=lam)
        else:
            pol = RLPolicy(model_path=path, forecaster=forecaster, lambda_wait=lam)
            _models[key] = pol.model
        return pol
    from model.rl_policies import DDQNPolicy, MaskablePPOPolicy

    cls = MaskablePPOPolicy if agent == "maskppo" else DDQNPolicy
    if key in _models:
        pol = cls(_models[key])
    else:
        pol = cls.load(path)
        _models[key] = pol.model
    pol.name = name
    return pol
