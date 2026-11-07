"""PPC Blue station importer (spec 03 §2). Run: python -m stations."""

from __future__ import annotations

import csv
import gzip
import json
import logging
import os
import re
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd
import yaml
from dotenv import load_dotenv
from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import LineString, Point, shape

log = logging.getLogger("stations")
ROOT = Path(__file__).resolve().parent.parent

COUNTY_CODES = {
    "ALBA": "AB", "ARAD": "AR", "ARGES": "AG", "BACAU": "BC", "BIHOR": "BH",
    "BISTRITA-NASAUD": "BN", "BOTOSANI": "BT", "BRAILA": "BR", "BRASOV": "BV",
    "BUCURESTI": "B", "BUZAU": "BZ", "CALARASI": "CL", "CARAS-SEVERIN": "CS",
    "CLUJ": "CJ", "CONSTANTA": "CT", "COVASNA": "CV", "DAMBOVITA": "DB", "DOLJ": "DJ",
    "GALATI": "GL", "GIURGIU": "GR", "GORJ": "GJ", "HARGHITA": "HR", "HUNEDOARA": "HD",
    "IALOMITA": "IL", "IASI": "IS", "ILFOV": "IF", "MARAMURES": "MM", "MEHEDINTI": "MH",
    "MURES": "MS", "NEAMT": "NT", "OLT": "OT", "PRAHOVA": "PH", "SALAJ": "SJ",
    "SATU MARE": "SM", "SIBIU": "SB", "SUCEAVA": "SV", "TELEORMAN": "TR", "TIMIS": "TM",
    "TULCEA": "TL", "VALCEA": "VL", "VASLUI": "VS", "VRANCEA": "VN",
}  # fmt: skip

_TO_M = Transformer.from_crs(4326, 3844, always_xy=True).transform


