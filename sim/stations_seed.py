"""Minimal reader of data/seed/stations_open.csv with provisional IDs (decision 0024).

Replaced by stations.store once T4/T5 exist.
"""

from __future__ import annotations

from pathlib import Path

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
        conns = [c for c in str(r["connectors"]).split(";") if c and c != "unknown"]
        if not isinstance(r["connectors"], str):
            conns = []
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
