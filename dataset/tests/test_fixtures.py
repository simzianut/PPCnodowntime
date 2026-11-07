from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "fixtures"
UTC = "datetime64[ns, UTC]"
SIGNALS = {"ppcblue_route_intent", "maps_ev_count", "maps_ice_count", "maps_traffic_index"}


def _load(name: str) -> pd.DataFrame:
    return pd.read_parquet(FIX / name)


def test_sessions_schema_and_overlap():
    df = _load("sessions_sample.parquet")
    assert list(df.columns) == [
        "session_id",
        "cp_id",
        "location_id",
        "arrival_ts",
        "departure_ts",
        "energy_kwh",
        "donor_charger_id",
        "split",
    ]
    assert str(df.arrival_ts.dtype) == UTC and str(df.departure_ts.dtype) == UTC
    assert df.energy_kwh.dtype == "float64"
    assert set(df.split) == {"test"}
    assert df.cp_id.str.match(r"^L\d{5}-\d+$").all()
    assert df.location_id.str.match(r"^L\d{5}$").all()
    assert (df.departure_ts > df.arrival_ts).all()
    for _, g in df.sort_values("arrival_ts").groupby("cp_id"):
        assert (g.arrival_ts.iloc[1:].to_numpy() >= g.departure_ts.iloc[:-1].to_numpy()).all()


def test_signals_schema_and_intents():
    sig = _load("signals_sample.parquet")
    ses = _load("sessions_sample.parquet")
    assert list(sig.columns) == ["ts", "location_id", "signal", "value", "eta_ts", "session_id"]
    assert str(sig.ts.dtype) == UTC and str(sig.eta_ts.dtype) == UTC
    assert sig.value.dtype == "float64"
    assert set(sig.signal) <= SIGNALS
    assert sig.location_id.str.match(r"^L\d{5}$").all()
    intents = sig[sig.signal == "ppcblue_route_intent"]
    linked = intents.session_id.dropna()
    assert linked.isin(ses.session_id).all()
    p_app = yaml.safe_load((ROOT / "config" / "signals.yaml").read_text())["p_app"]
    assert abs(linked.nunique() / len(ses) - p_app) < 0.12
    maps = sig[sig.signal != "ppcblue_route_intent"]
    assert maps.eta_ts.isna().all() and maps.session_id.isna().all()


def test_context_schema():
    df = _load("context_sample.parquet")
    assert list(df.columns) == [
        "ts_hour",
        "county_code",
        "temp_c",
        "precip_mm",
        "wind_kmh",
        "is_holiday",
    ]
    assert str(df.ts_hour.dtype) == UTC
    assert df[["temp_c", "precip_mm", "wind_kmh"]].dtypes.eq("float64").all()
    assert df.is_holiday.dtype == bool
    assert len(df) == 5 * 48


def test_locations_csv():
    df = pd.read_csv(FIX / "locations_sample_m1.csv")
    assert list(df.columns) == [
        "location_id",
        "county_code",
        "context_type",
        "cp_id",
        "power_class",
    ]
    assert df.location_id.str.match(r"^L\d{5}$").all()
    assert df.cp_id.str.match(r"^L\d{5}-\d+$").all()
    assert "DC_HPC" in set(df.power_class)
