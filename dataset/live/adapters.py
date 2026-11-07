from __future__ import annotations

import gzip
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
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


def normalise_status(raw: str | None, mapping: dict | None = None) -> str:
    if mapping is None:
        mapping = load_config()["status_map"]
    key = str(raw or "").strip().lower()
    val = mapping.get(key) or mapping.get(key.replace("_", " ").replace("-", " "))
    return val if val in STATUSES else "UNKNOWN"


@dataclass
class AmpState:
    locs: dict[str, dict] = field(default_factory=dict)  # uid -> location with chargers
    providers: dict[int, str] = field(default_factory=dict)
    latest: int | None = None
    need_resync: bool = False


def _rows(loc: dict, chargers: list, providers: dict, ts: datetime, mapping: dict):
    coords = loc.get("coordinates") or [None, None]
    pid = loc.get("provider_id")
    out = []
    for ch in chargers:
        raw = str(ch.get("status") or "")
        ct = ch.get("current_type")
        out.append(
            LiveStatus(
                ts=ts,
                source="ampwhere",
                source_location_id=str(loc["uid"]),
                source_cp_id=str(ch["id"]),
                connector=(ch.get("connector") or {}).get("type"),
                raw_status=raw,
                status=normalise_status(raw, mapping),
                name=loc.get("name"),
                lat=coords[0],
                lon=coords[1],
                operator=providers.get(pid),
                provider_id=pid,
                evse_id=ch.get("qr"),
                power_kw=ch.get("power"),
                current_type=ct.upper() if isinstance(ct, str) else None,
                source_updated_at=loc.get("updated_at"),
            )
        )
    return out


def parse_snapshot(
    payload: dict, ts: datetime, mapping: dict | None = None
) -> tuple[list[LiveStatus], AmpState]:
    mapping = mapping or load_config()["status_map"]
    providers = {
        int(k): (v or {}).get("name") for k, v in (payload.get("providers_data") or {}).items()
    }
    state = AmpState(providers=providers, latest=payload.get("latest_status_updated_at"))
    out: list[LiveStatus] = []
    for loc in payload["data"]:
        state.locs[str(loc["uid"])] = loc
        out.extend(_rows(loc, loc.get("chargers") or [], providers, ts, mapping))
    return out, state


def parse_delta(
    payload: dict, state: AmpState, ts: datetime, mapping: dict | None = None
) -> tuple[list[LiveStatus], AmpState]:
    """Merges changed charger statuses into state. Raises ValueError if unusable."""
    mapping = mapping or load_config()["status_map"]
    latest = payload.get("latest_status_updated_at")
    if latest is None or not isinstance(payload.get("data"), list):
        raise ValueError("delta without data or latest_status_updated_at")
    out: list[LiveStatus] = []
    for entry in payload["data"]:
        loc = state.locs.get(str(entry["uid"]))
        if loc is None:
            state.need_resync = True
            continue
        by_id = {str(c["id"]): c for c in loc.get("chargers") or []}
        changed = []
        for dc in entry.get("chargers") or []:
            cur = by_id.get(str(dc["id"]))
            if cur is None:
                state.need_resync = True
                continue
            cur["status"] = dc.get("status")
            changed.append(cur)
        for k in ("status", "updated_at", "last_used_at"):
            if k in entry:
                loc[k] = entry[k]
        out.extend(_rows(loc, changed, state.providers, ts, mapping))
    state.latest = latest
    return out, state


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
    """Replays JSONL of LiveStatus dicts, or a saved AmpWhere snapshot (.json, .json.gz)."""

    name = "file"
    last_kind = None

    def __init__(self, path, parser: Callable | None = None):
        self.path = Path(path)
        self.parser = parser or (lambda payload, ts: parse_snapshot(payload, ts)[0])

    def summary(self) -> str:
        return ""

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
    req = urllib.request.Request(url, headers=spec.get("headers") or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class AmpWhereAdapter:
    name = "ampwhere"

    def __init__(
        self,
        transport: Callable[[dict, float], tuple[int, bytes]] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        config: dict | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.transport = transport or urllib_transport
        self.sleep = sleep
        self.clock = clock
        self.cfg = config or load_config()
        self.aw = self.cfg["ampwhere"]
        self.state: AmpState | None = None
        self.last_kind: str | None = None
        self._last_request = 0.0
        self._last_snapshot = 0.0
        self._n = 0

    @property
    def _map(self) -> dict:
        return self.cfg["status_map"]

    def _wait_gap(self) -> None:
        wait = self._last_request + float(self.cfg["min_request_gap_s"]) - time.monotonic()
        if self._last_request and wait > 0:
            self.sleep(wait)

    def _request(self, spec: dict) -> bytes:
        bo = self.cfg["backoff"]
        delay = float(bo["initial_s"])
        retries = int(bo["max_retries"])
        timeout = float(self.cfg.get("request_timeout_s", 30))
        headers = {"User-Agent": self.aw["user_agent"], "Accept": "application/json"}
        spec = {**spec, "headers": headers}
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

    def _save_raw(self, body: bytes) -> None:
        ts = datetime.now(UTC)
        d = live_dir() / "raw" / ts.strftime("%Y%m%d")
        d.mkdir(parents=True, exist_ok=True)
        with gzip.open(d / f"{ts.strftime('%H%M%S')}_{self._n}.json.gz", "wb") as f:
            f.write(body)
        self._n += 1

    def _save_latest(self, body: bytes) -> None:
        d = live_dir()
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "ampwhere_snapshot_latest.json.gz.tmp"
        with gzip.open(tmp, "wb") as f:
            f.write(body)
        os.replace(tmp, d / "ampwhere_snapshot_latest.json.gz")

    def _snapshot(self) -> list[LiveStatus]:
        body = self._request({"url": self.aw["snapshot_url"]})
        self._save_raw(body)
        out, self.state = parse_snapshot(json.loads(body), datetime.now(UTC), self._map)
        self._save_latest(body)
        self._last_snapshot = self.clock()
        self.last_kind = "snapshot"
        return out

    def _delta(self) -> list[LiveStatus]:
        spec = {"url": self.aw["delta_url"], "params": {"status_updated_at": self.state.latest}}
        body = self._request(spec)
        self._save_raw(body)
        out, self.state = parse_delta(json.loads(body), self.state, datetime.now(UTC), self._map)
        self.last_kind = "delta"
        return out

    def fetch(self) -> list[LiveStatus]:
        if os.environ.get("AMPWHERE_ENABLED") != "1":
            raise StopCollecting("AMPWHERE_ENABLED is not 1; a human must enable the poller")
        st = self.state
        stale = st is None or st.need_resync or st.latest is None
        if stale or self.clock() - self._last_snapshot >= float(self.aw["resync_s"]):
            return self._snapshot()
        try:
            return self._delta()
        except ValueError as e:  # includes JSON decode errors
            log.warning("ampwhere delta unusable (%s); resyncing with a snapshot", e)
            return self._snapshot()

    def summary(self) -> str:
        """Current PPC Blue state counts from the in-memory state."""
        if self.state is None:
            return ""
        pid = int(self.aw["ppc_provider_id"])
        counts = Counter(
            normalise_status(c.get("status"), self._map)
            for loc in self.state.locs.values()
            if loc.get("provider_id") == pid
            for c in loc.get("chargers") or []
        )
        total = sum(counts.values())
        return f"ppc={total} " + " ".join(f"ppc_{k}={counts.get(k, 0)}" for k in STATUSES)
