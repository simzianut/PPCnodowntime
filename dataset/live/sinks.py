from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime

from dataset.live.config import live_dir
from dataset.live.models import LiveStatus

log = logging.getLogger("live")


class JsonlSink:
    def write(self, statuses: list[LiveStatus]) -> None:
        if not statuses:
            return
        d = live_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"statuses-{datetime.now(UTC).strftime('%Y%m%d')}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            for s in statuses:
                f.write(json.dumps(s.to_dict()) + "\n")


class DbSink:
    """Inserts into live_status if it exists (owned by T5). Never creates it."""

    def __init__(self):
        self._engine = None
        self._disabled = False

    def _init(self) -> bool:
        if self._engine is not None:
            return True
        if self._disabled:
            return False
        url = os.environ.get("DATABASE_URL")
        if not url:
            return self._disable("DATABASE_URL not set")
        try:
            from sqlalchemy import create_engine, inspect

            engine = create_engine(url)
            if not inspect(engine).has_table("live_status"):
                return self._disable("table live_status does not exist yet")
        except Exception as e:
            return self._disable(f"DB unavailable ({e!r})")
        self._engine = engine
        return True

    def _disable(self, why: str) -> bool:
        log.warning("DbSink disabled: %s; writing JSONL only", why)
        self._disabled = True
        return False

    def write(self, statuses: list[LiveStatus]) -> None:
        if not statuses or not self._init():
            return
        from sqlalchemy import text

        rows = [
            {"ts": s.ts, "cp_id": s.cp_id, "source": s.source, "raw": s.raw_status, "st": s.status}
            for s in statuses
        ]
        sql = text(
            "INSERT INTO live_status (ts, cp_id, source, raw_status, status) "
            "VALUES (:ts, :cp_id, :source, :raw, :st)"
        )
        with self._engine.begin() as conn:
            conn.execute(sql, rows)
