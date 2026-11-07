"""F0: per-CP hour-of-week arrival rate with empirical-Bayes shrinkage (decision 0052)."""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from model import forecast_data as fd
from model.experiments import forecast_common as fc
from model.experiments.registry import register
from model.experiments.runner import RunContext

TZ = "Europe/Bucharest"


def _how(ns: np.ndarray) -> np.ndarray:
    idx = pd.DatetimeIndex(ns.astype("datetime64[ns]"), tz="UTC").tz_convert(TZ)
    return (idx.day_of_week * 24 + idx.hour).to_numpy()


def fit_rates(cfg: dict, cps: pd.DataFrame, k: float, group_k: float) -> pd.DataFrame:
    """Counts of arrivals and exposure hours per (cp, hour-of-week), shrunk to the group mean."""
    p = fd.data_params(cfg)
    cp_code = {c: i for i, c in enumerate(cps["cp_id"])}
    n_cp = len(cps)
    act = pd.read_parquet(fd._data_file(cfg, "cp_active_days"))
    act = act[act["active"]].copy()
    act["date"] = pd.to_datetime(act["date"]).dt.tz_localize(None).dt.normalize()
    act = act[(act["date"] <= pd.Timestamp(p["train_end"])) & act["cp_id"].isin(cp_code)]
    dates = np.sort(act["date"].unique())
    day_ns = dates.astype("datetime64[ns]").astype("int64")
    how_by_day = _how((day_ns[:, None] + np.arange(24) * 3600 * fd.NS).reshape(-1)).reshape(-1, 24)
    di = np.searchsorted(dates, act["date"].to_numpy())
    ci = act["cp_id"].map(cp_code).to_numpy()
    expo = np.bincount(
        (ci[:, None] * 168 + how_by_day[di]).reshape(-1), minlength=n_cp * 168
    ).reshape(n_cp, 168)
    sess = pd.read_parquet(fd._data_file(cfg, "sessions"), columns=["cp_id", "arrival_ts"])
    sess = sess[sess["cp_id"].isin(cp_code)]
    arr = fd._ts_ns(sess["arrival_ts"])
    date = pd.to_datetime(arr.astype("datetime64[ns]")).normalize()
    key = pd.MultiIndex.from_arrays([sess["cp_id"].to_numpy(), date])
    ok = key.isin(pd.MultiIndex.from_frame(act[["cp_id", "date"]]))
    cnt = np.bincount(
        sess["cp_id"].map(cp_code).to_numpy()[ok] * 168 + _how(arr[ok]), minlength=n_cp * 168
    ).reshape(n_cp, 168)
    grp = (cps["power_class"] + "|" + cps["context_type"]).to_numpy()
    rate = np.zeros((n_cp, 168))
    for g in np.unique(grp):
        m = grp == g
        cg, eg = cnt[m].sum(0), expo[m].sum(0)
        mean_g = cg.sum() / max(eg.sum(), 1)
        prior = (cg + group_k * mean_g) / (eg + group_k)
        rate[m] = (cnt[m] + k * prior) / (expo[m] + k)
    out = pd.DataFrame(
        {
            "cp_id": np.repeat(cps["cp_id"].to_numpy(), 168),
            "how": np.tile(np.arange(168), n_cp),
            "rate": rate.reshape(-1),
        }
    )
    out["location_id"] = np.repeat(cps["location_id"].to_numpy(), 168)
    out["loc_rate"] = out.groupby(["location_id", "how"])["rate"].transform("sum")
    return out[["cp_id", "how", "rate", "loc_rate"]]


def _window(cs: np.ndarray, h: int) -> np.ndarray:
    return cs[:, h : h + fd.SLOTS] - cs[:, : fd.SLOTS]


def predict(rates: pd.DataFrame, cpdays: pd.DataFrame) -> dict:
    """p_H = 1 - exp(-integral of rate over (t, t+H]); n60 from the location-level rate."""
    cp_ids = list(dict.fromkeys(rates["cp_id"]))
    code = {c: i for i, c in enumerate(cp_ids)}
    R = rates["rate"].to_numpy().reshape(len(cp_ids), 168)
    L = rates["loc_rate"].to_numpy().reshape(len(cp_ids), 168)
    ci = cpdays["cp_id"].map(code).to_numpy()
    day_ns = cpdays["date"].to_numpy().astype("datetime64[D]").astype("datetime64[ns]")
    day_ns = day_ns.astype("int64")
    out = {k: np.zeros((len(cpdays), fd.SLOTS)) for k in ("p15", "p30", "p60", "n60")}
    steps = np.arange(fd.SLOTS + 12) * fd.SLOT_NS
    for a in range(0, len(cpdays), 4000):
        sl = slice(a, a + 4000)
        how = _how((day_ns[sl, None] + steps).reshape(-1)).reshape(-1, fd.SLOTS + 12)
        for name, tab in (("cp", R), ("loc", L)):
            cs = np.cumsum(tab[ci[sl, None], how] / 12.0, axis=1)
            cs = np.concatenate([np.zeros((cs.shape[0], 1)), cs], axis=1)
            if name == "cp":
                for key, h in (("p15", 3), ("p30", 6), ("p60", 12)):
                    out[key][sl] = 1.0 - np.exp(-_window(cs, h))
            else:
                out["n60"][sl] = _window(cs, 12)
    return {k: v.reshape(-1) for k, v in out.items()}


@register("f0_seasonal", kind="forecaster", promote={"model/f0_rates.parquet": "f0_rates.parquet"})
def train(ctx: RunContext) -> dict:
    t0 = time.monotonic()
    p = {"prior_strength": 100.0, "group_strength": 24.0, **(ctx.config.get("params") or {})}
    data = fd.get_dataset(ctx.config, ctx.log, features=False)
    cps = fd.load_cps(ctx.config)
    rates = fit_rates(ctx.config, cps, float(p["prior_strength"]), float(p["group_strength"]))
    ctx.path("model").mkdir(exist_ok=True)
    rates.to_parquet(ctx.path("model", "f0_rates.parquet"), index=False)
    preds = {s: predict(rates, data.cpdays_of(s)) for s in ("val", "test")}
    m = fc.finish(ctx, data, "f0_seasonal", [], preds, time.monotonic() - t0,
                  {"params": {"prior_strength": p["prior_strength"]}})  # fmt: skip
    return m
