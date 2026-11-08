from __future__ import annotations

import pandas as pd

from dataset import cps, dundee, weather

CFG = {
    "lat": 56.4,
    "lon": -2.9,
    "start": "2023-01-01",
    "end": "2024-12-31",
    "split_date": "2023-12-31",
    "sessions_dir": "sessions",
    "infos_dir": "infos",
    "speed_file": "speed.csv",
    "local_authority_file": "la.csv",
    "postcode_file": "pc.csv",
    "public_private_file": "pp.csv",
    "exclude_dundee": True,
    "exclude_private": True,
    "min_session_s": 120,
    "max_session_s": 86400,
    "max_lanes": 2,
    "power_class": {"ac": "AC_22", "rapid": "DC_50", "ultra-rapid": "DC_HPC"},
    "infer_kw_p95": {"rapid": 25, "ultra-rapid": 60},
}
HEADER = "CPID,Amount,Consumed(kWh),Duration,Start,Time\n"


def row(cpid, kwh="5", dur="00:30:00", start="2023-03-07", time="10:00:00"):
    return f"{cpid},0,{kwh},{dur},{start},{time}\n"


def test_build_schema_and_ids(tmp_path, monkeypatch):
    def fake_weather(lat, lon, start, end, name):
        hrs = pd.date_range("2023-01-01", "2024-12-31", freq="h", tz="UTC")
        return pd.DataFrame({"ts_hour": hrs, "temp_c": 5.0, "precip_mm": 0.0, "wind_kmh": 10.0})

    monkeypatch.setattr(weather, "fetch_hourly", fake_weather)
    raw = tmp_path / "raw" / "cps"
    (raw / "sessions").mkdir(parents=True)
    (raw / "infos").mkdir()
    (raw / "sessions" / "Mar23.csv").write_text(
        HEADER
        + row("50001")
        + row("50001", start="2023-03-14")
        + row("50001", start="2023-03-14")  # exact duplicate
        + row("50002", kwh="20", dur="1800")  # numeric duration = seconds
        + row("50002", kwh="20", dur="00:00:30")  # too short
        + row("50002", kwh="0")  # no energy
        + row("50002", start="2022-06-01")  # before the global range
        + row("50003", kwh="40", dur="00:30:00")  # not in speed file: 80 kW -> inferred HPC
        + row("APT61615")  # Dundee-operated, excluded
        + row("70001")  # private, excluded
    )
    (raw / "infos" / "speed.csv").write_text(
        "CPID,Connector_Type\n50001,AC\n50002,Rapid\n61615,Rapid\n70001,AC\n"
    )
    (raw / "infos" / "la.csv").write_text("CPID,local_authority\n50001,Stirling\n")
    (raw / "infos" / "pc.csv").write_text("CPID,Postcode\n50001,FK9 4TW\n")
    (raw / "infos" / "pp.csv").write_text("CPID,public/prive,Postcode\n70001,Private ,X\n")
    (tmp_path / "raw" / "dundee").mkdir()
    (tmp_path / "raw" / "dundee" / "a.csv").write_text("SDR ID,Site,CP ID\n1,S,61615\n")

    rep = cps.build(out_root=tmp_path, cfg=CFG)
    proc = tmp_path / "processed"
    s = pd.read_parquet(proc / "donor_sessions_cps.parquet")
    c = pd.read_parquet(proc / "donor_chargers_cps.parquet")
    assert list(s.columns) == dundee.SESSION_COLS
    assert list(c.columns) == dundee.CHARGER_COLS
    assert s["donor_session_id"].str.startswith("cps:").all()
    assert s["donor_charger_id"].str.startswith("cps:").all()
    assert set(c["donor_charger_id"]) == {"cps:50001#1", "cps:50002#1", "cps:50003#1"}
    cls = c.set_index("base_cp_id")["power_class"]
    assert cls["cps:50001"] == "AC_22" and cls["cps:50002"] == "DC_50"
    assert cls["cps:50003"] == "DC_HPC"
    assert c.set_index("base_cp_id").loc["cps:50001", "site"] == "Stirling FK9 4TW"
    assert s["arrival_ts"].iloc[0] == pd.Timestamp("2023-03-07 10:00", tz="UTC")
    d = rep["dropped"]
    assert d["duplicate_row"] == 1 and d["dundee_cp_id"] == 1 and d["private_cp_id"] == 1
    assert d["duration_too_short"] == 1 and d["kwh_blank_or_nonpositive"] == 1
    assert d["outside_global_range"] == 1 and rep["rows_out"] == 4
    assert (proc / "donor_occupancy_cps.parquet").exists()
