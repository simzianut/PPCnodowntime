"""Tests for the F0/F1/F2 forecasters on tiny synthetic data (no private data needed)."""

import json

import numpy as np
import pandas as pd
import pytest
import torch

from model import forecast_data as fd
from model import forecast_eval as fe
from model.experiments import runner
from model.experiments.f2_gru import probs_from, survival_nll
from model.features import FEATURE_COLUMNS, SignalIndex, build_training_dataset

TRAIN = pd.date_range("2023-08-20", "2023-09-10", freq="D")
VAL = pd.date_range("2023-10-01", "2023-10-05", freq="D")
TEST = pd.date_range("2024-01-10", "2024-01-14", freq="D")
FORECAST_COLS = ["ts", "cp_id", "p_arrival_15", "p_arrival_30", "p_arrival_60",
                 "expected_arrivals_60"]  # fmt: skip


def _make_data(root):
    """Write a tiny processed/ dir: 4 CPs at 3 locations, sessions, signals, context."""
    rng = np.random.default_rng(1)
    proc = root / "processed"
    proc.mkdir(parents=True)
    cps = pd.DataFrame(
        {
            "cp_id": ["L1-1", "L1-2", "L2-1", "L3-1"],
            "location_id": ["L1", "L1", "L2", "L3"],
            "power_class": ["AC_22", "DC_50", "DC_50", "DC_HPC"],
        }
    )
    locs = pd.DataFrame(
        {
            "location_id": ["L1", "L2", "L3"],
            "county_code": ["B", "B", "CJ"],
            "context_type": ["urban", "highway", "retail"],
        }
    )
    days = list(TRAIN) + list(VAL) + list(TEST)
    act = pd.DataFrame(
        [(c, d.date(), True) for c in cps["cp_id"] for d in days],
        columns=["cp_id", "date", "active"],
    )
    rows = []
    for c, loc in zip(cps["cp_id"], cps["location_id"], strict=True):
        for d in days:
            for h in rng.choice(24, size=rng.integers(1, 5), replace=False):
                a = pd.Timestamp(d, tz="UTC") + pd.Timedelta(
                    hours=int(h), minutes=int(rng.integers(0, 50))
                )
                rows.append((c, loc, a, a + pd.Timedelta(minutes=int(rng.integers(10, 40)))))
    sess = pd.DataFrame(rows, columns=["cp_id", "location_id", "arrival_ts", "departure_ts"])
    sess["session_id"] = [f"S{i}" for i in range(len(sess))]
    sig = []
    for loc in locs["location_id"]:
        for d in days:
            t0 = pd.Timestamp(d, tz="UTC")
            for h in range(24):
                sig.append(
                    (t0 + pd.Timedelta(hours=h), loc, "maps_traffic_index", rng.random(), pd.NaT)
                )
            for k in rng.choice(288, 20, replace=False):
                sig.append(
                    (t0 + pd.Timedelta(minutes=5 * int(k)), loc, "maps_ev_count", 1.0, pd.NaT)
                )
            for k in rng.choice(280, 6, replace=False):
                t = t0 + pd.Timedelta(minutes=5 * int(k))
                sig.append((t, loc, "ppcblue_route_intent", 1.0, t + pd.Timedelta(minutes=25)))
    sig = pd.DataFrame(sig, columns=["ts", "location_id", "signal", "value", "eta_ts"])
    sig = sig.sort_values("ts").reset_index(drop=True)
    sig["session_id"] = None
    hours = pd.date_range("2023-08-10", "2024-01-20", freq="h", tz="UTC")
    ctx = pd.concat(
        [
            pd.DataFrame(
                {
                    "ts_hour": hours,
                    "county_code": cc,
                    "temp_c": 10.0 + rng.random(len(hours)),
                    "precip_mm": 0.0,
                    "wind_kmh": 5.0,
                    "is_holiday": False,
                }  # fmt: skip
            )
            for cc in ("B", "CJ")
        ]
    )
    cps.to_parquet(proc / "charge_points.parquet")
    locs.to_parquet(proc / "locations.parquet")
    act.to_parquet(proc / "cp_active_days.parquet")
    sess.to_parquet(proc / "sessions.parquet")
    sig.to_parquet(proc / "signals.parquet")
    ctx.to_parquet(proc / "context.parquet")
    return sess, sig, ctx, cps, locs


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    return _make_data(tmp_path / "data")


