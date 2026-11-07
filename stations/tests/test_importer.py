import csv

import pytest
from shapely.geometry import Polygon, mapping

from stations import importer as imp

CFG = imp.load_config()


def ch(cid, qr, kw, typ="type2", cur="DC"):
    return {"id": cid, "qr": qr, "power": kw, "current_type": cur, "connector": {"type": typ}}


def loc(uid, name, lat, lon, chargers, pid=5, addr="Strada X, Oras"):
    return {"uid": uid, "provider_id": pid, "name": name, "address": addr,
            "coordinates": [lat, lon], "chargers": chargers}  # fmt: skip


COUNTIES = [
    ("AA", Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])),
    ("BB", Polygon([(10, 0), (20, 0), (20, 10), (10, 10)])),
]


class NoCtx:
    def classify(self, *a):
        return "other"


def test_unit_grouping_by_evse_prefix():
    cs = [ch(1, "RO*PPC*EA*1", 150, "ccs2"), ch(2, "RO*PPC*EA*2", 150, "chademo"),
          ch(3, "RO*PPC*EB*1", 22)]  # fmt: skip
    units = imp.group_units(cs)
    assert sorted(units) == ["RO*PPC*EA", "RO*PPC*EB"]
    a = imp.unit_attrs(units["RO*PPC*EA"], CFG)
    assert a["connectors"] == ["CCS2", "CHADEMO"] and a["max_kw"] == 150
    assert a["power_class"] == "DC_HPC" and a["current_type"] == "DC"


@pytest.mark.parametrize("kw,cls", [(11, "AC_22"), (22, "AC_22"), (22.1, "DC_50"),
                                    (99, "DC_50"), (100, "DC_HPC")])  # fmt: skip
def test_power_class_thresholds(kw, cls):
    assert imp.power_class(kw, CFG) == cls


def test_mislabelled_dc_type2_22_is_ac():
    a = imp.unit_attrs([ch(1, "RO*PPC*EA*1", 22, "type2", "DC")], CFG)
    assert a["current_type"] == "AC" and a["power_class"] == "AC_22"
    a = imp.unit_attrs([ch(1, "RO*PPC*EA*1", 22), ch(2, "RO*PPC*EA*2", 50, "ccs2")], CFG)
    assert a["current_type"] == "DC" and a["power_class"] == "DC_50"


def snap(*locs):
    return {"data": list(locs), "providers_data": {"1": {"name": "ElDrive"}}}


def test_stable_ids_and_other_operators(tmp_path):
    m = tmp_path / "map.csv"
    a = loc("u-a", "Alpha", 5, 5, [ch(1, "RO*PPC*EA*1", 22), ch(2, "RO*PPC*EB*1", 22)])
    b = loc("u-b", "Beta", 5, 15, [ch(3, "RO*PPC*EC*1", 150, "ccs2")])
    o = loc("u-o", "Other", 5, 5, [ch(4, "X*1", 50, "ccs2")], pid=1)
    r1 = imp.build_ampwhere(snap(a, b, o), CFG, COUNTIES, NoCtx(), m)
    locs, cps, refs, oth = r1
    ids1 = {x["name"]: x["location_id"] for x in locs}
    assert ids1 == {"Alpha": "L00001", "Beta": "L00002"}
    assert [c["cp_id"] for c in cps] == ["L00001-1", "L00001-2", "L00002-1"]
    assert len(oth) == 1 and oth[0]["operator"] == "ElDrive"
    assert all("u-o" not in r[1] for r in refs)
    assert ("ampwhere", "u-a|2", "L00001", "L00001-2") in refs
    # rerun with a new location that sorts first: existing ids are kept
    new = loc("u-0", "Aaa", 5, 5, [ch(9, "RO*PPC*ED*1", 22)])
    locs2 = imp.build_ampwhere(snap(new, a, b), CFG, COUNTIES, NoCtx(), m)[0]
    ids2 = {x["name"]: x["location_id"] for x in locs2}
    assert ids2 == {"Alpha": "L00001", "Beta": "L00002", "Aaa": "L00003"}
    with open(m, newline="") as f:
        assert len(list(csv.DictReader(f))) == 3


def test_county_assignment_and_nearest():
    assert imp.county_of(5, 5, COUNTIES) == "AA"
    assert imp.county_of(5, 15, COUNTIES) == "BB"
    assert imp.county_of(5, 25, COUNTIES) == "BB"  # outside: nearest polygon


def test_load_counties_requires_known_names(tmp_path):
    import json

    gj = {"features": [{"properties": {"shapeName": "NOWHERE"},
                        "geometry": mapping(COUNTIES[0][1])}]}  # fmt: skip
    p = tmp_path / "c.json"
    p.write_text(json.dumps(gj))
    with pytest.raises(ValueError):
        imp.load_counties(p)


