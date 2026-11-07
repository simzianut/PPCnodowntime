"""Station loading for the sim: importer output, PPC export or the open seed (decision 0024)."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from sim.config import ROOT

SEED_CSV = ROOT / "data" / "seed" / "stations_open.csv"

COUNTY_CODES = {
    "Alba": "AB", "Arad": "AR", "Argeș": "AG", "Bacău": "BC", "Bihor": "BH",
    "Bistrița-Năsăud": "BN", "Botoșani": "BT", "Brașov": "BV", "Brăila": "BR",
    "București": "B", "Buzău": "BZ", "Caraș-Severin": "CS", "Călărași": "CL",
    "Cluj": "CJ", "Constanța": "CT", "Covasna": "CV", "Dâmbovița": "DB", "Dolj": "DJ",
    "Galați": "GL", "Giurgiu": "GR", "Gorj": "GJ", "Harghita": "HR", "Hunedoara": "HD",
    "Ialomița": "IL", "Iași": "IS", "Ilfov": "IF", "Maramureș": "MM", "Mehedinți": "MH",
    "Mureș": "MS", "Neamț": "NT", "Olt": "OT", "Prahova": "PH", "Satu Mare": "SM",
    "Sălaj": "SJ", "Sibiu": "SB", "Suceava": "SV", "Teleorman": "TR", "Timiș": "TM",
    "Tulcea": "TL", "Vaslui": "VS", "Vâlcea": "VL", "Vrancea": "VN",
}  # fmt: skip

LOCATION_COLUMNS = [
    "location_id", "name", "partner", "address", "county_code", "lat", "lon",
    "context_type", "primary_source", "confidence",
]  # fmt: skip
CP_COLUMNS = [
    "cp_id", "location_id", "power_class", "max_kw", "connectors", "current_type",
    "county_code", "context_type",
]  # fmt: skip

_AC_CONNECTORS = {"Type2"}
_DC_CONNECTORS = {"CCS2", "CHAdeMO"}
_CONNECTOR_ALIASES = {"TYPE2": "Type2", "CCS2": "CCS2", "CCS": "CCS2", "CHADEMO": "CHAdeMO"}

log = logging.getLogger("sim.stations")


def normalize_connectors(value: object) -> list[str]:
    """Sorted unique connector types in the seed spelling (Type2, CCS2, CHAdeMO)."""
    if isinstance(value, str):
        items = value.replace("+", ";").split(";")
    elif isinstance(value, list | tuple | np.ndarray):
        items = [str(v) for v in value]
    else:
        return []
    out = set()
    for raw in items:
        c = raw.strip()
        if not c or c.lower() == "unknown":
            continue
        key = c.upper().replace(" ", "").replace("_", "").replace("-", "")
        out.add(_CONNECTOR_ALIASES.get(key, c))
    return sorted(out)


def provisional_location_id(row_number: int) -> str:
    return f"L1{row_number:04d}"


def _context_type(row: pd.Series) -> str:
    name = str(row.get("name") or "")
    if "aeroport" in name.lower() or "airport" in name.lower():
        return "airport"
    partner = row.get("partner")
    if isinstance(partner, str) and partner and partner != "fuel station":
        return "retail"
    if partner == "fuel station":
        return "highway"
    return "urban"


def _power_class(current_type: str, max_kw: float | None) -> str:
    if current_type == "AC":
        return "AC_22"
    if max_kw is not None and max_kw > 60:
        return "DC_HPC"
    return "DC_50"


def _cp_types(n: int, current_types: str | None) -> list[str]:
    kinds = (current_types or "").split(";") if isinstance(current_types, str) else []
    if kinds == ["AC"]:
        return ["AC"] * n
    if kinds == ["DC"] or not kinds:
        return ["DC"] * n
    # AC;DC: first CP DC, then alternate.
    return ["DC" if i % 2 == 0 else "AC" for i in range(n)]


def load_seed_stations(path: Path = SEED_CSV) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (locations, charge_points) with provisional IDs `L1nnnn`."""
    raw = pd.read_csv(path)
    locs, cps = [], []
    for i, row in enumerate(raw.itertuples(index=False), start=1):
        r = pd.Series(row._asdict())
        loc_id = provisional_location_id(i)
        ctx = _context_type(r)
        county = COUNTY_CODES.get(r["county"], str(r["county"]))
        locs.append(
            {
                "location_id": loc_id,
                "name": r["name"],
                "partner": r["partner"] if isinstance(r["partner"], str) else None,
                "address": r["address"] if isinstance(r["address"], str) else None,
                "county_code": county,
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
                "context_type": ctx,
                "primary_source": str(r["sources"]).split(";")[0],
                "confidence": r["confidence"],
            }
        )
        n = int(r["n_charge_points"]) if pd.notna(r["n_charge_points"]) else 2
        max_kw = float(r["max_kw"]) if pd.notna(r["max_kw"]) else None
        conns = normalize_connectors(r["connectors"])
        for k, ct in enumerate(_cp_types(n, r["current_types"]), start=1):
            usable = (_AC_CONNECTORS if ct == "AC" else _DC_CONNECTORS) & set(conns)
            pc = _power_class(ct, max_kw if ct == "DC" else None)
            cps.append(
                {
                    "cp_id": f"{loc_id}-{k}",
                    "location_id": loc_id,
                    "power_class": pc,
                    "max_kw": (22.0 if ct == "AC" else max_kw),
                    "connectors": sorted(usable),
                    "current_type": ct,
                    "county_code": county,
                    "context_type": ctx,
                }
            )
    return pd.DataFrame(locs, columns=LOCATION_COLUMNS), pd.DataFrame(cps, columns=CP_COLUMNS)


