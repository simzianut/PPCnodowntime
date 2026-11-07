from datetime import date

import numpy as np
import pandas as pd
import pytest

from sim.config import ROOT, TZ, signals_config
from sim.data import SimData, load_m1_fixtures
from sim.stations_seed import CP_COLUMNS, LOCATION_COLUMNS, load_seed_stations
from sim.synthetic import (
    CONTEXT_COLUMNS,
    FORECAST_TABLE_COLUMNS,
    SESSION_COLUMNS,
    SIGNAL_COLUMNS,
    SLOTS_PER_DAY,
    generate,
    oracle_forecasts,
)
from sim.types import Signal

FIX = ROOT / "fixtures"
TRAIN_DAYS, TEST_DAYS = 4, 3


def _subset() -> pd.DataFrame:
    locs, _ = load_seed_stations()
    picks = [locs[locs["context_type"] == c].head(n) for c, n in
             [("urban", 5), ("retail", 5), ("highway", 2), ("airport", 1)]]  # fmt: skip
    return pd.concat(picks, ignore_index=True)


def _gen(seed: int = 0) -> SimData:
    return generate(seed=seed, train_days=TRAIN_DAYS, test_days=TEST_DAYS, locations=_subset())


@pytest.fixture(scope="module")
def data() -> SimData:
    return _gen()


@pytest.mark.parametrize(
    ("attr", "fixture", "cols"),
    [
        ("sessions", "sessions_sample.parquet", SESSION_COLUMNS),
        ("signals", "signals_sample.parquet", SIGNAL_COLUMNS),
        ("context", "context_sample.parquet", CONTEXT_COLUMNS),
    ],
)
def test_dtypes_match_m1_fixtures(data, attr, fixture, cols):
    df = getattr(data, attr)
    ref = pd.read_parquet(FIX / fixture)
    assert list(df.columns) == cols == list(ref.columns)
    assert df.dtypes.to_dict() == ref.dtypes.to_dict()


def test_cps_and_locations_columns(data):
    assert list(data.cps.columns) == CP_COLUMNS
    assert list(data.locations.columns) == LOCATION_COLUMNS
    assert set(data.cps["location_id"]) == set(data.locations["location_id"])


def test_deterministic(data):
    again = _gen()
    for attr in ("sessions", "signals", "context", "forecasts"):
        pd.testing.assert_frame_equal(getattr(data, attr), getattr(again, attr))
    other = _gen(seed=1)
    assert not data.sessions["arrival_ts"].equals(other.sessions["arrival_ts"])


def test_sessions_valid(data):
    s = data.sessions
    assert s["session_id"].is_unique
    assert (s["departure_ts"] > s["arrival_ts"]).all()
    assert (s["energy_kwh"] > 0).all()
    assert set(s["cp_id"]) <= set(data.cps["cp_id"])
    assert (s["donor_charger_id"] == "synthetic").all()
    for _, g in s.sort_values("arrival_ts").groupby("cp_id"):
        assert (g["arrival_ts"].iloc[1:].to_numpy() >= g["departure_ts"].iloc[:-1].to_numpy()).all()
    local = s.assign(
        a=s["arrival_ts"].dt.tz_convert(TZ).dt.date, d=s["departure_ts"].dt.tz_convert(TZ).dt.date
    )
    assert (local["a"] != local["d"]).any(), "expected some sessions to cross midnight"
    per_cp_day = s.groupby("cp_id").size() / (TRAIN_DAYS + TEST_DAYS)
    assert (per_cp_day <= 1.0).any(), "expected some quiet CPs"


def test_splits(data):
    s = data.sessions
    local = s["arrival_ts"].dt.tz_convert(TZ).dt.date
    assert (s.loc[local < date(2024, 1, 1), "split"] == "train").all()
    assert (s.loc[local >= date(2024, 3, 11), "split"] == "test").all()
    assert len(data.days("train")) == TRAIN_DAYS
    assert len(data.days("test")) == TEST_DAYS


