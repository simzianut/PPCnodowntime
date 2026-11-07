from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from sim.baselines import AlwaysOn, NaiveQuietHours, choose_windows
from sim.types import Command, CpView, Policy, PowerClass, SimState, State, TargetState

TZ = ZoneInfo("Europe/Bucharest")
MONDAY = datetime(2024, 3, 4, tzinfo=TZ)  # a Monday


def _cps(*rows):
    return pd.DataFrame(rows, columns=["cp_id", "location_id", "power_class"])


def _sessions(rows):
    """rows: (cp_id, local datetime, split)."""
    df = pd.DataFrame(rows, columns=["cp_id", "arrival_local", "split"])
    df["arrival_ts"] = pd.to_datetime(df["arrival_local"]).dt.tz_localize(TZ).dt.tz_convert("UTC")
    return df.drop(columns="arrival_local")


def _busy_except(cp_id, quiet_hours, days=7, weeks=2):
    """Sessions at every hour of every day except the given local hours."""
    rows = []
    for w in range(weeks):
        for d in range(days):
            for h in range(24):
                if h not in quiet_hours:
                    ts = datetime(2024, 1, 1 + 7 * w + d, h, 30)
                    rows.append((cp_id, ts, "train"))
    return rows


def _view(cp_id, state, pc=PowerClass.DC_50):
    return CpView(cp_id, "L1", pc, ["CCS"], state, 0)


def _state(t, *views):
    return SimState(t=t, cps=list(views), signals=pd.DataFrame(), context={}, forecast=None)


def test_always_on():
    p = AlwaysOn()
    assert isinstance(p, Policy) and p.name == "always_on"
    assert p.decide(_state(MONDAY, _view("A", State.IDLE))) == []


def test_tie_break_all_zero_is_01_04():
    starts = choose_windows(np.zeros((2, 168)), 3)
    assert (starts == 1).all()


def test_empty_cp_gets_tie_break_window():
    p = NaiveQuietHours(_sessions([]), _cps(("A", "L1", "DC_50")), hours=3)
    assert (p.starts == 1).all()
    assert p.sleeping[0, [1, 2, 3]].all() and not p.sleeping[0, [0, 4]].any()


def test_window_choice_and_rate():
    p = NaiveQuietHours(
        _sessions(_busy_except("A", {5, 6, 7, 8})), _cps(("A", "L1", "DC_50")), hours=3
    )
    # 05-08 and 06-09 both sum to zero; centre 6.5 is nearer 03:00 than 7.5
    assert (p.starts[0] == 5).all()
    assert p.rates[0, 0] == pytest.approx(1.0)  # 2 sessions over 2 weeks


def test_midnight_crossing_window():
    p = NaiveQuietHours(
        _sessions(_busy_except("A", {23, 0, 1})), _cps(("A", "L1", "DC_50")), hours=3
    )
    assert (p.starts[0] == 23).all()
    # Monday 23:00-02:00 covers Monday 23 and Tuesday 0, 1; Sunday's window wraps into Monday
    assert p.sleeping[0, [23, 24, 25, 0, 1]].all()
    cmds = p.decide(_state(MONDAY.replace(hour=0, minute=40), _view("A", State.IDLE)))
    assert cmds == [Command("A", TargetState.DEEP_SLEEP, "quiet window 23-02")]


def test_uses_train_split_only():
    rows = _busy_except("A", {10, 11, 12})
    test_rows = [("A", datetime(2024, 1, 1 + d, h, 0), "test") for d in range(7) for h in (10, 11)]
    for _ in range(5):
        rows += test_rows
    p = NaiveQuietHours(_sessions(rows), _cps(("A", "L1", "DC_50")), hours=3)
    assert (p.starts[0] == 10).all()


def test_decide_targets_and_skips():
    cps = _cps(("dc", "L1", "DC_50"), ("ac", "L1", "AC_22"), ("ch", "L1", "DC_HPC"))
    p = NaiveQuietHours(_sessions([]), cps, hours=3)
    t = MONDAY.replace(hour=2, minute=15)
    cmds = p.decide(
        _state(
            t,
            _view("dc", State.IDLE),
            _view("ac", State.IDLE, PowerClass.AC_22),
            _view("ch", State.CHARGING, PowerClass.DC_HPC),
        )
    )
    assert cmds == [
        Command("dc", TargetState.DEEP_SLEEP, "quiet window 01-04"),
        Command("ac", TargetState.STANDBY, "quiet window 01-04"),
    ]
    # already in target: no command
    already = _state(t, _view("dc", State.DEEP_SLEEP), _view("ac", State.STANDBY, PowerClass.AC_22))
    assert p.decide(already) == []


def test_wakes_at_window_end_not_before():
    p = NaiveQuietHours(_sessions([]), _cps(("A", "L1", "DC_50")), hours=3)
    asleep = _view("A", State.DEEP_SLEEP)
    assert p.decide(_state(MONDAY.replace(hour=3, minute=55), asleep)) == []
    end = p.decide(_state(MONDAY.replace(hour=4), asleep))
    assert end == [Command("A", TargetState.IDLE, "window end")]
    standby = _view("A", State.STANDBY)
    assert p.decide(_state(MONDAY.replace(hour=12), standby))[0].target_state == TargetState.IDLE
    assert p.decide(_state(MONDAY.replace(hour=12), _view("A", State.IDLE))) == []


def test_utc_state_time_is_converted():
    p = NaiveQuietHours(_sessions([]), _cps(("A", "L1", "DC_50")), hours=3)
    t = datetime(2024, 3, 4, 0, 30, tzinfo=ZoneInfo("UTC"))  # 02:30 local
    assert len(p.decide(_state(t, _view("A", State.IDLE)))) == 1


def test_constructor_is_fast_for_500_cps():
    import time

    rng = np.random.default_rng(0)
    cps = _cps(*[(f"c{i}", f"L{i // 4}", "DC_50") for i in range(500)])
    n = 50_000
    base = pd.Timestamp("2024-01-01", tz="UTC")
    sessions = pd.DataFrame(
        {
            "cp_id": [f"c{i}" for i in rng.integers(0, 500, n)],
            "arrival_ts": base + pd.to_timedelta(rng.integers(0, 90 * 86400, n), unit="s"),
            "split": "train",
        }
    )
    t0 = time.perf_counter()
    p = NaiveQuietHours(sessions, cps)
    build = time.perf_counter() - t0
    views = [_view(f"c{i}", State.IDLE) for i in range(500)]
    t0 = time.perf_counter()
    for _ in range(50):
        p.decide(_state(MONDAY.replace(hour=2), *views))
    per_call = (time.perf_counter() - t0) / 50
    assert build < 2.0 and per_call < 0.01
