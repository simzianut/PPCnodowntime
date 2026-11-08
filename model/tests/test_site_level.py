"""site_level features (batch; online parity is in test_gru_forecaster), ensemble, cold start."""

import numpy as np
import pandas as pd
import torch

from model import forecast_data as fd
from model.experiments import f2_gru
from model.features import FEATURE_COLUMNS
from model.tests.test_forecasters import TEST, _cfg, env  # noqa: F401

NS, MIN, H = fd.NS, 60 * fd.NS, 3600 * fd.NS
C = {c: i for i, c in enumerate(FEATURE_COLUMNS)}


def _cp(power="DC_50"):
    return pd.Series({"county_code": "B", "power_class": power, "context_type": "urban",
                      "county_ev_per_1000": 2.0, "cps_at_location": 2.0})  # fmt: skip


def test_site_features_count_location_arrivals_and_sibling_sessions():
    t0 = 1000 * 86400 * NS
    t = np.array([t0 + 2 * H, t0 + 10 * H])
    a = (np.array([t0 + H + 30 * MIN]), np.array([t0 + H + 50 * MIN]))  # this CP: 1 session
    b = (np.array([t0 + 90 * MIN, t0 + 9 * H + 10 * MIN]), np.array([t0 + 3 * H, t0 + 12 * H]))
    loc_arr = np.sort(np.concatenate([a[0], b[0]]))
    X, *_ = fd._cp_features(t, _cp(), 0, a, loc_arr, fd.ContextGrid(fd._empty_ctx()),
                            fd._empty_sig(), t0, sibs=[a, b])  # fmt: skip
    assert X[0, C["loc_arrivals_1h"]] == 2 and X[0, C["loc_arrivals_3h"]] == 2
    assert X[1, C["loc_arrivals_1h"]] == 1 and X[1, C["loc_arrivals_3h"]] == 1
    assert X[0, C["loc_charging_now"]] == 1 and X[0, C["is_charging"]] == 0  # sibling charging
    assert X[1, C["loc_charging_now"]] == 1
    assert X[0, C["loc_rate_7d"]] == 2.0 and X[1, C["loc_rate_7d"]] == 3.0  # < 1 day of history
    alone, *_ = fd._cp_features(t, _cp(), 0, a, loc_arr, fd.ContextGrid(fd._empty_ctx()),
                                fd._empty_sig(), t0)  # fmt: skip
    assert alone[0, C["loc_charging_now"]] == 0 and alone[0, C["loc_arrivals_1h"]] == 2


def test_ensemble_of_identical_nets_matches_one_and_round_trips():
    torch.manual_seed(0)
    c = {"hidden": 8, "layers": 1, "dropout": 0.0, "mlp_hidden": 8}
    net = f2_gru.Net(5, 2, c).eval()
    ens = f2_gru.Ensemble([net, net]).eval()
    x, valid, stat = torch.randn(4, 6, 5), torch.ones(4, 6), torch.zeros(4, 4)
    a, b = net(x, valid, stat), ens(x, valid, stat)
    assert torch.allclose(a[0], b[0]) and torch.allclose(a[1], b[1])
    cal = {"kind": "temp_bias", "a": 1.0, "b": 0.0}
    ck = f2_gru.checkpoint(ens, c, ["f"] * 5, ["f"] * 5, {}, {}, cal)
    assert len(ck["members"]) == 2
    back = f2_gru.net_from_checkpoint({**ck, "dynamic_features": ["f"] * 5}).eval()
    assert isinstance(back, f2_gru.Ensemble) and torch.allclose(back(x, valid, stat)[0], a[0])


def test_cold_start_dropout_keeps_padding_and_uses_cold_values():
    names = ["hour_sin", "arrivals_cp_1h", "mins_since_last_arrival"]
    norm = {"mu": np.array([0.0, 0.5, 5.0]), "sd": np.array([1.0, 1.0, 2.0])}
    cold, mask = f2_gru.cold_start(names, norm)
    assert mask.tolist() == [False, True, True]
    assert cold[1].item() == (np.log1p(0.0) - 0.5) / 1.0
    assert abs(cold[2].item() - (np.log1p(fd.MINS_CAP) - 5.0) / 2.0) < 1e-5
    x = torch.ones(10, 3, 3)
    valid = torch.ones(10, 3)
    valid[:, 0] = 0
    g = torch.Generator().manual_seed(1)
    y, v2, *_ = f2_gru.drop_history((x, valid, torch.zeros(10)), 1.0, cold, mask, g)
    assert torch.all(y[:, 0, :] == 0) and torch.all(y[:, 1:, 0] == 1)
    assert torch.allclose(y[:, 1:, 1], cold[1].expand(10, 2))
    y0, *_ = f2_gru.drop_history((x, valid, torch.zeros(10)), 0.0, cold, mask, g)
    assert torch.all(y0[:, 1:, :] == 1)
