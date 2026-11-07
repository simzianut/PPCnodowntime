"""Evaluation metrics for demand forecaster (spec 02 §2).
Includes fast NumPy implementations of ROC AUC and Brier score.
"""

from __future__ import annotations

import numpy as np


def brier_score(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    """Compute mean squared error between probabilities and binary outcomes."""
    yt = np.asarray(y_true, dtype=float)
    yp = np.asarray(y_prob, dtype=float)
    return float(np.mean((yp - yt) ** 2))


def roc_auc_score(y_true: np.ndarray, y_score: np.ndarray) -> float:
    """Compute Area Under the Receiver Operating Characteristic Curve (ROC AUC)."""
    yt = np.asarray(y_true, dtype=int)
    ys = np.asarray(y_score, dtype=float)
    pos = yt == 1
    n_pos = int(np.sum(pos))
    n_neg = len(yt) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5
    # Rank order
    ranks = np.argsort(np.argsort(ys)) + 1
    u = np.sum(ranks[pos]) - n_pos * (n_pos + 1) / 2.0
    return float(u / (n_pos * n_neg))


def evaluate_predictions(
    y_true: dict[str, np.ndarray],
    y_pred: dict[str, np.ndarray],
) -> dict[str, dict[str, float]]:
    """Compute metrics across horizons (y15, y30, y60)."""
    metrics: dict[str, dict[str, float]] = {}
    for horizon in ("y15", "y30", "y60"):
        if horizon in y_true and horizon in y_pred:
            yt = y_true[horizon]
            yp = y_pred[horizon]
            metrics[horizon] = {
                "auc": round(roc_auc_score(yt, yp), 4),
                "brier": round(brier_score(yt, yp), 4),
                "mean_pred": round(float(np.mean(yp)), 4),
                "actual_rate": round(float(np.mean(yt)), 4),
            }
    return metrics
