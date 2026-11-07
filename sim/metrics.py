"""Per-policy KPI accumulator (spec 04 §2, CONTRACTS Kpis, decision 0026)."""

from __future__ import annotations

import math

import numpy as np

from sim.config import net_tariff_ron_per_kwh

KPI_FIELDS: tuple[str, ...] = (
    "overhead_kwh",
    "saved_kwh",
    "saved_pct",
    "wait_min_total",
    "wait_min_mean",
    "wait_min_p95",
    "sessions",
    "waited_sessions",
    "sleep_share",
    "overhead_ron",
    "saved_ron",
)


def _r(x: float, digits: int) -> float:
    return float(round(x, digits)) + 0.0  # + 0.0 turns -0.0 into 0.0


class PolicyMetrics:
    """Running totals for one policy; `kpis()` gives the CONTRACTS Kpis dict."""

    def __init__(self) -> None:
        self.overhead_wh = 0.0
        self.sessions = 0
        self.wait_min_total = 0.0
        self.sleep_s = 0.0
        self.cp_s = 0.0
        self._waits: list[float] = []  # non-zero waits only
        self._sorted: np.ndarray | None = None

    @property
    def waited_sessions(self) -> int:
        return len(self._waits)

    def add_energy_wh(self, wh: float) -> None:
        self.overhead_wh += float(wh)

    def add_session(self, wait_min: float = 0.0) -> None:
        """Count one arrived session and its added wait (0 included)."""
        self.sessions += 1
        if wait_min > 0:
            self.wait_min_total += wait_min
            self._waits.append(float(wait_min))
            self._sorted = None

    def add_time(self, cp_s: float, sleep_s: float) -> None:
        """Add simulated CP-seconds and the part of them spent in STANDBY or DEEP_SLEEP."""
        self.cp_s += float(cp_s)
        self.sleep_s += float(sleep_s)

    def merge(self, other: PolicyMetrics) -> None:
        """Fold another run's totals in (e.g. several days of one policy)."""
        self.overhead_wh += other.overhead_wh
        self.sessions += other.sessions
        self.wait_min_total += other.wait_min_total
        self.sleep_s += other.sleep_s
        self.cp_s += other.cp_s
        self._waits.extend(other._waits)
        self._sorted = None

    def wait_mean(self) -> float:
        return self.wait_min_total / self.sessions if self.sessions else 0.0

    def wait_p95(self) -> float:
        """np.percentile(all waits incl. zeros, 95) without materialising the zeros."""
        n = self.sessions
        if n == 0 or not self._waits:
            return 0.0
        if self._sorted is None:
            self._sorted = np.sort(np.asarray(self._waits))
        nz = self._sorted
        zeros = n - len(nz)
        pos = 0.95 * (n - 1)
        lo = math.floor(pos)
        hi = min(lo + 1, n - 1)

        def v(i: int) -> float:
            return 0.0 if i < zeros else float(nz[i - zeros])

        return v(lo) + (v(hi) - v(lo)) * (pos - lo)

    def sleep_share(self) -> float:
        return self.sleep_s / self.cp_s if self.cp_s > 0 else 0.0

    def kpis(self, baseline_wh: float) -> dict:
        """Kpis dict; savings are measured against `baseline_wh` (always_on, same run)."""
        saved_wh = baseline_wh - self.overhead_wh
        pct = 100.0 * saved_wh / baseline_wh if baseline_wh > 0 else 0.0
        ron = net_tariff_ron_per_kwh() / 1000.0
        return {
            "overhead_kwh": _r(self.overhead_wh / 1000.0, 4),
            "saved_kwh": _r(saved_wh / 1000.0, 4),
            "saved_pct": _r(pct, 2),
            "wait_min_total": _r(self.wait_min_total, 3),
            "wait_min_mean": _r(self.wait_mean(), 3),
            "wait_min_p95": _r(self.wait_p95(), 3),
            "sessions": int(self.sessions),
            "waited_sessions": int(self.waited_sessions),
            "sleep_share": _r(self.sleep_share(), 4),
            "overhead_ron": _r(self.overhead_wh * ron, 4),
            "saved_ron": _r(saved_wh * ron, 4),
        }
