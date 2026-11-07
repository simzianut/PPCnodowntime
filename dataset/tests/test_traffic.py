"""Traffic tests: no network, transport monkeypatched."""

from __future__ import annotations

import io
import json
import urllib.error
from datetime import date

import pytest

from dataset import traffic as t


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(b""))


def test_flow_index_math_and_clip():
    assert t.flow_to_index(30, 60) == pytest.approx(0.5)
    assert t.flow_to_index(70, 60) == 0.0
    assert t.flow_to_index(0, 60) == 1.0
    with pytest.raises(ValueError):
        t.flow_to_index(10, 0)


def test_duration_index_math_and_clip():
    assert t.duration_to_index(150, 100) == pytest.approx(0.5)
    assert t.duration_to_index(90, 100) == 0.0
    assert t.duration_to_index(500, 100) == 1.0


def test_week_expansion_168():
    prof = {"weekday": [100.0] * 23 + [200.0], "weekend": [100.0] * 24}
    week = t.profile_to_week(prof, 100.0)
    assert len(week) == 168
    assert week[23] == 1.0 and week[24 + 23] == 1.0  # Mon and Tue 23h
    assert week[4 * 24 + 23] == 1.0  # Fri
    assert week[5 * 24 + 23] == 0.0 and week[6 * 24 + 23] == 0.0  # Sat, Sun


def test_next_weekday():
    assert next_tue(date(2026, 11, 7)) == date(2026, 11, 10)
    assert t.next_weekday(date(2026, 11, 7), 5) == date(2026, 11, 14)  # Sat -> next Sat


def next_tue(d):
    return t.next_weekday(d, 1)


def test_cache_skip(tmp_path, monkeypatch):
    monkeypatch.setenv("MAPBOX_TOKEN", "secret-token")
    calls = []

    def fake_routes(a, b, depart, cfg, free_flow=False):
        calls.append(depart)
        return [{"duration": 120.0, "duration_typical": 100.0, "distance": 600.0}]

    monkeypatch.setattr(t, "mapbox_routes", fake_routes)
    cfg = t.load_config()
    today = date(2026, 11, 7)
    p1 = t.location_profile("L1", 44.4, 26.1, cfg, tmp_path, today)
    assert len(calls) == 25  # 24 samples + driving
    p2 = t.location_profile("L1", 44.4, 26.1, cfg, tmp_path, today)
    assert len(calls) == 25 and p1 == p2
    assert p1["weekday"][0] == 100.0
    assert t.free_flow_duration(p1) == 100.0


def test_north_south_fallback(monkeypatch):
    seq = []

    def fake_routes(a, b, depart, cfg, free_flow=False):
        seq.append((a, b))
        return [{"duration": 50.0, "distance": 10.0 if len(seq) == 1 else 500.0}]

    monkeypatch.setattr(t, "mapbox_routes", fake_routes)
    raw = t._raw_sample(44.4, 26.1, date(2026, 11, 10), 8, t.load_config())
    assert raw["bearing"] == 0 and len(seq) == 2


def test_401_clear_error(monkeypatch):
    def boom(url, timeout):
        raise _http_error(401)

    monkeypatch.setattr(t, "_urlopen", boom)
    with pytest.raises(t.TrafficAuthError, match="401"):
        t._get_json("http://x?key=abc")


def test_429_quota_error(monkeypatch):
    def boom(url, timeout):
        raise _http_error(429)

    monkeypatch.setattr(t, "_urlopen", boom)
    with pytest.raises(t.TrafficQuotaError) as ei:
        t._get_json("http://x?key=abc")
    assert "abc" not in str(ei.value)


def test_retry_once_on_5xx(monkeypatch):
    n = {"c": 0}

    def flaky(url, timeout):
        n["c"] += 1
        if n["c"] == 1:
            raise _http_error(503)
        return {"ok": 1}

    monkeypatch.setattr(t, "_urlopen", flaky)
    assert t._get_json("http://x", retry_wait_s=0) == {"ok": 1} and n["c"] == 2


def test_live_index_none_on_error(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", "k")
    monkeypatch.setattr(t, "_urlopen", lambda u, to: {"unexpected": 1})
    assert t.live_index(44.4, 26.1) is None


def test_tomtom_flow_parse(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", "k")
    body = {
        "flowSegmentData": {
            "currentSpeed": 30,
            "freeFlowSpeed": 60,
            "confidence": 0.9,
            "frc": "FRC2",
        }
    }
    monkeypatch.setattr(t, "_urlopen", lambda u, to: body)
    assert t.live_index(44.4, 26.1) == pytest.approx(0.5)


def test_redact():
    u = "https://a/b?x=1&access_token=pk.SECRET&key=ZZZ&depart_at=2026"
    r = t.redact(u)
    assert "SECRET" not in r and "ZZZ" not in r and "depart_at=2026" in r
    assert json.dumps(r)


def test_interpolate_circular_wraps():
    vals = [None] * 24
    vals[0], vals[2], vals[22] = 100.0, 120.0, 140.0
    for h in range(4, 22, 2):
        vals[h] = 100.0
    out = t.interpolate_circular(vals)
    assert out[1] == pytest.approx(110.0)
    assert out[23] == pytest.approx(120.0)  # between 22h (140) and 0h (100)
    assert all(v is not None for v in out)


def test_pilot_hourly_cache_kept(tmp_path, monkeypatch):
    cfg = t.load_config()
    d = tmp_path / "raw" / "traffic" / "mapbox"
    d.mkdir(parents=True)
    for dt in t.DAYTYPES:
        for h in range(24):
            raw = {"duration_typical": 100.0 + h, "duration": 1.0, "endpoints": [[0, 0], [1, 1]]}
            (d / f"L1_{dt}_{h:02d}.json").write_text(json.dumps(raw))
    (d / "L1_driving.json").write_text(json.dumps({"duration": 90.0}))
    monkeypatch.setattr(t, "mapbox_routes", lambda *a, **k: pytest.fail("no network"))
    p = t.location_profile("L1", 44.4, 26.1, cfg, tmp_path, date(2026, 11, 7))
    assert p["weekday"][5] == 105.0  # odd hour kept, not interpolated


def test_rate_limiter_spaces_calls():
    now = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        now[0] += s

    rl = t.RateLimiter(4, clock=lambda: now[0], sleep=sleep)
    for _ in range(9):
        rl.wait()
    assert now[0] == pytest.approx(8 * 0.25)  # 9 calls at 4/s span 2 s
    assert rl.count == 9


def test_rate_limiter_threads():
    rl = t.RateLimiter(200)
    from concurrent.futures import ThreadPoolExecutor

    start = t.time.monotonic()
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(lambda _: rl.wait(), range(21)))
    assert t.time.monotonic() - start >= 0.095  # 20 gaps x 5 ms
    assert rl.count == 21


def test_segment_filter():
    ok = {"frc": "FRC3", "confidence": 0.9, "free_flow_speed": 50}
    assert t.segment_usable(ok)
    assert not t.segment_usable({**ok, "frc": "FRC6"})
    assert not t.segment_usable({**ok, "confidence": 0.3})
    assert not t.segment_usable({**ok, "free_flow_speed": 10})


def test_live_index_none_for_filtered(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", "k")
    body = {
        "flowSegmentData": {"currentSpeed": 0, "freeFlowSpeed": 2, "confidence": 0.5, "frc": "FRC2"}
    }
    monkeypatch.setattr(t, "_urlopen", lambda u, to: body)
    assert t.live_index(44.4, 26.1) is None
