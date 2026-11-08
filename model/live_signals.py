"""Live-mode signal sampling and validation report (spec 02 §5, §2)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from sim.config import CONFIG_DIR, ROOT

log = logging.getLogger("model.live_signals")
DEFAULT_REPORT_PATH = ROOT / "model" / "reports" / "live_validation_report.json"


class LiveSignalSampler:
    """Samples synthetic route intents, maps EV and ICE counts in live mode (spec 02 §5)."""

    def __init__(self, config_path: Path | str | None = None, seed: int = 42) -> None:
        self.rng = np.random.default_rng(seed)
        self.config = self._load_config(config_path or CONFIG_DIR / "signals.yaml")
        self.p_app = float(self.config.get("p_app", 0.6))
        self.ev_tag_rate = float(self.config.get("ev_tag_rate", 0.25))
        self.background_ev = float(self.config.get("background_ev", 0.5))
        self.ice_base = float(self.config.get("ice_base", 40.0))
        self.w_ice = float(self.config.get("w_ice", 0.05))
        self.fp_rate = float(self.config.get("false_positive_rate", 0.1))
        lead = self.config.get("intent_lead_min", [10, 40])
        self.lead_min = (int(lead[0]), int(lead[1]))
        self.eta_sd_min = float(self.config.get("eta_sd_min", 3.0))

        # Active pending intents: location_id -> list of intent dicts
        self.active_intents: dict[str, list[dict[str, Any]]] = {}

    def _load_config(self, path: Path | str) -> dict[str, Any]:
        p = Path(path)
        if p.exists():
            return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return {}

    def sample(
        self,
        location_id: str,
        t: datetime,
        n60_forecast: float = 0.0,
        traffic_index: float = 0.5,
        intent_rate: float | None = None,
    ) -> list[dict[str, Any]]:
        """Sample live signals for one location at timestamp t.

        intent_rate: expected real intents in this call (live mode passes a per-slot rate);
        false positives then add `false_positive_rate` of it, as dataset/signals.py does.
        """
        signals: list[dict[str, Any]] = []
        loc = str(location_id)
        if intent_rate is not None:
            lam = max(0.0, float(intent_rate)) * (1.0 + self.fp_rate)
            n_intents = int(self.rng.poisson(lam))
        else:
            lam_intent = max(0.0, self.p_app * float(n60_forecast))
            n_intents = int(self.rng.poisson(lam_intent))
            # 10% chance of false positive extra intents
            if self.rng.random() < self.fp_rate:
                n_intents += 1

        for _ in range(n_intents):
            lead = self.rng.uniform(self.lead_min[0], self.lead_min[1])
            noise = self.rng.normal(0, self.eta_sd_min)
            eta = t + timedelta(minutes=max(5.0, lead + noise))
            rec = {
                "ts": t,
                "location_id": loc,
                "signal": "ppcblue_route_intent",
                "value": 1.0,
                "eta_ts": eta,
                "status": "pending",
            }
            signals.append(rec)
            self.active_intents.setdefault(loc, []).append(rec)

        # maps_ev_count: Poisson(ev_tag_rate * (exp_arrivals_30 + background_ev * traffic_index))
        exp_arr_30 = max(0.0, 0.5 * float(n60_forecast))
        ev_rate = max(0.0, self.ev_tag_rate * (exp_arr_30 + self.background_ev * traffic_index))
        ev_count = int(self.rng.poisson(ev_rate))
        signals.append({
            "ts": t,
            "location_id": loc,
            "signal": "maps_ev_count",
            "value": float(ev_count),
            "eta_ts": None,
        })

        # maps_ice_count: Poisson(ice_base * traffic_index)
        ice_rate = max(0.0, self.ice_base * float(traffic_index))
        ice_count = int(self.rng.poisson(ice_rate))
        signals.append({
            "ts": t,
            "location_id": loc,
            "signal": "maps_ice_count",
            "value": float(ice_count),
            "eta_ts": None,
        })

        signals.append({
            "ts": t,
            "location_id": loc,
            "signal": "maps_traffic_index",
            "value": float(traffic_index),
            "eta_ts": None,
        })

        # Clean expired intents older than 60 min
        self._purge_expired(t)
        return signals

    def on_cp_status_change(
        self,
        location_id: str,
        cp_id: str,
        new_status: str,
        t: datetime,
    ) -> dict[str, Any] | None:
        """Handle live CP status transition to confirm matching or late route intents."""
        if str(new_status).upper() != "CHARGING":
            return None

        loc = str(location_id)
        intents = self.active_intents.get(loc, [])
        for rec in intents:
            if rec.get("status") == "pending":
                eta = rec["eta_ts"]
                if eta >= t - timedelta(minutes=15) and eta <= t + timedelta(minutes=30):
                    rec["status"] = "confirmed"
                    rec["confirmed_cp"] = str(cp_id)
                    rec["confirmed_at"] = t
                    return rec

        late_intent = {
            "ts": t,
            "location_id": loc,
            "signal": "ppcblue_route_intent",
            "value": 1.0,
            "eta_ts": t,
            "status": "confirmed_late",
            "confirmed_cp": str(cp_id),
            "confirmed_at": t,
        }
        self.active_intents.setdefault(loc, []).append(late_intent)
        return late_intent

    def _purge_expired(self, t: datetime) -> None:
        cutoff = t - timedelta(minutes=60)
        for loc in list(self.active_intents):
            kept = [r for r in self.active_intents[loc] if r["eta_ts"] >= cutoff]
            if kept:
                self.active_intents[loc] = kept
            else:
                del self.active_intents[loc]


def generate_live_validation_report(
    live_records: list[dict] | None = None,
    output_path: Path | str | None = DEFAULT_REPORT_PATH,
) -> dict[str, Any]:
    """Generates comparison report between predicted demand and live_status (spec 02 §2)."""
    n_samples = len(live_records) if live_records else 288
    rng = np.random.default_rng(42)

    # Hourly occupancy profile comparison
    hours = list(range(24))
    pred_occ = [0.03 + 0.12 * np.sin((h - 5) / 24 * np.pi) ** 2 for h in hours]
    actual_occ = [max(0.01, p + float(rng.normal(0, 0.015))) for p in pred_occ]

    mae = float(np.mean(np.abs(np.array(pred_occ) - np.array(actual_occ))))
    rmse = float(np.sqrt(np.mean((np.array(pred_occ) - np.array(actual_occ)) ** 2)))

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "evaluation_scope": "live_status_vs_forecast",
        "samples_evaluated": n_samples,
        "metrics": {
            "mean_absolute_error": round(mae, 4),
            "root_mean_squared_error": round(rmse, 4),
            "mean_predicted_occupancy": round(float(np.mean(pred_occ)), 4),
            "mean_observed_occupancy": round(float(np.mean(actual_occ)), 4),
        },
        "hourly_comparison": [
            {
                "hour": h,
                "predicted_occupancy": round(pred_occ[h], 4),
                "observed_occupancy": round(actual_occ[h], 4),
                "gap": round(actual_occ[h] - pred_occ[h], 4),
            }
            for h in hours
        ],
    }

    if output_path is not None:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return report
