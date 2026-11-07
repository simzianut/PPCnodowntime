"""Feature engineering for EV demand forecaster (spec 02 §2).

Extracts calendar, weather, station, historical, and local signal features
with strict leakage guards (no session_id features, no future lookahead).
"""

from __future__ import annotations

import bisect
import math
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from model.types import PowerClass, SimState, State

FEATURE_COLUMNS = [
    "hour_sin",
    "hour_cos",
    "weekday",
    "is_holiday",
    "minute_of_day",
    "temp_c",
    "precip_mm",
    "wind_kmh",
    "arrivals_cp_1h",
    "arrivals_cp_3h",
    "arrivals_same_slot_4w",
    "is_charging",
    "power_class_code",
    "context_type_code",
    "county_ev_per_1000",
    "cps_at_location",
    "intent_eta_15",
    "intent_eta_30",
    "intent_eta_60",
    "maps_ev_count_15m",
    "weighted_flow_15m",
    "traffic_index",
]

POWER_CLASS_MAP = {
    PowerClass.AC_22.value: 0,
    PowerClass.DC_50.value: 1,
    PowerClass.DC_HPC.value: 2,
    "AC_22": 0,
    "DC_50": 1,
    "DC_HPC": 2,
}

CONTEXT_TYPE_MAP = {
    "urban": 0,
    "retail": 1,
    "highway": 2,
    "airport": 3,
    "other": 4,
}


class SignalIndex:
    """Fast pre-indexed lookup for location signals at discrete time slots."""

    def __init__(self, signals_df: pd.DataFrame | None) -> None:
        self.ev_counts: dict[tuple[str, datetime], float] = defaultdict(float)
        self.ice_counts: dict[tuple[str, datetime], float] = defaultdict(float)
        self.traffic_indices: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
        self.intents: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)

        if signals_df is None or signals_df.empty:
            return

        sig = signals_df.copy()
        sig["ts"] = pd.to_datetime(sig["ts"], utc=True)
        if "eta_ts" in sig.columns:
            sig["eta_ts"] = pd.to_datetime(sig["eta_ts"], utc=True)

        for _, row in sig.iterrows():
            loc = row["location_id"]
            ts = row["ts"]
            signal = row["signal"]
            val = float(row["value"])

            if signal == "maps_ev_count":
                self.ev_counts[(loc, ts)] += val
            elif signal == "maps_ice_count":
                self.ice_counts[(loc, ts)] += val
            elif signal == "maps_traffic_index":
                self.traffic_indices[loc].append((ts, val))
            elif signal == "ppcblue_route_intent" and pd.notna(row.get("eta_ts")):
                self.intents[loc].append((ts, row["eta_ts"]))

        # Sort traffic indices and intents by timestamp
        for loc in self.traffic_indices:
            self.traffic_indices[loc].sort(key=lambda x: x[0])
        for loc in self.intents:
            self.intents[loc].sort(key=lambda x: x[0])

    def lookup(self, location_id: str, t: datetime) -> dict[str, float]:
        """Extract signal metrics strictly at or before t."""
        # 1. 15-minute rolling window for EV and ICE counts: (t - 15m, t]
        ev_15m = 0.0
        ice_15m = 0.0
        for offset_min in (0, 5, 10):
            slot_t = t - timedelta(minutes=offset_min)
            ev_15m += self.ev_counts.get((location_id, slot_t), 0.0)
            ice_15m += self.ice_counts.get((location_id, slot_t), 0.0)

        weighted_flow = ev_15m + 0.05 * ice_15m

        # 2. Latest traffic index at or before t
        traffic_idx = 0.0
        loc_traffic = self.traffic_indices.get(location_id, [])
        if loc_traffic:
            # Binary search for latest ts <= t
            ts_list = [x[0] for x in loc_traffic]
            idx = bisect.bisect_right(ts_list, t)
            if idx > 0:
                traffic_idx = loc_traffic[idx - 1][1]

        # 3. Route intents received at or before t with future eta
        intent_15 = 0.0
        intent_30 = 0.0
        intent_60 = 0.0
        loc_intents = self.intents.get(location_id, [])
        for rec_ts, eta_ts in loc_intents:
            if rec_ts <= t and eta_ts > t:
                lead_s = (eta_ts - t).total_seconds()
                if lead_s <= 900.0:
                    intent_15 += 1.0
                elif lead_s <= 1800.0:
                    intent_30 += 1.0
                elif lead_s <= 3600.0:
                    intent_60 += 1.0

        return {
            "intent_eta_15": intent_15,
            "intent_eta_30": intent_30,
            "intent_eta_60": intent_60,
            "maps_ev_count_15m": ev_15m,
            "weighted_flow_15m": weighted_flow,
            "traffic_index": traffic_idx,
        }


