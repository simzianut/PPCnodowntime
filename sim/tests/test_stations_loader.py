import logging

import pandas as pd
import pytest

from sim.data import load_default, load_processed
from sim.stations_seed import (
    CP_COLUMNS,
    LOCATION_COLUMNS,
    load_seed_stations,
    load_stations,
    normalize_connectors,
)
from sim.synthetic import generate

LOC_CSV = (
    "location_id,name,partner,address,county_code,lat,lon,context_type,n_cps,max_kw,"
    "n_ac22,n_dc50,n_hpc\n"
    "L00001, Parcare Test Nord,Penny,Strada A 1,CJ,46.77,23.59,retail,2,50.0,1,1,0\n"
    "L00002,Benzinaria Test,,Strada B 2,B,44.43,26.10,highway,1,150.0,0,0,1\n"
)
CP_CSV = (
    "cp_id,location_id,power_class,max_kw,connectors,current_type,cp_count_estimated\n"
    "L00001-1,L00001,AC_22,22.0,TYPE2+TYPE2,AC,False\n"
    "L00001-2,L00001,DC_50,50.0,CCS2+CHADEMO+TYPE2,DC,False\n"
    "L00002-1,L00002,DC_HPC,150.0,CCS2+CCS2,DC,True\n"
)


def _write_csvs(base):
    p = base / "processed"
    p.mkdir(parents=True, exist_ok=True)
    (p / "ppc_locations.csv").write_text(LOC_CSV, encoding="utf-8-sig")
    (p / "ppc_charge_points.csv").write_text(CP_CSV, encoding="utf-8-sig")


def _write_parquet(base):
    p = base / "processed"
    p.mkdir(parents=True, exist_ok=True)
    locs = pd.DataFrame(
        {
            "location_id": ["L00009"],
            "name": ["Statie Parquet"],
            "operator": ["PPC Blue"],
            "partner": [None],
            "address": ["Strada C 3"],
            "county_code": ["TM"],
            "lat": [45.75],
            "lon": [21.23],
            "context_type": ["urban"],
            "primary_source": ["osm"],
            "confidence": ["medium"],
            "cp_count_estimated": [False],
        }
    )
    cps = pd.DataFrame(
        {
            "cp_id": ["L00009-1"],
            "location_id": ["L00009"],
            "power_class": ["DC_50"],
            "max_kw": [50.0],
            "connectors": [["CCS2", "CHADEMO"]],
            "current_type": ["DC"],
            "cp_count_estimated": [False],
        }
    )
    locs.to_parquet(p / "locations.parquet", index=False)
    cps.to_parquet(p / "charge_points.parquet", index=False)


def test_parquet_has_priority_over_csv(tmp_path):
    _write_csvs(tmp_path)
    _write_parquet(tmp_path)
    locs, cps = load_stations(tmp_path)
    assert list(locs.columns) == LOCATION_COLUMNS and list(cps.columns) == CP_COLUMNS
    assert locs.location_id.tolist() == ["L00009"]
    assert locs.iloc[0].primary_source == "osm" and locs.iloc[0].confidence == "medium"
    cp = cps.iloc[0]
    assert cp.connectors == ["CCS2", "CHAdeMO"]
    assert (cp.county_code, cp.context_type) == ("TM", "urban")


def test_csv_export(tmp_path, caplog):
    _write_csvs(tmp_path)
    with caplog.at_level(logging.INFO, logger="sim.stations"):
        locs, cps = load_stations(tmp_path)
    assert "2 locations, 3 charge points" in caplog.text
    assert list(locs.columns) == LOCATION_COLUMNS and list(cps.columns) == CP_COLUMNS
    assert locs.location_id.tolist() == ["L00001", "L00002"]
    assert locs.name.tolist() == ["Parcare Test Nord", "Benzinaria Test"]
    assert set(locs.primary_source) == {"ampwhere"} and set(locs.confidence) == {"high"}
    assert locs.partner.tolist() == ["Penny", None]
    by_id = cps.set_index("cp_id")
    assert by_id.loc["L00001-1", "connectors"] == ["Type2"]
    assert by_id.loc["L00001-2", "connectors"] == ["CCS2", "CHAdeMO", "Type2"]
    assert by_id.loc["L00002-1", "connectors"] == ["CCS2"]
    assert by_id.loc["L00001-2", "county_code"] == "CJ"
    assert by_id.loc["L00002-1", "context_type"] == "highway"
    assert by_id["max_kw"].dtype == "float64"


def test_seed_fallback(tmp_path):
    locs, cps = load_stations(tmp_path)
    seed_locs, seed_cps = load_seed_stations()
    pd.testing.assert_frame_equal(locs, seed_locs)
    pd.testing.assert_frame_equal(cps, seed_cps)
    allowed = {"Type2", "CCS2", "CHAdeMO"}
    assert all(set(c) <= allowed for c in cps.connectors)


def test_default_base_follows_data_dir(tmp_path, monkeypatch):
    _write_csvs(tmp_path)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    locs, _ = load_stations()
    assert locs.location_id.tolist() == ["L00001", "L00002"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("TYPE2+type2", ["Type2"]),
        ("CHADEMO;CCS2", ["CCS2", "CHAdeMO"]),
        (["ccs", "Type 2", "chademo"], ["CCS2", "CHAdeMO", "Type2"]),
        ("unknown", []),
        (None, []),
        (float("nan"), []),
    ],
)
def test_normalize_connectors(raw, expected):
    assert normalize_connectors(raw) == expected


def test_load_default_uses_local_stations(tmp_path, monkeypatch):
    _write_csvs(tmp_path)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    data = load_default(seed=1)
    assert data.cps.cp_id.tolist() == ["L00001-1", "L00001-2", "L00002-1"]
    assert set(data.sessions.cp_id) <= set(data.cps.cp_id)


def test_load_processed_uses_load_stations(tmp_path):
    _write_csvs(tmp_path)
    p = tmp_path / "processed"
    locs, cps = load_stations(tmp_path)
    synth = generate(seed=0, train_days=2, test_days=1, locations=locs, cps=cps)
    for name in ("sessions", "signals", "context"):
        getattr(synth, name).to_parquet(p / f"{name}.parquet", index=False)
    data = load_processed(tmp_path)
    assert data.locations.location_id.tolist() == ["L00001", "L00002"]
    assert data.cps.connectors.iloc[1] == ["CCS2", "CHAdeMO", "Type2"]
