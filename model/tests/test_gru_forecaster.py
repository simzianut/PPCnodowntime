"""F2 GRU service: batch forecasts, chunking, online parity with sim.Engine (synthetic data)."""

import numpy as np
import pandas as pd
import pytest

from model import forecast_data as fd
from model.gru_forecaster import GRUForecaster, load_gru, model_path, predict_days
from model.tests.test_forecasters import TEST, _cfg, env  # noqa: F401

KW = {"epochs": 1, "samples_per_epoch": 3000, "batch_size": 256, "hidden": 8, "mlp_hidden": 8}
COLS = ["ts", "cp_id", "p_arrival_15", "p_arrival_30", "p_arrival_60", "expected_arrivals_60"]


@pytest.fixture(params=[None, "cp_level"], ids=["base", "cp_level"])
def run_dir(env, request):  # noqa: F811
    from model.experiments import runner

    extra = {}
    if request.param:
        groups = [*fd.data_params(_cfg("f2_gru"))["feature_groups"], request.param]
        extra = {"feature_groups": groups, "calibration": "auto"}
    return runner.run_config(_cfg("f2_gru", **KW, **extra))


def _days():
    return [TEST[0], TEST[1], TEST[2]]


def test_load_and_predict_days_schema(run_dir, tmp_path):
    m = load_gru(model_path(run_dir))
    assert m.window == 36 and m.features
    df = predict_days(model_path(run_dir), _days(), config=None)
    assert list(df.columns) == COLS and str(df["ts"].dt.tz) == "UTC"
    assert len(df) == 4 * 3 * fd.SLOTS
    assert all(df[c].dtype == np.float32 for c in COLS[2:])
    assert (df["p_arrival_15"] <= df["p_arrival_30"]).all()
    assert (df["p_arrival_30"] <= df["p_arrival_60"]).all()
    assert df[COLS[2:]].notna().all().all()
    out = tmp_path / "f.parquet"
    assert predict_days(model_path(run_dir), _days(), out_path=out, chunk_days=1) == len(df)
    chunked = pd.read_parquet(out)
    pd.testing.assert_frame_equal(chunked, df)
    one = predict_days(model_path(run_dir), _days(), chunk_days=10)
    pd.testing.assert_frame_equal(one, df)


def _sim_data(env_frames):
    from sim.data import SimData

    sess, sig, ctx, cps, locs = env_frames
    c = cps.merge(locs[["location_id", "county_code", "context_type"]], on="location_id")
    c["max_kw"] = c["power_class"].map({"AC_22": 22.0, "DC_50": 50.0, "DC_HPC": 150.0})
    c["connectors"] = [[] for _ in range(len(c))]
    c["current_type"] = np.where(c["power_class"] == "AC_22", "AC", "DC")
    sess = sess.assign(split="test")
    return SimData(locs, c, sess, sig, ctx)


def test_online_forecaster_matches_batch_through_engine(run_dir, env, tmp_path):  # noqa: F811
    from sim.baselines import AlwaysOn
    from sim.engine import Engine
    from sim.forecast import ts_key

    # an arrival exactly at a slot start is not visible to the engine at that slot (real
    # timestamps have seconds); move the synthetic minute-grid sessions off the grid
    sess = env[0].copy()
    for c in ("arrival_ts", "departure_ts"):
        sess[c] = sess[c] + pd.Timedelta(seconds=30)
    sess.to_parquet(fd.data_root() / "sessions.parquet")
    data = _sim_data((sess, *env[1:]))
    batch = predict_days(model_path(run_dir), _days())
    batch["k"] = batch["ts"].astype("int64")
    fc = GRUForecaster.from_data(model_path(run_dir), tmp_path / "data", infer_arrivals=False)
    seen = {}

    class Rec:
        def predict(self, state):
            df = fc.predict(state)
            seen[ts_key(state.t)] = df
            return df

    eng = Engine(data, TEST[1].date(), [AlwaysOn()], forecaster=Rec())
    while not eng.done:
        eng.step()
    assert len(seen) >= 288
    ref = batch.set_index(["k", "cp_id"])
    worst = 0.0
    for k, (t, df) in enumerate(sorted(seen.items())):
        if k < 24:  # before UTC midnight the batch window is longer (see module docs)
            continue
        got = df.set_index("cp_id")
        exp = ref.loc[[(t, c) for c in got.index]]
        for col in COLS[2:]:
            worst = max(worst, float(np.abs(got[col].to_numpy() - exp[col].to_numpy()).max()))
    assert worst < 1e-4, worst


def test_live_mode_without_sessions_infers_arrivals(run_dir, env, tmp_path):  # noqa: F811
    from sim.baselines import AlwaysOn
    from sim.engine import Engine

    data = _sim_data(env)
    fc = GRUForecaster.from_data(model_path(run_dir), tmp_path / "data", sessions=None)
    eng = Engine(data, TEST[1].date(), [AlwaysOn()], forecaster=fc)
    for _ in range(60):
        eng.step()
    out = fc.predict(eng.sim_state("always_on"))
    assert len(out) == 4 and out.iloc[:, 1:].notna().all().all()
    assert sum(len(a) for a in fc._arr.values()) > 0