def encode_calendar(t: datetime, is_holiday: bool) -> dict[str, float]:
    """Encode cyclic time, weekday, minute of day, and holiday status."""
    hour = t.hour + t.minute / 60.0
    angle = 2.0 * math.pi * hour / 24.0
    return {
        "hour_sin": math.sin(angle),
        "hour_cos": math.cos(angle),
        "weekday": float(t.weekday()),
        "is_holiday": 1.0 if is_holiday else 0.0,
        "minute_of_day": float(t.hour * 60 + t.minute),
    }


def extract_features_from_simstate(
    state: SimState,
    locations_meta: dict[str, dict[str, Any]] | None = None,
    history_tracker: Any | None = None,
) -> pd.DataFrame:
    """Build feature matrix for online inference from SimState."""
    rows: list[dict[str, Any]] = []
    locations_meta = locations_meta or {}
    signal_index = SignalIndex(state.signals)

    default_ctx = {"temp_c": 15.0, "precip_mm": 0.0, "wind_kmh": 10.0, "is_holiday": False}

    for cp in state.cps:
        meta = locations_meta.get(cp.location_id, {})
        county = meta.get("county_code", "B")
        ctx = state.context.get(county, default_ctx)

        cal_feats = encode_calendar(state.t, bool(ctx.get("is_holiday", False)))
        sig_feats = signal_index.lookup(cp.location_id, state.t)

        power_code = POWER_CLASS_MAP.get(
            cp.power_class.value if hasattr(cp.power_class, "value") else str(cp.power_class),
            1,
        )
        ctx_code = CONTEXT_TYPE_MAP.get(str(meta.get("context_type", "urban")), 0)

        arr_1h = history_tracker.get_arrivals(cp.cp_id, 1) if history_tracker else 0.0
        arr_3h = history_tracker.get_arrivals(cp.cp_id, 3) if history_tracker else 0.0
        arr_4w = history_tracker.get_slot_arrivals(cp.cp_id, state.t) if history_tracker else 0.0

        is_charging = 1.0 if cp.state == State.CHARGING else 0.0

        row = {
            "cp_id": cp.cp_id,
            **cal_feats,
            "temp_c": float(ctx.get("temp_c", 15.0)),
            "precip_mm": float(ctx.get("precip_mm", 0.0)),
            "wind_kmh": float(ctx.get("wind_kmh", 10.0)),
            "arrivals_cp_1h": float(arr_1h),
            "arrivals_cp_3h": float(arr_3h),
            "arrivals_same_slot_4w": float(arr_4w),
            "is_charging": is_charging,
            "power_class_code": power_code,
            "context_type_code": ctx_code,
            "county_ev_per_1000": float(meta.get("county_ev_per_1000", 1.5)),
            "cps_at_location": float(meta.get("cps_at_location", 2)),
            **sig_feats,
        }
        rows.append(row)

    return pd.DataFrame(rows)


