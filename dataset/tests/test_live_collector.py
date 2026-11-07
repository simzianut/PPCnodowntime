import copy
import gzip
import json
from datetime import UTC, datetime

import pytest

from dataset import live_collector
from dataset.live.adapters import (
    AmpWhereAdapter,
    FileReplayAdapter,
    StopCollecting,
    normalise_status,
    parse_delta,
    parse_snapshot,
)
from dataset.live.config import load_config
from dataset.live.models import LiveStatus
from dataset.live.sinks import DbSink, JsonlSink

TS = datetime(2026, 11, 7, 14, 5, tzinfo=UTC)


def _st(raw="charging", status="CHARGING", loc="L1"):
    return LiveStatus(ts=TS, source="t", source_location_id=loc, raw_status=raw, status=status)


def _ch(cid, status, kind="ccs2", ct="dc"):
    return {
        "id": cid,
        "id_display": f"X*{cid}",
        "qr": f"RO*T*X*{cid}",
        "power": 50,
        "current_type": ct,
        "connector": {"type": kind},
        "status": status,
    }


def snapshot():
    return {
        "success": True,
        "data": [
            {
                "uid": "u1",
                "provider_id": 5,
                "name": "Loc One",
                "coordinates": [44.4, 26.1],
                "updated_at": "2026-11-07T10:00:00Z",
                "chargers": [_ch("1", "AVAILABLE", ct="ac"), _ch(2, "IN_USE")],
            },
            {
                "uid": "u2",
                "provider_id": 7,
                "name": "Loc Two",
                "coordinates": [45.0, 25.0],
                "updated_at": "2026-11-07T10:00:00Z",
                "chargers": [_ch("1", "UNAVAILABLE", "chademo"), _ch("3", "UNKNOWN")],
            },
        ],
        "providers_data": {"5": {"name": "PPC Blue"}, "7": {"name": "Other"}},
        "latest_status_updated_at": 1000,
    }


def delta(uid="u1", latest=2000):
    return {
        "success": True,
        "data": [
            {
                "uid": uid,
                "status": "IN_USE",
                "updated_at": "2026-11-07T11:00:00Z",
                "chargers": [{"id": "1", "status": "IN_USE"}, {"id": 2, "status": "IN_USE"}],
            }
        ],
        "latest_status_updated_at": latest,
    }


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    return tmp_path


def test_normalise_status():
    m = load_config()["status_map"]
    assert normalise_status("Charging", m) == "CHARGING"
    assert normalise_status("IN_USE", m) == "CHARGING"
    assert normalise_status("UNAVAILABLE", m) == "OFFLINE"
    assert normalise_status("AVAILABLE", m) == "AVAILABLE"
    assert normalise_status("FREE", m) == "AVAILABLE"
    assert normalise_status("weird", m) == "UNKNOWN"
    assert normalise_status("", m) == "UNKNOWN"


def test_parse_snapshot_fields():
    rows, state = parse_snapshot(snapshot(), TS)
    assert len(rows) == 4 and state.latest == 1000
    r = rows[0]
    assert (r.lat, r.lon) == (44.4, 26.1)
    assert r.operator == "PPC Blue" and r.provider_id == 5
    assert r.source == "ampwhere" and r.source_location_id == "u1" and r.source_cp_id == "1"
    assert r.connector == "ccs2" and r.current_type == "AC" and r.power_kw == 50
    assert r.evse_id == "RO*T*X*1" and r.source_updated_at == "2026-11-07T10:00:00Z"
    assert [x.status for x in rows] == ["AVAILABLE", "CHARGING", "OFFLINE", "UNKNOWN"]
    assert rows[2].connector == "chademo" and rows[2].operator == "Other"


