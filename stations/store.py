"""Python store API for PPC locations, charge points, states, commands and autonomy."""

from __future__ import annotations

import csv
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from stations.state import (
    VALID_CAUSES,
    VALID_STATES,
    VALID_TARGET_STATES,
    evaluate_command_transition,
)

log = logging.getLogger("stations.store")
ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"

ENERGY_TARIFFS_RON: dict[str, float] = {
    "net_b2b_ron_per_kwh": 1.15,
    "gross_retail_ron_per_kwh": 1.39,
}

# Engineering plan §3.1: 13 observed capacities in Romania (kW -> idle_w, sleep_w, precharge_wake_s)
CAPACITY_POWER_TABLE: dict[int, dict[str, float]] = {
    11: {"idle_w": 12.0, "sleep_w": 5.5, "precharge_wake_s": 0.5},
    17: {"idle_w": 18.0, "sleep_w": 7.5, "precharge_wake_s": 0.5},
    22: {"idle_w": 25.0, "sleep_w": 9.0, "precharge_wake_s": 0.5},
    43: {"idle_w": 65.0, "sleep_w": 16.0, "precharge_wake_s": 0.5},
    50: {"idle_w": 95.0, "sleep_w": 19.0, "precharge_wake_s": 5.0},
    60: {"idle_w": 105.0, "sleep_w": 20.0, "precharge_wake_s": 5.0},
    75: {"idle_w": 115.0, "sleep_w": 21.5, "precharge_wake_s": 5.0},
    85: {"idle_w": 120.0, "sleep_w": 22.0, "precharge_wake_s": 6.0},
    100: {"idle_w": 130.0, "sleep_w": 23.0, "precharge_wake_s": 6.0},
    150: {"idle_w": 150.0, "sleep_w": 24.5, "precharge_wake_s": 6.0},
    160: {"idle_w": 160.0, "sleep_w": 25.5, "precharge_wake_s": 7.0},
    200: {"idle_w": 175.0, "sleep_w": 27.0, "precharge_wake_s": 7.0},
    300: {"idle_w": 230.0, "sleep_w": 32.0, "precharge_wake_s": 8.0},
}

_DEFAULT_PRECHARGE_S = {"AC_22": 0.5, "DC_50": 5.0, "DC_HPC": 7.0}

_engine: Engine | None = None
_engine_url: str | None = None

# In-memory fallback state when DATABASE_URL is unset or DB is unreachable
_mem_autonomy: str = "ACT"
_mem_cp_state: dict[str, dict[str, Any]] = {}
_mem_commands: list[dict[str, Any]] = []
_mem_events: list[dict[str, Any]] = []


def reset_memory_store() -> None:
    """Reset in-memory fallback store (for tests)."""
    global _mem_autonomy
    _mem_autonomy = "ACT"
    _mem_cp_state.clear()
    _mem_commands.clear()
    _mem_events.clear()


def set_engine(engine: Engine | None) -> None:
    """Inject a custom SQLAlchemy engine (or None to reset)."""
    global _engine, _engine_url
    _engine = engine
    _engine_url = str(engine.url) if engine is not None else None


def _get_engine() -> Engine | None:
    global _engine, _engine_url
    if _engine is not None:
        return _engine
    url = os.environ.get("DATABASE_URL")
    if not url:
        return None
    try:
        eng = create_engine(url)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        _engine = eng
        _engine_url = url
        return _engine
    except Exception as exc:
        log.debug("DB unavailable (%r), using fallback store", exc)
        return None


def _has_tables(eng: Engine, *names: str) -> bool:
    try:
        existing = set(inspect(eng).get_table_names())
        return set(names) <= existing
    except Exception:
        return False


def _utc_dt(ts: datetime | str | None) -> datetime:
    if ts is None:
        return datetime.now(UTC)
    if isinstance(ts, str):
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    return ts.astimezone(UTC) if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


def _iso(dt: datetime | str | None) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return _utc_dt(dt).isoformat()


def _db_ts(eng: Engine, dt: datetime | None) -> datetime | str | None:
    if dt is None:
        return None
    return _iso(dt) if eng.dialect.name == "sqlite" else dt


def _ttl_seconds(config_dir: Path = CONFIG_DIR) -> int:
    sim_path = config_dir / "sim.yaml"
    if not sim_path.exists():
        return 900
    cfg = yaml.safe_load(sim_path.read_text(encoding="utf-8")) or {}
    slot_s = int(cfg.get("slot_s", 300))
    ttl_slots = int(cfg.get("recommendation_ttl_slots", 3))
    return slot_s * ttl_slots


