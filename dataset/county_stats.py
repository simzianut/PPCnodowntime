"""County EV statistics: Romanian M1 electric cars per 1000 residents (spec 01 §7)."""

from __future__ import annotations

import csv
import urllib.request
from collections import defaultdict
from pathlib import Path

import yaml

from dataset.dundee import CONFIG_PATH, data_dir

OUT_COLS = [
    "county_code",
    "county_name",
    "ev_count",
    "population",
    "ev_per_1000",
    "ev_per_1000_raw",
    "pool_group",
]

# Upper case, no diacritics, as spelled in the parc-auto file -> ISO 3166-2:RO code.
COUNTY_CODES = {
    "ALBA": "AB", "ARAD": "AR", "ARGES": "AG", "BACAU": "BC", "BIHOR": "BH",
    "BISTRITA NASAUD": "BN", "BOTOSANI": "BT", "BRAILA": "BR", "BRASOV": "BV",
    "BUCURESTI": "B", "BUZAU": "BZ", "CALARASI": "CL", "CARAS SEVERIN": "CS",
    "CLUJ": "CJ", "CONSTANTA": "CT", "COVASNA": "CV", "DIMBOVITA": "DB",
    "DOLJ": "DJ", "GALATI": "GL", "GIURGIU": "GR", "GORJ": "GJ",
    "HARGHITA": "HR", "HUNEDOARA": "HD", "IALOMITA": "IL", "IASI": "IS",
    "ILFOV": "IF", "MARAMURES": "MM", "MEHEDINTI": "MH", "MURES": "MS",
    "NEAMT": "NT", "OLT": "OT", "PRAHOVA": "PH", "SALAJ": "SJ",
    "SATU MARE": "SM", "SIBIU": "SB", "SUCEAVA": "SV", "TELEORMAN": "TR",
    "TIMIS": "TM", "TULCEA": "TL", "VILCEA": "VL", "VASLUI": "VS",
    "VRANCEA": "VN",
}  # fmt: skip


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))["county_ev"]


def download(cfg: dict, raw_dir: Path) -> Path:
    """Fetch the parc-auto file unless it is already present."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    dest = raw_dir / cfg["file_name"]
    if dest.exists() and dest.stat().st_size > 0:
        print(f"skip {dest.name} (exists)")
        return dest
    req = urllib.request.Request(cfg["url"], headers={"User-Agent": cfg["user_agent"]})
    with urllib.request.urlopen(req, timeout=300) as resp:
        body = resp.read()
    dest.write_bytes(body)
    print(f"downloaded {dest.name} ({len(body)} bytes)")
    return dest


def count_evs(path: Path) -> dict[str, int]:
    """Sum TOTAL_VEHICULE of M1 + ELECTRIC rows per county name (as spelled in the file)."""
    totals: dict[str, int] = defaultdict(int)
    with open(path, encoding="latin-1", newline="") as f:
        for r in csv.DictReader(f, delimiter=";"):
            if r["CATEGORIE_COMUNITARA"].strip() != "M1" or r["VALUE_NAME"].strip() != "ELECTRIC":
                continue
            totals[r["JUDET"].strip()] += int(r["TOTAL_VEHICULE"])
    return dict(totals)


def read_population(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["population"] = int(r["population"])
    return rows


def build_rows(ev_by_name: dict[str, int], pop_rows: list[dict], pool_groups: dict) -> list[dict]:
    ev_by_code: dict[str, int] = defaultdict(int)
    for name, n in ev_by_name.items():
        if name not in COUNTY_CODES:
            raise AssertionError(f"unmapped county {name!r}")
        ev_by_code[COUNTY_CODES[name]] += n
    pop = {r["county_code"]: r["population"] for r in pop_rows}
    assert set(ev_by_code) <= set(pop), f"no population for {set(ev_by_code) - set(pop)}"

    group_of = {c: g for g, members in pool_groups.items() for c in members}
    pooled = {
        g: sum(ev_by_code.get(c, 0) for c in members) / sum(pop[c] for c in members) * 1000
        for g, members in pool_groups.items()
    }

    out = []
    for r in sorted(pop_rows, key=lambda r: r["county_code"]):
        c = r["county_code"]
        ev = ev_by_code.get(c, 0)
        raw = ev / r["population"] * 1000
        g = group_of.get(c, "")
        out.append(
            {
                "county_code": c,
                "county_name": r["county_name"],
                "ev_count": ev,
                "population": r["population"],
                "ev_per_1000": round(pooled[g] if g else raw, 3),
                "ev_per_1000_raw": round(raw, 3),
                "pool_group": g,
            }
        )
    return out


def write_rows(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUT_COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def build() -> dict:
    cfg = load_config()
    dd = data_dir()
    raw = download(cfg, dd / "raw" / "ro_ev")
    ev_by_name = count_evs(raw)
    file_total = sum(ev_by_name.values())
    rows = build_rows(
        ev_by_name,
        read_population(dd / "seed" / "ro_county_population.csv"),
        cfg.get("pool_groups") or {},
    )
    assert len(rows) == 42, len(rows)
    total = sum(r["ev_count"] for r in rows)
    assert total == file_total, (total, file_total)
    write_rows(rows, dd / "seed" / "county_ev_stats.csv")
    print(f"national M1 ELECTRIC total: {total}")
    return {"counties": len(rows), "ev_total": total}


def main() -> None:
    build()


if __name__ == "__main__":
    main()
