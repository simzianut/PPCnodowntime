"""Shared forecaster evaluation (decision 0052): same metrics for F0, F1 and F2."""

from __future__ import annotations

import numpy as np

from model.calibration import IsotonicCalibrator
from model.metrics import brier_score

HORIZONS = {"y15": "p15", "y30": "p30", "y60": "p60"}
EPS = 1e-7


def roc_auc(y: np.ndarray, s: np.ndarray) -> float:
    """ROC AUC with tie-averaged ranks."""
    y = np.asarray(y) > 0
    n_pos = int(y.sum())
    n_neg = len(y) - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    order = np.argsort(s, kind="stable")
    ss = np.asarray(s)[order]
    new = np.r_[True, ss[1:] != ss[:-1]]
    start = np.flatnonzero(new)
    end = np.r_[start[1:], len(ss)]
    avg_rank = (start + end + 1) / 2.0  # mean of 1-based ranks start+1..end
    ranks = np.empty(len(ss))
    ranks[order] = np.repeat(avg_rank, end - start)
    return float((ranks[y].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def average_precision(y: np.ndarray, s: np.ndarray) -> float:
    """PR-AUC as average precision over distinct score thresholds."""
    y = (np.asarray(y) > 0).astype(np.float64)
    n_pos = y.sum()
    if n_pos == 0:
        return float("nan")
    order = np.argsort(-np.asarray(s), kind="stable")
    ss, yy = np.asarray(s)[order], y[order]
    tp = np.cumsum(yy)
    last = np.r_[ss[1:] != ss[:-1], True]
    tp, n = tp[last], (np.flatnonzero(last) + 1).astype(np.float64)
    prec, rec = tp / n, tp / n_pos
    return float(np.sum(np.diff(np.r_[0.0, rec]) * prec))


def log_loss(y: np.ndarray, p: np.ndarray) -> float:
    p = np.clip(np.asarray(p, dtype=np.float64), EPS, 1 - EPS)
    y = np.asarray(y, dtype=np.float64)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log1p(-p)))


def reliability(y: np.ndarray, p: np.ndarray, bins: int = 10) -> list[dict]:
    """Equal-count bins by predicted probability (probabilities are tiny, so not equal-width)."""
    order = np.argsort(p, kind="stable")
    out = []
    for i, idx in enumerate(np.array_split(order, bins)):
        if len(idx) == 0:
            continue
        out.append(
            {
                "bin": i,
                "n": int(len(idx)),
                "mean_pred": round(float(np.mean(p[idx])), 6),
                "obs_rate": round(float(np.mean(y[idx])), 6),
            }
        )
    return out


def poisson_deviance(y: np.ndarray, mu: np.ndarray) -> float:
    y = np.asarray(y, dtype=np.float64)
    mu = np.maximum(np.asarray(mu, dtype=np.float64), 1e-9)
    with np.errstate(divide="ignore", invalid="ignore"):
        term = np.where(y > 0, y * np.log(y / mu), 0.0)
    return float(2.0 * np.mean(term - (y - mu)))


def enforce_monotone(p15, p30, p60) -> tuple[tuple[np.ndarray, ...], dict]:
    """Cumulative max across horizons. Returns fixed arrays and violation rates before fixing."""
    p15, p30, p60 = (np.asarray(x, dtype=np.float64) for x in (p15, p30, p60))
    viol = {
        "p15_gt_p30": float(np.mean(p15 > p30)),
        "p30_gt_p60": float(np.mean(p30 > p60)),
        "any": float(np.mean((p15 > p30) | (p30 > p60))),
    }
    q30 = np.maximum(p30, p15)
    q60 = np.maximum(p60, q30)
    return (p15, q30, q60), viol


def fit_isotonic(score: np.ndarray, y: np.ndarray, max_knots: int = 4000) -> IsotonicCalibrator:
    """Fast PAVA on quantile-binned scores; returns edy's IsotonicCalibrator format."""
    s = np.asarray(score, dtype=np.float64)
    yy = np.asarray(y, dtype=np.float64)
    if len(s) == 0 or yy.min() == yy.max():
        return IsotonicCalibrator()
    order = np.argsort(s, kind="stable")
    s, yy = s[order], yy[order]
    nb = min(max_knots, len(s))
    edges = np.linspace(0, len(s), nb + 1).astype(np.int64)
    edges = np.unique(edges)
    xs, ms, ws = [], [], []
    for a, b in zip(edges[:-1], edges[1:], strict=True):
        xs.append(s[b - 1])
        ms.append(yy[a:b].mean())
        ws.append(b - a)
    bx, bm, bw = [], [], []
    for x, m, w in zip(xs, ms, ws, strict=True):
        bx.append(x)
        bm.append(m)
        bw.append(w)
        while len(bm) > 1 and bm[-2] > bm[-1]:
            w2 = bw[-2] + bw[-1]
            bm[-2] = (bm[-2] * bw[-2] + bm[-1] * bw[-1]) / w2
            bw[-2], bx[-2] = w2, bx[-1]
            bx.pop(), bm.pop(), bw.pop()
    kx = [0.0, *bx, 1.0]
    ky = [bm[0], *bm, bm[-1]]
    return IsotonicCalibrator(knot_x=kx, knot_y=ky)


def _binary_block(y: np.ndarray, p: np.ndarray) -> dict:
    return {
        "n": int(len(y)),
        "base_rate": round(float(np.mean(y)), 6),
        "mean_pred": round(float(np.mean(p)), 6),
        "auc": round(roc_auc(y, p), 5),
        "pr_auc": round(average_precision(y, p), 5),
        "brier": round(brier_score(y, p), 6),
        "log_loss": round(log_loss(y, p), 6),
        "reliability": reliability(y, p),
    }


def _block(probs: dict, targets: dict, sel) -> dict:
    out = {}
    for ty, pk in HORIZONS.items():
        y = targets[ty] if sel is None else targets[ty][sel]
        p = probs[pk] if sel is None else probs[pk][sel]
        out[ty] = _binary_block(y, p)
    n = targets["n60"] if sel is None else targets["n60"][sel]
    mu = probs["n60"] if sel is None else probs["n60"][sel]
    out["n60"] = {
        "mean_true": round(float(np.mean(n)), 6),
        "mean_pred": round(float(np.mean(mu)), 6),
        "poisson_deviance": round(poisson_deviance(n, mu), 6),
        "mae": round(float(np.mean(np.abs(np.asarray(n, np.float64) - mu))), 6),
    }
    return out


def evaluate(probs: dict, targets: dict, meta: dict) -> dict:
    """probs: p15,p30,p60,n60. targets: y15,y30,y60,n60. meta: power_class codes + names."""
    for k in ("p15", "p30", "p60"):
        probs[k] = np.asarray(probs[k], dtype=np.float64)
    assert np.all(probs["p15"] <= probs["p30"] + 1e-12), "p15 > p30"
    assert np.all(probs["p30"] <= probs["p60"] + 1e-12), "p30 > p60"
    res = {"overall": _block(probs, targets, None), "by_power_class": {}}
    codes = np.asarray(meta["power_class"])
    names = meta.get("power_class_names") or sorted(set(codes.tolist()))
    for i, name in enumerate(names):
        sel = codes == i if np.issubdtype(codes.dtype, np.integer) else codes == name
        if sel.any():
            res["by_power_class"][name] = _block(probs, targets, sel)
    return res
