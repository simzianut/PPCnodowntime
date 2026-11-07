"""T8 signal generator tests on tiny synthetic inputs."""

from __future__ import annotations

import numpy as np
import pandas as pd

from dataset import signals

CFG = {
    "p_app": 0.6,
    "intent_lead_min": [10, 40],
    "eta_sd_min": 3,
    "false_positive_rate": 0.1,
    "ev_tag_rate": 0.25,
    "background_ev": 0.5,
    "ice_base": 40,
    "rain_boost": 0.3,
    "traffic_noise_sd": 0.1,
}
H0 = pd.Timestamp("2024-03-01", tz="UTC")
N_HOURS = 24 * 10


def _run(n_sess=4000):
    rng = np.random.default_rng(1)
    locs = pd.DataFrame(
        {
            "location_id": ["L1", "L2"],
            "county_code": ["B", "CJ"],
            "context_type": ["urban", "highway"],
        }
    )
    arr = H0 + pd.to_timedelta(rng.uniform(1, N_HOURS - 1, n_sess), unit="h")
    sess = pd.DataFrame(
        {
            "session_id": [f"S{i}" for i in range(n_sess)],
            "location_id": rng.choice(["L1", "L2"], n_sess),
            "arrival_ts": arr,
        }
    )
    hrs = pd.date_range(H0, periods=N_HOURS, freq="h")
    ctx = pd.DataFrame(
        {
            "ts_hour": list(hrs) * 2,
            "county_code": ["B"] * N_HOURS + ["CJ"] * N_HOURS,
            "precip_mm": rng.uniform(0, 8, 2 * N_HOURS),
        }
    )
    traffic, counts = signals.traffic_matrix(locs, signals.Path("/nonexistent"))
    arrs = signals.build_arrays(rng, CFG, locs, sess, ctx, traffic, H0.value, N_HOURS)
    return arrs, signals.to_table(arrs, locs["location_id"].to_numpy()), counts, sess


def test_intent_share_and_timing():
    arrs, table, _, sess = _run()
    assert abs(arrs["n_real"] / arrs["n_sessions"] - CFG["p_app"]) < 0.02
    df = table.to_pandas()
    real = df[(df["signal"] == "ppcblue_route_intent") & df["session_id"].notna()]
    arrival = sess.set_index("session_id")["arrival_ts"].reindex(real["session_id"]).to_numpy()
    assert (real["ts"].to_numpy() < arrival).all()
    lead = (arrival - real["ts"].to_numpy()) / np.timedelta64(1, "m")
    assert lead.min() >= 10 and lead.max() <= 40
    err = (real["eta_ts"].to_numpy() - arrival) / np.timedelta64(1, "m")
    assert abs(err.mean()) < 0.5 and 2 < err.std() < 4
    fp = df[(df["signal"] == "ppcblue_route_intent") & df["session_id"].isna()]
    assert len(fp) > 0 and fp["eta_ts"].notna().all()


def test_ev_nonzero_and_traffic_range():
    _, table, counts, _ = _run()
    df = table.to_pandas()
    ev = df[df["signal"] == "maps_ev_count"]
    assert len(ev) > 0 and (ev["value"] > 0).all()
    tr = df[df["signal"] == "maps_traffic_index"]
    assert len(tr) == 2 * N_HOURS
    assert tr["value"].between(0, 1).all()
    assert counts["synthetic"] == 2


def test_schema_dtypes():
    _, table, _, _ = _run()
    df = table.to_pandas()
    assert list(df.columns) == ["ts", "location_id", "signal", "value", "eta_ts", "session_id"]
    assert str(df["ts"].dtype) == "datetime64[ns, UTC]"
    assert str(df["eta_ts"].dtype) == "datetime64[ns, UTC]"
    assert df["location_id"].dtype.name == "category"
    assert df["signal"].dtype.name == "category"
    assert set(df["signal"].cat.categories) == set(signals.SIGNALS)
    assert df["value"].dtype == "float64"
    assert df["ts"].is_monotonic_increasing
