from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

STATUSES = ("CHARGING", "AVAILABLE", "OFFLINE", "UNKNOWN")


@dataclass
class LiveStatus:
    ts: datetime  # tz-aware UTC
    source: str
    source_location_id: str
    raw_status: str
    status: str
    source_cp_id: str | None = None
    connector: str | None = None
    cp_id: str | None = None  # None until M3's station DB exists
    name: str | None = None
    lat: float | None = None
    lon: float | None = None
    operator: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ts"] = self.ts.isoformat()
        return d