def build_training_dataset(
    sessions_df: pd.DataFrame,
    signals_df: pd.DataFrame,
    context_df: pd.DataFrame,
    locations_df: pd.DataFrame,
    slot_min: int = 5,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate training feature table and targets (y15, y30, y60, n60) across all slots."""
    sessions = sessions_df.copy()
    sessions["arrival_ts"] = pd.to_datetime(sessions["arrival_ts"], utc=True)
    sessions["departure_ts"] = pd.to_datetime(sessions["departure_ts"], utc=True)

    context = context_df.copy()
    context["ts_hour"] = pd.to_datetime(context["ts_hour"], utc=True)

    # Fast signal index
    signal_index = SignalIndex(signals_df)

    min_ts = sessions["arrival_ts"].min().floor("D")
    max_ts = sessions["departure_ts"].max().ceil("D")
    slots = pd.date_range(min_ts, max_ts, freq=f"{slot_min}min", tz="UTC")

    loc_meta_by_cp: dict[str, dict[str, Any]] = {}
    cps_by_loc: dict[str, int] = locations_df.groupby("location_id")["cp_id"].count().to_dict()

    for _, row in locations_df.iterrows():
        cp_id = row["cp_id"]
        loc_id = row["location_id"]
        loc_meta_by_cp[cp_id] = {
            "location_id": loc_id,
            "county_code": row.get("county_code", "B"),
            "context_type": row.get("context_type", "urban"),
            "power_class": row.get("power_class", "DC_50"),
            "cps_at_location": cps_by_loc.get(loc_id, 2),
            "county_ev_per_1000": 1.5,
        }

    # Pre-index sorted arrival timestamps and intervals
    arrivals_by_cp: dict[str, list[datetime]] = {}
    intervals_by_cp: dict[str, list[tuple[datetime, datetime]]] = {}
    for cp_id, group in sessions.groupby("cp_id"):
        arrivals_by_cp[cp_id] = sorted(group["arrival_ts"].tolist())
        intervals_by_cp[cp_id] = list(zip(group["arrival_ts"], group["departure_ts"], strict=False))

    arrivals_by_loc: dict[str, list[datetime]] = {}
    for loc_id, group in sessions.groupby("location_id"):
        arrivals_by_loc[loc_id] = sorted(group["arrival_ts"].tolist())

    ctx_lookup: dict[tuple[datetime, str], dict[str, Any]] = {}
    for _, crow in context.iterrows():
        ctx_lookup[(crow["ts_hour"], crow["county_code"])] = crow.to_dict()

    rows: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    all_cp_ids = list(loc_meta_by_cp.keys())

    delta_1h = timedelta(hours=1)
    delta_3h = timedelta(hours=3)
    delta_15m = timedelta(minutes=15)
    delta_30m = timedelta(minutes=30)
    delta_60m = timedelta(minutes=60)

    for t in slots:
        t_hour = t.floor("h")
        t15 = t + delta_15m
        t30 = t + delta_30m
        t60 = t + delta_60m

        for cp_id in all_cp_ids:
            meta = loc_meta_by_cp[cp_id]
            loc_id = meta["location_id"]
            county = meta["county_code"]

            ctx = ctx_lookup.get(
                (t_hour, county),
                {"temp_c": 15.0, "precip_mm": 0.0, "wind_kmh": 10.0, "is_holiday": False},
            )
            cal_feats = encode_calendar(t, bool(ctx.get("is_holiday", False)))
            sig_feats = signal_index.lookup(loc_id, t)

            # Historical arrivals via bisect
            cp_arrs = arrivals_by_cp.get(cp_id, [])
            n_arr = len(cp_arrs)
            if n_arr > 0:
                idx_t = bisect.bisect_right(cp_arrs, t)
                idx_1h = bisect.bisect_right(cp_arrs, t - delta_1h)
                idx_3h = bisect.bisect_right(cp_arrs, t - delta_3h)
                arr_1h = float(idx_t - idx_1h)
                arr_3h = float(idx_t - idx_3h)

                idx_15 = bisect.bisect_right(cp_arrs, t15)
                idx_30 = bisect.bisect_right(cp_arrs, t30)
                idx_60 = bisect.bisect_right(cp_arrs, t60)
                y15 = 1.0 if idx_15 > idx_t else 0.0
                y30 = 1.0 if idx_30 > idx_t else 0.0
                y60 = 1.0 if idx_60 > idx_t else 0.0
            else:
                arr_1h = 0.0
                arr_3h = 0.0
                y15 = 0.0
                y30 = 0.0
                y60 = 0.0

            # Location arrivals n60 via bisect
            loc_arrs = arrivals_by_loc.get(loc_id, [])
            if loc_arrs:
                idx_loc_t = bisect.bisect_right(loc_arrs, t)
                idx_loc_60 = bisect.bisect_right(loc_arrs, t60)
                n60 = float(idx_loc_60 - idx_loc_t)
            else:
                n60 = 0.0

            intervals = intervals_by_cp.get(cp_id, [])
            is_charging = 1.0 if any(start <= t < end for start, end in intervals) else 0.0

            power_code = POWER_CLASS_MAP.get(meta["power_class"], 1)
            ctx_code = CONTEXT_TYPE_MAP.get(meta["context_type"], 0)

            row = {
                "ts": t,
                "cp_id": cp_id,
                "location_id": loc_id,
                **cal_feats,
                "temp_c": float(ctx.get("temp_c", 15.0)),
                "precip_mm": float(ctx.get("precip_mm", 0.0)),
                "wind_kmh": float(ctx.get("wind_kmh", 10.0)),
                "arrivals_cp_1h": arr_1h,
                "arrivals_cp_3h": arr_3h,
                "arrivals_same_slot_4w": 0.0,
                "is_charging": is_charging,
                "power_class_code": power_code,
                "context_type_code": ctx_code,
                "county_ev_per_1000": float(meta["county_ev_per_1000"]),
                "cps_at_location": float(meta["cps_at_location"]),
                **sig_feats,
            }
            rows.append(row)
            targets.append({"y15": y15, "y30": y30, "y60": y60, "n60": n60})

    return pd.DataFrame(rows), pd.DataFrame(targets)
