from __future__ import annotations

import gzip
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from dataset.live.config import live_dir, load_config
from dataset.live.models import STATUSES, LiveStatus

log = logging.getLogger("live")


class StopCollecting(Exception):
    """Raised on 403/429 or a disabled adapter: the loop must exit cleanly."""


class Adapter(Protocol):
    name: str

    def fetch(self) -> list[LiveStatus]: ...


def normalise_status(raw: str, mapping: dict | None = None) -> str:
    if mapping is None:
        mapping = load_config()["status_map"]
    key = str(raw or "").strip().lower()
    val = mapping.get(key) or mapping.get(key.replace("_", " ").replace("-", " "))
    return val if val in STATUSES else "UNKNOWN"


def parse_ampwhere(payload: dict | list, ts: datetime) -> list[LiveStatus]:
    raise NotImplementedError("fill from captured sample")


def _from_dict(d: dict, default_ts: datetime) -> LiveStatus:
    d = dict(d)
    ts = d.pop("ts", None)
    ts = datetime.fromisoformat(ts) if isinstance(ts, str) else default_ts
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    d.setdefault("source", "file")
    d.setdefault("source_location_id", "")
    d.setdefault("raw_status", "")
    d.setdefault("status", normalise_status(d["raw_status"]))
    return LiveStatus(ts=ts.astimezone(UTC), **d)


class FileReplayAdapter:
    """Replays JSONL of LiveStatus dicts, or a saved raw response run through a parser."""

    name = "file"

    def __init__(self, path, parser: Callable | None = None):
        self.path = Path(path)
        self.parser = parser or parse_ampwhere

    def fetch(self) -> list[LiveStatus]:
        opener = gzip.open if self.path.suffix == ".gz" else open
        with opener(self.path, "rt", encoding="utf-8") as f:
            text = f.read()
        now = datetime.now(UTC)
        if ".jsonl" in self.path.name:
            rows = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
            return [_from_dict(r, now) for r in rows]
        return self.parser(json.loads(text), now)


def urllib_transport(spec: dict, timeout: float) -> tuple[int, bytes]:
    url = spec["url"]
    params = spec.get("params")
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    body = spec.get("body")
    data = None
    headers = dict(spec.get("headers") or {})
    if body is not None:
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        if not isinstance(body, str):
            headers.setdefault("Content-Type", "application/json")
    method = spec.get("method", "GET" if data is None else "POST")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class AmpWhereAdapter:
    name = "ampwhere"

    def __init__(
        self,
        spec_path: Path | None = None,
        transport: Callable[[dict, float], tuple[int, bytes]] | None = None,
        parser: Callable[[dict | list, datetime], list[LiveStatus]] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        config: dict | None = None,
    ):
        self.spec_path = spec_path or live_dir() / "ampwhere_request.json"
        self.transport = transport or urllib_transport
        self.parser = parser or parse_ampwhere
        self.sleep = sleep
        self.cfg = config or load_config()
        self._last_request = 0.0

    def _load_requests(self) -> list[dict]:
        with open(self.spec_path, encoding="utf-8") as f:
            spec = json.load(f)
        reqs = spec.get("requests")
        if reqs:
            base = {k: v for k, v in spec.items() if k != "requests"}
            return [{**base, **r} for r in reqs]
        return [spec]

    def _wait_gap(self) -> None:
        gap = float(self.cfg["min_request_gap_s"])
        wait = self._last_request + gap - time.monotonic()
        if self._last_request and wait > 0:
            self.sleep(wait)

    def _request(self, spec: dict) -> bytes:
        bo = self.cfg["backoff"]
        delay = float(bo["initial_s"])
        retries = int(bo["max_retries"])
        timeout = float(self.cfg.get("request_timeout_s", 30))
        for attempt in range(retries + 1):
            self._wait_gap()
            self._last_request = time.monotonic()
            try:
                code, body = self.transport(spec, timeout)
                err = f"HTTP {code}"
            except OSError as e:  # timeouts and URLError are OSError subclasses
                code, body, err = None, b"", repr(e)
            if code in (403, 429):
                raise StopCollecting(f"HTTP {code} from ampwhere")
            if code is not None and code < 500:
                if code >= 400:
                    raise RuntimeError(f"HTTP {code} from ampwhere")
                return body
            if attempt == retries:
                raise RuntimeError(f"ampwhere failed after retries: {err}")
            log.warning("ampwhere %s, retry in %.0fs", err, delay)
            self.sleep(delay)
            delay = min(delay * float(bo["factor"]), float(bo["max_s"]))
        raise RuntimeError("unreachable")

    def _save_raw(self, body: bytes, ts: datetime, n: int) -> None:
        d = live_dir() / "raw" / ts.strftime("%Y%m%d")
        d.mkdir(parents=True, exist_ok=True)
        with gzip.open(d / f"{ts.strftime('%H%M%S')}_{n}.json.gz", "wb") as f:
            f.write(body)

    def fetch(self) -> list[LiveStatus]:
        if os.environ.get("AMPWHERE_ENABLED") != "1":
            raise StopCollecting("AMPWHERE_ENABLED is not 1; a human must enable the poller")
        out: list[LiveStatus] = []
        for n, spec in enumerate(self._load_requests()):
            body = self._request(spec)
            ts = datetime.now(UTC)
            self._save_raw(body, ts, n)
            out.extend(self.parser(json.loads(body), ts))
        return out