def _cfg(name, seed=0, **params):
    cfg = runner.resolve_config(f"config/train/{name}.yaml", seed)
    cfg["device"] = "cpu"
    cfg["data"].pop("county_ev")  # not in the synthetic data dir
    cfg["params"].update(
        n_train_cpdays=40, n_val_cpdays=12, n_test_cpdays=10, num_threads=1,
        train_end="2023-09-30", val_start="2023-10-01", val_end="2023-12-31",
        test_start="2024-01-01", **params,
    )  # fmt: skip
    return cfg


def test_sample_is_independent_of_run_seed(env):
    _, _, _, cps_raw, locs = env
    cps = fd.load_cps(_cfg("f0_seasonal"))
    act = pd.read_parquet(fd.data_root() / "cp_active_days.parquet")
    p = fd.data_params(_cfg("f0_seasonal"))
    a = fd.draw_sample(cps, act, p)
    b = fd.draw_sample(cps, act, {**p})
    pd.testing.assert_frame_equal(a, b)
    assert set(a["split"]) == {"train", "val", "test"}
    c = fd.draw_sample(cps, act, {**p, "sample_seed": 7})
    assert not a[["cp_id", "date"]].equals(c[["cp_id", "date"]])
    # run seed does not enter the cache key either
    assert fd.sample_key(_cfg("f0_seasonal", seed=0)) == fd.sample_key(_cfg("f0_seasonal", seed=5))


def test_inactive_days_are_excluded(env):
    cps = fd.load_cps(_cfg("f0_seasonal"))
    act = pd.read_parquet(fd.data_root() / "cp_active_days.parquet")
    act.loc[
        (act["cp_id"] == "L1-1") & (act["date"] < pd.Timestamp("2023-09-01").date()), "active"
    ] = False
    cd = fd.draw_sample(cps, act, {**fd.data_params(_cfg("f0_seasonal")), "n_train_cpdays": 1000})
    bad = cd[(cd["cp_id"] == "L1-1") & (cd["date"] < pd.Timestamp("2023-09-01"))]
    assert bad.empty


def _slots(date):
    return pd.date_range(pd.Timestamp(date, tz="UTC"), periods=fd.SLOTS, freq="5min")


