"""Every file in fixtures/api/ matches the contract models (spec 04 §9)."""

from __future__ import annotations

import re

import numpy as np
import pytest
from pydantic import TypeAdapter

from api.fixture_store import FIXTURES_DIR, RecordedDay, load_json
from api.fixtures_gen import build_policies, build_stations
from api.models import EvalResults, Kpis, Location, PolicyInfo
from sim.config import sim_config
from sim.stations_seed import load_seed_stations


@pytest.fixture(scope="module")
def day() -> RecordedDay:
    return RecordedDay()


def test_only_known_files():
    names = {p.name for p in FIXTURES_DIR.iterdir()}
    assert names == {"stations.json", "policies.json", "eval_results.json", "run_day.json"}


def test_stations_fixture():
    locs = TypeAdapter(list[Location]).validate_python(load_json("stations.json"))
    assert len(locs) == 116
    assert all(re.fullmatch(r"L1\d{4}", loc.location_id) for loc in locs)
    cps = [cp for loc in locs for cp in loc.charge_points]
    assert len({cp.cp_id for cp in cps}) == len(cps) > 200
    assert all(cp.state is None for cp in cps)


def test_stations_and_policies_are_up_to_date():
    assert load_json("stations.json") == build_stations(*load_seed_stations())
    assert load_json("policies.json") == build_policies()


def test_policies_fixture():
    pols = TypeAdapter(list[PolicyInfo]).validate_python(load_json("policies.json"))
    by = {p.name.value: p for p in pols}
    assert set(by) == {"always_on", "naive_quiet_hours", "cost_rule", "rl_ppo"}
    assert by["cost_rule"].lambdas == sim_config().lambda_grid
    assert by["rl_ppo"].lambdas == [sim_config().default_lambda]
    assert by["always_on"].lambdas == [] and all(p.available for p in pols)


def test_eval_fixture():
    raw = load_json("eval_results.json")
    assert raw["fixture"] is True
    ev = EvalResults.model_validate(raw)
    rows = {(r.policy.value, r.lambda_): r for r in ev.results}
    assert rows[("always_on", None)].saved_kwh == 0
    assert rows[("always_on", None)].wait_min_total == 0
    assert ("naive_quiet_hours", None) in rows
    grid = sim_config().lambda_grid
    for p in ("cost_rule", "rl_ppo"):
        saved = [rows[(p, lam)].saved_kwh for lam in grid]
        wait = [rows[(p, lam)].wait_min_total for lam in grid]
        assert saved == sorted(saved, reverse=True)
        assert wait == sorted(wait, reverse=True)
    for lam in grid:
        assert rows[("rl_ppo", lam)].saved_kwh > rows[("cost_rule", lam)].saved_kwh


def test_run_day_fixture(day: RecordedDay):
    assert day.n_slots == 288 and day.slot_s == 300 and str(day.date) == "2024-03-12"
    station_cps = {cp["cp_id"] for loc in load_json("stations.json") for cp in loc["charge_points"]}
    assert set(day.cp_ids) == station_cps
    assert (FIXTURES_DIR / "run_day.json").stat().st_size < 5_000_000
    for p in day.policies:
        for k in range(day.n_slots):
            Kpis.model_validate(day.kpis[p][k])
    final = {p: day.kpis[p][-1] for p in day.policies}
    assert final["always_on"]["saved_kwh"] == 0 and final["always_on"]["wait_min_total"] == 0
    assert not np.isin(day.states["always_on"], [3, 4]).any()  # never STANDBY/DEEP_SLEEP
    for p in day.policies:
        waits = [a.wait_min for slot in day.arrivals[p] for a in slot]
        assert final[p]["sessions"] == len(waits)
        assert final[p]["wait_min_total"] == pytest.approx(sum(waits), abs=1e-2)
        assert final[p]["waited_sessions"] == sum(w > 0 for w in waits)
    assert final["rl_ppo"]["saved_kwh"] > final["naive_quiet_hours"]["saved_kwh"] > 0
    assert day.p30 is not None and day.p30.shape == (288, len(day.cp_ids))
