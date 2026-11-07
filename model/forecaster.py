"""LightGBM EV demand forecaster using native LightGBM engine (spec 02 §2).

Trains three calibrated binary classifiers (y15, y30, y60) and one Poisson
regressor (n60), with probability calibration via Isotonic Regression.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from model.calibration import IsotonicCalibrator
from model.features import (
    FEATURE_COLUMNS,
    extract_features_from_simstate,
)
from model.metrics import evaluate_predictions
from model.types import Forecaster, SimState


class DemandForecaster(Forecaster):
    """Calibrated native LightGBM demand forecaster conforming to Forecaster protocol."""

    def __init__(
        self,
        features: list[str] | None = None,
        num_boost_round: int = 80,
        learning_rate: float = 0.05,
        num_leaves: int = 15,
        min_data_in_leaf: int = 5,
        seed: int = 42,
    ) -> None:
        self.features = features or list(FEATURE_COLUMNS)
        self.num_boost_round = num_boost_round
        self.learning_rate = learning_rate
        self.num_leaves = num_leaves
        self.min_data_in_leaf = min_data_in_leaf
        self.seed = seed

        self.boosters: dict[str, lgb.Booster] = {}
        self.calibrators: dict[str, IsotonicCalibrator] = {
            "y15": IsotonicCalibrator(),
            "y30": IsotonicCalibrator(),
            "y60": IsotonicCalibrator(),
        }
        self.meta: dict[str, Any] = {}

    def fit(
        self,
        X_train: pd.DataFrame,
        y_train: pd.DataFrame,
        X_val: pd.DataFrame | None = None,
        y_val: pd.DataFrame | None = None,
    ) -> DemandForecaster:
        """Fit binary classifiers and Poisson regressor, then calibrate probabilities."""
        X_tr = X_train[self.features]
        # Split a slice for calibration if no validation set provided
        if X_val is None or y_val is None:
            n_split = max(1, int(len(X_tr) * 0.8))
            X_fit, y_fit = X_tr.iloc[:n_split], y_train.iloc[:n_split]
            X_cal, y_cal = X_tr.iloc[n_split:], y_train.iloc[n_split:]
        else:
            X_fit, y_fit = X_tr, y_train
            X_cal, y_cal = X_val[self.features], y_val

        # 1. Train binary classifiers for horizons
        base_binary_params = {
            "objective": "binary",
            "metric": "binary_logloss",
            "learning_rate": self.learning_rate,
            "num_leaves": self.num_leaves,
            "min_data_in_leaf": self.min_data_in_leaf,
            "seed": self.seed,
            "verbosity": -1,
        }

        for target in ("y15", "y30", "y60"):
            dtrain = lgb.Dataset(X_fit, label=y_fit[target].to_numpy())
            bst = lgb.train(base_binary_params, dtrain, num_boost_round=self.num_boost_round)
            self.boosters[target] = bst

            # Probability calibration on held-out slice
            if not X_cal.empty and len(np.unique(y_cal[target])) > 1:
                raw_probs = bst.predict(X_cal)
                calibrator = IsotonicCalibrator().fit(raw_probs, y_cal[target].to_numpy())
                self.calibrators[target] = calibrator

        # 2. Train Poisson regressor for expected arrivals at location (n60)
        poisson_params = {
            "objective": "poisson",
            "metric": "poisson",
            "learning_rate": self.learning_rate,
            "num_leaves": self.num_leaves,
            "min_data_in_leaf": self.min_data_in_leaf,
            "seed": self.seed,
            "verbosity": -1,
        }
        dtrain_p = lgb.Dataset(X_fit, label=y_fit["n60"].to_numpy())
        bst_p = lgb.train(poisson_params, dtrain_p, num_boost_round=self.num_boost_round)
        self.boosters["n60"] = bst_p

        # 3. Compute evaluation metrics
        preds: dict[str, np.ndarray] = {}
        true_dict: dict[str, np.ndarray] = {}
        for target in ("y15", "y30", "y60"):
            eval_X = X_cal if not X_cal.empty else X_fit
            eval_y = y_cal if not X_cal.empty else y_fit
            raw = self.boosters[target].predict(eval_X)
            cal = self.calibrators[target].predict(raw)
            preds[target] = cal
            true_dict[target] = eval_y[target].to_numpy()

        metrics = evaluate_predictions(true_dict, preds)
        self.meta = {
            "features": self.features,
            "metrics": metrics,
            "training_samples": len(X_train),
        }
        return self

    def predict_proba(self, X: pd.DataFrame) -> dict[str, np.ndarray]:
        """Generate calibrated probabilities and Poisson expected counts."""
        X_feats = X[self.features]
        res: dict[str, np.ndarray] = {}
        for target in ("y15", "y30", "y60"):
            if target in self.boosters:
                raw = self.boosters[target].predict(X_feats)
                res[target] = self.calibrators[target].predict(raw)
            else:
                res[target] = np.zeros(len(X_feats))

        if "n60" in self.boosters:
            res["n60"] = np.maximum(0.0, self.boosters["n60"].predict(X_feats))
        else:
            res["n60"] = np.zeros(len(X_feats))
        return res

    def predict(self, state: SimState) -> pd.DataFrame:
        """Online inference from SimState conforming to Forecaster protocol."""
        if not state.cps:
            return pd.DataFrame(
                columns=[
                    "cp_id",
                    "p_arrival_15",
                    "p_arrival_30",
                    "p_arrival_60",
                    "expected_arrivals_60",
                ]
            )

        features_df = extract_features_from_simstate(state)
        preds = self.predict_proba(features_df)

        res_df = pd.DataFrame(
            {
                "cp_id": features_df["cp_id"],
                "p_arrival_15": preds["y15"],
                "p_arrival_30": preds["y30"],
                "p_arrival_60": preds["y60"],
                "expected_arrivals_60": preds["n60"],
            }
        )
        return res_df

    def save(self, artifacts_dir: Path | str = "artifacts") -> None:
        """Save forecaster models and metadata under 20 MB."""
        out_dir = Path(artifacts_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        # Save model boosters to string dict
        models_data: dict[str, str] = {}
        for name, bst in self.boosters.items():
            models_data[name] = bst.model_to_string()

        # Combine into single compact artifacts/forecaster.lgb
        with (out_dir / "forecaster.lgb").open("w", encoding="utf-8") as f:
            json.dump(models_data, f)

        # Save metadata and calibrator knots
        calibrators_data = {k: v.to_dict() for k, v in self.calibrators.items()}
        meta_payload = {
            **self.meta,
            "calibrators": calibrators_data,
        }
        with (out_dir / "forecaster_meta.json").open("w", encoding="utf-8") as f:
            json.dump(meta_payload, f, indent=2)

    @classmethod
    def load(cls, artifacts_dir: Path | str = "artifacts") -> DemandForecaster:
        """Load trained forecaster from artifacts directory."""
        dir_path = Path(artifacts_dir)
        meta_path = dir_path / "forecaster_meta.json"
        model_path = dir_path / "forecaster.lgb"

        with meta_path.open("r", encoding="utf-8") as f:
            meta = json.load(f)

        instance = cls(features=meta.get("features"))
        instance.meta = meta

        # Restore calibrators
        for k, v in meta.get("calibrators", {}).items():
            instance.calibrators[k] = IsotonicCalibrator.from_dict(v)

        # Restore boosters
        with model_path.open("r", encoding="utf-8") as f:
            models_data = json.load(f)

        for name, model_str in models_data.items():
            instance.boosters[name] = lgb.Booster(model_str=model_str)

        return instance
