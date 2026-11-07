import logging

import pandas as pd

from sim.stations_seed import CP_COLUMNS, LOCATION_COLUMNS, load_seed_stations, load_stations

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
