import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from sim.config import signals_config
from sim.obs import OBS_DIM, OBS_FIELDS, action_mask, build_obs
from sim.types import FORECAST_COLUMNS, CpView, PowerClass, SimState, State

TZ = ZoneInfo("Europe/Bucharest")
T = datetime(2024, 3, 6, 14, 30, tzinfo=TZ)  # Wednesday
F = {name: i for i, name in enumerate(OBS_FIELDS)}


def _cp(cp_id, loc, state=State.IDLE, pc=PowerClass.DC_50, secs=0):
    return CpView(cp_id, loc, pc, ["CCS"], state, secs)


def _sig(rows):
    """rows: (minutes from T for ts, location, signal, value, eta minutes from T or None)."""
    out = []
    for dts, loc, kind, val, deta in rows:
        ts = pd.Timestamp(T + timedelta(minutes=dts)).tz_convert("UTC")
        eta = (
            pd.Timestamp(T + timedelta(minutes=deta)).tz_convert("UTC")
            if deta is not None
            else pd.NaT
        )
        out.append((ts, loc, kind, val, eta))
    df = pd.DataFrame(out, columns=["ts", "location_id", "signal", "value", "eta_ts"])
    df["eta_ts"] = pd.to_datetime(df["eta_ts"], utc=True)
    return df


def _state(cps, signals=None, context=None, forecast=None, t=T):
    sig = signals if signals is not None else _sig([])
    return SimState(t=t, cps=cps, signals=sig, context=context or {}, forecast=forecast)


def test_fields():
    assert len(OBS_FIELDS) == OBS_DIM == 31
    assert OBS_FIELDS[19] == "state_charging" and OBS_FIELDS[28] == "power_ac_22"


def test_forecast_and_none():
    cps = [_cp("A", "L1"), _cp("B", "L1")]
    fc = pd.DataFrame(
        [("B", 0.1, 0.2, 0.3, 2.0), ("A", 0.4, 0.5, 0.6, 0.0)], columns=FORECAST_COLUMNS
    )
    obs = build_obs(_state(cps, forecast=fc), ["A", "B"])
    assert obs.dtype == np.float32 and obs.shape == (2, 31)
    np.testing.assert_allclose(
        obs[:, 0:4], [[0.4, 0.5, 0.6, 0], [0.1, 0.2, 0.3, np.log1p(2)]], rtol=1e-6
    )
    assert (build_obs(_state(cps), ["A"])[:, 0:4] == 0).all()


def test_intent_buckets_and_edges():
    rows = [
        (-5, "L1", "ppcblue_route_intent", 1, 0),  # eta == t: excluded
        (-5, "L1", "ppcblue_route_intent", 1, 15),  # bucket 0 (upper edge)
        (-5, "L1", "ppcblue_route_intent", 1, 16),  # bucket 1
        (-5, "L1", "ppcblue_route_intent", 1, 30),  # bucket 1
        (-5, "L1", "ppcblue_route_intent", 1, 60),  # bucket 2
        (-5, "L1", "ppcblue_route_intent", 1, 61),  # beyond
        (5, "L1", "ppcblue_route_intent", 1, 10),  # ts in the future
        (-5, "L2", "ppcblue_route_intent", 1, 10),  # other location
    ] + [(-1, "L1", "ppcblue_route_intent", 1, 50)] * 6
    obs = build_obs(_state([_cp("A", "L1")], _sig(rows)), ["A"])[0]
    np.testing.assert_allclose(obs[4:7], [1 / 5, 2 / 5, 1.0])


def test_flow_and_traffic():
    w = signals_config()["w_ice"]
    rows = [
        (-5, "L1", "maps_ev_count", 2, None),
        (0, "L1", "maps_ice_count", 10, None),
        (-15, "L1", "maps_ev_count", 100, None),  # exactly t-15: excluded
        (-20, "L1", "maps_traffic_index", 0.3, None),
        (-10, "L1", "maps_traffic_index", 0.7, None),
        (-30, "L1", "maps_traffic_index", 0.1, None),
        (5, "L1", "maps_traffic_index", 0.9, None),  # future
    ]
    cps = [_cp("A", "L1"), _cp("B", "L2")]
    obs = build_obs(_state(cps, _sig(rows)), ["A", "B"])
    assert obs[0, 7] == pytest.approx(np.log1p(2 + w * 10))
    assert obs[0, 8] == pytest.approx(0.7)
    assert obs[1, 7] == 0 and obs[1, 8] == 0


def test_time_holiday_state_power():
    cps = [_cp("A", "L1", State.DEEP_SLEEP, PowerClass.AC_22, secs=1800)]
    ctx = {"B": {"is_holiday": False}, "CJ": {"is_holiday": True}}
    obs = build_obs(_state(cps, context=ctx), ["A"])[0]
    angle = 2 * np.pi * (14 * 60 + 30) / 1440
    assert obs[9] == pytest.approx(np.sin(angle)) and obs[10] == pytest.approx(np.cos(angle))
    assert list(obs[11:18]) == [0, 0, 1, 0, 0, 0, 0]
    assert obs[18] == 1
    assert list(obs[19:25]) == [0, 0, 0, 0, 1, 0]
    assert obs[25] == pytest.approx(0.5)
    assert list(obs[28:31]) == [1, 0, 0]
    assert build_obs(_state([_cp("A", "L1", secs=7200)]), ["A"])[0, 25] == 1
    assert build_obs(_state(cps, context={"B": {"is_holiday": False}}), ["A"])[0, 18] == 0


def test_siblings():
    cps = [
        _cp("A", "L1", State.IDLE),
        _cp("B", "L1", State.IDLE),
        _cp("C", "L1", State.CHARGING),
        _cp("D", "L2", State.STANDBY),
    ]
    obs = build_obs(_state(cps), ["C", "A", "D"])
    np.testing.assert_allclose(obs[:, 26], [1.0, 0.5, 0.0])
    np.testing.assert_allclose(obs[:, 27], [0.2, 0.2, 0.0])


def test_mask():
    assert list(action_mask(_cp("A", "L1", pc=PowerClass.AC_22))) == [True, True, False]
    assert list(action_mask(_cp("A", "L1", State.STANDBY))) == [True, True, True]
    for s in (State.CHARGING, State.WAKING, State.OFFLINE):
        assert list(action_mask(_cp("A", "L1", s))) == [True, False, False]


def test_speed_500_cps():
    rng = np.random.default_rng(0)
    locs = [f"L{i}" for i in range(116)]
    cps = [_cp(f"c{i}", locs[i % 116], State.IDLE) for i in range(500)]
    rows = []
    for loc in locs:
        for k in range(12):
            for kind in ("maps_ev_count", "maps_ice_count", "maps_traffic_index"):
                rows.append((-5 * k, loc, kind, float(rng.random() * 10), None))
        for _ in range(3):
            rows.append(
                (
                    -int(rng.integers(0, 60)),
                    loc,
                    "ppcblue_route_intent",
                    1,
                    int(rng.integers(1, 60)),
                )
            )
    fc = pd.DataFrame(
        {"cp_id": [c.cp_id for c in cps], **{c: rng.random(500) for c in FORECAST_COLUMNS[1:]}}
    )
    state = _state(cps, _sig(rows), forecast=fc)
    ids = [c.cp_id for c in cps]
    build_obs(state, ids)
    t0 = time.perf_counter()
    for _ in range(20):
        build_obs(state, ids)
    per_call = (time.perf_counter() - t0) / 20
    print(f"build_obs 500 CPs: {per_call * 1000:.2f} ms")
    assert per_call < 0.1