def get_energy_tariffs() -> dict[str, float]:
    """Return Romania B2B net and retail gross electricity tariffs (RON/kWh)."""
    return dict(ENERGY_TARIFFS_RON)


def get_power_profile(
    power_class: str,
    max_kw: float | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Return power draw and wake durations for `power_class` (and optional exact `max_kw`)."""
    pc = str(getattr(power_class, "value", power_class))
    path = config_path or (CONFIG_DIR / "power_profiles.yaml")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if pc not in raw or not isinstance(raw[pc], dict):
        raise ValueError(f"unknown power_class: {power_class!r}")

    entry = dict(raw[pc])
    out: dict[str, Any] = {
        "idle_w": float(entry["idle_w"]),
        "standby_w": float(entry["standby_w"]),
        "deep_sleep_w": float(entry["deep_sleep_w"]) if entry["deep_sleep_w"] is not None else None,
        "standby_wake_s": float(entry["standby_wake_s"]),
        "deep_sleep_wake_s": (
            float(entry["deep_sleep_wake_s"]) if entry["deep_sleep_wake_s"] is not None else None
        ),
        "wake_extra_wh": float(raw.get("wake_extra_wh") or 0.0),
        "precharge_wake_s": _DEFAULT_PRECHARGE_S.get(pc, 5.0),
    }
    if max_kw is not None and not pd.isna(max_kw):
        nearest_kw = min(CAPACITY_POWER_TABLE, key=lambda k: abs(k - float(max_kw)))
        cap = CAPACITY_POWER_TABLE[nearest_kw]
        out["idle_w"] = cap["idle_w"]
        out["precharge_wake_s"] = cap["precharge_wake_s"]
        if out["deep_sleep_w"] is None:
            out["standby_w"] = cap["sleep_w"]
        else:
            out["deep_sleep_w"] = cap["sleep_w"]
    return out


def _parse_bbox(
    bbox: str | tuple[float, float, float, float] | list[float] | None,
) -> tuple[float, float, float, float] | None:
    if bbox is None:
        return None
    if isinstance(bbox, str):
        parts = [p.strip() for p in bbox.split(",")]
        if len(parts) != 4:
            raise ValueError(
                "bbox must have 4 comma-separated numbers: minLon,minLat,maxLon,maxLat"
            )
        vals = tuple(float(p) for p in parts)
    elif len(bbox) == 4:
        vals = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    else:
        raise ValueError("bbox must have 4 values: (min_lon, min_lat, max_lon, max_lat)")
    min_lon, min_lat, max_lon, max_lat = vals
    if min_lon > max_lon or min_lat > max_lat:
        raise ValueError("invalid bbox: min bounds exceed max bounds")
    return min_lon, min_lat, max_lon, max_lat


def _clean_conns(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, str):
        return [c.upper() for c in val.split(";") if c]
    return [str(c).upper() for c in val if c is not None]


def _load_locations_fallback() -> list[dict[str, Any]]:
    data_dir = Path(os.environ.get("DATA_DIR", ROOT / "data"))
    loc_pq = data_dir / "processed" / "locations.parquet"
    cp_pq = data_dir / "processed" / "charge_points.parquet"

    if loc_pq.exists() and cp_pq.exists():
        dl = pd.read_parquet(loc_pq)
        dc = pd.read_parquet(cp_pq)
    else:
        from stations.importer import (
            ContextClassifier,
            build_seed,
            load_config,
            load_counties,
        )

        cfg = load_config()
        counties = load_counties(ROOT / "data" / "seed" / "ro_counties.geojson")
        clf = ContextClassifier(cfg, [], [])
        with open(ROOT / "data" / "seed" / "stations_open.csv", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        locs, cps, _ = build_seed(rows, cfg, counties, clf)
        dl = pd.DataFrame(locs)
        dc = pd.DataFrame(cps)

    cps_by_loc: dict[str, list[dict[str, Any]]] = {}
    for row in dc.to_dict(orient="records"):
        cid = str(row["cp_id"])
        mk = row.get("max_kw")
        cp_item: dict[str, Any] = {
            "cp_id": cid,
            "power_class": str(row["power_class"]),
            "max_kw": None if mk is None or pd.isna(mk) else float(mk),
            "connectors": _clean_conns(row.get("connectors")),
            "current_type": str(row["current_type"]),
        }
        if cid in _mem_cp_state:
            cp_item["state"] = _mem_cp_state[cid]["state"]
        cps_by_loc.setdefault(str(row["location_id"]), []).append(cp_item)

    out: list[dict[str, Any]] = []
    for row in dl.to_dict(orient="records"):
        lid = str(row["location_id"])
        partner = row.get("partner")
        addr = row.get("address")
        out.append(
            {
                "location_id": lid,
                "name": str(row["name"]),
                "partner": None
                if partner is None or pd.isna(partner) or partner == ""
                else str(partner),
                "address": None if addr is None or pd.isna(addr) or addr == "" else str(addr),
                "county_code": str(row["county_code"]),
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "context_type": str(row["context_type"]),
                "primary_source": str(row["primary_source"]),
                "confidence": str(row["confidence"]),
                "charge_points": sorted(cps_by_loc.get(lid, []), key=lambda c: c["cp_id"]),
            }
        )
    return sorted(out, key=lambda x: x["location_id"])


def _load_locations_db(
    eng: Engine,
    bbox_tuple: tuple[float, float, float, float] | None = None,
    county: str | None = None,
    location_id: str | None = None,
) -> list[dict[str, Any]]:
    dialect = eng.dialect.name
    if dialect == "postgresql":
        lat_Lon_expr = "ST_Y(geom::geometry) AS lat, ST_X(geom::geometry) AS lon"
    else:
        lat_Lon_expr = "lat, lon"

    clauses: list[str] = []
    params: dict[str, Any] = {}
    if location_id is not None:
        clauses.append("location_id = :location_id")
        params["location_id"] = location_id
    if county is not None:
        clauses.append("UPPER(county_code) = :county")
        params["county"] = county.upper()
    if bbox_tuple is not None and dialect == "postgresql":
        clauses.append(
            "ST_Intersects(geom, ST_MakeEnvelope("
            ":min_lon, :min_lat, :max_lon, :max_lat, 4326)::geography)"
        )
        params.update(
            min_lon=bbox_tuple[0],
            min_lat=bbox_tuple[1],
            max_lon=bbox_tuple[2],
            max_lat=bbox_tuple[3],
        )

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    loc_sql = text(
        f"SELECT location_id, name, partner, address, county_code, {lat_Lon_expr}, "
        f"context_type, primary_source, confidence FROM locations {where} ORDER BY location_id"
    )
    has_state = _has_tables(eng, "cp_state")
    if has_state:
        cp_sql = text(
            "SELECT c.cp_id, c.location_id, c.power_class, c.max_kw, c.connectors, "
            "c.current_type, s.state FROM charge_points c "
            "LEFT JOIN cp_state s ON s.cp_id = c.cp_id ORDER BY c.cp_id"
        )
    else:
        cp_sql = text(
            "SELECT cp_id, location_id, power_class, max_kw, connectors, "
            "current_type, NULL AS state FROM charge_points ORDER BY cp_id"
        )

    with eng.connect() as conn:
        loc_rows = [dict(r._mapping) for r in conn.execute(loc_sql, params)]
        if not loc_rows:
            return []
        cp_rows = [dict(r._mapping) for r in conn.execute(cp_sql)]

    cps_by_loc: dict[str, list[dict[str, Any]]] = {}
    for r in cp_rows:
        conns = r["connectors"]
        if isinstance(conns, str):
            conns = json.loads(conns) if conns.startswith("[") else _clean_conns(conns)
        else:
            conns = _clean_conns(conns)
        item: dict[str, Any] = {
            "cp_id": str(r["cp_id"]),
            "power_class": str(r["power_class"]),
            "max_kw": float(r["max_kw"]) if r["max_kw"] is not None else None,
            "connectors": conns,
            "current_type": str(r["current_type"]),
        }
        if r.get("state") is not None:
            item["state"] = str(r["state"])
        cps_by_loc.setdefault(str(r["location_id"]), []).append(item)

    out: list[dict[str, Any]] = []
    for r in loc_rows:
        lat, lon = float(r["lat"]), float(r["lon"])
        if bbox_tuple is not None and dialect != "postgresql":
            min_lon, min_lat, max_lon, max_lat = bbox_tuple
            if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
                continue
        lid = str(r["location_id"])
        out.append(
            {
                "location_id": lid,
                "name": str(r["name"]),
                "partner": r["partner"] or None,
                "address": r["address"] or None,
                "county_code": str(r["county_code"]),
                "lat": lat,
                "lon": lon,
                "context_type": str(r["context_type"]),
                "primary_source": str(r["primary_source"]),
                "confidence": str(r["confidence"]),
                "charge_points": cps_by_loc.get(lid, []),
            }
        )
    return out


def list_locations(
    bbox: str | tuple[float, float, float, float] | list[float] | None = None,
    county: str | None = None,
) -> list[dict[str, Any]]:
    """List locations (with charge_points), filtered by optional bbox and county code."""
    bbox_tuple = _parse_bbox(bbox)
    eng = _get_engine()
    if eng is not None and _has_tables(eng, "locations", "charge_points"):
        rows = _load_locations_db(eng, bbox_tuple=bbox_tuple, county=county)
        if rows or _has_any_locations(eng):
            return rows

    locs = _load_locations_fallback()
    if county is not None:
        c_up = county.upper()
        locs = [loc for loc in locs if loc["county_code"].upper() == c_up]
    if bbox_tuple is not None:
        min_lon, min_lat, max_lon, max_lat = bbox_tuple
        locs = [
            loc
            for loc in locs
            if min_lon <= loc["lon"] <= max_lon and min_lat <= loc["lat"] <= max_lat
        ]
    return locs


def _has_any_locations(eng: Engine) -> bool:
    with eng.connect() as conn:
        return bool(conn.execute(text("SELECT 1 FROM locations LIMIT 1")).scalar())


def get_location(location_id: str) -> dict[str, Any] | None:
    """Return a single Location dict by `location_id`, or None if not found."""
    eng = _get_engine()
    if eng is not None and _has_tables(eng, "locations", "charge_points"):
        rows = _load_locations_db(eng, location_id=location_id)
        if rows:
            return rows[0]
        if _has_any_locations(eng):
            return None

    for loc in _load_locations_fallback():
        if loc["location_id"] == location_id:
            return loc
    return None


def get_autonomy() -> str:
    """Return global autonomy mode: 'ACT' or 'RECOMMEND'."""
    eng = _get_engine()
    if eng is not None and _has_tables(eng, "settings"):
        with eng.connect() as conn:
            val = conn.execute(
                text("SELECT value FROM settings WHERE key = 'autonomy_mode'")
            ).scalar()
        if val is not None:
            if isinstance(val, str) and val.startswith('"'):  # json text (sqlite); psycopg decodes
                val = json.loads(val)
            return str(val)
    return _mem_autonomy


def set_autonomy(mode: str) -> str:
    """Set global autonomy mode ('ACT' or 'RECOMMEND') and return it."""
    global _mem_autonomy
    m = str(getattr(mode, "value", mode)).upper()
    if m not in ("ACT", "RECOMMEND"):
        raise ValueError(f"autonomy mode must be 'ACT' or 'RECOMMEND', got {mode!r}")
    _mem_autonomy = m
    eng = _get_engine()
    if eng is not None and _has_tables(eng, "settings"):
        payload = json.dumps(m)
        sql = (
            "INSERT INTO settings (key, value) VALUES ('autonomy_mode', CAST(:v AS jsonb)) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            if eng.dialect.name == "postgresql"
            else "INSERT INTO settings (key, value) VALUES ('autonomy_mode', :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
        with eng.begin() as conn:
            conn.execute(text(sql), {"v": payload})
    return m


def _lookup_cp_meta(cp_id: str, eng: Engine | None) -> tuple[str, str]:
    """Return (current_state, power_class) for `cp_id`."""
    state = _mem_cp_state.get(cp_id, {}).get("state", "IDLE")
    power_class = "DC_50"
    if eng is not None and _has_tables(eng, "charge_points"):
        with eng.connect() as conn:
            pc_row = conn.execute(
                text("SELECT power_class FROM charge_points WHERE cp_id = :cid"), {"cid": cp_id}
            ).scalar()
            if pc_row:
                power_class = str(pc_row)
            if _has_tables(eng, "cp_state"):
                st_row = conn.execute(
                    text("SELECT state FROM cp_state WHERE cp_id = :cid"), {"cid": cp_id}
                ).scalar()
                if st_row:
                    state = str(st_row)
    return state, power_class


def apply_command(
    cmd: Any,
    mode: str | None = None,
    *,
    policy: str = "rl_ppo",
    from_state: str | None = None,
    power_class: str | None = None,
    run_id: str | None = None,
    ts: datetime | str | None = None,
) -> dict[str, Any] | None:
    """Apply or recommend a policy command per spec 03 §4–7 and decision 0018."""
    if isinstance(cmd, dict):
        cp_id = str(cmd["cp_id"])
        raw_target = str(getattr(cmd["target_state"], "value", cmd["target_state"]))
        reason = str(cmd.get("reason") or "")
        policy = str(cmd.get("policy") or policy)
        from_state = cmd.get("from_state") or from_state
        power_class = cmd.get("power_class") or power_class
        run_id = cmd.get("run_id") if "run_id" in cmd else run_id
        ts = cmd.get("ts") if "ts" in cmd else ts
    else:
        cp_id = str(cmd.cp_id)
        raw_target = str(getattr(cmd.target_state, "value", cmd.target_state))
        reason = str(getattr(cmd, "reason", "") or "")

    eff_mode = str(getattr(mode, "value", mode)).upper() if mode is not None else get_autonomy()
    if eff_mode not in ("ACT", "RECOMMEND"):
        raise ValueError(f"invalid autonomy mode: {mode!r}")

    eng = _get_engine()
    if from_state is None or power_class is None:
        db_state, db_pc = _lookup_cp_meta(cp_id, eng)
        from_state = str(getattr(from_state, "value", from_state or db_state))
        power_class = str(getattr(power_class, "value", power_class or db_pc))
    else:
        from_state = str(getattr(from_state, "value", from_state))
        power_class = str(getattr(power_class, "value", power_class))

    prof = get_power_profile(power_class)
    tr = evaluate_command_transition(from_state, raw_target, power_class, prof)

    # Decision 0018 & A4: same, waking, and offline commands are dropped silently
    if tr.status == "dropped":
        return None

    ts_dt = _utc_dt(ts)
    if tr.status == "rejected":
        status = "rejected"
        cmd_reason = tr.drop_reason or "busy"
        expires_dt = None
    elif eff_mode == "RECOMMEND":
        status = "pending"
        cmd_reason = reason
        expires_dt = ts_dt + timedelta(seconds=_ttl_seconds())
    else:
        status = "applied"
        cmd_reason = reason
        expires_dt = None

    row = _persist_command(
        eng=eng,
        ts_dt=ts_dt,
        run_id=str(run_id) if run_id is not None else None,
        cp_id=cp_id,
        policy=str(getattr(policy, "value", policy)),
        from_state=from_state,
        target_state=tr.effective_target,
        reason=cmd_reason,
        status=status,
        expires_dt=expires_dt,
    )

    if status == "applied" and run_id is None:
        _update_live_cp_state(
            eng, cp_id, from_state, tr.new_state, "agent", ts_dt, last_command_id=row["id"]
        )
    return row


def _persist_command(
    *,
    eng: Engine | None,
    ts_dt: datetime,
    run_id: str | None,
    cp_id: str,
    policy: str,
    from_state: str,
    target_state: str,
    reason: str,
    status: str,
    expires_dt: datetime | None,
) -> dict[str, Any]:
    if eng is not None and _has_tables(eng, "commands"):
        with eng.begin() as conn:
            if status == "pending":
                conn.execute(
                    text(
                        "UPDATE commands SET status = 'expired' "
                        "WHERE cp_id = :cp_id AND policy = :policy AND status = 'pending' "
                        "AND ((:run_id IS NULL AND run_id IS NULL) OR run_id = :run_id)"
                    ),
                    {"cp_id": cp_id, "policy": policy, "run_id": run_id},
                )
            res = conn.execute(
                text(
                    "INSERT INTO commands (ts, run_id, cp_id, policy, from_state, target_state, "
                    "reason, status, expires_at) VALUES (:ts, :run_id, :cp_id, :policy, "
                    ":from_state, :target_state, :reason, :status, :expires_at) RETURNING id"
                ),
                {
                    "ts": _db_ts(eng, ts_dt),
                    "run_id": run_id,
                    "cp_id": cp_id,
                    "policy": policy,
                    "from_state": from_state,
                    "target_state": target_state,
                    "reason": reason,
                    "status": status,
                    "expires_at": _db_ts(eng, expires_dt),
                },
            )
            cmd_id = int(res.scalar_one())
    else:
        if status == "pending":
            for c in _mem_commands:
                if (
                    c["cp_id"] == cp_id
                    and c["policy"] == policy
                    and c["run_id"] == run_id
                    and c["status"] == "pending"
                ):
                    c["status"] = "expired"
        cmd_id = len(_mem_commands) + 1
        _mem_commands.append(
            {
                "id": cmd_id,
                "ts": _iso(ts_dt),
                "run_id": run_id,
                "cp_id": cp_id,
                "policy": policy,
                "from_state": from_state,
                "target_state": target_state,
                "reason": reason,
                "status": status,
                "expires_at": _iso(expires_dt),
                "decided_by": None,
                "decided_at": None,
            }
        )

    return {
        "id": cmd_id,
        "ts": _iso(ts_dt),
        "cp_id": cp_id,
        "policy": policy,
        "from_state": from_state,
        "target_state": target_state,
        "reason": reason,
        "status": status,
        "expires_at": _iso(expires_dt),
    }


def decide_recommendation(
    id: int,
    approve: bool,
    who: str = "operator",
    ts: datetime | str | None = None,
) -> dict[str, Any]:
    """Approve or reject a pending recommendation (raises KeyError on 404, ValueError on 409)."""
    now_dt = _utc_dt(ts)
    new_status = "approved" if approve else "rejected"
    eng = _get_engine()

    if eng is not None and _has_tables(eng, "commands"):
        with eng.begin() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT id, ts, cp_id, policy, from_state, target_state, reason, "
                        "status, expires_at FROM commands WHERE id = :id"
                    ),
                    {"id": int(id)},
                )
                .mappings()
                .first()
            )
            if row is None:
                raise KeyError(f"recommendation {id} not found")
            cur_status = str(row["status"])
            exp = row["expires_at"]
            exp_dt = _utc_dt(exp) if exp is not None else None
            if cur_status == "pending" and exp_dt is not None and now_dt > exp_dt:
                conn.execute(
                    text("UPDATE commands SET status = 'expired' WHERE id = :id"),
                    {"id": int(id)},
                )
                raise ValueError(f"recommendation {id} has expired")
            if cur_status != "pending":
                raise ValueError(f"recommendation {id} is already {cur_status}")

            conn.execute(
                text(
                    "UPDATE commands SET status = :status, decided_by = :who, decided_at = :at "
                    "WHERE id = :id"
                ),
                {"status": new_status, "who": who, "at": _db_ts(eng, now_dt), "id": int(id)},
            )
            return {
                "id": int(row["id"]),
                "ts": _iso(row["ts"]),
                "cp_id": str(row["cp_id"]),
                "policy": str(row["policy"]),
                "from_state": str(row["from_state"]),
                "target_state": str(row["target_state"]),
                "reason": str(row["reason"]),
                "status": new_status,
                "expires_at": _iso(exp_dt),
            }

    for c in _mem_commands:
        if c["id"] == int(id):
            exp_dt = _utc_dt(c["expires_at"]) if c.get("expires_at") else None
            if c["status"] == "pending" and exp_dt is not None and now_dt > exp_dt:
                c["status"] = "expired"
                raise ValueError(f"recommendation {id} has expired")
            if c["status"] != "pending":
                raise ValueError(f"recommendation {id} is already {c['status']}")
            c["status"] = new_status
            c["decided_by"] = who
            c["decided_at"] = _iso(now_dt)
            return {
                "id": c["id"],
                "ts": c["ts"],
                "cp_id": c["cp_id"],
                "policy": c["policy"],
                "from_state": c["from_state"],
                "target_state": c["target_state"],
                "reason": c["reason"],
                "status": c["status"],
                "expires_at": c["expires_at"],
            }
    raise KeyError(f"recommendation {id} not found")


def list_recommendations(
    run_id: str | None = None,
    status: str | None = "pending",
    now: datetime | str | None = None,
) -> list[dict[str, Any]]:
    """List recommendations (auto-expiring pending rows past `expires_at` when `now` is given)."""
    now_dt = _utc_dt(now) if now is not None else None
    eng = _get_engine()
    if eng is not None and _has_tables(eng, "commands"):
        with eng.begin() as conn:
            if now_dt is not None:
                conn.execute(
                    text(
                        "UPDATE commands SET status = 'expired' "
                        "WHERE status = 'pending' AND expires_at IS NOT NULL AND expires_at < :now"
                    ),
                    {"now": _db_ts(eng, now_dt)},
                )
            clauses: list[str] = ["expires_at IS NOT NULL"]
            params: dict[str, Any] = {}
            if run_id is not None:
                clauses.append("run_id = :run_id")
                params["run_id"] = str(run_id)
            if status is not None:
                clauses.append("status = :status")
                params["status"] = str(status)
            where = "WHERE " + " AND ".join(clauses)
            rows = (
                conn.execute(
                    text(
                        f"SELECT id, ts, cp_id, policy, from_state, target_state, reason, "
                        f"status, expires_at FROM commands {where} ORDER BY id"
                    ),
                    params,
                )
                .mappings()
                .all()
            )
            return [
                {
                    "id": int(r["id"]),
                    "ts": _iso(r["ts"]),
                    "cp_id": str(r["cp_id"]),
                    "policy": str(r["policy"]),
                    "from_state": str(r["from_state"]),
                    "target_state": str(r["target_state"]),
                    "reason": str(r["reason"]),
                    "status": str(r["status"]),
                    "expires_at": _iso(r["expires_at"]),
                }
                for r in rows
            ]

    out: list[dict[str, Any]] = []
    for c in _mem_commands:
        if c.get("expires_at") is None:
            continue
        if now_dt is not None and c["status"] == "pending":
            if now_dt > _utc_dt(c["expires_at"]):
                c["status"] = "expired"
        if run_id is not None and c["run_id"] != str(run_id):
            continue
        if status is not None and c["status"] != status:
            continue
        out.append(
            {
                "id": c["id"],
                "ts": c["ts"],
                "cp_id": c["cp_id"],
                "policy": c["policy"],
                "from_state": c["from_state"],
                "target_state": c["target_state"],
                "reason": c["reason"],
                "status": c["status"],
                "expires_at": c["expires_at"],
            }
        )
    return out


def _update_live_cp_state(
    eng: Engine | None,
    cp_id: str,
    from_state: str,
    to_state: str,
    cause: str,
    ts_dt: datetime,
    last_command_id: int | None = None,
) -> None:
    _mem_cp_state[cp_id] = {
        "cp_id": cp_id,
        "state": to_state,
        "since_ts": _iso(ts_dt),
        "last_command_id": last_command_id,
    }
    if eng is not None and _has_tables(eng, "cp_state", "state_events"):
        with eng.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO cp_state (cp_id, state, since_ts, last_command_id) "
                    "VALUES (:cp_id, :state, :ts, :cmd_id) "
                    "ON CONFLICT (cp_id) DO UPDATE SET state = EXCLUDED.state, "
                    "since_ts = EXCLUDED.since_ts, "
                    "last_command_id = COALESCE(EXCLUDED.last_command_id, cp_state.last_command_id)"
                ),
                {
                    "cp_id": cp_id,
                    "state": to_state,
                    "ts": _db_ts(eng, ts_dt),
                    "cmd_id": last_command_id,
                },
            )
            conn.execute(
                text(
                    "INSERT INTO state_events (ts, cp_id, from_state, to_state, cause, run_id) "
                    "VALUES (:ts, :cp_id, :from_state, :to_state, :cause, NULL)"
                ),
                {
                    "ts": _db_ts(eng, ts_dt),
                    "cp_id": cp_id,
                    "from_state": from_state,
                    "to_state": to_state,
                    "cause": cause,
                },
            )
    else:
        _mem_events.append(
            {
                "id": len(_mem_events) + 1,
                "ts": _iso(ts_dt),
                "cp_id": cp_id,
                "from_state": from_state,
                "to_state": to_state,
                "cause": cause,
                "run_id": None,
            }
        )


def record_state_event(
    cp_id: str,
    from_state: str,
    to_state: str,
    cause: str,
    ts: datetime | str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Record a state transition event (and update `cp_state` in live mode)."""
    fs = str(getattr(from_state, "value", from_state))
    tos = str(getattr(to_state, "value", to_state))
    c = str(cause)
    if fs not in VALID_STATES:
        raise ValueError(f"invalid from_state: {from_state!r}")
    if tos not in VALID_STATES:
        raise ValueError(f"invalid to_state: {to_state!r}")
    if c not in VALID_CAUSES:
        raise ValueError(f"invalid cause: {cause!r}")

    ts_dt = _utc_dt(ts)
    eng = _get_engine()
    if run_id is None:
        _update_live_cp_state(eng, str(cp_id), fs, tos, c, ts_dt)
        return {
            "ts": _iso(ts_dt),
            "cp_id": str(cp_id),
            "from_state": fs,
            "to_state": tos,
            "cause": c,
            "run_id": None,
        }

    if eng is not None and _has_tables(eng, "state_events"):
        with eng.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO state_events (ts, cp_id, from_state, to_state, cause, run_id) "
                    "VALUES (:ts, :cp_id, :from_state, :to_state, :cause, :run_id)"
                ),
                {
                    "ts": _db_ts(eng, ts_dt),
                    "cp_id": str(cp_id),
                    "from_state": fs,
                    "to_state": tos,
                    "cause": c,
                    "run_id": str(run_id),
                },
            )
    else:
        _mem_events.append(
            {
                "id": len(_mem_events) + 1,
                "ts": _iso(ts_dt),
                "cp_id": str(cp_id),
                "from_state": fs,
                "to_state": tos,
                "cause": c,
                "run_id": str(run_id),
            }
        )
    return {
        "ts": _iso(ts_dt),
        "cp_id": str(cp_id),
        "from_state": fs,
        "to_state": tos,
        "cause": c,
        "run_id": str(run_id),
    }


