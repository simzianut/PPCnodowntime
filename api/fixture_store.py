"""Readers for fixtures/api/; RecordedDay is the only reader of run_day.json."""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from sim.config import ROOT
from sim.types import STATE_INDEX, STATES, State

FIXTURES_DIR = ROOT / "fixtures" / "api"
RUN_DAY_FILE = "run_day.json"
KPI_FIELDS = [
    "overhead_kwh", "saved_kwh", "saved_pct", "overhead_ron", "saved_ron", "wait_min_total",
    "wait_min_mean", "wait_min_p95", "sessions", "waited_sessions", "sleep_share",
]  # fmt: skip


@cache
def ron_per_kwh() -> float:
    from stations.store import get_energy_tariffs

    return float(get_energy_tariffs()["net_b2b_ron_per_kwh"])


def with_ron(kpis: dict) -> dict:
    """Kpis with `overhead_ron`/`saved_ron` (= kWh x net B2B tariff) filled in when missing."""
    if "overhead_ron" in kpis and "saved_ron" in kpis:
        return kpis
    r = ron_per_kwh()
    out = {**kpis}
    out.setdefault("overhead_ron", round(kpis["overhead_kwh"] * r, 4))
    out.setdefault("saved_ron", round(kpis["saved_kwh"] * r, 4))
    return {f: out[f] for f in KPI_FIELDS if f in out} | out


def load_json(name: str, fixtures_dir: Path = FIXTURES_DIR):
    """Load a JSON (or .json.gz) fixture file."""
    path = fixtures_dir / name
    if not path.exists() and (fixtures_dir / f"{name}.gz").exists():
        path = fixtures_dir / f"{name}.gz"
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        return json.load(f)


@dataclass(frozen=True)
class Arrival:
    cp: int
    t_s: int
    wait_min: float


@dataclass(frozen=True)
class Cmd:
    cp: int
    from_state: State
    target_state: State
    reason: str


class RecordedDay:
    """A recorded replay day: per-policy states, events, commands and KPIs per slot."""

    def __init__(self, source: Path | str | dict | None = None):
        if source is None:
            raw = load_json(RUN_DAY_FILE)
        elif isinstance(source, dict):
            raw = source
        else:
            path = Path(source)
            raw = load_json(path.name, path.parent)
        self.raw = raw
        self.date = date.fromisoformat(raw["date"])
        self.tz = ZoneInfo(raw["tz"])
        self.slot_s: int = raw["slot_s"]
        self.n_slots: int = raw["n_slots"]
        self.lambda_: float = raw["lambda"]
        self.policies: list[str] = raw["policies"]
        self.cp_ids: list[str] = raw["cp_ids"]
        self.cp_index = {c: i for i, c in enumerate(self.cp_ids)}
        n_cp = len(self.cp_ids)
        self.intents: list[tuple[int, int, int]] = [tuple(x) for x in raw.get("intents", [])]

        self.states: dict[str, np.ndarray] = {}
        self.transitions: dict[str, list[list[tuple[int, int, State]]]] = {}
        self.arrivals: dict[str, list[list[Arrival]]] = {}
        self.departures: dict[str, list[list[tuple[int, int]]]] = {}
        self.commands: dict[str, list[list[Cmd]]] = {}
        self.kpis: dict[str, list[dict]] = {}
        p30 = []
        for p in self.policies:
            st = np.empty((self.n_slots + 1, n_cp), dtype=np.int8)
            st[0] = [STATE_INDEX[State(s)] for s in raw["initial"][p]]
            self.states[p] = st
            self.transitions[p], self.arrivals[p], self.departures[p] = [], [], []
            self.commands[p], self.kpis[p] = [], []
        for k, rec in enumerate(raw["slots"]):
            assert rec["slot"] == k, "slots must be in order"
            for p in self.policies:
                st = self.states[p]
                st[k + 1] = st[k]
                tr = [(c, t, State(s)) for c, t, s in rec["transitions"].get(p, [])]
                for c, _, s in tr:
                    st[k + 1, c] = STATE_INDEX[s]
                self.transitions[p].append(tr)
                self.arrivals[p].append([Arrival(*a) for a in rec["arrivals"].get(p, [])])
                self.departures[p].append([tuple(d) for d in rec["departures"].get(p, [])])
                self.commands[p].append(
                    [Cmd(c, State(f), State(t), r) for c, f, t, r in rec["commands"].get(p, [])]
                )
                self.kpis[p].append(with_ron(rec["kpis"][p]))
            if "p30" in rec:
                p30.append(rec["p30"])
        self.p30 = np.asarray(p30, dtype=np.float32) if p30 else None

    def midnight(self, day: date | None = None) -> datetime:
        d = day or self.date
        return datetime(d.year, d.month, d.day, tzinfo=self.tz)

    def ts(self, t_s: int, day: date | None = None) -> datetime:
        return self.midnight(day) + timedelta(seconds=int(t_s))

    def state_at(self, policy: str, n_done: int) -> np.ndarray:
        """CP state codes after `n_done` slots have been processed."""
        return self.states[policy][n_done]

    def zero_kpis(self) -> dict:
        return {f: 0 for f in KPI_FIELDS}

    def kpis_at(self, policy: str, n_done: int) -> dict:
        return self.kpis[policy][n_done - 1] if n_done > 0 else self.zero_kpis()

    @staticmethod
    def state_name(code: int) -> str:
        return STATES[int(code)].value
