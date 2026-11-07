"""Unit tests for live signal sampling and live validation report (spec 02 §5, §2)."""

from datetime import datetime, timezone
from model.live_signals import LiveSignalSampler, generate_live_validation_report


def test_live_signals_sample():
    sampler = LiveSignalSampler(seed=42)
    now = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)

    signals = sampler.sample("L00001", now, n60_forecast=2.5, traffic_index=0.7)
    assert len(signals) >= 3

    kinds = {s["signal"] for s in signals}
    assert "maps_ev_count" in kinds
    assert "maps_ice_count" in kinds
    assert "maps_traffic_index" in kinds

    # Test CP charging confirmation
    confirmed = sampler.on_cp_status_change("L00001", "L00001-1", "CHARGING", now)
    assert confirmed is not None
    assert confirmed["status"] in ("confirmed", "confirmed_late")


def test_live_validation_report(tmp_path):
    out = tmp_path / "live_validation_report.json"
    rep = generate_live_validation_report(output_path=out)

    assert out.exists()
    assert "metrics" in rep
    assert rep["metrics"]["mean_absolute_error"] >= 0.0
    assert len(rep["hourly_comparison"]) == 24
