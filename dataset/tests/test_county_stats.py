from __future__ import annotations

import csv
from pathlib import Path

from dataset import county_stats

HEADER = (
    ";JUDET;CATEGORIE_NATIONALA;CATEGORIE_COMUNITARA;MARCA;DESCRIERE_COMERCIALA;"
    "VALUE_NAME;TOTAL_VEHICULE\n"
)
SEED = Path(__file__).resolve().parents[2] / "data" / "seed"


def _write(tmp_path):
    lines = [
        "1;CLUJ;AUTOTURISM;M1     ;TESLA;MODEL 3;ELECTRIC;10",
        "2;CLUJ;AUTOTURISM;M1     ;TESLA;MODEL Y;ELECTRIC;5",
        "3;CLUJ;AUTOTURISM;M1     ;DACIA;SANDERO;BENZINA;99",  # wrong fuel
        "4;CLUJ;AUTOBUZ;M3     ;ATP;EV105;ELECTRIC;7",  # wrong category
        "5;BUCURESTI;AUTOTURISM;M1     ;RENAULT;ZOE;ELECTRIC;30",
        "6;ILFOV;AUTOTURISM;M1     ;RENAULT;ZOE;ELECTRIC;10",
        "7;DIMBOVITA;AUTOTURISM;M1     ;KIA;NIRO;ELECTRIC;2",
    ]
    p = tmp_path / "x.csv"
    p.write_bytes((HEADER + "\n".join(lines) + "\n").encode("latin-1"))
    return p


def test_filter_and_aggregate(tmp_path):
    ev = county_stats.count_evs(_write(tmp_path))
    assert ev == {"CLUJ": 15, "BUCURESTI": 30, "ILFOV": 10, "DIMBOVITA": 2}


def test_pooling(tmp_path):
    ev = county_stats.count_evs(_write(tmp_path))
    pops = [
        {"county_code": c, "county_name": c, "population": p}
        for c, p in [("B", 1000), ("IF", 3000), ("CJ", 500), ("DB", 100), ("AB", 100)]
    ]
    rows = {r["county_code"]: r for r in county_stats.build_rows(ev, pops, {"B_IF": ["B", "IF"]})}
    assert rows["B"]["ev_count"] == 30 and rows["B"]["population"] == 1000
    assert rows["B"]["ev_per_1000"] == rows["IF"]["ev_per_1000"] == 10.0
    assert rows["B"]["ev_per_1000_raw"] == 30.0 and rows["IF"]["ev_per_1000_raw"] == 3.333
    assert rows["B"]["pool_group"] == "B_IF"
    assert rows["CJ"]["ev_per_1000"] == rows["CJ"]["ev_per_1000_raw"] == 30.0
    assert rows["CJ"]["pool_group"] == ""
    assert rows["DB"]["ev_count"] == 2  # DIMBOVITA maps to DB
    assert rows["AB"]["ev_count"] == 0
    assert list(rows) == sorted(rows)


def test_seed_files():
    with open(SEED / "ro_county_population.csv", encoding="utf-8") as f:
        pop = list(csv.DictReader(f))
    assert len(pop) == 42
    assert len({r["county_code"] for r in pop}) == 42
    assert sum(int(r["population"]) for r in pop) == 19_041_322
    with open(SEED / "county_ev_stats.csv", encoding="utf-8") as f:
        stats = list(csv.DictReader(f))
    assert len(stats) == 42
    assert len({r["county_code"] for r in stats}) == 42
    assert sum(int(r["population"]) for r in stats) == 19_041_322
