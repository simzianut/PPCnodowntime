import time as _time
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

from sim.config import TZ, PowerProfile, PowerProfiles, SimConfig
from sim.data import SimData
from sim.engine import Engine, compatible
from sim.types import (
    ACTIONS,
    DEEP_SLEEP,
    IDLE,
    STANDBY,
    WAKING,
    Autonomy,
    Command,
    PowerClass,
    SimState,
    State,
    TargetState,
)

DAY = date(2024, 3, 12)
SLOT = 300
CFG = SimConfig(
    slot_s=SLOT, lambda_grid=[0.1], default_lambda=0.1, naive_quiet_hours_per_day=3,
    min_awake_per_location=1, recommendation_ttl_slots=3, eval_days="all",
)  # fmt: skip


def profiles(extra_wh: float = 0.0) -> PowerProfiles:
    dc = PowerProfile(idle_w=150, standby_w=30, deep_sleep_w=5, standby_wake_s=10,
                      deep_sleep_wake_s=180)  # fmt: skip
    ac = PowerProfile(idle_w=10, standby_w=3, deep_sleep_w=None, standby_wake_s=10,
                      deep_sleep_wake_s=None)  # fmt: skip
    by = {PowerClass.AC_22: ac, PowerClass.DC_50: dc, PowerClass.DC_HPC: dc}
    return PowerProfiles(by, extra_wh)


def at(hh: int, mm: int = 0, ss: int = 0, day: date = DAY) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(day, time(hh, mm, ss)), tz=TZ).tz_convert("UTC")


def make_data(cps, sessions=(), signals=(), context=()) -> SimData:
    """cps: (cp_id, power_class, connectors); sessions: (cp_id, arrival, departure)."""
    cp_rows = [
        {
            "cp_id": c,
            "location_id": c.rsplit("-", 1)[0],
            "power_class": pc,
            "max_kw": 22.0 if pc == "AC_22" else 50.0,
            "connectors": list(conns),
            "current_type": "AC" if pc == "AC_22" else "DC",
            "county_code": "B",
            "context_type": "urban",
        }  # fmt: skip
        for c, pc, conns in cps
    ]
    cps_df = pd.DataFrame(cp_rows)
    locs = pd.DataFrame({"location_id": sorted(set(cps_df["location_id"]))})
    utc = "datetime64[ns, UTC]"
    sess = pd.DataFrame(
        {
            "session_id": [f"S{i:05d}" for i in range(len(sessions))],
            "cp_id": [s[0] for s in sessions],
            "location_id": [s[0].rsplit("-", 1)[0] for s in sessions],
            "arrival_ts": pd.Series([s[1] for s in sessions], dtype=utc),
            "departure_ts": pd.Series([s[2] for s in sessions], dtype=utc),
            "energy_kwh": 10.0,
            "donor_charger_id": "x",
            "split": "test",
        }
    )
    sig = pd.DataFrame(
        {
            "ts": pd.Series([s[0] for s in signals], dtype=utc),
            "location_id": pd.Series([s[1] for s in signals], dtype=object),
            "signal": pd.Series([s[2] for s in signals], dtype=object),
            "value": pd.Series([1.0] * len(signals), dtype=float),
            "eta_ts": pd.Series([s[3] for s in signals], dtype=utc),
            "session_id": pd.Series([None] * len(signals), dtype=object),
        }
    )
    ctx = pd.DataFrame(
        {
            "ts_hour": pd.Series([c[0] for c in context], dtype=utc),
            "county_code": pd.Series([c[1] for c in context], dtype=object),
            "temp_c": pd.Series([c[2] for c in context], dtype=float),
            "precip_mm": 0.0,
            "wind_kmh": 5.0,
            "is_holiday": False,
        }
    )
    return SimData(locations=locs, cps=cps_df, sessions=sess, signals=sig, context=ctx)