def test_context_rules():
    # motorway along lon 25 (x), place node at (26, 45)
    clf = imp.ContextClassifier(CFG, [[(25.0, 44.0), (25.0, 46.0)]], [(26.0, 45.0)])
    c = clf.classify
    assert c("Aeroportul Henri Coanda", "", 50, 10) == "airport"
    assert c("Foo", "Aeroport Timisoara, Strada 2", 50, 10) == "airport"
    assert c("Mega Image Otopeni", "Calea X, Otopeni", 50, 10) == "retail"
    assert c("Foo", "", 45.0, 25.003) == "highway"
    assert c("Carrefour X", "", 45.0, 25.003) == "retail"  # retail before highway
    assert c("OMV Foo", "", 50, 10) == "highway"  # fuel brand away from towns
    assert c("OMV Foo", "", 45.0, 26.01) == "urban"  # fuel brand in town
    assert c("Penny Foo", "", 50, 10) == "retail"
    assert c("Foo", "", 45.0, 26.01) == "urban"
    assert c("Foo", "", 50, 10) == "other"
    assert c("Foo Parking", "", 50, 10) == "other"


def test_match_open_within_50m():
    locs = [{"location_id": "L00001", "lat": 45.0, "lon": 25.0}]
    rows = [
        {"lat": "45.0001", "lon": "25.0", "source_ids": "node/1;OCM-5", "name": "n"},
        {"lat": "45.01", "lon": "25.0", "source_ids": "node/2", "name": "far"},
    ]
    refs, unm = imp.match_open(rows, locs, CFG)
    assert refs == [("osm", "node/1", "L00001", None), ("ocm", "OCM-5", "L00001", None)]
    assert [u["name"] for u in unm] == ["far"]


def test_deduplicate_seed_rows_distance_and_priority():
    rows = [
        {
            "name": "Enel X Penny Otopeni",
            "lat": "44.5500",
            "lon": "26.0700",
            "sources": "osm",
            "source_ids": "node/10",
            "max_kw": "",
            "connectors": "TYPE2",
            "confidence": "high",
        },
        {
            "name": "PPC Blue Penny Otopeni",
            "lat": "44.5501",
            "lon": "26.0700",
            "sources": "ocm",
            "source_ids": "OCM-99",
            "max_kw": "50",
            "connectors": "",
            "confidence": "high",
        },
        {
            "name": "PPC Blue Penny Otopeni Nord",
            "lat": "44.5509",
            "lon": "26.0700",
            "sources": "press",
            "source_ids": "press/1",
            "max_kw": "60",
            "connectors": "CCS2",
            "confidence": "medium",
        },
        {
            "name": "Completely Different Site",
            "lat": "44.5510",
            "lon": "26.0700",
            "sources": "osm",
            "source_ids": "node/20",
            "max_kw": "22",
            "connectors": "TYPE2",
            "confidence": "low",
        },
    ]
    deduped = imp.deduplicate_seed_rows(rows)
    assert len(deduped) == 2
    primary = deduped[0]
    assert primary["name"] == "PPC Blue Penny Otopeni"
    assert primary["max_kw"] == "50"
    assert primary["connectors"] == "TYPE2"
    assert primary["sources"] == "ocm;osm;press"
    assert primary["source_ids"] == "OCM-99;node/10;press/1"


def test_open_seed_and_merged_fallback_acceptance(tmp_path):
    import json
    import shutil

    # Cache empty OSM files so run() does not make live network calls
    osm_dir = tmp_path / "raw" / "osm"
    osm_dir.mkdir(parents=True, exist_ok=True)
    (osm_dir / "ro_motorways.json").write_text(json.dumps({"elements": []}), encoding="utf-8")
    (osm_dir / "ro_places.json").write_text(
        json.dumps({"elements": [{"lon": 26.1025, "lat": 44.4268}]}), encoding="utf-8"
    )

    rep_open = imp.run(tmp_path, CFG)
    assert rep_open["mode"] == "seed"
    assert rep_open["locations"] == 116

    # Check Spec 03 §9: every location has county + context_type, and no two < 50 m apart
    import pandas as pd

    dl = pd.read_parquet(tmp_path / "processed" / "locations.parquet")
    assert len(dl) == 116
    assert dl["county_code"].notna().all() and (dl["county_code"] != "").all()
    assert set(dl["context_type"]) <= {"urban", "retail", "highway", "airport", "other"}
    pts = [imp.proj(float(r.lon), float(r.lat)) for r in dl.itertuples()]
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            assert pts[i].distance(pts[j]) >= 50.0

    # Now add local ppc_stations_merged.csv and verify run() loads it
    merged_dir = tmp_path / "raw" / "stations"
    merged_dir.mkdir(parents=True, exist_ok=True)
    merged_csv = merged_dir / "ppc_stations_merged.csv"
    shutil.copy(imp.ROOT / "data" / "seed" / "stations_open.csv", merged_csv)
    with open(merged_csv, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "Extra Merged Site",
                "Penny",
                "Strada Noua 1",
                "Brasov",
                "Brasov",
                "45.6500",
                "25.6000",
                "2",
                "CCS2",
                "150",
                "DC",
                "hermes",
                "hermes/1",
                "high",
                "local",
                "operational",
                "",
            ]
        )
    rep_merged = imp.run(tmp_path, CFG)
    assert rep_merged["mode"] == "merged"
    assert rep_merged["locations"] == 117
