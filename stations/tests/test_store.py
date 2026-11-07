"""Unit and SQLAlchemy DB tests for `stations.store` (spec 03 §5–7, engineering plan)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text

from sim.types import Command, TargetState
from stations import store


@pytest.fixture(autouse=True)
def _clean_store(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store.set_engine(None)
    store.reset_memory_store()
    yield
    store.set_engine(None)
    store.reset_memory_store()


@pytest.fixture()
def sqlite_engine():
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE locations ("
                "location_id TEXT PRIMARY KEY, name TEXT NOT NULL, "
                "operator TEXT DEFAULT 'PPC Blue', "
                "partner TEXT, address TEXT, county_code TEXT NOT NULL, lat REAL NOT NULL, "
                "lon REAL NOT NULL, context_type TEXT NOT NULL, primary_source TEXT NOT NULL, "
                "confidence TEXT NOT NULL, cp_count_estimated INTEGER DEFAULT 0)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE charge_points ("
                "cp_id TEXT PRIMARY KEY, location_id TEXT NOT NULL, power_class TEXT NOT NULL, "
                "max_kw REAL, connectors TEXT NOT NULL, current_type TEXT NOT NULL)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE cp_state ("
                "cp_id TEXT PRIMARY KEY, state TEXT NOT NULL, since_ts TIMESTAMP, "
                "last_command_id INTEGER)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE state_events ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TIMESTAMP NOT NULL, cp_id TEXT NOT NULL, "
                "from_state TEXT NOT NULL, to_state TEXT NOT NULL, "
                "cause TEXT NOT NULL, run_id TEXT)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE commands ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TIMESTAMP NOT NULL, run_id TEXT, "
                "cp_id TEXT NOT NULL, policy TEXT NOT NULL, from_state TEXT NOT NULL, "
                "target_state TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL, "
                "expires_at TIMESTAMP, decided_by TEXT, decided_at TIMESTAMP)"
            )
        )
        conn.execute(text("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"))
        conn.execute(text("INSERT INTO settings (key, value) VALUES ('autonomy_mode', '\"ACT\"')"))
        conn.execute(
            text(
                "INSERT INTO locations VALUES "
                "('L00001', 'PPC Penny Otopeni', 'PPC Blue', 'Penny', 'Calea Bucurestilor', "
                "'IF', 44.55, 26.07, 'retail', 'osm', 'high', 0), "
                "('L00002', 'PPC Cluj Center', 'PPC Blue', NULL, 'Str. Memorandumului', "
                "'CJ', 46.77, 23.59, 'urban', 'ocm', 'high', 0)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO charge_points VALUES "
                "('L00001-1', 'L00001', 'DC_50', 50.0, '[\"CCS2\",\"CHADEMO\"]', 'DC'), "
                "('L00001-2', 'L00001', 'AC_22', 22.0, '[\"TYPE2\"]', 'AC'), "
                "('L00002-1', 'L00002', 'DC_HPC', 150.0, '[\"CCS2\"]', 'DC')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO cp_state (cp_id, state, since_ts) VALUES "
                "('L00001-1', 'IDLE', '2026-11-07T00:00:00+00:00'), "
                "('L00001-2', 'IDLE', '2026-11-07T00:00:00+00:00'), "
                "('L00002-1', 'IDLE', '2026-11-07T00:00:00+00:00')"
            )
        )
    return eng


def test_power_profiles_and_13_capacity_engineering_lookup():
    ac = store.get_power_profile("AC_22")
    assert ac["idle_w"] == 25.0 and ac["standby_w"] == 9.0 and ac["deep_sleep_w"] is None
    assert ac["standby_wake_s"] == 10.0 and ac["precharge_wake_s"] == 0.5

    dc50 = store.get_power_profile("DC_50")
    assert dc50["idle_w"] == 105.0 and dc50["standby_w"] == 35.0 and dc50["deep_sleep_w"] == 20.0
    assert dc50["deep_sleep_wake_s"] == 180.0 and dc50["precharge_wake_s"] == 5.0

    hpc = store.get_power_profile("DC_HPC")
    assert hpc["idle_w"] == 165.0 and hpc["standby_w"] == 50.0 and hpc["deep_sleep_w"] == 25.5

    # Exact 13-capacity engineering table lookups
    p11 = store.get_power_profile("AC_22", max_kw=11)
    assert p11["idle_w"] == 12.0 and p11["standby_w"] == 5.5 and p11["deep_sleep_w"] is None

    p50 = store.get_power_profile("DC_50", max_kw=50)
    assert p50["idle_w"] == 95.0 and p50["deep_sleep_w"] == 19.0 and p50["precharge_wake_s"] == 5.0

    p300 = store.get_power_profile("DC_HPC", max_kw=300)
    assert (
        p300["idle_w"] == 230.0 and p300["deep_sleep_w"] == 32.0 and p300["precharge_wake_s"] == 8.0
    )

    tariffs = store.get_energy_tariffs()
    assert tariffs == {"net_b2b_ron_per_kwh": 1.15, "gross_retail_ron_per_kwh": 1.39}

    with pytest.raises(ValueError):
        store.get_power_profile("UNKNOWN")


def test_list_and_get_locations_db(sqlite_engine):
    store.set_engine(sqlite_engine)
    all_locs = store.list_locations()
    assert len(all_locs) == 2
    assert all_locs[0]["location_id"] == "L00001"
    assert len(all_locs[0]["charge_points"]) == 2
    assert all_locs[0]["charge_points"][0]["state"] == "IDLE"

    if_locs = store.list_locations(county="if")
    assert [loc["location_id"] for loc in if_locs] == ["L00001"]

    bbox_locs = store.list_locations(bbox="25.0,44.0,27.0,45.0")
    assert [loc["location_id"] for loc in bbox_locs] == ["L00001"]

    loc1 = store.get_location("L00001")
    assert loc1 is not None and loc1["name"] == "PPC Penny Otopeni"
    assert store.get_location("L99999") is None

    with pytest.raises(ValueError):
        store.list_locations(bbox="bad,bbox")
    with pytest.raises(ValueError):
        store.list_locations(bbox=(27.0, 45.0, 25.0, 44.0))


def test_list_and_get_locations_fallback():
    locs = store.list_locations()
    assert len(locs) >= 116
    first_id = locs[0]["location_id"]
    got = store.get_location(first_id)
    assert got is not None and got["location_id"] == first_id
    assert store.get_location("L99999") is None


def test_autonomy_switch_db_and_memory(sqlite_engine):
    assert store.get_autonomy() == "ACT"
    assert store.set_autonomy("RECOMMEND") == "RECOMMEND"
    assert store.get_autonomy() == "RECOMMEND"

    store.set_engine(sqlite_engine)
    assert store.get_autonomy() == "ACT"
    assert store.set_autonomy("RECOMMEND") == "RECOMMEND"
    assert store.get_autonomy() == "RECOMMEND"

    with pytest.raises(ValueError):
        store.set_autonomy("INVALID")


def test_apply_command_act_and_reject_and_drop(sqlite_engine):
    store.set_engine(sqlite_engine)
    t0 = datetime(2026, 11, 7, 2, 0, tzinfo=UTC)

    # 1. Same-state command (IDLE -> IDLE) is dropped silently
    assert store.apply_command(Command("L00001-1", TargetState.IDLE, "keep"), "ACT", ts=t0) is None

    # 2. ACT command (IDLE -> DEEP_SLEEP on DC_50) applies and updates cp_state
    res = store.apply_command(Command("L00001-1", TargetState.DEEP_SLEEP, "quiet"), "ACT", ts=t0)
    assert res is not None
    assert res["status"] == "applied"
    assert res["target_state"] == "DEEP_SLEEP"
    assert store.get_cp_states()["L00001-1"]["state"] == "DEEP_SLEEP"

    # 3. AC_22 DEEP_SLEEP command is coerced to STANDBY
    res_ac = store.apply_command(
        Command("L00001-2", TargetState.DEEP_SLEEP, "ac sleep"), "ACT", ts=t0
    )
    assert res_ac is not None
    assert res_ac["status"] == "applied"
    assert res_ac["target_state"] == "STANDBY"
    assert store.get_cp_states()["L00001-2"]["state"] == "STANDBY"

    # 4. Wake command on STANDBY CP enters WAKING; subsequent sleep command while WAKING is dropped
    res_wake = store.apply_command(Command("L00001-2", TargetState.IDLE, "wake"), "ACT", ts=t0)
    assert res_wake is not None and res_wake["status"] == "applied"
    assert store.get_cp_states()["L00001-2"]["state"] == "WAKING"
    assert (
        store.apply_command(Command("L00001-2", TargetState.STANDBY, "sleep"), "ACT", ts=t0) is None
    )

    # 5. CHARGING CP rejects commands with status='rejected', reason='busy'
    store.record_state_event("L00002-1", "IDLE", "CHARGING", "arrival", ts=t0)
    rej = store.apply_command(Command("L00002-1", TargetState.STANDBY, "try sleep"), "ACT", ts=t0)
    assert rej is not None
    assert rej["status"] == "rejected"
    assert rej["reason"] == "busy"
    assert store.get_cp_states()["L00002-1"]["state"] == "CHARGING"


def test_recommend_lifecycle_approve_reject_and_expire(sqlite_engine):
    store.set_engine(sqlite_engine)
    store.set_autonomy("RECOMMEND")
    t0 = datetime(2026, 11, 7, 3, 0, tzinfo=UTC)

    # Create a pending recommendation
    rec1 = store.apply_command(Command("L00001-1", TargetState.STANDBY, "p30=0.01"), ts=t0)
    assert rec1 is not None
    assert rec1["status"] == "pending"
    assert rec1["expires_at"] == (t0 + timedelta(minutes=15)).isoformat()
    # cp_state remains IDLE while recommendation is pending
    assert store.get_cp_states()["L00001-1"]["state"] == "IDLE"

    # A new command for the same (policy, cp_id) expires the previous pending one
    rec2 = store.apply_command(Command("L00001-1", TargetState.DEEP_SLEEP, "p30=0.005"), ts=t0)
    assert rec2 is not None and rec2["id"] != rec1["id"]
    pending = store.list_recommendations(status="pending")
    assert [r["id"] for r in pending] == [rec2["id"]]

    # Approve rec2 -> status becomes 'approved' (applied at start of next slot per Decision 0025)
    decided = store.decide_recommendation(
        rec2["id"], approve=True, who="vlad", ts=t0 + timedelta(minutes=2)
    )
    assert decided["status"] == "approved"

    # Deciding again raises ValueError (409 Conflict)
    with pytest.raises(ValueError):
        store.decide_recommendation(rec2["id"], approve=False, ts=t0 + timedelta(minutes=3))

    # Unknown id raises KeyError (404 Not Found)
    with pytest.raises(KeyError):
        store.decide_recommendation(99999, approve=True)

    # Expiry check: deciding after expires_at marks as expired and raises ValueError (409)
    rec3 = store.apply_command(Command("L00001-2", TargetState.STANDBY, "sleep ac"), ts=t0)
    assert rec3 is not None
    with pytest.raises(ValueError, match="expired"):
        store.decide_recommendation(rec3["id"], approve=True, ts=t0 + timedelta(minutes=20))