def test_features_match_existing_builder_and_have_no_lookahead(env):
    sess, sig, ctx, cps_raw, locs = env
    cfg = _cfg("f0_seasonal")
    cps = fd.load_cps(cfg).set_index("cp_id")
    day = TEST[1]
    t = _slots(day).as_unit("ns").asi8
    cp_id = "L1-1"
    by_cp = fd._sessions_by(sess, "cp_id", True)
    by_loc = {k: v[0] for k, v in fd._sessions_by(sess, "location_id", False).items()}
    loc_index = {"L1": 0, "L2": 1, "L3": 2}
    needed = np.array([(0 << fd.DAY_SHIFT) | (day.value // (86400 * 10**9))])
    log = __import__("logging").getLogger("t")
    tables = fd.load_signal_tables(fd._data_file(cfg, "signals"), loc_index, needed, log)
    grid = fd.ContextGrid(ctx)
    X, y, bucket, n60 = fd._cp_features(
        t, cps.loc[cp_id], 0, by_cp[cp_id], by_loc["L1"], grid, tables
    )
    # parity with model/features.py (arrivals_same_slot_4w is 0 there by design)
    cp_tbl = pd.read_parquet(fd.data_root() / "charge_points.parquet")
    ref_X, ref_y = build_training_dataset(
        sess, sig, ctx, locs, charge_points_df=cp_tbl, slots=_slots(day), slot_min=5
    )
    keep = (ref_X["cp_id"] == cp_id).to_numpy()
    ref_X, ref_y = ref_X[keep], ref_y[keep]
    for k, name in enumerate(FEATURE_COLUMNS):
        cp_level = name in fd.FEATURE_GROUPS["cp_level"]  # see test_cp_level
        if name == "arrivals_same_slot_4w" or cp_level:
            continue
        np.testing.assert_allclose(X[:, k], ref_X[name].to_numpy(), atol=1e-5, err_msg=name)
    for k, h in enumerate(("y15", "y30", "y60")):
        np.testing.assert_array_equal(y[:, k], ref_y[h].to_numpy().astype(np.uint8))
    np.testing.assert_allclose(n60, ref_y["n60"].to_numpy())
    # no lookahead: dropping all arrivals and signals after the cut changes no feature before it
    cut = _slots(day)[100]
    sess2 = sess[sess["arrival_ts"] <= cut]
    sig2 = sig[sig["ts"] <= cut]
    by_cp2 = fd._sessions_by(sess2, "cp_id", True)
    by_loc2 = {k: v[0] for k, v in fd._sessions_by(sess2, "location_id", False).items()}
    pq_path = fd.data_root() / "signals_cut.parquet"
    sig2.to_parquet(pq_path)
    tables2 = fd.load_signal_tables(pq_path, loc_index, needed, log)
    X2, *_ = fd._cp_features(t, cps.loc[cp_id], 0, by_cp2[cp_id], by_loc2["L1"], grid, tables2)
    # is_charging at t uses a session that started <= t; its departure may be after the cut
    np.testing.assert_array_equal(X[:101], X2[:101])


def test_first_arrival_bucket_consistent_with_horizons(env):
    sess, sig, ctx, _, _ = env
    cps = fd.load_cps(_cfg("f0_seasonal")).set_index("cp_id")
    by_cp = fd._sessions_by(sess, "cp_id", True)
    by_loc = {k: v[0] for k, v in fd._sessions_by(sess, "location_id", False).items()}
    t = _slots(TEST[0]).as_unit("ns").asi8
    _, y, b, _ = fd._cp_features(
        t, cps.loc["L2-1"], 1, by_cp["L2-1"], by_loc["L2"], fd.ContextGrid(ctx), fd._empty_sig()
    )
    assert np.array_equal(y[:, 0], (b <= 2)) and np.array_equal(y[:, 1], (b <= 5))
    assert np.array_equal(y[:, 2], (b <= 11)) and y[:, 2].any() and (b == 12).any()


def test_survival_head_is_monotone_and_nll_matches_manual():
    torch.manual_seed(0)
    logits, lograte = torch.randn(64, 12), torch.randn(64)
    p15, p30, p60, mu = probs_from(logits, lograte)
    assert torch.all(p15 <= p30) and torch.all(p30 <= p60) and torch.all(mu > 0)
    h = torch.sigmoid(logits[0])
    bucket = torch.tensor([2, 12])
    nll = survival_nll(logits[:2], bucket)
    manual0 = -(torch.log(1 - h[0]) + torch.log(1 - h[1]) + torch.log(h[2]))
    assert torch.isclose(nll[0], -(torch.log(1 - torch.sigmoid(logits[0, :2])).sum()
                                   + torch.log(torch.sigmoid(logits[0, 2]))))  # fmt: skip
    assert torch.isclose(nll[1], -torch.log(1 - torch.sigmoid(logits[1])).sum())
    assert manual0.isfinite()


def test_enforce_monotone_reports_violations():
    (a, b, c), v = fe.enforce_monotone([0.1, 0.3], [0.2, 0.1], [0.15, 0.2])
    assert v["any"] == 1.0 and v["p15_gt_p30"] == 0.5
    assert np.all(a <= b) and np.all(b <= c)


def test_eval_metrics_on_known_inputs():
    y = np.array([0, 0, 1, 1])
    assert fe.roc_auc(y, np.array([0.1, 0.2, 0.8, 0.9])) == 1.0
    assert fe.roc_auc(y, np.array([0.5, 0.5, 0.5, 0.5])) == 0.5
    assert fe.roc_auc(y, np.array([0.9, 0.8, 0.2, 0.1])) == 0.0
    assert fe.average_precision(y, np.array([0.1, 0.2, 0.8, 0.9])) == 1.0
    assert fe.average_precision(np.array([1, 0, 1, 0]), np.array([0.9, 0.8, 0.7, 0.1])) == (
        pytest.approx((1.0 + 2 / 3) / 2)
    )
    p = np.array([0.1, 0.2, 0.8, 0.9])
    assert fe.log_loss(y, p) == pytest.approx(-np.mean(np.log([0.9, 0.8, 0.8, 0.9])))
    assert fe.poisson_deviance(np.array([1.0, 2.0]), np.array([1.0, 2.0])) == pytest.approx(0.0)
    assert fe.poisson_deviance(np.array([0.0]), np.array([2.0])) == pytest.approx(4.0)
    assert len(fe.reliability(y, p, 2)) == 2
    cal = fe.fit_isotonic(np.array([0.1, 0.2, 0.3, 0.4]), np.array([0, 1, 0, 1]))
    out = cal.predict(np.array([0.1, 0.2, 0.3, 0.4]))
    assert np.all(np.diff(out) >= -1e-12)


def _check_outputs(run_dir, data_n_test):
    fcast = pd.read_parquet(run_dir / "forecasts.parquet")
    assert list(fcast.columns) == FORECAST_COLS
    n_test = json.loads((run_dir / "metrics.json").read_text())["sizes"]["test_cpdays"]
    assert abs(n_test - data_n_test) <= 2 and len(fcast) == n_test * fd.SLOTS
    assert (fcast["p_arrival_15"] <= fcast["p_arrival_30"]).all()
    assert (fcast["p_arrival_30"] <= fcast["p_arrival_60"]).all()
    assert str(fcast["ts"].dt.tz) == "UTC"
    m = json.loads((run_dir / "metrics.json").read_text())
    assert 0.0 <= m["test"]["overall"]["y30"]["auc"] <= 1.0
    assert set(m["test"]["by_power_class"]) <= {"AC_22", "DC_50", "DC_HPC"}
    return m


def test_f0_end_to_end_and_deterministic(env):
    cfg = _cfg("f0_seasonal")
    d1 = runner.run_config(cfg)
    m = _check_outputs(d1, 10)
    first = (d1 / "metrics.json").read_text()
    runner.run_config(_cfg("f0_seasonal", seed=3), overwrite=True)
    assert (runner.repro.runs_dir() / runner.make_run_id(_cfg("f0_seasonal", seed=3))
            / "metrics.json").read_text() == first  # fmt: skip
    assert m["features"] == []



def test_f2_end_to_end_and_same_seed_identical(env):
    kw = {"epochs": 1, "samples_per_epoch": 3000, "batch_size": 256, "hidden": 8, "mlp_hidden": 8}
    d1 = runner.run_config(_cfg("f2_gru", **kw))
    _check_outputs(d1, 10)
    first = (d1 / "metrics.json").read_text()
    d2 = runner.run_config(_cfg("f2_gru", **kw), overwrite=True)
    assert (d2 / "metrics.json").read_text() == first
    ck = torch.load(d1 / "model" / "f2_gru.pt", weights_only=False)
    assert {"state_dict", "config", "norm", "features"} <= set(ck)


def test_signal_index_is_used_by_parity_reference():
    assert SignalIndex(None).lookup("L", pd.Timestamp("2024-01-01", tz="UTC"))["traffic_index"] == 0
