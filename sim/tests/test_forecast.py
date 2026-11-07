from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from sim.forecast import PrecomputedForecaster, ZeroForecaster
from sim.types import FORECAST_COLUMNS, CpView, Forecaster, PowerClass, SimState, State

TZ = ZoneInfo("Europe/Bucharest")


def _state(t, ids):
    cps = [CpView(c, "L1", PowerClass.DC_50, ["CCS"], State.IDLE, 0) for c in ids]
    return SimState(t=t, cps=cps, signals=pd.DataFrame(), context={}, forecast=None)


def _table():
    ts0 = pd.Timestamp("2024-03-04 10:00", tz="UTC")
    ts1 = ts0 + pd.Timedelta(minutes=5)
    rows = [
        (ts0, "A", 0.1, 0.2, 0.3, 0.4),
        (ts0, "B", 0.5, 0.6, 0.7, 0.8),
        (ts1, "A", 0.9, 0.9, 0.9, 1.5),
    ]
    return pd.DataFrame(rows, columns=["ts", *FORECAST_COLUMNS])


def test_lookup_ordering_and_missing():
    f = PrecomputedForecaster(_table())
    assert isinstance(f, Forecaster)
    t = datetime(2024, 3, 4, 12, 0, tzinfo=TZ)  # 10:00 UTC
    out = f.predict(_state(t, ["B", "X", "A"]))
    assert list(out.columns) == FORECAST_COLUMNS
    assert list(out["cp_id"]) == ["B", "X", "A"]
    np.testing.assert_allclose(
        out.iloc[:, 1:].to_numpy(), [[0.5, 0.6, 0.7, 0.8], [0] * 4, [0.1, 0.2, 0.3, 0.4]]
    )
    out = f.predict(_state(t.replace(minute=5), ["A", "B"]))
    np.testing.assert_allclose(out["expected_arrivals_60"], [1.5, 0.0])


def test_missing_slot_and_utc_input():
    f = PrecomputedForecaster(_table())
    out = f.predict(_state(datetime(2024, 3, 4, 13, 0, tzinfo=TZ), ["A"]))
    assert (out.iloc[:, 1:].to_numpy() == 0).all()
    utc = datetime(2024, 3, 4, 10, 0, tzinfo=ZoneInfo("UTC"))
    assert f.predict(_state(utc, ["A"]))["p_arrival_15"].iloc[0] == 0.1


def test_zero_forecaster():
    out = ZeroForecaster().predict(_state(datetime(2024, 3, 4, tzinfo=TZ), ["A", "B"]))
    assert list(out["cp_id"]) == ["A", "B"] and (out.iloc[:, 1:].to_numpy() == 0).all()


def test_predict_is_fast():
    import time

    ids = [f"c{i}" for i in range(500)]
    slots = pd.date_range("2024-03-04", periods=288 * 7, freq="5min", tz="UTC")
    n = len(slots) * len(ids)
    rng = np.random.default_rng(0)
    df = pd.DataFrame(
        {
            "ts": np.repeat(slots, len(ids)),
            "cp_id": np.tile(ids, len(slots)),
            **{c: rng.random(n) for c in FORECAST_COLUMNS[1:]},
        }
    )
    f = PrecomputedForecaster(df)
    t = slots[100].tz_convert(TZ).to_pydatetime()
    state = _state(t, ids)
    f.predict(state)
    t0 = time.perf_counter()
    for _ in range(100):
        out = f.predict(state)
    assert (time.perf_counter() - t0) / 100 < 0.005
    row = df[(df["ts"] == slots[100]) & (df["cp_id"] == "c7")]
    assert out.loc[7, "p_arrival_30"] == row["p_arrival_30"].iloc[0]