_COMMAND_STATUSES = ("applied", "pending", "approved", "rejected", "expired")
_HISTORY_TABLES = ("commands", "state_events")


def run_history_enabled() -> bool:
    """True when a DB is reachable and has the `commands` and `state_events` tables."""
    eng = _get_engine()
    return eng is not None and _has_tables(eng, *_HISTORY_TABLES)


def _opt_db_ts(eng: Engine, ts: datetime | str | None) -> datetime | str | None:
    return None if ts is None else _db_ts(eng, _utc_dt(ts))


def _history_command_row(eng: Engine, run_id: str, c: dict[str, Any]) -> dict[str, Any]:
    status = str(c["status"])
    from_state = str(getattr(c["from_state"], "value", c["from_state"]))
    target = str(getattr(c["target_state"], "value", c["target_state"]))
    if status not in _COMMAND_STATUSES:
        raise ValueError(f"invalid command status: {status!r}")
    if from_state not in VALID_STATES or target not in VALID_TARGET_STATES:
        raise ValueError(f"invalid command states: {from_state!r} -> {target!r}")
    return {
        "ts": _db_ts(eng, _utc_dt(c["ts"])),
        "run_id": run_id,
        "cp_id": str(c["cp_id"]),
        "policy": str(getattr(c["policy"], "value", c["policy"])),
        "from_state": from_state,
        "target_state": target,
        "reason": str(c.get("reason") or ""),
        "status": status,
        "expires_at": _opt_db_ts(eng, c.get("expires_at")),
        "decided_by": c.get("decided_by"),
        "decided_at": _opt_db_ts(eng, c.get("decided_at")),
    }


