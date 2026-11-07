from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dataset import context, mapping, weather

MULT = {"urban": 1.0, "retail": 1.2, "highway": 0.6, "airport": 0.8, "other": 0.8}


def ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


def donors_df(rows):
    return pd.DataFrame(
        rows, columns=["donor_charger_id", "power_class", "sessions_per_day", "util_quantile"]
    )


def utc_series(values) -> pd.Series:
    return pd.Series(pd.to_datetime(values, utc=True)).astype("datetime64[ns, UTC]")


def sess(arrivals_utc, minutes=60, kwh=10.0):
    a = utc_series(arrivals_utc)
    return pd.DataFrame(
        {"arrival_ts": a, "departure_ts": a + pd.Timedelta(minutes=minutes), "energy_kwh": kwh}
    )


def ns_to_utc(v) -> list[pd.Timestamp]:
    return list(pd.to_datetime(v, unit="ns", utc=True))


def test_demand_formula():
    cps = pd.DataFrame(
        {
            "cp_id": ["L00001-1", "L00001-2", "L00002-1"],
            "location_id": ["L00001", "L00001", "L00002"],
            "power_class": ["AC_22", "AC_22", "DC_50"],
        }
    )
    locs = pd.DataFrame(
        {
            "location_id": ["L00001", "L00002"],
            "county_code": ["CJ", "VS"],
            "context_type": ["retail", "highway"],
        }
    )
    ev = pd.Series({"CJ": 5.0, "VS": 1.0})
    d = mapping.demand_index(cps, locs, ev, MULT).set_index("cp_id")["demand"]
    assert d["L00001-1"] == pytest.approx(5.0 * 1.2 / 2)
    assert d["L00002-1"] == pytest.approx(1.0 * 0.6 / 1)


def dem_frame(cp_ids, loc, pc, demands):
    return pd.DataFrame({"cp_id": cp_ids, "location_id": loc, "power_class": pc, "demand": demands})


def test_distinct_donors_at_location():
    dem = dem_frame(["L1-1", "L1-2", "L1-3"], "L1", "AC_22", [1.0, 1.0, 1.0])
    donors = donors_df(
        [("A#1", "AC_22", 1.0, 1.0), ("B#1", "AC_22", 0.9, 0.9), ("C#1", "AC_22", 0.1, 0.1)]
    )
    m = mapping.choose_donors(dem, donors, seed=1)
    assert m["donor_charger_id"].nunique() == 3
    assert not m["donor_fallback"].any()


def test_closest_quantile_and_relax_when_exhausted():
    dem = dem_frame(["L1-1", "L1-2", "L2-1"], ["L1", "L1", "L2"], "AC_22", [1.0, 1.0, 0.5])
    donors = donors_df([("A#1", "AC_22", 1.0, 1.0)])
    m = mapping.choose_donors(dem, donors, seed=1)
    assert set(m["donor_charger_id"]) == {"A#1"}  # only one donor: relaxed


def test_hpc_fallback_borrows_dc50():
    dem = dem_frame(["L1-1", "L1-2"], "L1", "DC_HPC", [2.0, 1.0])
    donors = donors_df(
        [("H#1", "DC_HPC", 9.0, 1.0), ("D#1", "DC_50", 8.0, 0.9), ("D#2", "DC_50", 0.2, 0.1)]
    )
    m = mapping.choose_donors(dem, donors, seed=3).set_index("cp_id")
    assert m.loc["L1-1", "donor_charger_id"] == "H#1"  # quantile 1.0, processed first
    assert m.loc["L1-2", "donor_charger_id"] == "D#2"  # quantile 0.5, closest DC_50 is 0.1
    assert bool(m.loc["L1-2", "donor_fallback"]) and not bool(m.loc["L1-1", "donor_fallback"])


def test_zero_rate_donor_never_chosen():
    cps = pd.DataFrame({"cp_id": ["L00001-1"], "location_id": ["L00001"], "power_class": ["AC_22"]})
    locs = pd.DataFrame(
        {"location_id": ["L00001"], "county_code": ["CJ"], "context_type": ["urban"]}
    )
    donors = donors_df([("Z#1", "AC_22", 0.0, 1.0), ("A#1", "AC_22", 1.0, 0.2)]).assign(
        first_ts=ts("2023-03-01 10:00"), last_ts=ts("2023-03-01 10:00")
    )
    ds = sess(["2023-03-01 10:00"]).assign(donor_charger_id="A#1")
    cfg = {
        "seed": 1,
        "max_scale": 3.0,
        "start": "2023-01-01",
        "end": "2023-12-31",
        "split_date": "2023-06-30",
        "context_multiplier": MULT,
    }
    s, m, _ = mapping.map_sessions(cps, locs, pd.Series({"CJ": 2.0}), donors, ds, cfg)
    assert m["donor_charger_id"].tolist() == ["A#1"]
    assert list(s.columns) == mapping.SESSION_COLS


