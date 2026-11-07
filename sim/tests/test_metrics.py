import numpy as np
import pytest

from sim.metrics import KPI_FIELDS, PolicyMetrics


def test_kpi_fields_match_contract():
    assert set(PolicyMetrics().kpis(0.0)) == set(KPI_FIELDS)
    assert KPI_FIELDS == (
        "overhead_kwh", "saved_kwh", "saved_pct", "wait_min_total", "wait_min_mean",
        "wait_min_p95", "sessions", "waited_sessions", "sleep_share",
        "overhead_ron", "saved_ron",
    )  # fmt: skip


def test_ron_kpis_use_net_b2b_tariff():
    m = PolicyMetrics()
    m.add_energy_wh(2500.0)
    k = m.kpis(10000.0)
    assert (k["overhead_kwh"], k["saved_kwh"]) == (2.5, 7.5)
    assert k["overhead_ron"] == pytest.approx(2.5 * 1.15)
    assert k["saved_ron"] == pytest.approx(7.5 * 1.15)


def test_empty_metrics_are_zero():
    k = PolicyMetrics().kpis(0.0)
    assert all(v == 0 for v in k.values())


@pytest.mark.parametrize("n_zero,n_wait", [(0, 1), (1, 0), (19, 1), (20, 1), (5, 30), (100, 7)])
def test_p95_matches_numpy_over_all_sessions(n_zero, n_wait):
    rng = np.random.default_rng(n_zero * 31 + n_wait)
    waits = np.concatenate([np.zeros(n_zero), rng.uniform(0.1, 5, n_wait)])
    rng.shuffle(waits)
    m = PolicyMetrics()
    for w in waits:
        m.add_session(float(w))
    assert m.wait_p95() == pytest.approx(np.percentile(waits, 95))
    assert m.wait_mean() == pytest.approx(waits.mean())
    assert m.waited_sessions == n_wait
    assert m.sessions == n_zero + n_wait


def test_savings_against_baseline():
    m = PolicyMetrics()
    m.add_energy_wh(750.0)
    k = m.kpis(baseline_wh=1000.0)
    assert k["overhead_kwh"] == 0.75
    assert k["saved_kwh"] == 0.25
    assert k["saved_pct"] == 25.0
    same = m.kpis(baseline_wh=m.overhead_wh)
    assert same["saved_kwh"] == 0 and same["saved_pct"] == 0
    assert PolicyMetrics().kpis(baseline_wh=0.0)["saved_pct"] == 0.0


def test_sleep_share_and_rounding():
    m = PolicyMetrics()
    m.add_time(cp_s=3000, sleep_s=1000)
    m.add_session(10 / 60)
    k = m.kpis(0.0)
    assert k["sleep_share"] == 0.3333
    assert k["wait_min_total"] == 0.167
    assert isinstance(k["sessions"], int) and isinstance(k["sleep_share"], float)


def test_merge():
    a, b = PolicyMetrics(), PolicyMetrics()
    a.add_session(3.0)
    a.add_energy_wh(10)
    b.add_session(0.0)
    b.add_session(1.0)
    b.add_time(100, 50)
    a.merge(b)
    assert (a.sessions, a.waited_sessions, a.wait_min_total) == (3, 2, 4.0)
    assert a.overhead_wh == 10 and a.sleep_share() == 0.5
    assert a.wait_p95() == pytest.approx(np.percentile([3.0, 0.0, 1.0], 95))
