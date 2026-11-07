from __future__ import annotations

import pandas as pd
import pytest

from dataset import dundee, weather

HEADER = "SDR ID,Site,CP ID,Connector Type,Consum(kWh),Duration,Start,End,Postcode\n"
CFG = {
    "lat": 56.4,
    "lon": -2.9,
    "start": "2023-01-01",
    "end": "2024-12-31",
    "split_date": "2023-12-31",
    "min_session_s": 120,
    "max_session_s": 86400,
    "max_lanes": 2,
    "power_class": {"ac": "AC_22", "rapid": "DC_50", "ultra_rapid": "DC_HPC"},
    "files": [{"name": "a.csv", "item_id": "x"}],
}


def row(
    sdr,
    cp="C1",
    kwh="5",
    dur="00:30:00",
    start="10/01/2023 10:00",
    end="10/01/2023 10:30",
    conn="ac",
    site="S",
):
    return f'{sdr},"{site}",{cp},{conn},{kwh},{dur},{start},{end},DD1\n'


def run_clean(rows: list[str], tmp_path):
    (tmp_path / "a.csv").write_text(HEADER + "".join(rows))
    raw = dundee.load_raw(tmp_path, CFG)
    return dundee.clean(raw, CFG)


def test_duration_and_end_fallback(tmp_path):
    df, rep = run_clean(
        [
            row("1", dur="00:30:30"),
            row("2", dur="", start="11/01/2023 10:00", end="11/01/2023 10:45"),
        ],
        tmp_path,
    )
    d = df.set_index("sdr_id")
    assert d.loc["1", "departure_ts"] == pd.Timestamp("2023-01-10 10:30:30", tz="UTC")
    assert d.loc["2", "departure_ts"] == pd.Timestamp("2023-01-11 10:45", tz="UTC")
    assert rep["rows_out"] == 2


def test_every_drop_rule(tmp_path):
    df, rep = run_clean(
        [
            row("ok"),
            row("ok"),  # duplicate SDR ID
            row("blank_kwh", kwh=""),
            row("neg_kwh", kwh="-1"),
            row("zero_kwh", kwh="0"),
            row("end_lt_start", end="10/01/2023 09:00"),
            row("no_dep", dur="", end=""),
            row("zero_dur", dur="00:00:00", end="10/01/2023 10:00"),
            row("short", dur="00:01:59", end="10/01/2023 10:02"),
            row("long", dur="24:00:01", end="11/01/2023 10:00"),
            row("bad_start", start="garbage"),
        ],
        tmp_path,
    )
    d = rep["dropped"]
    assert list(df["sdr_id"]) == ["ok"]
    assert d["unparseable_start"] == 1
    assert d["duplicate_sdr_id"] == 1
    assert d["kwh_blank_or_nonpositive"] == 3
    assert d["end_before_start"] == 1
    assert d["no_usable_departure"] == 1
    assert d["departure_not_after_arrival"] == 1
    assert d["duration_too_short"] == 1
    assert d["duration_too_long"] == 1
    assert rep["rows_in"] == 11


def test_dst_parsing(tmp_path):
    df, _ = run_clean(
        [
            row("summer", start="10/07/2023 10:00", end="10/07/2023 10:30"),
            row("winter", start="10/01/2023 10:00"),
            row("gap", start="26/03/2023 01:30", end="26/03/2023 02:30"),
            row("fold", start="29/10/2023 01:30", end="29/10/2023 02:30"),
        ],
        tmp_path,
    )
    d = df.set_index("sdr_id")["arrival_ts"]
    assert d["summer"] == pd.Timestamp("2023-07-10 09:00", tz="UTC")
    assert d["winter"] == pd.Timestamp("2023-01-10 10:00", tz="UTC")
    assert d["gap"] == pd.Timestamp("2023-03-26 01:00", tz="UTC")  # shifted forward to 02:00 BST
    assert d["fold"] == pd.Timestamp("2023-10-29 00:30", tz="UTC")  # first (BST) occurrence


