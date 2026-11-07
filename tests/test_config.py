from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parent.parent / "config"
POWER_KEYS = {"idle_w", "standby_w", "deep_sleep_w", "standby_wake_s", "deep_sleep_wake_s"}


def load(name):
    return yaml.safe_load((CONFIG / name).read_text())


def test_power_profiles_keys():
    cfg = load("power_profiles.yaml")
    for power_class in ("AC_22", "DC_50", "DC_HPC"):
        assert set(cfg[power_class]) == POWER_KEYS
    assert "wake_extra_wh" in cfg


def test_signals_keys():
    cfg = load("signals.yaml")
    assert set(cfg) == {
        "p_app",
        "intent_lead_min",
        "eta_sd_min",
        "false_positive_rate",
        "ev_tag_rate",
        "w_ice",
        "background_ev",
        "ice_base",
        "rain_boost",
        "traffic_noise_sd",
        "seed",
        "context_multiplier",
    }
    assert set(cfg["context_multiplier"]) == {"urban", "retail", "highway", "airport", "other"}


def test_sim_keys():
    cfg = load("sim.yaml")
    assert set(cfg) == {
        "slot_s",
        "lambda_grid",
        "default_lambda",
        "naive_quiet_hours_per_day",
        "min_awake_per_location",
        "recommendation_ttl_slots",
        "eval_days",
    }
    assert cfg["default_lambda"] in cfg["lambda_grid"]
