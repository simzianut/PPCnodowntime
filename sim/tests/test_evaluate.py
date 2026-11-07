"""Unit tests for sim.evaluate (spec 04 §5)."""

import json
from api.models import EvalResults
from sim.data import load_m1_fixtures
from sim.evaluate import evaluate


def test_evaluate_smoke(tmp_path):
    data = load_m1_fixtures()
    days = data.days()[:1]
    out_file = tmp_path / "eval_results.json"

    res = evaluate(data=data, days=days, lambdas=[0.1], output_path=out_file)

    assert out_file.exists()
    assert json.loads(out_file.read_text()) == res
    parsed = EvalResults.model_validate(res)
    assert len(parsed.test_days) == 1

    by_key = {(r.policy.value, r.lambda_): r for r in parsed.results}
    assert ("always_on", None) in by_key
    assert by_key[("always_on", None)].saved_kwh == 0.0
    assert by_key[("always_on", None)].wait_min_total == 0.0
    assert ("naive_quiet_hours", None) in by_key
    assert ("cost_rule", 0.1) in by_key


def test_evaluate_includes_oracle_and_rl_agents(tmp_path, monkeypatch):
    from model import rl_select
    from model.tests.test_rl_select import make_artifacts

    make_artifacts(tmp_path / "artifacts")
    monkeypatch.setattr(rl_select, "ROOT", tmp_path)
    monkeypatch.setenv("RL_AGENT", "maskppo")
    data = load_m1_fixtures()
    res = evaluate(data=data, days=data.days()[:1], lambdas=[0.1])
    parsed = EvalResults.model_validate(res)
    names = [r.policy.value for r in parsed.results]
    assert {"oracle", "rl_maskppo", "rl_ddqn", "rl_ppo"} <= set(names)
    assert all(set(r) == set(res["results"][0]) for r in res["results"])


def test_default_days_follow_forecast_coverage():
    import dataclasses

    import pandas as pd

    from sim.evaluate import _resolve_test_days

    data = load_m1_fixtures()
    all_days = _resolve_test_days(data, "all", "all")
    d0 = all_days[0]
    ts = pd.date_range(pd.Timestamp(d0, tz="Europe/Bucharest"), periods=288, freq="5min")
    fc = pd.DataFrame({"ts": ts.tz_convert("UTC"), "cp_id": "x"})
    cut = dataclasses.replace(data, forecasts=fc)
    assert _resolve_test_days(cut, None, "all") == [d0]
    assert _resolve_test_days(cut, "all", "all") == [d0]
