"""Live mode: status reader (snapshot/delta/matching) and LiveEngine on observed events."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from sim.live import (
    ARRIVE,
    DEPART,
    GO_OFFLINE,
    GO_ONLINE,
    LiveEngine,
    LiveStatusReader,
    cp_status,
)
from sim.stations_seed import load_seed_stations
from sim.synthetic import generate
from sim.types import CHARGING, DEEP_SLEEP, IDLE, OFFLINE, Command, TargetState

ZONE = ZoneInfo("Europe/Bucharest")
UTC = ZoneInfo("UTC")


def ts(s: str) -> float:
    """Local wall time 'YYYY-MM-DD HH:MM:SS' -> epoch seconds."""
    return datetime.fromisoformat(s).replace(tzinfo=ZONE).timestamp()


def refs_for(cp_ids, connectors: int = 1) -> pd.DataFrame:
    """source_refs rows: CP L1-2 gets keys 'uid-L1|L1-2#0'.. (and a location row with no cp)."""
    rows = []
    for cp in cp_ids:
        loc = cp.split("-")[0]
        rows.append(("ampwhere", f"uid-{loc}", loc, None))
        rows += [("ampwhere", f"uid-{loc}|{cp}#{i}", loc, cp) for i in range(connectors)]
    df = pd.DataFrame(rows, columns=["source", "source_id", "location_id", "cp_id"])
    return df.drop_duplicates("source_id").reset_index(drop=True)


def row(t: float, cp: str, status: str, conn: int = 0, sua: float | None = None,
        provider: int = 5) -> dict:  # fmt: skip
    loc = cp.split("-")[0]
    iso = datetime.fromtimestamp(t, UTC).isoformat()
    return {"ts": iso, "source": "ampwhere", "source_location_id": f"uid-{loc}",
            "raw_status": status, "status": status, "source_cp_id": f"{cp}#{conn}",
            "connector": "ccs2", "cp_id": None, "provider_id": provider,
            "source_updated_at": None if sua is None else
            datetime.fromtimestamp(sua, UTC).isoformat().replace("+00:00", "Z")}  # fmt: skip


def write_rows(live_dir: Path, rows: list[dict]) -> None:
    """Append rows to statuses-YYYYMMDD.jsonl by their UTC date, like the collector."""
    live_dir.mkdir(parents=True, exist_ok=True)
    for r in rows:
        d = datetime.fromisoformat(r["ts"]).astimezone(UTC).strftime("%Y%m%d")
        with (live_dir / f"statuses-{d}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(r) + "\n")


CPS = ["L1-1", "L1-2", "L2-1"]


def test_cp_status_aggregation():
    assert cp_status(["AVAILABLE", "CHARGING"]) == "CHARGING"
    assert cp_status(["OFFLINE", "AVAILABLE"]) == "AVAILABLE"
    assert cp_status(["OFFLINE", "OFFLINE"]) == "OFFLINE"
    assert cp_status(["OFFLINE", "UNKNOWN"]) == "UNKNOWN"


def test_reader_snapshot_delta_latest_wins(tmp_path):
    t = ts("2026-11-08 10:00:03")
    snap = [row(t, cp, "AVAILABLE") for cp in CPS] + [row(t, "X9-1", "CHARGING", provider=8)]
    delta = [row(t + 300, "L1-1", "CHARGING", sua=t + 280), row(t + 300, "L1-2", "AVAILABLE")]
    write_rows(tmp_path, snap + delta + [row(t + 600, "L1-1", "AVAILABLE", sua=t + 590)])
    r = LiveStatusReader(tmp_path, refs_for(CPS))
    r.refresh(t + 400)  # the third poll is in the future
    assert r.status_at("L1-1", t + 299) == CHARGING  # event time = source_updated_at
    assert r.status_at("L1-1", t + 250) == IDLE
    assert r.status_at("L2-1", t + 1) == IDLE and r.status_at("X9-1", t + 1) is None
    s = r.summary()
    assert s["rows"] == 6 and s["matched_rows"] == 5 and s["ppc_row_match_rate"] == 1.0
    assert s["polls"] == 2 and s["snapshots"] == 1 and s["cps_with_status"] == 3
    r.refresh(t + 700)
    assert r.status_at("L1-1", t + 595) == IDLE and r.stats.rows == 7


def test_reader_connectors_and_events(tmp_path):
    t = ts("2026-11-08 10:00:03")
    rows = [row(t, cp, "AVAILABLE", conn=i) for cp in CPS for i in range(2)]
    rows += [row(t + 300, "L1-1", "CHARGING", conn=1, sua=t + 200),
             row(t + 300, "L1-2", "OFFLINE", conn=0), row(t + 300, "L1-2", "OFFLINE", conn=1),
             row(t + 600, "L1-1", "AVAILABLE", conn=1, sua=t + 550),
             row(t + 600, "L1-2", "AVAILABLE", conn=0)]  # fmt: skip
    write_rows(tmp_path, rows)
    r = LiveStatusReader(tmp_path, refs_for(CPS, connectors=2))
    r.refresh(t + 1000)
    assert r.initial(t + 10) == {"L1-1": IDLE, "L1-2": IDLE, "L2-1": IDLE}
    ev = r.take_events(t + 100, t + 400)
    assert [(e[1], e[2]) for e in ev] == [("L1-1", ARRIVE), ("L1-2", GO_OFFLINE)]
    assert ev[0][0] == t + 200 and ev[1][0] == t + 300
    ev = r.take_events(t + 700, t + 1000)  # late events are clamped to the slot start
    assert {(e[1], e[2]) for e in ev} == {("L1-1", DEPART), ("L1-2", GO_ONLINE)}
    assert all(e[0] == t + 700 for e in ev)
    assert r.take_events(t + 1000, t + 1300) == []


def test_reader_files_cross_midnight(tmp_path):
    r = LiveStatusReader(tmp_path, refs_for(CPS))
    names = [p.name for p in r.files(ts("2026-11-08 00:30:00"))]
    assert names == ["statuses-20261106.jsonl", "statuses-20261107.jsonl"]
    yday = ts("2026-11-07 23:50:03")
    write_rows(tmp_path, [row(yday, cp, "CHARGING") for cp in CPS])
    r.refresh(ts("2026-11-08 00:30:00"))
    assert r.initial(ts("2026-11-08 00:05:00")) == dict.fromkeys(CPS, CHARGING)


# ---- LiveEngine


@pytest.fixture(scope="module")
def data():
    locs, _ = load_seed_stations()
    return generate(seed=0, train_days=2, test_days=1, locations=locs.head(2))


class SleepAll:
    name = "sleeper"

    def decide(self, state):
        return [Command(cp.cp_id, TargetState.DEEP_SLEEP, "test") for cp in state.cps
                if cp.state.value == "IDLE"]  # fmt: skip


def test_live_engine_arrival_wait_energy(data):
    cps = data.cps["cp_id"].tolist()[:3]
    day = date(2026, 11, 8)
    eng = LiveEngine(data, day, [SleepAll()], initial={cps[0]: IDLE, cps[1]: CHARGING,
                     cps[2]: IDLE}, start_slot=120)  # fmt: skip
    assert eng.slot == 120
    s0 = eng.slot_epoch(120)
    res = eng.step()  # the sleeper puts cps[0] and cps[2] in deep sleep at 10:00
    assert res.slot == 120 and eng.states("sleeper")[0] == DEEP_SLEEP
    eng.feed([(s0 + 360, cps[0], ARRIVE), (s0 + 400, cps[1], DEPART),
              (s0 + 420, cps[2], GO_OFFLINE)])  # fmt: skip
    res = eng.step()
    k = res.kpis["sleeper"]
    wake = eng._ds_wake[0] if not np.isnan(eng._ds_wake[0]) else eng._sb_wake[0]
    assert k["sessions"] == 1 and k["waited_sessions"] == 1
    assert k["wait_min_total"] == pytest.approx(wake / 60.0)
    ev = {e["type"]: e for e in res.events["sleeper"]}
    assert ev["arrival"]["cp_id"] == cps[0] and ev["departure"]["cp_id"] == cps[1]
    st = eng.states("sleeper")
    assert st[2] == OFFLINE and st[1] == IDLE
    eng.step()
    assert eng.states("sleeper")[0] == CHARGING
    assert eng._sims["always_on"].metrics.kpis(0)["wait_min_total"] == 0
    assert 0 < k["overhead_kwh"] and k["saved_kwh"] > 0


def test_live_engine_signals_kept_and_trimmed(data):
    cp = data.cps["cp_id"].iloc[0]
    loc = data.cps["location_id"].iloc[0]
    eng = LiveEngine(data, date(2026, 11, 8), [SleepAll()], initial={cp: IDLE}, start_slot=0)
    for _ in range(15):
        t = pd.Timestamp(eng.t)
        sig = pd.DataFrame(
            {
                "ts": [t, t],
                "location_id": [loc, loc],
                "signal": ["maps_ev_count", "ppcblue_route_intent"],
                "value": [1.0, 1.0],
                "eta_ts": [pd.NaT, t + pd.Timedelta("20min")],
            }
        )
        eng.feed([], sig)  # fmt: skip
        eng.step()
    assert (eng._sig["signal"] == "ppcblue_route_intent").sum() == 15
    assert (eng._sig["signal"] == "maps_ev_count").sum() <= 13
    assert len(eng.timeline("sleeper", cp)["intents"]) == 15