def test_scaling_ratio_and_cap():
    m = pd.DataFrame(
        {
            "cp_id": ["a", "b", "c"],
            "power_class": "AC_22",
            "demand": [1.0, 2.0, 100.0],
            "donor_charger_id": ["A#1", "A#1", "B#1"],
        }
    )
    donors = donors_df([("A#1", "AC_22", 1.0, 0.5), ("B#1", "AC_22", 3.0, 1.0)])
    out = mapping.scaling(m, donors, max_scale=3.0).set_index("cp_id")
    # class median rate 2.0, class median demand 2.0
    assert out.loc["a", "target_rate"] == pytest.approx(1.0)
    assert out.loc["b", "ratio"] == pytest.approx(2.0)
    assert out.loc["c", "ratio"] == 3.0 and bool(out.loc["c", "capped"])


def test_thinning_rate_within_tolerance():
    rng = np.random.default_rng(0)
    keep = mapping.thin_mask(100_000, 0.3, rng)
    assert keep.mean() == pytest.approx(0.3, abs=0.01)


def test_top_up_rate_and_no_overlap():
    rng = np.random.default_rng(0)
    hour = 3600 * 10**9
    # primary: 1 h sessions starting every 10 h; candidates: 1 h every 5 h (half overlap primary)
    p_arr = np.arange(0, 20_000) * 10 * hour
    p_dep = p_arr + hour
    c_arr = np.arange(0, 40_000) * 5 * hour + hour // 2
    c_dep = c_arr + hour
    keep, skipped = mapping.top_up_mask(p_arr, p_dep, c_arr, c_dep, 0.6, rng)
    free = np.ones(len(c_arr), bool)
    free[::2] = False  # every second candidate overlaps a primary session
    assert not keep[~free].any()
    assert keep[free].mean() == pytest.approx(0.6, abs=0.01)
    assert skipped > 0
    arr = np.concatenate([p_arr, c_arr[keep]])
    dep = np.concatenate([p_dep, c_dep[keep]])
    o = np.argsort(arr)
    assert (arr[o][1:] >= dep[o][:-1]).all()


def test_london_to_bucharest_wall_clock_across_dst():
    # 2023-03-26: both zones switch at 01:00 UTC
    utc = utc_series(["2023-03-25 08:10", "2023-03-26 07:10", "2023-03-26 02:30"])
    wall = mapping.wall_clock(utc)
    assert [t.strftime("%H:%M") for t in wall] == ["08:10", "08:10", "03:30"]
    ro = mapping.wall_to_ro_utc(wall)
    assert list(ro) == [ts("2023-03-25 06:10"), ts("2023-03-26 05:10"), ts("2023-03-26 01:00")]


def test_ro_holiday_takes_sunday_timeline():
    # 2023-05-01 (Mon) is a RO holiday; Sunday of that ISO week is 2023-05-07
    s = sess(["2023-04-30 09:00", "2023-05-01 09:00", "2023-05-07 14:00", "2023-05-08 09:00"])
    ro = {pd.Timestamp("2023-05-01")}
    tl, st = mapping.donor_timeline(s, ro, set())
    got = ns_to_utc(tl["arr"])
    # 15:00 London (BST) on 05-01 wall -> 15:00 Bucharest (EEST) = 12:00 UTC
    assert ts("2023-05-01 12:00") in got
    assert ts("2023-05-01 07:00") not in got  # the Monday's own 10:00 wall session is gone
    assert st["days_swapped"] == 1 and len(got) == 4


def test_sct_only_holiday_takes_adjacent_week():
    s = sess(["2023-05-22 08:00", "2023-05-29 11:00", "2023-06-05 17:00"])
    sct = {pd.Timestamp("2023-05-29")}
    tl, _ = mapping.donor_timeline(s, set(), sct)
    got = ns_to_utc(tl["arr"])
    assert ts("2023-05-29 06:00") in got  # previous week's 09:00 wall clock
    assert ts("2023-05-29 09:00") not in got  # own 12:00 wall session gone
    # previous week outside the donor span -> next week
    tl2, _ = mapping.donor_timeline(s.iloc[1:], set(), sct)
    assert ts("2023-05-29 15:00") in ns_to_utc(tl2["arr"])


def test_split_boundary_uses_bucharest_date():
    a = utc_series(["2023-12-31 21:30", "2023-12-31 22:10"])  # 23:30 and 00:10 local
    assert list(mapping.assign_split(a, "2023-12-31")) == ["train", "test"]


