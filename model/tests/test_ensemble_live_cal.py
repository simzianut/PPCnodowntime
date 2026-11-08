"""Ensemble run dir end to end on synthetic data; live-calibration helpers (decision 0084)."""

import json

import numpy as np

from model import forecast_data as fd
from model import live_calibration as lc
from model.experiments import ensemble, runner
from model.gru_forecaster import load_gru, model_path, predict_days
from model.tests.test_forecasters import TEST, _cfg, env  # noqa: F401

KW = {"epochs": 1, "samples_per_epoch": 2000, "batch_size": 256, "hidden": 8, "mlp_hidden": 8}


def test_ensemble_run_dir_promotable_and_loadable(env):  # noqa: F811
    dirs = [runner.run_config(_cfg("f2_gru", seed=s, **KW)) for s in (0, 1)]
    out = ensemble.build(dirs, "f2_ens_test")
    man = json.loads((out / "manifest.json").read_text())
    met = json.loads((out / "metrics.json").read_text())
    assert man["status"] == "ok" and len(man["members"]) == 2
    assert met["members"] == man["members"] and 0 <= met["test"]["overall"]["y30"]["auc"] <= 1
    m = load_gru(model_path(out))
    assert len(m.net.nets) == 2
    df = predict_days(model_path(out), [TEST[0]])
    assert len(df) == 4 * fd.SLOTS and (df["p_arrival_15"] <= df["p_arrival_30"]).all()


def test_first_arrival_bucket_and_observed_mask():
    arr = np.zeros((20, 2), bool)
    arr[4, 0] = True  # cp 0 arrives at slot 4
    obs = np.ones(20, bool)
    b = lc.first_arrival_bucket(arr, obs)
    assert b[0, 0] == 3 and b[3, 0] == 0 and b[4, 0] == 12 and b[0, 1] == 12
    assert (b[8:] == -1).all()  # fewer than 12 observed slots ahead
    obs[10] = False
    b2 = lc.first_arrival_bucket(arr, obs)
    assert b2[0, 0] == -1 and b2[1, 1] == -1  # window crosses an unobserved slot


def test_live_calibration_json_only_applies_to_matching_model(tmp_path, monkeypatch):
    from api.live_mode import live_calibration_for

    f2 = tmp_path / "f2_gru_live.pt"
    f2.write_bytes(b"x")
    assert live_calibration_for(f2) is None
    (tmp_path / "f2_live_calibration.json").write_text(
        json.dumps({"model": "f2_gru.pt", "a": 1, "b": 0})
    )
    assert live_calibration_for(f2) is None
    (tmp_path / "f2_live_calibration.json").write_text(
        json.dumps({"model": "f2_gru_live.pt", "a": 0.5, "b": -1})
    )
    assert live_calibration_for(f2) == {"kind": "temp_bias", "a": 0.5, "b": -1.0}
    monkeypatch.setenv("LIVE_F2_CALIBRATION", str(tmp_path / "missing.json"))
    assert live_calibration_for(f2) is None