def _history_event_row(eng: Engine, run_id: str, e: dict[str, Any]) -> dict[str, Any]:
    fs = str(getattr(e["from_state"], "value", e["from_state"]))
    tos = str(getattr(e["to_state"], "value", e["to_state"]))
    cause = str(e["cause"])
    if fs not in VALID_STATES or tos not in VALID_STATES:
        raise ValueError(f"invalid state event: {fs!r} -> {tos!r}")
    if cause not in VALID_CAUSES:
        raise ValueError(f"invalid cause: {cause!r}")
    return {
        "ts": _db_ts(eng, _utc_dt(e["ts"])),
        "cp_id": str(e["cp_id"]),
        "from_state": fs,
        "to_state": tos,
        "cause": cause,
        "run_id": run_id,
    }


def record_run_history(
    run_id: str, commands: list[dict[str, Any]], events: list[dict[str, Any]]
) -> int:
    """Bulk-insert a sim run's commands and state events in one transaction; returns rows written.

    Without a reachable DB (or without the tables) nothing is written and 0 is returned.
    """
    if not commands and not events:
        return 0
    eng = _get_engine()
    if eng is None or not _has_tables(eng, *_HISTORY_TABLES):
        return 0
    rid = str(run_id)
    cmd_rows = [_history_command_row(eng, rid, c) for c in commands]
    ev_rows = [_history_event_row(eng, rid, e) for e in events]
    with eng.begin() as conn:
        if cmd_rows:
            conn.execute(
                text(
                    "INSERT INTO commands (ts, run_id, cp_id, policy, from_state, target_state, "
                    "reason, status, expires_at, decided_by, decided_at) VALUES (:ts, :run_id, "
                    ":cp_id, :policy, :from_state, :target_state, :reason, :status, :expires_at, "
                    ":decided_by, :decided_at)"
                ),
                cmd_rows,
            )
        if ev_rows:
            conn.execute(
                text(
                    "INSERT INTO state_events (ts, cp_id, from_state, to_state, cause, run_id) "
                    "VALUES (:ts, :cp_id, :from_state, :to_state, :cause, :run_id)"
                ),
                ev_rows,
            )
    return len(cmd_rows) + len(ev_rows)


def get_cp_states() -> dict[str, dict[str, Any]]:
    """Return current live `cp_state` rows keyed by `cp_id`."""
    eng = _get_engine()
    if eng is not None and _has_tables(eng, "cp_state"):
        with eng.connect() as conn:
            rows = (
                conn.execute(text("SELECT cp_id, state, since_ts, last_command_id FROM cp_state"))
                .mappings()
                .all()
            )
        return {
            str(r["cp_id"]): {
                "cp_id": str(r["cp_id"]),
                "state": str(r["state"]),
                "since_ts": _iso(r["since_ts"]),
                "last_command_id": int(r["last_command_id"])
                if r["last_command_id"] is not None
                else None,
            }
            for r in rows
        }
    return dict(_mem_cp_state)
