"""Trainer registry. Add a model: one line in MODULES, and @register on its function."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass, field

# model name -> "module:function". Imported only when that model runs.
MODULES: dict[str, str] = {
    "smoke": "model.experiments.smoke:train",
}


@dataclass
class TrainerSpec:
    fn: Callable
    kind: str
    promote: dict[str, str] = field(default_factory=dict)


_REGISTERED: dict[str, TrainerSpec] = {}


def register(model_name: str, *, kind: str, promote: dict[str, str] | None = None):
    """Decorator. `promote` maps a file in the run dir to its artifacts/ file name."""

    def deco(fn):
        _REGISTERED[model_name] = TrainerSpec(fn, kind, dict(promote or {}))
        return fn

    return deco


def get_trainer(model_name: str) -> TrainerSpec:
    if model_name not in _REGISTERED:
        target = MODULES.get(model_name)
        if target is None:
            raise KeyError(f"unknown model {model_name!r}; known: {sorted(MODULES)}")
        importlib.import_module(target.split(":")[0])
    if model_name not in _REGISTERED:
        raise KeyError(f"module for {model_name!r} did not call register()")
    return _REGISTERED[model_name]
