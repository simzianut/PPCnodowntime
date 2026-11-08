"""cp_level features on a hand-built table, and the temp_bias / auto calibration helpers."""

import numpy as np
import pytest
import torch

from model import forecast_data as fd
from model.experiments import f2_gru
from model.experiments.f2_gru import affine_probs, survival_nll
from model.features import FEATURE_COLUMNS
from model.gru_forecaster import GRUModel

NS = fd.NS
DAY = 86400 * NS
MIN = 60 * NS


def test_feature_groups_and_width():
    new = ["arrivals_cp_7d", "arrivals_cp_28d", "mins_since_last_arrival"]
    assert FEATURE_COLUMNS[-8:-5] == new and fd.FEATURE_GROUPS["cp_level"] == new
    assert FEATURE_COLUMNS[-5:] == fd.FEATURE_GROUPS["site_level"]
    assert set(new) <= set(fd.COUNT_COLUMNS)
    old = fd.feature_names(["calendar_history", "traffic", "ev_flow", "intents"])
    assert len(old) == 22 and not set(new) & set(old)
    assert fd.feature_names(["calendar_history", "cp_level"])[-3:] == new


def test_cp_level_values():
    t0 = 100 * DAY
    arr = np.array([t0 + 2 * DAY, t0 + 5 * DAY, t0 + 9 * DAY, t0 + 9 * DAY + 3 * MIN])
    t = np.array([t0 + 1 * DAY, t0 + 9 * DAY + 3 * MIN, t0 + 40 * DAY])
    r7, r28, mins = fd.cp_level_features(arr, t, t0)
    assert (r7[0], r28[0], mins[0]) == (0.0, 0.0, 40320)  # nothing yet
    # 7d window (t-7d, t] holds the 5d, 9d, 9d+3m arrivals; history 9 d clips to 7 d
    assert r7[1] == pytest.approx(3 / 7) and r28[1] == pytest.approx(4 / (9 + 3 / 1440))
    assert mins[1] == 0.0  # an arrival exactly at t counts (side="right")
    assert r7[2] == 0 and r28[2] == 0 and mins[2] == 40320  # last one ~31 d ago, capped


def test_cp_level_history_normalisation_and_cap():
    t0 = 100 * DAY
    arr = np.array([t0 + 10 * MIN])
    r7, r28, mins = fd.cp_level_features(arr, np.array([t0 + 30 * MIN]), t0)
    assert r7[0] == 1.0 and r28[0] == 1.0 and mins[0] == pytest.approx(20)  # history < 1 d -> 1 d
    r7, _, _ = fd.cp_level_features(arr, np.array([t0 + 3 * DAY]), t0)
    assert r7[0] == pytest.approx(1 / 3)
    none = fd.cp_level_features(np.zeros(0, np.int64), np.array([t0]), None)
    assert none[0][0] == 0 and none[2][0] == 40320
    old = fd.cp_level_features(arr, np.array([t0 + 29 * DAY]), t0)
    assert old[2][0] == 40320  # capped at 28 d


def test_cp_level_matches_bruteforce():
    rng = np.random.default_rng(0)
    arr = np.sort(rng.integers(0, 60 * DAY, 200))
    t = np.sort(rng.integers(0, 60 * DAY, 50))
    r7, r28, mins = fd.cp_level_features(arr, t, int(arr[0]))
    for i, ti in enumerate(t):
        a = arr[arr <= ti]
        days = np.clip((ti - arr[0]) / DAY, 1, 7)
        assert r7[i] == pytest.approx(((a > ti - 7 * DAY).sum()) / days)
        days = np.clip((ti - arr[0]) / DAY, 1, 28)
        assert r28[i] == pytest.approx(((a > ti - 28 * DAY).sum()) / days)
        exp = min((ti - a.max()) / MIN, 40320) if len(a) else 40320
        assert mins[i] == pytest.approx(exp)