def test_lanes_three_way_overlap(tmp_path):
    df, _ = run_clean(
        [
            row("a", start="10/01/2023 10:00", dur="01:00:00", end="10/01/2023 11:00"),
            row("b", start="10/01/2023 10:10", dur="01:00:00", end="10/01/2023 11:10"),
            row("c", start="10/01/2023 10:20", dur="01:00:00", end="10/01/2023 11:20"),
            # a's lane frees at 11:00, so lane 1 is reused
            row("d", start="10/01/2023 11:00", dur="00:30:00", end="10/01/2023 11:30"),
            row("e", cp="C2", start="10/01/2023 10:00"),
        ],
        tmp_path,
    )
    full, n0 = dundee.assign_lanes(df)
    assert n0 == 0 and full.set_index("sdr_id").loc["c", "donor_charger_id"] == "C1#3"
    out, n_dropped = dundee.assign_lanes(df, max_lanes=2)
    assert n_dropped == 1 and "c" not in set(out["sdr_id"])
    out = out.set_index("sdr_id")
    assert out.loc["a", "donor_charger_id"] == "C1#1"
    assert out.loc["b", "donor_charger_id"] == "C1#2"
    assert out.loc["d", "donor_charger_id"] == "C1#1"
    assert out.loc["e", "donor_charger_id"] == "C2#1"
    dundee.assert_no_overlap(out.reset_index())


def test_split_boundary(tmp_path):
    df, _ = run_clean(
        [
            row("last_train", start="31/12/2023 23:30", end="01/01/2024 00:00"),
            row("first_test", start="01/01/2024 00:00", end="01/01/2024 00:30"),
        ],
        tmp_path,
    )
    out = dundee.add_split(df, "2023-12-31").set_index("sdr_id")
    assert out.loc["last_train", "split"] == "train"
    assert out.loc["first_test", "split"] == "test"


def test_build_end_to_end(tmp_path, monkeypatch):
    def fake_weather(lat, lon, start, end, name):
        hrs = pd.date_range("2023-01-01", "2024-12-31", freq="h", tz="UTC")
        return pd.DataFrame({"ts_hour": hrs, "temp_c": 5.0, "precip_mm": 0.0, "wind_kmh": 10.0})

    monkeypatch.setattr(weather, "fetch_hourly", fake_weather)
    rows = [
        row("1", start="10/01/2023 10:00", dur="01:00:00"),
        row("2", start="17/01/2023 10:00", end="17/01/2023 11:00", dur="01:00:00"),
        row(
            "3",
            start="10/01/2024 10:00",
            end="10/01/2024 11:00",
            dur="01:00:00",
            cp="NEW",
            conn="rapid",
        ),
        row("4", start="01/01/2023 10:00", dur="00:30:00", cp="C9", conn="ultra_rapid"),
    ]
    raw_dir = tmp_path / "raw" / "dundee"
    raw_dir.mkdir(parents=True)
    (raw_dir / "a.csv").write_text(HEADER + "".join(rows))
    rep = dundee.build(out_root=tmp_path, cfg=CFG)
    proc = tmp_path / "processed"
    s = pd.read_parquet(proc / "donor_sessions.parquet")
    c = pd.read_parquet(proc / "donor_chargers.parquet").set_index("donor_charger_id")
    o = pd.read_parquet(proc / "donor_occupancy.parquet")
    assert list(s.columns) == dundee.SESSION_COLS
    assert s["temp_c"].notna().all() and rep["weather_coverage"] == 1.0
    assert c.loc["NEW#1", "util_basis"] == "test"
    assert c.loc["C1#1", "util_basis"] == "train"
    assert c.loc["C1#1", "sessions_per_day"] == pytest.approx(2 / 8)
    assert c.loc["C1#1", "power_class"] == "AC_22"
    # Tuesday 10:00 local = hour_of_week 34; 1h occupied in 2 Tuesdays of a 2-Tuesday span
    r = o[(o["donor_charger_id"] == "C1#1") & (o["hour_of_week"] == 34)]
    assert r["occupancy_share"].iloc[0] == pytest.approx(1.0)
    assert (proc / "reports" / "dundee_cleaning.json").exists()
