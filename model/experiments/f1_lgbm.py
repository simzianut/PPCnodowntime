"""F1: LightGBM (3 binary + 1 Poisson) with isotonic calibration (decisions 0032, 0052)."""

from __future__ import annotations

import json
import time

import lightgbm as lgb
import numpy as np

from model import forecast_data as fd
from model import forecast_eval as fe
from model.experiments import forecast_common as fc
from model.experiments.registry import register
from model.experiments.runner import RunContext
from model.metrics import evaluate_predictions

DEFAULTS = {
    "num_threads": 8,
    "learning_rate": 0.08,
    "num_leaves": 63,
    "min_data_in_leaf": 200,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "max_bin": 127,
    "num_boost_round": 600,
    "early_stopping_rounds": 30,
    "calibrate": True,
}


def _params(p: dict, seed: int, objective: str) -> dict:
    return {
        "objective": objective,
        "metric": "binary_logloss" if objective == "binary" else "poisson",
        "learning_rate": p["learning_rate"],
        "num_leaves": int(p["num_leaves"]),
        "min_data_in_leaf": int(p["min_data_in_leaf"]),
        "feature_fraction": p["feature_fraction"],
        "bagging_fraction": p["bagging_fraction"],
        "bagging_freq": 1,
        "max_bin": int(p["max_bin"]),
        "num_threads": int(p["num_threads"]),
        "deterministic": True,
        "force_row_wise": True,
        "seed": seed,
        "bagging_seed": seed,
        "feature_fraction_seed": seed,
        "verbosity": -1,
    }


@register(
    "f1_lgbm",
    kind="forecaster",
    promote={
        "model/forecaster.lgb": "forecaster.lgb",
        "model/forecaster_meta.json": "forecaster_meta.json",
    },
)
def train(ctx: RunContext) -> dict:
    t0 = time.monotonic()
    p = {**DEFAULTS, **(ctx.config.get("params") or {})}
    groups = fd.data_params(ctx.config)["feature_groups"]
    names = fd.feature_names(groups)
    data = fd.get_dataset(ctx.config, ctx.log)
    Xtr, Xva, Xte = (data.X(s, names) for s in fd.SPLITS)
    ytr, yva = data.y("train"), data.y("val")
    ntr, nva = data.n60("train"), data.n60("val")
    ctx.log.info(
        "rows train=%d val=%d test=%d features=%d", len(Xtr), len(Xva), len(Xte), len(names)
    )
    boosters: dict[str, lgb.Booster] = {}
    best: dict[str, int] = {}
    for i, h in enumerate(("y15", "y30", "y60", "n60")):
        obj = "poisson" if h == "n60" else "binary"
        ltr = ntr if h == "n60" else ytr[:, i]
        lva = nva if h == "n60" else yva[:, i]
        dtr = lgb.Dataset(Xtr, label=ltr, feature_name=names, free_raw_data=False)
        dva = lgb.Dataset(Xva, label=lva, reference=dtr)
        bst = lgb.train(
            _params(p, ctx.seed, obj),
            dtr,
            num_boost_round=int(p["num_boost_round"]),
            valid_sets=[dva],
            callbacks=[lgb.early_stopping(int(p["early_stopping_rounds"]), verbose=False)],
        )
        boosters[h], best[h] = bst, bst.best_iteration
        ctx.log.info("%s best_iteration=%d", h, bst.best_iteration)
    del Xtr, dtr

    def raw(X):
        r = {
            f"p{h}": boosters[f"y{h}"].predict(X, num_iteration=best[f"y{h}"]) for h in (15, 30, 60)
        }
        r["n60"] = np.maximum(0.0, boosters["n60"].predict(X, num_iteration=best["n60"]))
        return r

    raw_val, raw_test = raw(Xva), raw(Xte)
    cals = {}
    for i, h in enumerate((15, 30, 60)):
        cals[f"y{h}"] = fe.fit_isotonic(raw_val[f"p{h}"], yva[:, i])

    def calibrated(r):
        return {**r, **{f"p{h}": cals[f"y{h}"].predict(r[f"p{h}"]) for h in (15, 30, 60)}}

    final = (
        {"val": calibrated(raw_val), "test": calibrated(raw_test)}
        if p["calibrate"]
        else {"val": raw_val, "test": raw_test}
    )
    metrics = fc.finish(ctx, data, "f1_lgbm", names, final, time.monotonic() - t0,
                        {"best_iteration": best, "calibrated": bool(p["calibrate"])})  # fmt: skip
    # uncalibrated test metrics, for the before/after comparison
    unc, _ = fc.fix_monotone(raw_test)
    metrics["test_uncalibrated"] = fe.evaluate(unc, fc.targets_of(data, "test"), data.meta("test"))
    # artifacts in edy's format (loadable by DemandForecaster.load)
    ctx.path("model").mkdir(exist_ok=True)
    ctx.path("model", "forecaster.lgb").write_text(
        json.dumps({h: b.model_to_string(num_iteration=best[h]) for h, b in boosters.items()})
    )
    val_fixed, _ = fc.fix_monotone(final["val"])
    old_metrics = evaluate_predictions(
        {f"y{h}": yva[:, i] for i, h in enumerate((15, 30, 60))},
        {f"y{h}": val_fixed[f"p{h}"] for h in (15, 30, 60)},
    )
    meta = {
        "features": names,
        "metrics": old_metrics,
        "training_samples": int(len(ytr)),
        "calibrators": {k: v.to_dict() for k, v in cals.items()},
    }
    ctx.path("model", "forecaster_meta.json").write_text(json.dumps(meta))
    imp = {
        h: dict(zip(names, (float(x) for x in b.feature_importance("gain")), strict=True))
        for h, b in boosters.items()
    }
    ctx.path("model", "feature_importance.json").write_text(json.dumps(imp, indent=2))
    return metrics