def _with_location_attrs(cps: pd.DataFrame, locs: pd.DataFrame) -> pd.DataFrame:
    attrs = locs[["location_id", "county_code", "context_type"]]
    cps = cps.drop(columns=["county_code", "context_type"], errors="ignore")
    cps = cps.merge(attrs, on="location_id", how="left")
    cps["connectors"] = [normalize_connectors(c) for c in cps["connectors"]]
    cps["max_kw"] = cps["max_kw"].astype("float64")
    return cps[CP_COLUMNS].reset_index(drop=True)


def _clean_locations(locs: pd.DataFrame) -> pd.DataFrame:
    locs = locs.copy()
    if "primary_source" not in locs:
        locs["primary_source"] = "ampwhere"
    if "confidence" not in locs:
        locs["confidence"] = "high"
    locs["primary_source"] = locs["primary_source"].fillna("ampwhere")
    locs["confidence"] = locs["confidence"].fillna("high")
    locs["name"] = locs["name"].astype(str).str.strip()
    for col in ("partner", "address"):
        locs[col] = locs[col].astype(object).where(locs[col].notna(), None)
    locs["lat"] = locs["lat"].astype("float64")
    locs["lon"] = locs["lon"].astype("float64")
    return locs[LOCATION_COLUMNS].reset_index(drop=True)


def load_stations(base: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(locations, charge_points): importer parquet, else PPC CSV export, else the seed."""
    if base is None:
        from sim.data import data_dir

        base = data_dir()
    p = Path(base) / "processed"
    loc_pq, cp_pq = p / "locations.parquet", p / "charge_points.parquet"
    loc_csv, cp_csv = p / "ppc_locations.csv", p / "ppc_charge_points.csv"
    if loc_pq.exists() and cp_pq.exists():
        source = str(loc_pq.parent)
        locs = _clean_locations(pd.read_parquet(loc_pq))
        cps = _with_location_attrs(pd.read_parquet(cp_pq), locs)
    elif loc_csv.exists() and cp_csv.exists():
        source = str(loc_csv.parent) + " (ppc_*.csv)"
        locs = _clean_locations(
            pd.read_csv(loc_csv, encoding="utf-8-sig", dtype={"location_id": str})
        )
        raw_cps = pd.read_csv(
            cp_csv, encoding="utf-8-sig", dtype={"cp_id": str, "location_id": str}
        )
        cps = _with_location_attrs(raw_cps, locs)
    else:
        source = "seed " + str(SEED_CSV)
        locs, cps = load_seed_stations()
    log.info("stations from %s: %d locations, %d charge points", source, len(locs), len(cps))
    return locs, cps