def load_config(path: Path | None = None) -> dict:
    with open(path or ROOT / "config" / "stations.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fold(text: str | None) -> str:
    """Lower-case, accent-free text."""
    s = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def proj(lon: float, lat: float) -> Point:
    return Point(*_TO_M(lon, lat))


# ---------- counties ----------


def load_counties(
    path: Path, name_codes: dict[str, str] = COUNTY_CODES
) -> list[tuple[str, object]]:
    with open(path, encoding="utf-8") as f:
        feats = json.load(f)["features"]
    out = []
    for ft in feats:
        name = ft["properties"]["shapeName"]
        if name not in name_codes:
            raise ValueError(f"county name without ISO code: {name}")
        out.append((name_codes[name], shape(ft["geometry"])))
    return out


def county_of(lat: float, lon: float, counties: list[tuple[str, object]]) -> str:
    """Containing polygon, else the nearest one (coast points)."""
    pt = Point(lon, lat)
    for code, geom in counties:
        if geom.contains(pt):
            return code
    return min(counties, key=lambda c: c[1].distance(pt))[0]


# ---------- units ----------


def power_class(kw: float, cfg: dict) -> str:
    if kw <= cfg["power"]["ac_max_kw"]:
        return "AC_22"
    return "DC_HPC" if kw >= cfg["power"]["hpc_min_kw"] else "DC_50"


def group_units(chargers: list[dict]) -> dict[str, list[dict]]:
    """EVSE prefix -> connectors of one physical unit."""
    units: dict[str, list[dict]] = {}
    for ch in chargers:
        qr = ch.get("qr") or f"ID*{ch['id']}*0"
        units.setdefault(qr.rsplit("*", 1)[0], []).append(ch)
    return units


def unit_attrs(conns: list[dict], cfg: dict) -> dict:
    kws = [float(c.get("power") or 0) for c in conns]
    types = [(c.get("connector") or {}).get("type", "") for c in conns]
    max_kw = max(kws)
    ac_max = cfg["power"]["ac_max_kw"]
    all_ac = all(t == "type2" and k <= ac_max for t, k in zip(types, kws, strict=True))
    return {
        "connectors": [t.upper() for t in types],
        "max_kw": max_kw,
        "current_type": "AC" if all_ac else "DC",
        "power_class": power_class(max_kw, cfg),
    }


# ---------- context ----------


def _kw_pattern(words: list[str]) -> re.Pattern:
    return re.compile(r"\b(" + "|".join(re.escape(fold(w)) for w in words) + r")\b")


def retail_partner(name: str, address: str, cfg: dict) -> str | None:
    m = _kw_pattern(cfg["context"]["retail_keywords"]).search(fold(f"{name} {address}"))
    return m.group(1).title() if m else None


def is_airport(name: str, address: str, cfg: dict) -> bool:
    return bool(_kw_pattern(cfg["context"]["airport_keywords"]).search(fold(f"{name} {address}")))


class ContextClassifier:
    def __init__(self, cfg: dict, motorways: list, places: list):
        self.cfg = cfg
        lines = [LineString([_TO_M(x, y) for x, y in w]) for w in motorways if len(w) > 1]
        self.line_tree = STRtree(lines) if lines else None
        pts = [proj(lon, lat) for lon, lat in places]
        self.pt_tree = STRtree(pts) if pts else None

    def classify(self, name: str, address: str, lat: float, lon: float) -> str:
        c = self.cfg["context"]
        if is_airport(name, address, self.cfg):
            return "airport"
        if retail_partner(name, address, self.cfg):
            return "retail"
        p = proj(lon, lat)
        if self.line_tree is not None:
            if len(self.line_tree.query(p, predicate="dwithin", distance=c["highway_m"])):
                return "highway"
        near_town = self.pt_tree is not None and len(
            self.pt_tree.query(p, predicate="dwithin", distance=c["urban_km"] * 1000)
        )
        if not near_town and _kw_pattern(c["fuel_keywords"]).search(fold(f"{name} {address}")):
            return "highway"
        if near_town:
            return "urban"
        return "other"


def _overpass(query: str, cfg: dict) -> dict:
    o = cfg["overpass"]
    req = urllib.request.Request(
        o["url"],
        data=urllib.parse.urlencode({"data": query}).encode(),
        headers={"User-Agent": o["user_agent"]},
    )
    with urllib.request.urlopen(req, timeout=o["timeout_s"]) as r:
        return json.load(r)


def _cached(path: Path, query: str, cfg: dict) -> dict:
    if not path.exists():
        log.info("overpass query -> %s", path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_overpass(query, cfg)), encoding="utf-8")
    return json.loads(path.read_text(encoding="utf-8"))


_AREA = 'area["ISO3166-1"="RO"]->.a;'


def load_motorways(data_dir: Path, cfg: dict) -> list[list[tuple[float, float]]]:
    q = f'[out:json][timeout:200];{_AREA}way["highway"="motorway"](area.a);out geom;'
    d = _cached(data_dir / "raw" / "osm" / "ro_motorways.json", q, cfg)
    return [[(p["lon"], p["lat"]) for p in e["geometry"]] for e in d["elements"] if "geometry" in e]


def load_places(data_dir: Path, cfg: dict) -> list[tuple[float, float]]:
    q = f'[out:json][timeout:200];{_AREA}node["place"~"^(city|town)$"](area.a);out;'
    d = _cached(data_dir / "raw" / "osm" / "ro_places.json", q, cfg)
    return [(e["lon"], e["lat"]) for e in d["elements"]]


# ---------- ids ----------


def assign_location_ids(keys_sorted: list[str], map_path: Path | None) -> dict[str, str]:
    """Reuse ids from map_path, append new keys (already sorted) as max+1."""
    ids: dict[str, str] = {}
    if map_path and map_path.exists():
        with open(map_path, encoding="utf-8", newline="") as f:
            ids = {r["uid"]: r["location_id"] for r in csv.DictReader(f)}
    nxt = max((int(v[1:]) for v in ids.values()), default=0) + 1
    for k in keys_sorted:
        if k not in ids:
            ids[k] = f"L{nxt:05d}"
            nxt += 1
    if map_path:
        map_path.parent.mkdir(parents=True, exist_ok=True)
        with open(map_path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["uid", "location_id"])
            w.writerows(sorted(ids.items(), key=lambda kv: kv[1]))
    return ids