def test_signals(data):
    sig = data.signals
    assert set(sig["signal"]) == {s.value for s in Signal}
    assert sig["ts"].is_monotonic_increasing
    maps = sig[sig["signal"] != "ppcblue_route_intent"]
    n_loc = len(data.locations)
    assert len(maps) == 3 * n_loc * SLOTS_PER_DAY * (TRAIN_DAYS + TEST_DAYS)
    tr = maps.loc[maps["signal"] == "maps_traffic_index", "value"]
    assert tr.between(0, 1).all()
    assert maps["eta_ts"].isna().all() and maps["session_id"].isna().all()

    intents = sig[sig["signal"] == "ppcblue_route_intent"]
    assert intents["eta_ts"].notna().all()
    true = intents[intents["session_id"].notna()]
    assert true["session_id"].is_unique
    assert set(true["session_id"]) <= set(data.sessions["session_id"])
    cfg = signals_config()
    assert abs(len(true) / len(data.sessions) - cfg["p_app"]) < 0.05
    fp_share = intents["session_id"].isna().sum() / len(true)
    assert abs(fp_share - cfg["false_positive_rate"]) < 0.05
    lead = (true["eta_ts"] - true["ts"]).dt.total_seconds() / 60
    assert lead.between(cfg["intent_lead_min"][0] - 20, cfg["intent_lead_min"][1] + 20).all()


def test_context(data):
    c = data.context
    counties = data.locations["county_code"].nunique()
    assert len(c) == counties * 24 * (TRAIN_DAYS + TEST_DAYS)
    assert not c.duplicated(["ts_hour", "county_code"]).any()
    assert c["temp_c"].between(-30, 45).all() and (c["precip_mm"] >= 0).all()


def test_forecasts(data):
    f = data.forecasts
    assert list(f.columns) == FORECAST_TABLE_COLUMNS
    assert isinstance(f["cp_id"].dtype, pd.CategoricalDtype)
    n_slots = SLOTS_PER_DAY * (TRAIN_DAYS + TEST_DAYS)
    assert len(f) == len(data.cps) * n_slots
    assert not f.duplicated(["ts", "cp_id"]).any()
    assert set(f["cp_id"].astype(str)) == set(data.cps["cp_id"])
    p = f[["p_arrival_15", "p_arrival_30", "p_arrival_60"]].to_numpy()
    assert ((p >= 0.001) & (p <= 0.999)).all()
    assert (np.diff(p, axis=1) >= 0).all()
    assert (f["expected_arrivals_60"] >= 0).all()


def test_forecast_informative(data):
    f = data.forecasts
    s = data.sessions
    one = s["cp_id"].value_counts().index[0]
    fc = f[f["cp_id"] == one].set_index("ts")
    arr = np.sort(s.loc[s["cp_id"] == one, "arrival_ts"].dt.as_unit("ns").astype("int64"))
    ts = fc.index.as_unit("ns").asi8
    gap = np.append(arr, 2**62)[np.searchsorted(arr, ts, side="right")] - ts
    hit = gap <= 30 * 60 * 10**9
    p30 = fc["p_arrival_30"].to_numpy()
    assert p30[hit].mean() > p30[~hit].mean() + 0.3


def test_oracle_forecasts_standalone():
    d = load_m1_fixtures()
    f = oracle_forecasts(d.sessions, d.cps, d.days(), seed=3)
    assert len(f) == len(d.cps) * SLOTS_PER_DAY * len(d.days())


def test_load_m1_fixtures():
    d = load_m1_fixtures()
    assert list(d.cps.columns) == CP_COLUMNS
    assert list(d.locations.columns) == LOCATION_COLUMNS
    assert set(d.sessions["cp_id"]) <= set(d.cps["cp_id"])
    assert d.locations["location_id"].is_unique
    assert d.cps.loc[d.cps["power_class"] == "AC_22", "current_type"].eq("AC").all()
    assert d.days() == [date(2024, 3, 12), date(2024, 3, 13)]
    assert d.forecasts is not None
    assert len(d.forecasts) == len(d.cps) * SLOTS_PER_DAY * 2
