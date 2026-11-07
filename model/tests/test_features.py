from datetime import UTC, datetime

import pandas as pd

from model.features import (
    SignalIndex,
    encode_calendar,
    extract_features_from_simstate,
)
from model.types import CpView, PowerClass, SimState, State


def test_encode_calendar():
    t = datetime(2026, 11, 7, 6, 0, tzinfo=UTC)
    res = encode_calendar(t, is_holiday=True)
    assert abs(res["hour_sin"] - 1.0) < 1e-5
    assert abs(res["hour_cos"] - 0.0) < 1e-5
    assert res["weekday"] == 5.0
    assert res["is_holiday"] == 1.0
    assert res["minute_of_day"] == 360.0


def test_signal_index_no_lookahead():
    t = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    sig_df = pd.DataFrame(
        [
            # In the past, should be included
            {
                "ts": t - pd.Timedelta(minutes=5),
                "location_id": "L1",
                "signal": "maps_ev_count",
                "value": 2.0,
            },
            # In the future, must NOT be included
            {
                "ts": t + pd.Timedelta(minutes=5),
                "location_id": "L1",
                "signal": "maps_ev_count",
                "value": 10.0,
            },
            # Route intent with eta in 20 min
            {
                "ts": t - pd.Timedelta(minutes=2),
                "location_id": "L1",
                "signal": "ppcblue_route_intent",
                "value": 1.0,
                "eta_ts": t + pd.Timedelta(minutes=20),
            },
        ]
    )
    idx = SignalIndex(sig_df)
    feats = idx.lookup("L1", t)

    assert feats["maps_ev_count_15m"] == 2.0
    assert feats["intent_eta_30"] == 1.0
    assert feats["intent_eta_15"] == 0.0


def test_extract_features_from_simstate():
    t = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    cps = [
        CpView("L1-1", "L1", PowerClass.DC_50, ["CCS"], State.IDLE, 300),
    ]
    state = SimState(
        t=t,
        cps=cps,
        signals=pd.DataFrame(columns=["ts", "location_id", "signal", "value", "eta_ts"]),
        context={"B": {"temp_c": 18.0, "precip_mm": 0.0, "wind_kmh": 5.0, "is_holiday": False}},
    )
    df = extract_features_from_simstate(state)
    assert len(df) == 1
    assert "hour_sin" in df.columns
    assert "maps_ev_count_15m" in df.columns
    assert df["cp_id"].iloc[0] == "L1-1"