def test_delta_merge_keeps_static_and_dupe_ids():
    _, state = parse_snapshot(snapshot(), TS)
    rows, state = parse_delta(delta(), state, TS)
    assert len(rows) == 2 and state.latest == 2000
    assert {r.source_location_id for r in rows} == {"u1"}
    assert all(r.status == "CHARGING" and r.name == "Loc One" for r in rows)
    assert rows[0].lat == 44.4 and rows[0].operator == "PPC Blue" and rows[0].power_kw == 50
    # u2 charger "1" shares an id with u1 charger "1" and must be untouched
    assert state.locs["u2"]["chargers"][0]["status"] == "UNAVAILABLE"
    assert state.locs["u1"]["chargers"][0]["status"] == "IN_USE"


def test_delta_only_changed_chargers():
    _, state = parse_snapshot(snapshot(), TS)
    d = delta()
    d["data"][0]["chargers"] = [{"id": "1", "status": "UNAVAILABLE"}]
    rows, _ = parse_delta(d, state, TS)
    assert [r.source_cp_id for r in rows] == ["1"] and rows[0].status == "OFFLINE"


def test_delta_without_latest_raises():
    _, state = parse_snapshot(snapshot(), TS)
    d = delta()
    del d["latest_status_updated_at"]
    with pytest.raises(ValueError):
        parse_delta(d, state, TS)


class FakeNet:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, spec, timeout):
        self.calls.append(spec)
        r = self.responses.pop(0)
        return r if isinstance(r, tuple) else (200, json.dumps(r).encode())


def _adapter(net, clock=lambda: 0.0):
    return AmpWhereAdapter(transport=net, sleep=lambda s: None, clock=clock)


def test_adapter_snapshot_then_delta_and_raw(data_dir, monkeypatch):
    monkeypatch.setenv("AMPWHERE_ENABLED", "1")
    net = FakeNet([snapshot(), delta()])
    ad = _adapter(net)
    assert len(ad.fetch()) == 4 and ad.last_kind == "snapshot"
    assert len(ad.fetch()) == 2 and ad.last_kind == "delta"
    assert net.calls[1]["params"] == {"status_updated_at": 1000}
    assert "PPCnodowntime" in net.calls[0]["headers"]["User-Agent"]
    raws = list((data_dir / "raw" / "live" / "raw").glob("*/*_*.json.gz"))
    assert len(raws) == 2
    assert json.loads(gzip.open(raws[0]).read())["success"] is True
    s = ad.summary()
    assert s.startswith("ppc=2 ") and "ppc_CHARGING=2" in s and "ppc_AVAILABLE=0" in s


def test_snapshot_latest_written_atomically(data_dir, monkeypatch):
    monkeypatch.setenv("AMPWHERE_ENABLED", "1")
    ad = _adapter(FakeNet([snapshot(), delta()]))
    ad.fetch()
    live = data_dir / "raw" / "live"
    latest = live / "ampwhere_snapshot_latest.json.gz"
    first = json.loads(gzip.open(latest).read())
    assert first["latest_status_updated_at"] == 1000
    ad.fetch()  # a delta must not overwrite it
    assert json.loads(gzip.open(latest).read()) == first
    assert not list(live.glob("*.tmp"))


def test_unknown_uid_forces_resync(monkeypatch):
    monkeypatch.setenv("AMPWHERE_ENABLED", "1")
    net = FakeNet([snapshot(), delta(uid="nope"), snapshot()])
    ad = _adapter(net)
    ad.fetch()
    assert ad.fetch() == [] and ad.state.need_resync
    ad.fetch()
    assert ad.last_kind == "snapshot" and not ad.state.need_resync


def test_bad_delta_falls_back_to_snapshot(monkeypatch):
    monkeypatch.setenv("AMPWHERE_ENABLED", "1")
    bad = copy.deepcopy(delta())
    del bad["latest_status_updated_at"]
    net = FakeNet([snapshot(), bad, snapshot()])
    ad = _adapter(net)
    ad.fetch()
    assert len(ad.fetch()) == 4 and ad.last_kind == "snapshot"