def test_validate_rejects_overlap():
    s = pd.DataFrame(
        {
            "cp_id": ["X-1", "X-1"],
            "arrival_ts": utc_series(["2023-01-01 10:00", "2023-01-01 10:30"]),
            "departure_ts": utc_series(["2023-01-01 11:00", "2023-01-01 11:30"]),
        }
    )
    with pytest.raises(AssertionError):
        mapping.validate(s, ["X-1"])


def test_context_columns(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    calls = []

    def fake(lat, lon, start, end, name):
        calls.append(name)
        hours = pd.date_range("2023-11-30", "2023-12-02", freq="h", tz="UTC", inclusive="left")
        n = len(hours)
        return pd.DataFrame(
            {"ts_hour": hours, "temp_c": 1.0, "precip_mm": 0.0, "wind_kmh": np.full(n, 5.0)}
        )

    monkeypatch.setattr(weather, "fetch_hourly", fake)
    cfg = {
        "start": "2023-11-30",
        "end": "2023-12-01",
        "request_gap_s": 0,
        "retry_wait_s": 0,
        "max_retries": 0,
    }
    rep = context.build(tmp_path, cfg)
    ctx = pd.read_parquet(tmp_path / "processed" / "context.parquet")
    assert list(ctx.columns) == context.COLS
    assert str(ctx["ts_hour"].dtype) == "datetime64[ns, UTC]"
    assert rep["counties"] == 42 == len(calls)
    assert {"B", "IF", "CJ"} <= set(ctx["county_code"])
    one = ctx[ctx["county_code"] == "CJ"].set_index("ts_hour")["is_holiday"]
    assert bool(one[ts("2023-11-30 10:00")])  # St Andrew's Day
    assert bool(one[ts("2023-12-01 21:00")])  # 1 Dec 23:00 Bucharest, National Day
    assert not bool(one[ts("2023-12-01 22:00")])  # 2 Dec 00:00 Bucharest
    assert ctx["is_holiday"].dtype == bool


def test_declone_breaks_identical_timelines_without_overlap():
    # 08:00 and 12:00 UTC, plus 21:30 UTC (23:30 local) and 22:25 UTC (00:25 next local day)
    rows = [(f"2023-03-{d:02d} {hm}", m) for d in range(1, 21)
            for hm, m in (("08:00", 50), ("12:00", 50), ("21:30", 50), ("22:25", 30))]  # fmt: skip
    arr = utc_series([r[0] for r in rows])
    one = pd.DataFrame(
        {
            "arrival_ts": arr,
            "departure_ts": arr + pd.to_timedelta([r[1] for r in rows], unit="min"),
            "energy_kwh": 5.0,
            "location_id": "L",
            "donor_charger_id": "A#1",
        }
    )
    s = pd.concat([one.assign(cp_id="X-1"), one.assign(cp_id="Y-1")], ignore_index=True)
    assert mapping.clone_groups(s) == 1
    out, dropped = mapping.declone(s, 7, 20, "2023-03-01", "2023-03-31")
    assert mapping.clone_groups(out) == 0
    assert len(out) + dropped == len(s) and dropped > 0  # cross-midnight pairs can collide
    mapping.validate(out, ["X-1", "Y-1"])
    x = out[out["cp_id"] == "X-1"]
    hour = x["arrival_ts"].dt.round("h").dt.hour
    day_ = x[hour.isin([8, 12])]
    shift = day_["arrival_ts"] - day_["arrival_ts"].dt.round("h")
    assert (shift.abs() <= pd.Timedelta(minutes=20)).all() and (shift != pd.Timedelta(0)).all()
    per_day = shift.groupby(day_["arrival_ts"].dt.date).nunique()
    assert (per_day == 1).all()  # one offset per CP and local day
    again, _ = mapping.declone(s, 7, 20, "2023-03-01", "2023-03-31")
    pd.testing.assert_frame_equal(out, again)


def test_active_spans_and_days():
    donors = pd.DataFrame(
        {
            "donor_charger_id": ["A#1"],
            "first_ts": [ts("2023-03-02 07:00")],  # 07:00 London -> 07:00 Bucharest
            "last_ts": [ts("2023-03-04 22:30")],  # 22:30 London -> 22:30 Bucharest
        }
    )
    m = mapping.active_spans(pd.DataFrame({"cp_id": ["X-1"], "donor_charger_id": ["A#1"]}), donors)
    assert m.loc[0, "active_from"] == ts("2023-03-02 05:00")
    assert m.loc[0, "active_to"] == ts("2023-03-04 20:30")
    d = mapping.active_days(m, "2023-03-01", "2023-03-06")
    assert list(d.columns) == ["cp_id", "date", "active"]
    assert d["active"].tolist() == [False, True, True, True, False, False]
    assert str(d["date"].iloc[0]) == "2023-03-01"