# ---------- build ----------

LOC_COLS = ["location_id", "name", "operator", "partner", "address", "county_code", "lat", "lon",
            "context_type", "primary_source", "confidence", "cp_count_estimated"]  # fmt: skip
CP_COLS = ["cp_id", "location_id", "power_class", "max_kw", "connectors", "current_type",
           "cp_count_estimated"]  # fmt: skip
OTHER_COLS = ["uid", "provider_id", "operator", "name", "lat", "lon", "county_code",
              "n_connectors", "max_kw", "power_class"]  # fmt: skip


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else v


def build_ampwhere(snap: dict, cfg: dict, counties, clf, map_path):
    pid = cfg["ppc_provider_id"]
    provs = snap.get("providers_data", {})
    ppc, other = [], []
    for loc in snap["data"]:
        (ppc if loc["provider_id"] == pid else other).append(loc)
    for loc in ppc + other:
        loc["_county"] = county_of(*loc["coordinates"], counties)
    ppc.sort(key=lambda r: (r["_county"], r["name"] or "", r["uid"]))
    ids = assign_location_ids([r["uid"] for r in ppc], map_path)

    locs, cps, refs = [], [], []
    for loc in ppc:
        lid = ids[loc["uid"]]
        lat, lon = loc["coordinates"]
        name, addr = loc["name"] or "", loc.get("address") or ""
        locs.append({
            "location_id": lid, "name": name, "operator": "PPC Blue",
            "partner": retail_partner(name, addr, cfg), "address": addr,
            "county_code": loc["_county"], "lat": lat, "lon": lon,
            "context_type": clf.classify(name, addr, lat, lon),
            "primary_source": "ampwhere", "confidence": "high", "cp_count_estimated": False,
        })  # fmt: skip
        refs.append(("ampwhere", loc["uid"], lid, None))
        units = group_units(loc["chargers"])
        for n, prefix in enumerate(sorted(units), 1):
            conns = units[prefix]
            cp_id = f"{lid}-{n}"
            cps.append({"cp_id": cp_id, "location_id": lid, **unit_attrs(conns, cfg),
                        "cp_count_estimated": False})  # fmt: skip
            refs += [("ampwhere", f"{loc['uid']}|{c['id']}", lid, cp_id) for c in conns]
    oth = []
    for loc in other:
        chs = loc.get("chargers") or []
        mk = max((float(c.get("power") or 0) for c in chs), default=0.0)
        oth.append({
            "uid": loc["uid"], "provider_id": loc["provider_id"],
            "operator": (provs.get(str(loc["provider_id"])) or {}).get("name"),
            "name": loc["name"], "lat": loc["coordinates"][0], "lon": loc["coordinates"][1],
            "county_code": loc["_county"], "n_connectors": len(chs), "max_kw": mk,
            "power_class": power_class(mk, cfg),
        })  # fmt: skip
    return locs, cps, refs, oth


def match_open(seed_rows: list[dict], locs: list[dict], cfg: dict):
    """Attach OSM/OCM ids of open rows to AmpWhere locations within match_radius_m."""
    pts = [proj(loc["lon"], loc["lat"]) for loc in locs]
    tree = STRtree(pts)
    refs, unmatched = [], []
    for r in seed_rows:
        p = proj(float(r["lon"]), float(r["lat"]))
        i = int(tree.nearest(p))
        if p.distance(pts[i]) > cfg["match_radius_m"]:
            unmatched.append(r)
            continue
        for sid in filter(None, (r.get("source_ids") or "").split(";")):
            src = "ocm" if sid.upper().startswith("OCM") else "osm"
            refs.append((src, sid, locs[i]["location_id"], None))
    return refs, unmatched


