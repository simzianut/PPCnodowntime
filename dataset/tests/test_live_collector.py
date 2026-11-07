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
)
from dataset.live.config import load_config
from dataset.live.models import LiveStatus
from dataset.live.sinks import DbSink, JsonlSink

TS = datetime(2026, 11, 7, 14, 5, tzinfo=UTC)


def _st(raw="charging", status="CHARGING", loc="L1"):
    return LiveStatus(ts=TS, source="t", source_location_id=loc, raw_status=raw, status=status)


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    return tmp_path


def _spec(data_dir, spec):
    live = data_dir / "raw" / "live"
    live.mkdir(parents=True, exist_ok=True)
    (live / "ampwhere_request.json").write_text(json.dumps(spec))
    return live


def test_normalise_status():
    m = load_config()["status_map"]
    assert normalise_status("Charging", m) == "CHARGING"
    assert normalise_status("IN USE", m) == "CHARGING"
    assert normalise_status("Out_Of_Order", m) == "OFFLINE"
    assert normalise_status("FREE", m) == "AVAILABLE"
    assert normalise_status("weird", m) == "UNKNOWN"
    assert normalise_status("", m) == "UNKNOWN"


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
    assert list((data_dir / "raw" / "live").glob("statuses-*.jsonl"))


def test_403_stops_collecting(data_dir, monkeypatch):
    monkeypatch.setenv("AMPWHERE_ENABLED", "1")
    _spec(data_dir, {"url": "http://example.invalid"})
    ad = AmpWhereAdapter(transport=lambda spec, t: (403, b"no"), sleep=lambda s: None)
    with pytest.raises(StopCollecting):
        ad.fetch()
    live_collector.setup_logging()
    rc = live_collector.run(ad, [], 300, False, load_config(), sleep=lambda s: None)
    assert rc == 2


def test_ampwhere_saves_raw_and_backs_off(data_dir, monkeypatch):
    monkeypatch.setenv("AMPWHERE_ENABLED", "1")
    spec = {
        "url": "http://example.invalid",
        "requests": [{"params": {"r": 1}}, {"params": {"r": 2}}],
    }
    live = _spec(data_dir, spec)
    calls, sleeps = [], []

    def transport(spec, timeout):
        calls.append(spec)
        return (503, b"") if len(calls) == 1 else (200, b'{"ok": 1}')

    ad = AmpWhereAdapter(transport=transport, parser=lambda p, ts: [_st()], sleep=sleeps.append)
    assert len(ad.fetch()) == 2 and len(calls) == 3
    assert sleeps[0] == 5  # backoff after the 503
    raws = list((live / "raw").glob("*/*_*.json.gz"))
    assert len(raws) == 2
    assert json.loads(gzip.open(raws[0]).read()) == {"ok": 1}


def test_ampwhere_requires_enable(monkeypatch):
    monkeypatch.delenv("AMPWHERE_ENABLED", raising=False)
    with pytest.raises(StopCollecting):
        AmpWhereAdapter(transport=lambda s, t: (200, b"{}")).fetch()


def test_dbsink_noop_without_url(caplog):
    sink = DbSink()
    sink.write([_st()])
    sink.write([_st()])
    assert sum("DbSink disabled" in r.message for r in caplog.records) <= 1


def test_file_adapter_returns_statuses(data_dir):
    p = data_dir / "a.jsonl"
    p.write_text(json.dumps({"source_location_id": "Z", "raw_status": "busy"}))
    out = FileReplayAdapter(p).fetch()
    assert out[0].status == "CHARGING" and out[0].ts.tzinfo is not None
