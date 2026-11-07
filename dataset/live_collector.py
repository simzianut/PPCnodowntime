from __future__ import annotations

import argparse
import logging
import os
import random
import sys
import time
from collections import Counter
from datetime import UTC, datetime

from dotenv import load_dotenv

from dataset.live.adapters import Adapter, AmpWhereAdapter, FileReplayAdapter, StopCollecting
from dataset.live.config import live_dir, load_config
from dataset.live.models import STATUSES
from dataset.live.sinks import DbSink, JsonlSink

log = logging.getLogger("live")


def setup_logging() -> None:
    d = live_dir()
    d.mkdir(parents=True, exist_ok=True)
    for h in list(log.handlers):
        h.close()
        log.removeHandler(h)
    log.setLevel(logging.INFO)
    log.propagate = False
    fmt = logging.Formatter("%(message)s")
    for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(d / "collector.log")):
        h.setFormatter(fmt)
        log.addHandler(h)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def poll_once(adapter: Adapter, sinks: list, fails: int = 0) -> int:
    t0 = time.monotonic()
    statuses = adapter.fetch()
    for sink in sinks:
        try:
            sink.write(statuses)
        except Exception:
            log.exception("%s sink %s failed", _now(), type(sink).__name__)
    counts = Counter(s.status for s in statuses)
    locs = len({(s.source, s.source_location_id) for s in statuses})
    ms = int((time.monotonic() - t0) * 1000)
    per = " ".join(f"{k}={counts.get(k, 0)}" for k in STATUSES)
    kind = getattr(adapter, "last_kind", None)
    kind = f"kind={kind} " if kind else ""
    summ = adapter.summary() if hasattr(adapter, "summary") else ""
    summ = f"{summ} " if summ else ""
    log.info(
        "%s poll ok adapter=%s %sn=%d locs=%d %s %sms=%d fails=%d",
        _now(), adapter.name, kind, len(statuses), locs, per, summ, ms, fails,
    )  # fmt: skip
    return len(statuses)


def run(
    adapter: Adapter, sinks: list, interval: float, once: bool, cfg: dict, sleep=time.sleep
) -> int:
    fails = 0
    max_fails = int(cfg["max_consecutive_failures"])
    try:
        while True:
            try:
                poll_once(adapter, sinks, fails)
                fails = 0
            except StopCollecting as e:
                log.info("%s poll stop adapter=%s reason=%s", _now(), adapter.name, e)
                return 2
            except Exception as e:
                fails += 1
                log.exception(
                    "%s poll fail adapter=%s err=%r fails=%d", _now(), adapter.name, e, fails
                )
                if fails >= max_fails:
                    log.info("%s poll stop adapter=%s reason=max_failures", _now(), adapter.name)
                    return 1
            if once:
                return 0 if fails == 0 else 1
            nxt = (int(time.time() // interval) + 1) * interval + random.uniform(0, 3)
            sleep(max(0.0, nxt - time.time()))
    except KeyboardInterrupt:
        log.info("%s poll stop adapter=%s reason=interrupted", _now(), adapter.name)
        return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    cfg = load_config()
    p = argparse.ArgumentParser(prog="dataset.live_collector")
    p.add_argument("--adapter", choices=["ampwhere", "file"], default="ampwhere")
    p.add_argument("--file")
    p.add_argument("--once", action="store_true")
    default_iv = float(os.environ.get("LIVE_POLL_S") or cfg["poll_s"])
    p.add_argument("--interval", type=float, default=default_iv)
    a = p.parse_args(argv)
    if a.adapter == "file":
        if not a.file:
            p.error("--file is required with --adapter file")
        adapter: Adapter = FileReplayAdapter(a.file)
    else:
        adapter = AmpWhereAdapter(config=cfg)
    setup_logging()
    return run(adapter, [JsonlSink(), DbSink()], a.interval, a.once, cfg)


if __name__ == "__main__":
    sys.exit(main())