def test_resync_after_resync_s(monkeypatch):
    monkeypatch.setenv("AMPWHERE_ENABLED", "1")
    now = [0.0]
    net = FakeNet([snapshot(), delta(), snapshot()])
    ad = _adapter(net, clock=lambda: now[0])
    ad.fetch()
    now[0] = 100
    ad.fetch()
    assert ad.last_kind == "delta"
    now[0] = 21600 + 1
    ad.fetch()
    assert ad.last_kind == "snapshot"


def test_backoff_on_503(monkeypatch):
    monkeypatch.setenv("AMPWHERE_ENABLED", "1")
    sleeps = []
    net = FakeNet([(503, b""), snapshot()])
    ad = AmpWhereAdapter(transport=net, sleep=sleeps.append)
    assert len(ad.fetch()) == 4
    assert sleeps[0] == 5


def test_403_stops_collecting(monkeypatch):
    monkeypatch.setenv("AMPWHERE_ENABLED", "1")
    ad = _adapter(FakeNet([(403, b"no")] * 3))
    with pytest.raises(StopCollecting):
        ad.fetch()
    live_collector.setup_logging()
    rc = live_collector.run(ad, [], 300, False, load_config(), sleep=lambda s: None)
    assert rc == 2


def test_ampwhere_requires_enable(monkeypatch):
    monkeypatch.delenv("AMPWHERE_ENABLED", raising=False)
    with pytest.raises(StopCollecting):
        _adapter(FakeNet([snapshot()])).fetch()


def test_heartbeat_has_ppc_counts(data_dir, monkeypatch, capsys):
    monkeypatch.setenv("AMPWHERE_ENABLED", "1")
    live_collector.setup_logging()
    live_collector.poll_once(_adapter(FakeNet([snapshot()])), [])
    line = capsys.readouterr().out.strip().splitlines()[-1]
    assert " poll ok adapter=ampwhere kind=snapshot n=4 locs=2 " in line
    assert " ppc=2 ppc_CHARGING=1 ppc_AVAILABLE=1 ppc_OFFLINE=0 ppc_UNKNOWN=0 " in line
    assert line.endswith("fails=0")


def test_jsonl_sink(data_dir):
    JsonlSink().write([_st(), _st("free", "AVAILABLE", "L2")])
    files = list((data_dir / "raw" / "live").glob("statuses-*.jsonl"))
    assert len(files) == 1
    rows = [json.loads(ln) for ln in files[0].read_text().splitlines()]
    assert [r["status"] for r in rows] == ["CHARGING", "AVAILABLE"]
    assert rows[0]["ts"].startswith("2026-11-07T14:05")


def test_collector_once_file_replay(data_dir, capsys):
    src = data_dir / "in.jsonl"
    rows = [
        {"source": "x", "source_location_id": "A", "raw_status": "Charging"},
        {"source": "x", "source_location_id": "B", "raw_status": "Available"},
        {"source": "x", "source_location_id": "B", "raw_status": "???"},
    ]
    src.write_text("\n".join(json.dumps(r) for r in rows))
    assert live_collector.main(["--adapter", "file", "--file", str(src), "--once"]) == 0
    line = capsys.readouterr().out.strip().splitlines()[-1]
    assert " poll ok adapter=file n=3 locs=2 CHARGING=1 AVAILABLE=1 OFFLINE=0 UNKNOWN=1 " in line
    assert line.endswith("fails=0")
    assert "poll ok" in (data_dir / "raw" / "live" / "collector.log").read_text()


def test_file_replay_snapshot_json(data_dir):
    p = data_dir / "snap.json.gz"
    with gzip.open(p, "wt", encoding="utf-8") as f:
        json.dump(snapshot(), f)
    out = FileReplayAdapter(p).fetch()
    assert len(out) == 4 and out[0].operator == "PPC Blue"


def test_dbsink_noop_without_url(caplog):
    sink = DbSink()
    sink.write([_st()])
    sink.write([_st()])
    assert sum("DbSink disabled" in r.message for r in caplog.records) <= 1