def build_seed(rows: list[dict], cfg: dict, counties, clf):
    """Fallback: open-data CSV, estimated 2 CPs per location when the count is unknown."""
    rows = [dict(r, _c=county_of(float(r["lat"]), float(r["lon"]), counties)) for r in rows]
    rows.sort(key=lambda r: (r["_c"], r["name"], r["lat"], r["lon"]))
    locs, cps, refs = [], [], []
    for i, r in enumerate(rows, 1):
        lid, lat, lon = f"L{i:05d}", float(r["lat"]), float(r["lon"])
        n_known = _num(r.get("n_charge_points"))
        est = n_known is None
        kw = _num(r.get("max_kw"))
        if kw is None:
            pc = "DC_50" if (r.get("current_types") or "") == "DC" else "AC_22"
        else:
            pc = power_class(kw, cfg)
        conns = [c.upper() for c in (r.get("connectors") or "").split(";") if c]
        addr = r.get("address") or ""
        locs.append({
            "location_id": lid, "name": r["name"], "operator": "PPC Blue",
            "partner": r.get("partner") or None, "address": addr,
            "county_code": r["_c"], "lat": lat, "lon": lon,
            "context_type": clf.classify(r["name"], addr, lat, lon),
            "primary_source": r["sources"].split(";")[0], "confidence": r["confidence"],
            "cp_count_estimated": est,
        })  # fmt: skip
        for n in range(1, int(n_known or 2) + 1):
            cps.append({"cp_id": f"{lid}-{n}", "location_id": lid, "power_class": pc,
                        "max_kw": kw, "connectors": conns,
                        "current_type": "AC" if pc == "AC_22" else "DC",
                        "cp_count_estimated": est})  # fmt: skip
        for sid in filter(None, (r.get("source_ids") or "").split(";")):
            refs.append(("ocm" if sid.upper().startswith("OCM") else "osm", sid, lid, None))
    return locs, cps, refs


def upsert_db(locs, cps, refs, url: str) -> bool:
    """Upsert only if the T5 tables exist; never creates tables."""
    from sqlalchemy import create_engine, inspect, text

    eng = create_engine(url)
    if not {"locations", "charge_points", "source_refs"} <= set(inspect(eng).get_table_names()):
        log.info("DB tables missing (T5 migrations), skipping DB upsert")
        return False
    loc_sql = (
        "INSERT INTO locations(location_id,name,operator,partner,address,county_code,geom,"
        "context_type,primary_source,confidence,cp_count_estimated) VALUES "
        "(:location_id,:name,:operator,:partner,:address,:county_code,"
        "ST_SetSRID(ST_MakePoint(:lon,:lat),4326)::geography,:context_type,"
        ":primary_source,:confidence,:cp_count_estimated) ON CONFLICT (location_id) DO UPDATE "
        "SET name=EXCLUDED.name,partner=EXCLUDED.partner,address=EXCLUDED.address,"
        "county_code=EXCLUDED.county_code,geom=EXCLUDED.geom,"
        "context_type=EXCLUDED.context_type,primary_source=EXCLUDED.primary_source,"
        "confidence=EXCLUDED.confidence,cp_count_estimated=EXCLUDED.cp_count_estimated"
    )
    cp_sql = (
        "INSERT INTO charge_points(cp_id,location_id,power_class,max_kw,connectors,"
        "current_type) VALUES (:cp_id,:location_id,:power_class,:max_kw,:connectors,"
        ":current_type) ON CONFLICT (cp_id) DO UPDATE SET power_class=EXCLUDED.power_class,"
        "max_kw=EXCLUDED.max_kw,connectors=EXCLUDED.connectors,"
        "current_type=EXCLUDED.current_type"
    )
    ref_sql = (
        "INSERT INTO source_refs(source,source_id,location_id,cp_id) VALUES "
        "(:source,:source_id,:location_id,:cp_id) ON CONFLICT (source,source_id) DO UPDATE "
        "SET location_id=EXCLUDED.location_id,cp_id=EXCLUDED.cp_id"
    )
    cp_rows = [{k: v for k, v in r.items() if k != "cp_count_estimated"} for r in cps]
    ref_rows = [dict(zip(("source", "source_id", "location_id", "cp_id"), r, strict=True))
                for r in refs]  # fmt: skip
    with eng.begin() as c:
        c.execute(text(loc_sql), locs)
        c.execute(text(cp_sql), cp_rows)
        c.execute(text(ref_sql), ref_rows)
    return True


