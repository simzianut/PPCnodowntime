from pathlib import Path

import numpy as np
import pandas as pd

from model.features import FEATURE_COLUMNS
from model.forecaster import DemandForecaster
from model.types import CpView, PowerClass, SimState, State


def _make_dummy_training_data(n_samples: int = 100) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(42)
    feats = {col: rng.normal(0, 1, n_samples) for col in FEATURE_COLUMNS}
    feats["cp_id"] = [f"L1-{i % 3 + 1}" for i in range(n_samples)]
    X = pd.DataFrame(feats)

    y = pd.DataFrame(
        {
            "y15": (rng.uniform(0, 1, n_samples) > 0.8).astype(float),
            "y30": (rng.uniform(0, 1, n_samples) > 0.7).astype(float),
            "y60": (rng.uniform(0, 1, n_samples) > 0.6).astype(float),
            "n60": rng.poisson(1.2, n_samples).astype(float),
        }
    )
    return X, y


def test_forecaster_train_and_predict(tmp_path: Path):
    X, y = _make_dummy_training_data(120)
    forecaster = DemandForecaster(num_boost_round=10)
    forecaster.fit(X, y)

    # Test predict_proba
    probs = forecaster.predict_proba(X)
    assert "y15" in probs
    assert "y30" in probs
    assert "y60" in probs
    assert "n60" in probs
    assert len(probs["y15"]) == len(X)
    assert np.all((probs["y15"] >= 0.0) & (probs["y15"] <= 1.0))

    # Test save and load roundtrip
    forecaster.save(tmp_path)
    loaded = DemandForecaster.load(tmp_path)
    loaded_probs = loaded.predict_proba(X)
    np.testing.assert_allclose(probs["y15"], loaded_probs["y15"], atol=1e-5)


def test_forecaster_predict_simstate():
    X, y = _make_dummy_training_data(100)
    forecaster = DemandForecaster(num_boost_round=10)
    forecaster.fit(X, y)

    t = pd.Timestamp("2026-11-07 12:00:00", tz="UTC")
    cps = [
        CpView("L1-1", "L1", PowerClass.DC_50, ["CCS"], State.IDLE, 300),
        CpView("L1-2", "L1", PowerClass.AC_22, ["Type2"], State.IDLE, 300),
    ]
    state = SimState(
        t=t,
        cps=cps,
        signals=pd.DataFrame(columns=["ts", "location_id", "signal", "value", "eta_ts"]),
        context={"B": {"temp_c": 18.0, "precip_mm": 0.0, "wind_kmh": 5.0, "is_holiday": False}},
        forecast=None,
    )
    res_df = forecaster.predict(state)
    assert len(res_df) == 2
    assert list(res_df.columns) == [
        "cp_id",
        "p_arrival_15",
        "p_arrival_30",
        "p_arrival_60",
        "expected_arrivals_60",
    ]
