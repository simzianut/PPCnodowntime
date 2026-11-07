"""Shared plumbing for forecaster trainers: dataset access and uniform output files."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from model import forecast_data as fd
from model import forecast_eval as fe
from model.experiments.runner import RunContext


def targets_of(data: fd.ForecastData, split: str) -> dict:
    y = data.y(split)
    return {"y15": y[:, 0], "y30": y[:, 1], "y60": y[:, 2], "n60": data.n60(split)}


def fix_monotone(pred: dict) -> tuple[dict, dict]:
    (a, b, c), viol = fe.enforce_monotone(pred["p15"], pred["p30"], pred["p60"])
    return {"p15": a, "p30": b, "p60": c, "n60": np.asarray(pred["n60"], np.float64)}, viol


def write_forecasts(ctx: RunContext, data: fd.ForecastData, pred: dict) -> None:
    df = pd.DataFrame(
        {
            "ts": data.timestamps("test"),
            "cp_id": data.cp_ids("test"),
            "p_arrival_15": pred["p15"].astype(np.float32),
            "p_arrival_30": pred["p30"].astype(np.float32),
            "p_arrival_60": pred["p60"].astype(np.float32),
            "expected_arrivals_60": pred["n60"].astype(np.float32),
        }
    )
    df.to_parquet(ctx.path("forecasts.parquet"), index=False)


def finish(
    ctx: RunContext,
    data: fd.ForecastData,
    model: str,
    names: list[str],
    preds: dict[str, dict],
    train_time_s: float,
    extra: dict | None = None,
) -> dict:
    """Evaluate val/test, write forecasts.parquet and timing.json, return metrics dict."""
    p = fd.data_params(ctx.config)
    metrics: dict = {
        "model": model,
        "features": names,
        "feature_groups": list(p["feature_groups"]),
        "signals": (ctx.config.get("data") or {}).get("signals", "signals.parquet"),
        "sizes": {f"{s}_cpdays": int((data.cpdays["split"] == s).sum()) for s in fd.SPLITS},
        "violations": {},
    }
    for split in ("val", "test"):
        pred, viol = fix_monotone(preds[split])
        metrics["violations"][split] = viol
        metrics[split] = fe.evaluate(pred, targets_of(data, split), data.meta(split))
        if split == "test":
            write_forecasts(ctx, data, pred)
    metrics.update(extra or {})
    ctx.path("timing.json").write_text(json.dumps({"train_time_s": round(train_time_s, 2)}))
    return metrics