def run(data_dir: Path, cfg: dict | None = None, seed_dir: Path | None = None) -> dict:
    cfg = cfg or load_config()
    seed_dir = seed_dir or ROOT / "data" / "seed"
    counties = load_counties(seed_dir / "ro_counties.geojson")
    clf = ContextClassifier(cfg, load_motorways(data_dir, cfg), load_places(data_dir, cfg))
    with open(seed_dir / "stations_open.csv", encoding="utf-8", newline="") as f:
        seed_rows = list(csv.DictReader(f))
    snap_path = data_dir / "raw" / "live" / "ampwhere_snapshot_latest.json.gz"
    oth, unmatched, mode = [], [], "seed"
    if snap_path.exists():
        mode = "ampwhere"
        with gzip.open(snap_path, "rt", encoding="utf-8") as f:
            snap = json.load(f)
        map_path = data_dir / "raw" / "stations" / "location_id_map.csv"
        locs, cps, refs, oth = build_ampwhere(snap, cfg, counties, clf, map_path)
        extra, unmatched = match_open(seed_rows, locs, cfg)
        refs += extra
        log.info("open-data rows unmatched (not added): %d", len(unmatched))
    else:
        log.info("AmpWhere snapshot absent, using data/seed/stations_open.csv")
        locs, cps, refs = build_seed(seed_rows, cfg, counties, clf)

    out = data_dir / "processed"
    (out / "reports").mkdir(parents=True, exist_ok=True)
    dl = pd.DataFrame(locs, columns=LOC_COLS)
    dc = pd.DataFrame(cps, columns=CP_COLS)
    dr = pd.DataFrame(refs, columns=["source", "source_id", "location_id", "cp_id"])
    dr = dr.drop_duplicates(["source", "source_id"])
    do = pd.DataFrame(oth, columns=OTHER_COLS)
    for name, df in [("locations", dl), ("charge_points", dc), ("source_refs", dr),
                     ("other_chargers", do)]:  # fmt: skip
        df.to_parquet(out / f"{name}.parquet", index=False)

    url = os.environ.get("DATABASE_URL")
    if url:
        upsert_db(locs, cps, refs, url)
    else:
        log.info("DATABASE_URL not set, skipping DB upsert")

    def counts(s: pd.Series) -> dict:
        return {k: int(v) for k, v in s.value_counts().items()}

    n_conn = int(dr.cp_id.notna().sum()) if mode == "ampwhere" else None
    report = {
        "mode": mode,
        "locations": len(dl),
        "charge_points": len(dc),
        "connectors": n_conn,
        "by_source": counts(dl.primary_source),
        "refs_by_source": counts(dr.source),
        "by_county": counts(dl.county_code),
        "cps_by_power_class": counts(dc.power_class),
        "by_context_type": counts(dl.context_type),
        "open_data_unmatched": [
            {"name": r["name"], "lat": r["lat"], "lon": r["lon"], "source_ids": r["source_ids"]}
            for r in unmatched
        ],
        "other_chargers": len(do),
    }
    (out / "reports" / "stations_coverage.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    load_dotenv(ROOT / ".env")
    data_dir = Path(os.environ.get("DATA_DIR", ROOT / "data"))
    r = run(data_dir)
    print(f"mode={r['mode']} locations={r['locations']} cps={r['charge_points']}")
    print("power_class:", r["cps_by_power_class"])
    print("context:", r["by_context_type"])
    print("open-data unmatched:", len(r["open_data_unmatched"]), "other:", r["other_chargers"])