def test_online_features_use_history_start():
    from model.gru_forecaster import _F, GRUForecaster

    fc = GRUForecaster.__new__(GRUForecaster)
    t = 50 * DAY
    fc._arr = {"c": np.array([t - 3 * 3600 * NS, t - 10 * MIN])}
    fc._hist_start = t - 3 * 3600 * NS  # live: history began 3 h ago -> clipped to 1 d
    x = np.zeros(len(FEATURE_COLUMNS), np.float32)
    fc._arrival_features(x, "c", t)
    assert x[_F["arrivals_cp_7d"]] == pytest.approx(2.0) and x[_F["mins_since_last_arrival"]] == 10
    y = np.zeros(len(FEATURE_COLUMNS), np.float32)
    fc._arrival_features(y, "none", t)
    assert y[_F["arrivals_cp_28d"]] == 0 and y[_F["mins_since_last_arrival"]] == 40320


def _synthetic(a, b, n=60000, seed=0):
    rng = np.random.default_rng(seed)
    true_l = rng.normal(-3.5, 1.0, (n, 12))
    lg = (true_l - b) / a  # raw logits that need l' = a*l + b to match the truth
    u = rng.random((n, 12)) < 1 / (1 + np.exp(-true_l))
    bucket = np.where(u.any(1), u.argmax(1), 12)
    return lg.astype(np.float32), bucket


def test_temp_bias_recovers_known_parameters_and_is_monotone():
    lg, bucket = _synthetic(0.7, -0.4)
    a, b = f2_gru.fit_temp_bias(lg, bucket)
    assert a == pytest.approx(0.7, abs=0.1) and b == pytest.approx(-0.4, abs=0.15)
    p = affine_probs(np.random.default_rng(1).normal(-3, 2, (500, 12)), 1.3, 0.5)
    assert np.all(p["p15"] <= p["p30"]) and np.all(p["p30"] <= p["p60"])
    ref = f2_gru.probs_from(torch.from_numpy(lg[:50]), torch.zeros(50))
    np.testing.assert_allclose(affine_probs(lg[:50], 1.0, 0.0)["p60"], ref[2].numpy(), rtol=1e-4)
    fitted = survival_nll(torch.tensor(a * lg + b), torch.tensor(bucket)).mean()
    assert fitted <= survival_nll(torch.tensor(lg), torch.tensor(bucket)).mean() + 1e-6


def _raw_val(n_cd=40, seed=0):
    lg, bucket = _synthetic(0.7, -0.4, n=n_cd * fd.SLOTS, seed=seed)
    res = f2_gru.probs_from(torch.from_numpy(lg), torch.zeros(len(lg)))
    raw = {k: v.double().numpy() for k, v in zip(("p15", "p30", "p60", "n60"), res, strict=True)}
    raw["logits"] = lg
    y = np.stack([bucket <= 2, bucket <= 5, bucket <= 11], 1).astype(np.uint8)
    return raw, y, bucket.astype(np.uint8)


def test_auto_choice_is_deterministic_and_scores_recorded():
    raw, y, bucket = _raw_val()
    k1, s1 = f2_gru.choose_calibration(raw, y, bucket, 40, 7)
    k2, s2 = f2_gru.choose_calibration(raw, y, bucket, 40, 7)
    assert (k1, s1) == (k2, s2) and set(s1) == {"isotonic", "temp_bias"}
    assert k1 == min(s1, key=lambda k: s1[k])


def test_calibration_kind_back_compat():
    kind = f2_gru._calibration_kind
    assert kind({}) == "isotonic" and kind({"calibrate": False}) == "none"
    assert kind({"calibrate": True}) == "isotonic"
    assert kind({"calibrate": True, "calibration": "auto"}) == "auto"


def test_gru_model_applies_temp_bias_and_old_checkpoint_path():
    raw, y, bucket = _raw_val(n_cd=4)
    base = {"net": None, "config": {}, "features": [], "dyn_names": [], "norm": {}}
    iso = f2_gru.fit_calibration("isotonic", raw, y, bucket)
    old = GRUModel(**base, calibrators=iso["calibrators"])  # no calibration key
    new = GRUModel(**base, calibrators=iso["calibrators"], calibration={"kind": "isotonic"})
    a, b = old.finish(raw), new.finish(raw)
    for k in a:
        np.testing.assert_array_equal(a[k], b[k])
    tb = GRUModel(**base, calibrators={}, calibration={"kind": "temp_bias", "a": 0.8, "b": -0.2})
    ref = affine_probs(raw["logits"], 0.8, -0.2)
    np.testing.assert_allclose(tb.finish(raw)["p60"], ref["p60"])