def slot_of(state: SimState) -> int:
    midnight = datetime.combine(DAY, time(0), tzinfo=state.t.tzinfo)
    return int((state.t - midnight).total_seconds() // SLOT)


class Scripted:
    """Policy that returns `script[slot]` as [(cp_id, target)]; `every` repeats a fixed list."""

    def __init__(self, name: str, script=None, every=None) -> None:
        self.name = name
        self.script = script or {}
        self.every = every
        self.seen: list[SimState] = []

    def decide(self, state: SimState) -> list[Command]:
        self.seen.append(state)
        cmds = self.every if self.every is not None else self.script.get(slot_of(state), [])
        return [Command(c, TargetState(t), f"test {t}") for c, t in cmds]


class AlwaysOnP:
    name = "always_on"

    def decide(self, state):
        return []


def engine(data, *policies, **kw) -> Engine:
    kw.setdefault("sim_cfg", CFG)
    kw.setdefault("profiles", profiles())
    return Engine(data, DAY, list(policies), **kw)


def run_all(e: Engine) -> list:
    out = []
    while not e.done:
        out.append(e.step())
    return out


DC1 = [("L00001-1", "DC_50", ["CCS2"])]


# ---- acceptance: always_on, deep-sleep wait -------------------------------------------------


def test_always_on_saves_nothing_and_never_waits():
    data = synth(n_loc=20, seed=3)
    e = engine(data, AlwaysOnP())
    k = e.run()["always_on"]
    assert k["saved_kwh"] == 0 and k["saved_pct"] == 0
    assert k["wait_min_total"] == 0 and k["waited_sessions"] == 0 and k["wait_min_p95"] == 0
    assert k["sessions"] > 0 and k["sleep_share"] == 0


def test_hidden_always_on_is_not_reported():
    e = engine(make_data(DC1), Scripted("rl_ppo"))
    assert e.policy_names == ["rl_ppo"]
    r = e.step()
    assert list(r.kpis) == ["rl_ppo"] and list(r.events) == ["rl_ppo"]
    assert e.run()["rl_ppo"]["saved_kwh"] == 0


def test_deep_sleep_arrival_waits_exact_wake_time_and_energy():
    data = make_data(DC1, [("L00001-1", at(1), at(2))])
    pol = Scripted("rl_ppo", {0: [("L00001-1", "DEEP_SLEEP")]})
    e = engine(data, pol, profiles=profiles(extra_wh=2.0))
    steps = run_all(e)
    k = e.kpis()["rl_ppo"]
    assert k["wait_min_total"] == 180 / 60 == k["wait_min_mean"]
    assert k["sessions"] == 1 and k["waited_sessions"] == 1

    s12 = steps[12]
    assert s12.events["rl_ppo"] == [
        {"type": "arrival", "cp_id": "L00001-1", "ts": "2024-03-12T01:00:00+02:00",
         "wait_min": 3.0, "moved_from": None, "queued": False},
    ]  # fmt: skip
    assert s12.wait_min["rl_ppo"][0] == 3.0
    assert s12.overhead_kwh["rl_ppo"][0] == pytest.approx((150 * 180 / 3600 + 2.0) / 1000)
    deps = [ev for s in steps for ev in s.events["rl_ppo"] if ev["type"] == "departure"]
    assert deps == [{"type": "departure", "cp_id": "L00001-1", "ts": "2024-03-12T02:03:00+02:00"}]

    # DEEP_SLEEP 1 h at 5 W + WAKING 180 s at 150 W + 2 Wh wake + IDLE from 02:03 to 24:00
    want_wh = 5 * 1 + 150 * 180 / 3600 + 2.0 + 150 * (86400 - 7380) / 3600
    assert e.metrics["rl_ppo"].overhead_wh == pytest.approx(want_wh)
    ao_wh = 150 * (86400 - 3600) / 3600
    assert e.metrics["always_on"].overhead_wh == pytest.approx(ao_wh)
    assert k["saved_kwh"] == round((ao_wh - want_wh) / 1000, 4)
    assert k["sleep_share"] == round(3600 / 86400, 4)

    tl = e.timeline("rl_ppo", "L00001-1")
    assert [(s["from"][11:19], s["to"][11:19], s["state"]) for s in tl["states"]] == [
        ("00:00:00", "01:00:00", "DEEP_SLEEP"),
        ("01:00:00", "01:03:00", "WAKING"),
        ("01:03:00", "02:03:00", "CHARGING"),
        ("02:03:00", "00:00:00", "IDLE"),
    ]
    assert tl["states"][-1]["to"] == "2024-03-13T00:00:00+02:00"
    assert tl["arrivals"] == [{"ts": "2024-03-12T01:00:00+02:00", "wait_min": 3.0}]
    assert tl["commands"] == [
        {"ts": "2024-03-12T00:00:00+02:00", "target_state": "DEEP_SLEEP",
         "reason": "test DEEP_SLEEP", "status": "applied"},
    ]  # fmt: skip
    assert tl["forecast"] == [] and tl["intents"] == []


def test_minute_ticks_decide_per_slot_and_match_slot_steps():
    """Decision 0091: one tick per minute, decisions at slot starts, same totals as step()."""
    data = make_data(DC1, [("L00001-1", at(1), at(2))])
    pol = Scripted("rl_ppo", {0: [("L00001-1", "DEEP_SLEEP")]})
    e = engine(data, pol)
    ticks = []
    while not e.done:
        ticks.append(e.step_minute())
    assert len(ticks) == e.n_minutes == 1440 and len(pol.seen) == 288
    assert all(r.slot == r.minute // 5 and r.decided == (r.minute % 5 == 0) for r in ticks)
    arr = [r.minute for r in ticks if r.events["rl_ppo"]]
    assert arr == [60, 123]  # arrival 01:00, departure 02:03, each on its own minute
    assert ticks[61].changes["rl_ppo"] == [] and ticks[62].changes["rl_ppo"] == []
    assert ticks[63].changes["rl_ppo"] == [("L00001-1", State.CHARGING)]  # woke at 01:03
    kwh = [r.kpis["rl_ppo"]["overhead_kwh"] for r in ticks[:5]]
    assert kwh == sorted(kwh) and kwh[0] < kwh[4]  # KPIs move inside a slot
    ref = engine(data, Scripted("rl_ppo", {0: [("L00001-1", "DEEP_SLEEP")]}))
    assert e.kpis() == ref.run()
    e2 = engine(data, Scripted("rl_ppo", {0: [("L00001-1", "DEEP_SLEEP")]}))
    e2.run_to_minute(62)
    assert (e2.slot, e2.minute) == (12, 62) and e2.step_minute().minute == 62


def test_standby_wake_wait_and_end_of_day_cut():
    data = make_data(DC1, [("L00001-1", at(1), at(2)), ("L00001-1", at(23, 59), at(23, 59, 30))])
    pol = Scripted("rl_ppo", {0: [("L00001-1", "STANDBY")], 200: [("L00001-1", "DEEP_SLEEP")]})
    e = engine(data, pol)
    run_all(e)
    arr = e.timeline("rl_ppo", "L00001-1")["arrivals"]
    assert [a["wait_min"] for a in arr] == [round(10 / 60, 3), 1.0]
    assert e.metrics["rl_ppo"].wait_min_total == pytest.approx(10 / 60 + 1.0)
    assert e.states("rl_ppo")[0] == WAKING  # the wake runs past midnight and is cut


def test_arrival_partway_through_waking_waits_remaining_time():
    data = make_data(DC1, [("L00001-1", at(1, 1), at(2))])
    pol = Scripted("rl_ppo", {0: [("L00001-1", "DEEP_SLEEP")], 12: [("L00001-1", "IDLE")]})
    e = engine(data, pol, profiles=profiles(extra_wh=2.0))
    steps = run_all(e)
    assert e.kpis()["rl_ppo"]["wait_min_total"] == 2.0
    assert steps[12].overhead_kwh["rl_ppo"][0] == pytest.approx((150 * 180 / 3600 + 2.0) / 1000)
    tl = e.timeline("rl_ppo", "L00001-1")["states"]
    assert [(s["from"][11:19], s["state"]) for s in tl[:3]] == [
        ("00:00:00", "DEEP_SLEEP"), ("01:00:00", "WAKING"), ("01:03:00", "CHARGING"),
    ]  # fmt: skip


def test_deep_sleep_to_standby_goes_through_waking():
    pol = Scripted("rl_ppo", {0: [("L00001-1", "DEEP_SLEEP")], 1: [("L00001-1", "STANDBY")]})
    e = engine(make_data(DC1), pol, profiles=profiles(extra_wh=2.0))
    e.step()
    r = e.step()
    assert r.changes["rl_ppo"] == [("L00001-1", State.STANDBY)]
    assert r.overhead_kwh["rl_ppo"][0] == pytest.approx((150 * 180 + 30 * 120) / 3.6e6 + 0.002)
    tl = e.timeline("rl_ppo", "L00001-1")["states"]
    assert [(s["from"][11:19], s["state"]) for s in tl] == [
        ("00:00:00", "DEEP_SLEEP"), ("00:05:00", "WAKING"), ("00:08:00", "STANDBY"),
    ]  # fmt: skip


# ---- siblings and queueing -------------------------------------------------------------------


def test_moves_to_idle_compatible_sibling():
    cps = [("L00001-1", "DC_50", ["CCS2"]), ("L00001-2", "DC_50", ["CCS2", "CHAdeMO"])]
    data = make_data(cps, [("L00001-1", at(1), at(2))])
    e = engine(data, Scripted("rl_ppo", {0: [("L00001-1", "DEEP_SLEEP")]}))
    steps = run_all(e)
    ev = steps[12].events["rl_ppo"][0]
    assert (ev["cp_id"], ev["moved_from"], ev["wait_min"]) == ("L00001-2", "L00001-1", 0.0)
    assert e.kpis()["rl_ppo"]["wait_min_total"] == 0
    assert e.states("rl_ppo").tolist() == [DEEP_SLEEP, IDLE]


def test_incompatible_sibling_is_not_used():
    cps = [("L00001-1", "DC_50", ["CCS2"]), ("L00001-2", "AC_22", ["Type2"])]
    data = make_data(cps, [("L00001-1", at(1), at(2))])
    e = engine(data, Scripted("rl_ppo", {0: [("L00001-1", "DEEP_SLEEP")]}))
    steps = run_all(e)
    ev = steps[12].events["rl_ppo"][0]
    assert (ev["cp_id"], ev["moved_from"], ev["wait_min"]) == ("L00001-1", None, 3.0)


def test_compatibility_rules():
    assert compatible("DC", ["CCS2"], "DC_50", "DC", ["CCS2", "CHAdeMO"], "DC_HPC")
    assert not compatible("DC", ["CCS2"], "DC_50", "DC", ["CHAdeMO"], "DC_50")
    assert not compatible("DC", ["CCS2"], "DC_50", "AC", ["CCS2"], "DC_50")
    assert compatible("DC", [], "DC_50", "DC", ["CHAdeMO"], "DC_50")
    assert not compatible("DC", [], "DC_50", "DC", [], "DC_HPC")


def test_busy_own_cp_wakes_sleeping_sibling_and_counts_wait():
    cps = [("L00001-1", "DC_50", ["CCS2"]), ("L00001-2", "DC_50", ["CCS2"])]
    sessions = [("L00001-2", at(1), at(2)), ("L00001-1", at(1, 30), at(2))]
    e = engine(make_data(cps, sessions), Scripted("rl_ppo", {0: [("L00001-2", "DEEP_SLEEP")]}))
    steps = run_all(e)
    y = steps[12].events["rl_ppo"][0]
    assert (y["cp_id"], y["moved_from"], y["wait_min"]) == ("L00001-1", "L00001-2", 0.0)
    x = steps[18].events["rl_ppo"][0]
    assert (x["cp_id"], x["moved_from"], x["wait_min"]) == ("L00001-2", "L00001-1", 3.0)
    assert steps[18].wait_min["rl_ppo"].tolist() == [0.0, 3.0]
    deps = {
        ev["ts"][11:19] for s in steps for ev in s.events["rl_ppo"] if ev["type"] == "departure"
    }
    assert deps == {"02:00:00", "02:03:00"}
    k = e.kpis()["rl_ppo"]
    assert (k["sessions"], k["waited_sessions"], k["wait_min_total"]) == (2, 1, 3.0)


def test_queue_when_all_compatible_busy_is_not_counted_as_wait():
    cps = [("L00001-1", "DC_50", ["CCS2"]), ("L00001-2", "DC_50", ["CCS2"])]
    sessions = [
        ("L00001-1", at(1), at(3)),
        ("L00001-2", at(1), at(2, 30)),
        ("L00001-1", at(2), at(2, 30)),  # both busy: queues for -2, which frees first
    ]
    e = engine(make_data(cps, sessions), AlwaysOnP(), Scripted("rl_ppo"))
    steps = run_all(e)
    q = steps[24].events["rl_ppo"][0]
    assert (q["cp_id"], q["moved_from"], q["queued"], q["wait_min"]) == (
        "L00001-2", "L00001-1", True, 0.0,
    )  # fmt: skip
    deps = sorted(
        (ev["ts"][11:19], ev["cp_id"])
        for s in steps
        for ev in s.events["rl_ppo"]
        if ev["type"] == "departure"
    )
    assert deps == [("02:30:00", "L00001-2"), ("03:00:00", "L00001-1"), ("03:00:00", "L00001-2")]
    for name in ("rl_ppo", "always_on"):
        assert e.kpis()[name]["wait_min_total"] == 0
    tl = e.timeline("rl_ppo", "L00001-2")["states"]
    assert [(s["from"][11:19], s["to"][11:19], s["state"]) for s in tl] == [
        ("00:00:00", "01:00:00", "IDLE"),
        ("01:00:00", "03:00:00", "CHARGING"),
        ("03:00:00", "00:00:00", "IDLE"),
    ]


def test_queue_ties_go_to_own_cp():
    cps = [("L00001-1", "DC_50", []), ("L00001-2", "DC_50", [])]
    sessions = [
        ("L00001-1", at(1), at(3)),
        ("L00001-2", at(1), at(3)),
        ("L00001-2", at(2), at(2, 30)),
    ]
    steps = run_all(engine(make_data(cps, sessions), Scripted("rl_ppo")))
    q = steps[24].events["rl_ppo"][0]
    assert (q["cp_id"], q["moved_from"], q["queued"]) == ("L00001-2", None, True)


# ---- day boundaries ---------------------------------------------------------------------------


def test_session_spanning_midnight_starts_charging():
    prev = date(2024, 3, 11)
    data = make_data(DC1, [("L00001-1", at(23, day=prev), at(1))])
    pol = Scripted("rl_ppo", every=[("L00001-1", "STANDBY")])
    e = engine(data, pol)
    assert e.sim_state("rl_ppo").cps[0].state == State.CHARGING
    steps = run_all(e)
    assert pol.seen[0].cps[0].state == State.CHARGING
    assert steps[12].events["rl_ppo"] == [
        {"type": "departure", "cp_id": "L00001-1", "ts": "2024-03-12T01:00:00+02:00"}
    ]
    assert e.kpis()["rl_ppo"]["sessions"] == 0
    tl = e.timeline("rl_ppo", "L00001-1")
    assert [(s["from"][11:19], s["state"]) for s in tl["states"]] == [
        ("00:00:00", "CHARGING"), ("01:00:00", "STANDBY"),
    ]  # fmt: skip


@pytest.mark.parametrize("day,n_slots", [(date(2024, 3, 31), 276), (date(2024, 10, 27), 300)])
def test_dst_days_cover_the_local_day(day, n_slots):
    from sim.synthetic import slot_starts

    nxt = day + timedelta(days=1)
    sessions = [
        ("L00001-1", at(23, 30, day=day), at(23, 40, day=day)),
        ("L00001-1", at(0, 30, day=nxt), at(0, 40, day=nxt)),
    ]
    data = make_data(DC1, sessions)
    e = Engine(data, day, [AlwaysOnP()], sim_cfg=CFG, profiles=profiles())
    assert e.n_slots == n_slots
    starts = [pd.Timestamp(e._slot_dt[k]) for k in range(e.n_slots)]
    assert starts == list(slot_starts([day]))
    assert e.run()["always_on"]["sessions"] == 1
    assert e.t == datetime.combine(nxt, time(0), tzinfo=e.t.tzinfo)
    assert e.metrics["always_on"].overhead_wh == pytest.approx(150 * (n_slots * SLOT - 600) / 3600)
    e2 = Engine(data, nxt, [AlwaysOnP()], sim_cfg=CFG, profiles=profiles())
    assert e2.n_slots == 288 and e2.run()["always_on"]["sessions"] == 1


def test_dst_wait_cut_uses_local_day_end():
    day = date(2024, 10, 27)
    data = make_data(DC1, [("L00001-1", at(23, 59, day=day), at(23, 59, 30, day=day))])
    pol = Scripted("rl_ppo", every=[("L00001-1", "DEEP_SLEEP")])
    e = Engine(data, day, [pol], sim_cfg=CFG, profiles=profiles())
    assert e.run()["rl_ppo"]["wait_min_total"] == 1.0


def test_departure_at_slot_start_is_seen_by_policy():
    data = make_data(DC1, [("L00001-1", at(0, 7), at(0, 17))])  # waits 3 min, frees at 00:20
    pol = Scripted("rl_ppo", every=[("L00001-1", "DEEP_SLEEP")])
    e = engine(data, AlwaysOnP(), pol, profiles=profiles(extra_wh=1.0))
    steps = run_all(e)
    assert pol.seen[4].cps[0].state == State.IDLE
    assert steps[4].events["rl_ppo"] == [
        {"type": "departure", "cp_id": "L00001-1", "ts": "2024-03-12T00:20:00+02:00"}
    ]
    want = (5 * 420 + 150 * 180 + 5 * (86400 - 1200)) / 3600 + 1.0
    assert e.metrics["rl_ppo"].overhead_wh == pytest.approx(want)
    assert e.kpis()["rl_ppo"]["sleep_share"] == round((86400 - 780) / 86400, 4)
    tl = e.timeline("rl_ppo", "L00001-1")["states"]
    assert [(s["from"][11:19], s["state"]) for s in tl] == [
        ("00:00:00", "DEEP_SLEEP"), ("00:07:00", "WAKING"), ("00:10:00", "CHARGING"),
        ("00:20:00", "DEEP_SLEEP"),
    ]  # fmt: skip


def test_wake_end_at_slot_start_is_seen_by_policy():
    data = make_data(DC1, [("L00001-1", at(0, 2), at(0, 32))])  # wake ends at 00:05
    pol = Scripted("rl_ppo", {0: [("L00001-1", "DEEP_SLEEP")]})
    e = engine(data, pol)
    e.run_to(2)
    cp = pol.seen[1].cps[0]
    assert (cp.state, cp.seconds_in_state) == (State.CHARGING, 0)


def test_policies_cannot_change_shared_inputs():
    signals = [(at(0), "L00001", "maps_ev_count", pd.NaT)]
    data = make_data(DC1, signals=signals, context=[(at(0), "B", 10.0)])

    class Mutator:
        name = "cost_rule"

        def decide(self, s):
            s.context["B"]["temp_c"] = -99.0
            s.context["CJ"] = {}
            s.signals.loc[:, "value"] = 42.0
            s.cps[0].connectors.append("CHAdeMO")
            return []

    peek = Scripted("rl_ppo")
    e = engine(data, Mutator(), peek)
    e.run_to(2)
    for s in peek.seen:
        assert s.context == {
            "B": {"temp_c": 10.0, "precip_mm": 0.0, "wind_kmh": 5.0, "is_holiday": False}
        }
        assert s.signals["value"].tolist() == [1.0] and s.cps[0].connectors == ["CCS2"]
    assert e.sim_state("cost_rule").signals["value"].tolist() == [1.0]


# ---- recommendations --------------------------------------------------------------------------


def rec_engine(script):
    cps = [("L00001-1", "DC_50", ["CCS2"]), ("L00002-1", "AC_22", ["Type2"])]
    return engine(
        make_data(cps),
        Scripted("rl_ppo", script),
        Scripted("cost_rule", script),
        autonomy=Autonomy.RECOMMEND,
        focus_policy="rl_ppo",
    )


def test_recommend_focus_pending_others_act_and_approve_applies_next_slot():
    e = rec_engine({0: [("L00001-1", "STANDBY")]})
    r0 = e.step()
    assert r0.new_recommendations == [
        {"id": 1, "ts": "2024-03-12T00:00:00+02:00", "cp_id": "L00001-1", "policy": "rl_ppo",
         "from_state": "IDLE", "target_state": "STANDBY", "reason": "test STANDBY",
         "status": "pending", "expires_at": "2024-03-12T00:15:00+02:00"},
    ]  # fmt: skip
    assert e.states("rl_ppo")[0] == IDLE and e.states("cost_rule")[0] == STANDBY
    assert r0.changes == {"rl_ppo": [], "cost_rule": [("L00001-1", State.STANDBY)]}
    assert e.decide_recommendation(1, approve=True)["status"] == "approved"
    assert e.states("rl_ppo")[0] == IDLE
    r1 = e.step()
    assert e.states("rl_ppo")[0] == STANDBY
    assert r1.changes["rl_ppo"] == [("L00001-1", State.STANDBY)]
    assert [(x["id"], x["status"]) for x in r1.new_recommendations] == [(1, "approved")]
    assert e.step().new_recommendations == []
    tl = e.timeline("rl_ppo", "L00001-1")
    assert tl["commands"] == [
        {"ts": "2024-03-12T00:00:00+02:00", "target_state": "STANDBY",
         "reason": "test STANDBY", "status": "approved"},
    ]  # fmt: skip
    assert [(s["from"][11:19], s["state"]) for s in tl["states"]] == [
        ("00:00:00", "IDLE"), ("00:05:00", "STANDBY"),
    ]  # fmt: skip
    with pytest.raises(ValueError):
        e.decide_recommendation(1, approve=False)
    with pytest.raises(KeyError):
        e.decide_recommendation(99, approve=True)


def test_reject_is_reported_in_next_step():
    e = rec_engine({0: [("L00001-1", "STANDBY")]})
    e.step()
    assert e.decide_recommendation(1, approve=False)["status"] == "rejected"
    r = e.step()
    assert [(x["id"], x["status"]) for x in r.new_recommendations] == [(1, "rejected")]
    assert e.states("rl_ppo")[0] == IDLE


def test_recommendation_expires_after_ttl():
    e = rec_engine({0: [("L00001-1", "STANDBY")]})
    e.run_to(2)
    assert [r["status"] for r in e.recommendations()] == ["pending"]
    e.decide_recommendation(1, approve=False)
    assert e.recommendations("rejected")[0]["id"] == 1

    e = rec_engine({0: [("L00001-1", "STANDBY")]})
    e.run_to(2)
    r = e.step()  # ends at 00:15 == expires_at: reported by this step, not the next (T52)
    assert [(x["id"], x["status"]) for x in r.new_recommendations] == [(1, "expired")]
    assert e.states("rl_ppo")[0] == IDLE

    e = rec_engine({0: [("L00001-1", "STANDBY")]})
    e.run_to(3)  # self.t == expires_at: too late to approve
    with pytest.raises(ValueError):
        e.decide_recommendation(1, approve=True)
    assert e.step().new_recommendations == []  # already reported


def test_listing_never_offers_an_expired_recommendation():
    e = rec_engine({0: [("L00001-1", "STANDBY")]})
    e.run_to(3)  # self.t == expires_at
    assert e.recommendations("pending") == []
    assert [(x["id"], x["status"]) for x in e.recommendations()] == [(1, "expired")]
    with pytest.raises(ValueError):
        e.decide_recommendation(1, approve=True)
    assert e.step().new_recommendations == []  # the step that reached 00:15 reported it


def test_step_reports_expiry_at_its_end_so_callers_never_see_a_stale_pending():
    e = rec_engine({0: [("L00001-1", "STANDBY")]})
    seen: dict[int, str] = {}
    for _ in range(4):
        for x in e.step().new_recommendations:
            seen[x["id"]] = x["status"]
        # what a caller tracking StepResults believes is pending must still be decidable
        live = {x["id"] for x in e.recommendations("pending")}
        assert {i for i, s in seen.items() if s == "pending"} == live
    assert seen == {1: "expired"}


def test_one_pending_per_cp():
    e = rec_engine({k: [("L00001-1", "STANDBY")] for k in range(2)} | {
        2: [("L00001-1", "DEEP_SLEEP"), ("L00002-1", "DEEP_SLEEP")]})  # fmt: skip
    e.run_to(2)
    assert len(e.recommendations()) == 1
    r = e.step()
    got = [(x["id"], x["cp_id"], x["target_state"], x["status"]) for x in r.new_recommendations]
    assert got == [
        (1, "L00001-1", "STANDBY", "expired"),
        (2, "L00001-1", "DEEP_SLEEP", "pending"),
        (3, "L00002-1", "STANDBY", "pending"),  # AC: DEEP_SLEEP becomes STANDBY
    ]
    assert [x["id"] for x in e.recommendations("pending")] == [2, 3]


def test_switching_to_act_keeps_pending_recommendations():
    e = rec_engine({k: [("L00001-1", "STANDBY")] for k in range(5)})
    e.step()
    e.autonomy = Autonomy.ACT
    e.step()
    assert e.states("rl_ppo")[0] == STANDBY
    assert e.recommendations("pending")[0]["id"] == 1
    statuses = [c["status"] for c in e.timeline("rl_ppo", "L00001-1")["commands"]]
    assert statuses == ["pending", "applied"]


def test_no_recommendation_for_command_that_would_be_dropped():
    data = make_data(DC1, [("L00001-1", at(23, day=date(2024, 3, 11)), at(2))])
    e = engine(
        data, Scripted("rl_ppo", every=[("L00001-1", "STANDBY")]), autonomy=Autonomy.RECOMMEND
    )
    e.run_to(10)
    assert e.recommendations() == []


def test_pending_recommendations_expire_at_end_of_day():
    e = rec_engine({287: [("L00001-1", "STANDBY")]})
    steps = run_all(e)
    assert [x["status"] for x in steps[-1].new_recommendations] == ["expired"]
    with pytest.raises(ValueError):
        e.decide_recommendation(1, approve=True)


# ---- commands, observation, misc ---------------------------------------------------------------


def test_repeated_command_is_recorded_once():
    e = engine(make_data(DC1), Scripted("rl_ppo", every=[("L00001-1", "STANDBY")]))
    e.run()
    cmds = e.timeline("rl_ppo", "L00001-1")["commands"]
    assert len(cmds) == 1 and cmds[0]["status"] == "applied"
    assert e.kpis()["rl_ppo"]["sleep_share"] == 1.0


def test_signals_window_context_and_intents():
    intent = "ppcblue_route_intent"
    signals = [
        (at(23, 30, day=date(2024, 3, 11)), "L00001", intent, at(0, 10)),
        (at(0), "L00001", intent, at(0, 20)),
        (at(0, 30), "L00001", "maps_ev_count", pd.NaT),
        (at(1), "L00001", intent, at(1, 20)),
        (at(1, 0, 1), "L00001", intent, at(1, 30)),
        (at(0, 40), "L00099", intent, at(1)),
    ]
    context = [(at(0), "B", 1.0), (at(1), "B", 2.0), (at(1), "CJ", 9.0)]
    data = make_data(DC1, signals=signals, context=context)
    e = engine(data, Scripted("rl_ppo"))
    s0 = e.sim_state("rl_ppo")
    assert [t.strftime("%H:%M:%S") for t in s0.signals["ts"].dt.tz_convert(TZ)] == [
        "23:30:00", "00:00:00",
    ]  # fmt: skip
    assert s0.context == {"B": {"temp_c": 1.0, "precip_mm": 0.0, "wind_kmh": 5.0,
                                "is_holiday": False}}  # fmt: skip
    e.run_to(11)
    assert e.sim_state("rl_ppo").context["B"]["temp_c"] == 1.0
    e.run_to(12)
    s12 = e.sim_state("rl_ppo")
    assert [t.strftime("%H:%M:%S") for t in s12.signals["ts"].dt.tz_convert(TZ)] == [
        "00:30:00", "01:00:00",
    ]  # fmt: skip
    assert s12.context["B"]["temp_c"] == 2.0
    assert s12.t == datetime.combine(DAY, time(1), tzinfo=s12.t.tzinfo)
    assert e.t == s12.t and e.slot == 12
    intents = e.timeline("rl_ppo", "L00001-1")["intents"]
    assert intents == [
        {"ts": "2024-03-11T23:30:00+02:00", "eta_ts": "2024-03-12T00:10:00+02:00"},
        {"ts": "2024-03-12T00:00:00+02:00", "eta_ts": "2024-03-12T00:20:00+02:00"},
        {"ts": "2024-03-12T01:00:00+02:00", "eta_ts": "2024-03-12T01:20:00+02:00"},
    ]


class ConstForecaster:
    def __init__(self, p30: float) -> None:
        self.p30 = p30

    def predict(self, state: SimState) -> pd.DataFrame:
        ids = [c.cp_id for c in state.cps]
        z = np.zeros(len(ids))
        return pd.DataFrame(
            {"cp_id": ids, "p_arrival_15": z, "p_arrival_30": z + self.p30,
             "p_arrival_60": z, "expected_arrivals_60": z}
        )  # fmt: skip


def test_forecast_is_passed_to_policy_and_recorded():
    pol = Scripted("rl_ppo")
    e = engine(make_data(DC1), pol, forecaster=ConstForecaster(0.25))
    e.run_to(3)
    assert pol.seen[0].forecast["p_arrival_30"].tolist() == [0.25]
    fc = e.timeline("rl_ppo", "L00001-1")["forecast"]
    assert fc == [
        {"ts": f"2024-03-12T00:{m:02d}:00+02:00", "p_arrival_30": 0.25} for m in (0, 5, 10)
    ]


def test_seconds_in_state_and_cp_view():
    pol = Scripted("rl_ppo", {0: [("L00001-1", "STANDBY")]})
    e = engine(make_data(DC1), pol)
    e.run_to(4)
    cp = e.sim_state("rl_ppo").cps[0]
    assert (cp.cp_id, cp.location_id, cp.power_class, cp.connectors) == (
        "L00001-1", "L00001", PowerClass.DC_50, ["CCS2"],
    )  # fmt: skip
    assert (cp.state, cp.seconds_in_state) == (State.STANDBY, 4 * SLOT)


def test_cp_subset_ignores_other_sessions_and_siblings():
    cps = [("L00001-1", "DC_50", ["CCS2"]), ("L00001-2", "DC_50", ["CCS2"])]
    sessions = [("L00001-1", at(1), at(2)), ("L00001-2", at(1), at(2))]
    pol = Scripted("rl_ppo", {0: [("L00001-1", "DEEP_SLEEP"), ("L00001-2", "DEEP_SLEEP")]})
    e = engine(make_data(cps, sessions), pol, cp_ids=["L00001-1"])
    assert e.cp_ids == ["L00001-1"]
    k = e.run()["rl_ppo"]
    assert (k["sessions"], k["wait_min_total"]) == (1, 3.0)


def test_api_errors():
    data = make_data(DC1)
    with pytest.raises(ValueError):
        engine(data, Scripted("rl_ppo"), Scripted("rl_ppo"))
    with pytest.raises(ValueError):
        engine(data, Scripted("rl_ppo"), focus_policy="cost_rule")
    with pytest.raises(KeyError):
        engine(data, Scripted("rl_ppo"), cp_ids=["nope"])
    e = engine(data, Scripted("rl_ppo"))
    e.run_to(5)
    with pytest.raises(ValueError):
        e.run_to(4)
    e.run_to(5)
    e.run()
    assert e.done and e.slot == e.n_slots == 288
    with pytest.raises(RuntimeError):
        e.step()


def test_unknown_target_raises():
    class Bad:
        name = "rl_ppo"

        def decide(self, state):
            return [Command("L00001-1", "CHARGING", "nope")]

    with pytest.raises(ValueError):
        engine(make_data(DC1), Bad()).step()


# ---- synthetic day: determinism and speed -------------------------------------------------------


def synth(n_loc: int = 150, per_loc: int = 2, per_cp: int = 5, seed: int = 0) -> SimData:
    rng = np.random.default_rng(seed)
    cps, sessions, signals = [], [], []
    t0 = at(0)
    for i in range(1, n_loc + 1):
        loc = f"L{i:05d}"
        for k in range(1, per_loc + 1):
            pc = rng.choice(["AC_22", "DC_50", "DC_HPC"])
            conns = [] if rng.random() < 0.4 else (["Type2"] if pc == "AC_22" else ["CCS2"])
            cp = f"{loc}-{k}"
            cps.append((cp, str(pc), conns))
            seg = 86400 / per_cp
            for s in range(per_cp):
                a = s * seg + rng.uniform(0, seg - 5400)
                d = a + rng.uniform(1200, 5400)
                sessions.append(
                    (cp, t0 + pd.Timedelta(seconds=int(a)), t0 + pd.Timedelta(seconds=int(d)))
                )
            if rng.random() < 0.1:
                sessions.append((cp, t0 - pd.Timedelta(hours=1), t0 + pd.Timedelta(minutes=20)))
        for _ in range(40):
            ts = t0 + pd.Timedelta(seconds=int(rng.uniform(-3600, 86400)))
            signals.append((ts, loc, "ppcblue_route_intent", ts + pd.Timedelta(minutes=20)))
    context = [(t0 + pd.Timedelta(hours=h), "B", 10.0 + h) for h in range(24)]
    return make_data(cps, sessions, signals, context)


class NightSleep:
    name = "naive_quiet_hours"

    def decide(self, state):
        target = "DEEP_SLEEP" if 1 <= state.t.hour < 5 else "IDLE"
        return [Command(c.cp_id, TargetState(target), "night") for c in state.cps]


class StandbyIdle:
    name = "cost_rule"

    def decide(self, state):
        wake = slot_of(state) % 4 == 0
        out = []
        for c in state.cps:
            if c.state == State.IDLE:
                out.append(Command(c.cp_id, TargetState.STANDBY, "idle"))
            elif wake and c.state == State.STANDBY:
                out.append(Command(c.cp_id, TargetState.IDLE, "wake"))
        return out


class Toggler:
    name = "rl_ppo"

    def decide(self, state):
        k = slot_of(state)
        return [Command(c.cp_id, ACTIONS[(k + i) % 3], "toggle") for i, c in enumerate(state.cps)]


def four_policies():
    return [AlwaysOnP(), NightSleep(), StandbyIdle(), Toggler()]


def test_determinism():
    data = synth(n_loc=30, seed=7)
    runs = []
    for _ in range(2):
        e = engine(data, *four_policies(), forecaster=ConstForecaster(0.1))
        steps = run_all(e)
        runs.append(
            (
                e.kpis(),
                [(s.changes, s.events, {k: v.tolist() for k, v in s.overhead_kwh.items()})
                 for s in steps],
                e.timeline("rl_ppo", e.cp_ids[3]),
            )
        )  # fmt: skip
    assert runs[0] == runs[1]
    k = runs[0][0]
    assert k["always_on"]["saved_kwh"] == 0 and k["always_on"]["wait_min_total"] == 0
    assert k["naive_quiet_hours"]["saved_kwh"] > 0
    assert all(v["sessions"] == k["always_on"]["sessions"] for v in k.values())


def test_overhead_sums_match_totals():
    data = synth(n_loc=10, seed=1)
    e = engine(data, *four_policies())
    total = {n: 0.0 for n in e.policy_names}
    waits = {n: 0.0 for n in e.policy_names}
    for s in run_all(e):
        for n in e.policy_names:
            total[n] += float(s.overhead_kwh[n].sum())
            waits[n] += float(s.wait_min[n].sum())
    for n in e.policy_names:
        assert total[n] * 1000 == pytest.approx(e.metrics[n].overhead_wh)
        assert waits[n] == pytest.approx(e.metrics[n].wait_min_total)


def test_benchmark_300_cps_4_policies():
    data = synth(n_loc=150, per_loc=2, per_cp=5, seed=0)
    start = _time.perf_counter()
    e = engine(data, *four_policies(), forecaster=ConstForecaster(0.1))
    k = e.run()
    elapsed = _time.perf_counter() - start
    print(f"\nengine benchmark: {len(e.cp_ids)} CPs, 4 policies, 288 slots: {elapsed:.2f} s")
    assert k["always_on"]["sessions"] == 300 * 5
    assert elapsed < 20


# ---- per-CP power (decision 0049) ----------------------------------------------------------


def _hpc_data(*kws: float | None) -> SimData:
    data = make_data([(f"L{i:05d}-1", "DC_HPC", ["CCS2"]) for i in range(len(kws))])
    data.cps["max_kw"] = pd.Series(kws, dtype="float64")
    return data


def test_default_profiles_use_per_cp_watts_from_max_kw():
    e = Engine(_hpc_data(300.0, 100.0, None), DAY, [AlwaysOnP()], sim_cfg=CFG)
    assert e.idle_w.tolist() == [230.0, 130.0, 165.0]
    k = e.run()["always_on"]
    assert k["overhead_kwh"] == pytest.approx((230 + 130 + 165) * 24 / 1000)
    assert k["overhead_ron"] == pytest.approx(k["overhead_kwh"] * 1.15, abs=1e-4)
