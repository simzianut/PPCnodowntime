"""Deterministic generator of fixtures/api/ from the seed stations (python -m api.fixtures_gen)."""

from __future__ import annotations

import json
import math
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from api.fixture_store import FIXTURES_DIR
from sim.config import TZ, PowerProfile, power_profiles, sim_config
from sim.stations_seed import load_seed_stations
from sim.types import PolicyName, State

SEED = 20240312
RUN_DATE = date(2024, 3, 12)
TEST_DAYS = [date(2024, 3, d) for d in (12, 13, 14, 19, 21)]
GENERATED_AT = "2026-11-07T12:00:00Z"

HOURLY = np.array(
    [0.15, 0.1, 0.08, 0.08, 0.1, 0.2, 0.45, 0.8, 1.0, 0.95, 0.9, 1.0,
     1.1, 1.05, 0.95, 0.95, 1.1, 1.3, 1.35, 1.2, 0.95, 0.7, 0.45, 0.25]
)  # fmt: skip
HOURLY_P = HOURLY / HOURLY.sum()
BASE_SESSIONS = {"AC_22": 2.5, "DC_50": 3.5, "DC_HPC": 7.0}
CTX_MULT = {"retail": 1.4, "highway": 1.6, "airport": 1.8, "urban": 1.0, "other": 0.8}
POWER_KEY = {
    State.CHARGING: None, State.IDLE: "idle_w", State.WAKING: "idle_w",
    State.STANDBY: "standby_w", State.DEEP_SLEEP: "deep_sleep_w", State.OFFLINE: None,
}  # fmt: skip
MIN_SESSION_GAP_S = 15 * 60


@dataclass(frozen=True)
class CpInfo:
    cp_id: str
    power_class: str
    context_type: str
    profile: PowerProfile

    @property
    def mean_sessions(self) -> float:
        return BASE_SESSIONS[self.power_class] * CTX_MULT.get(self.context_type, 1.0)


@dataclass(frozen=True)
class Params:
    min_gap_s: int
    deep_gap_s: int
    late_p: float
    lead_s: int
    delay_s: int
    prefix: str


@dataclass
class CpResult:
    transitions: list[tuple[int, State]]  # excludes the initial IDLE at t=0
    commands: list[tuple[int, State, State, str]]
    arrivals: list[tuple[int, float]]
    departures: list[int]


def policy_params(policy: str, lam: float) -> Params:
    """Map λ to sleep aggressiveness: smaller λ sleeps more and wakes later."""
    a = min(max(-math.log10(lam) / 2, 0.0), 1.0)
    if policy == PolicyName.cost_rule:
        return Params(
            int((90 - 60 * a) * 60),
            int((240 - 150 * a) * 60),
            0.05 + 0.35 * a,
            15 * 60,
            10 * 60,
            "",
        )
    return Params(
        int((75 - 50 * a) * 60),
        int((200 - 130 * a) * 60),
        0.03 + 0.27 * a,
        10 * 60,
        5 * 60,
        "ppo ",
    )


def cp_infos(cps: pd.DataFrame) -> list[CpInfo]:
    prof = power_profiles()
    return [
        CpInfo(r.cp_id, r.power_class, r.context_type, prof[r.power_class])
        for r in cps.itertuples(index=False)
    ]


def gen_sessions(rng: np.random.Generator, info: CpInfo, day_s: int) -> list[tuple[int, int]]:
    n = int(rng.poisson(info.mean_sessions))
    hours = rng.choice(24, size=n, p=HOURLY_P)
    arrivals = np.sort(hours * 3600 + rng.integers(0, 3600, size=n))
    if info.power_class == "AC_22":
        durs = rng.integers(60, 181, size=n) * 60
    elif info.power_class == "DC_HPC":
        durs = rng.integers(15, 36, size=n) * 60
    else:
        durs = rng.integers(20, 51, size=n) * 60
    out, free_at = [], -MIN_SESSION_GAP_S
    for a, d in zip(arrivals.tolist(), durs.tolist(), strict=True):
        if a >= free_at + MIN_SESSION_GAP_S and a < day_s - 60:
            out.append((a, d))
            free_at = a + d
    return out


def sleep_target(info: CpInfo, deep: bool) -> State:
    return State.DEEP_SLEEP if deep and info.profile.has_deep_sleep else State.STANDBY


def plan_commands(
    policy: str,
    lam: float | None,
    sessions: list[tuple[int, int]],
    info: CpInfo,
    p30: np.ndarray,
    rng: np.random.Generator,
    day_s: int,
    slot_s: int,
) -> list[tuple[int, State, str]]:
    """Commands (t at slot start, target, reason) a policy would send to one CP."""
    if policy == PolicyName.always_on:
        return []
    if policy == PolicyName.naive_quiet_hours:
        start, end = 3600 // slot_s, 4 * 3600 // slot_s
        tgt = sleep_target(info, True)
        cmds = [(k * slot_s, tgt, "quiet hours 01:00-04:00") for k in range(start, end)]
        cmds.append((end * slot_s, State.IDLE, "quiet hours end"))
        return [c for c in cmds if c[0] < day_s]

    prm = policy_params(policy, lam)
    starts = [0] + [a + d for a, d in sessions]
    ends = [a for a, _ in sessions] + [day_s]
    cmds = []
    n_slots = day_s // slot_s
    for gs, ge in zip(starts, ends, strict=True):
        if ge - gs < prm.min_gap_s:
            continue
        sleep_t = math.ceil((gs + (prm.delay_s if gs > 0 else 0)) / slot_s) * slot_s
        last = ge >= day_s
        wake_t = None
        if not last:
            if rng.random() < prm.late_p:
                wake_t = None if rng.random() < 0.5 else (ge // slot_s) * slot_s
            else:
                wake_t = ((ge - prm.lead_s) // slot_s) * slot_s
        if (wake_t if wake_t is not None else ge) - sleep_t < 2 * slot_s:
            continue
        tgt = sleep_target(info, ge - gs >= prm.deep_gap_s)
        k = min(sleep_t // slot_s, n_slots - 1)
        cmds.append((sleep_t, tgt, f"{prm.prefix}p30={p30[k]:.2f}"))
        if wake_t is not None:
            k = min(wake_t // slot_s, n_slots - 1)
            cmds.append((wake_t, State.IDLE, f"{prm.prefix}p30={p30[k]:.2f}"))
    return [c for c in cmds if c[0] < day_s]


def simulate_cp(
    sessions: list[tuple[int, int]],
    cmds: list[tuple[int, State, str]],
    info: CpInfo,
    day_s: int,
) -> CpResult:
    """Event-driven state machine for one CP (decision 0018 rules, no sibling handover)."""
    times, states = [0], [State.IDLE]
    applied, arrivals, departures = [], [], []
    prof = info.profile

    def state_at(t: int) -> tuple[State, int]:
        i = bisect_right(times, t) - 1
        return states[i], i

    events = [(t, 0, (tgt, reason)) for t, tgt, reason in cmds]
    events += [(a, 1, (d,)) for a, d in sessions]
    events.sort(key=lambda e: (e[0], e[1]))
    for t, kind, payload in events:
        s, i = state_at(t)
        if kind == 0:
            tgt, reason = payload
            if s in (State.CHARGING, State.WAKING, State.OFFLINE) or s == tgt:
                continue
            applied.append((t, s, tgt, reason))
            if tgt == State.IDLE:
                wake = prof.standby_wake_s if s == State.STANDBY else prof.deep_sleep_wake_s
                times += [t, t + int(wake)]
                states += [State.WAKING, State.IDLE]
            else:
                times.append(t)
                states.append(tgt)
            continue
        (dur,) = payload
        if s == State.IDLE:
            start = t
        elif s == State.WAKING:
            start = times[i + 1]
            del times[i + 1 :], states[i + 1 :]
        elif s in (State.STANDBY, State.DEEP_SLEEP):
            wake = prof.standby_wake_s if s == State.STANDBY else prof.deep_sleep_wake_s
            start = t + int(wake)
            times.append(t)
            states.append(State.WAKING)
        else:
            raise AssertionError(f"arrival at {s} on {info.cp_id}")
        times.append(start)
        states.append(State.CHARGING)
        arrivals.append((t, round((min(start, day_s) - t) / 60, 3)))
        dep = start + dur
        times.append(dep)
        states.append(State.IDLE)
        if dep < day_s:
            departures.append(dep)
    trans = [(t, s) for t, s in zip(times[1:], states[1:], strict=True) if t < day_s]
    return CpResult(trans, applied, arrivals, departures)


def p30_matrix(
    infos: list[CpInfo], sessions: list[list[tuple[int, int]]], rng, n_slots: int, slot_s: int
) -> np.ndarray:
    """Noisy-oracle P(arrival within 30 min) per slot and CP."""
    t = np.arange(n_slots) * slot_s
    hour = (t // 3600) % 24
    out = np.zeros((n_slots, len(infos)))
    for j, (info, sess) in enumerate(zip(infos, sessions, strict=True)):
        rate = info.mean_sessions * HOURLY_P[hour]
        p = 1 - np.exp(-rate * 0.5)
        for a, _ in sess:
            p[(a > t) & (a <= t + 1800)] += 0.5
        out[:, j] = p + rng.normal(0, 0.04, size=n_slots)
    return np.clip(out, 0.0, 0.99).round(2)


def cum_at(result: CpResult, info: CpInfo, bounds: np.ndarray, day_s: int):
    """Cumulative overhead J and sleep seconds at each bound."""
    ts = [0] + [t for t, _ in result.transitions] + [day_s]
    ss = [State.IDLE] + [s for _, s in result.transitions]
    seg = np.diff(ts)
    w = np.array([(getattr(info.profile, POWER_KEY[s]) or 0) if POWER_KEY[s] else 0 for s in ss])
    sl = np.array([s in (State.STANDBY, State.DEEP_SLEEP) for s in ss], dtype=float)
    t_arr = np.array(ts, dtype=float)
    e = np.concatenate([[0], np.cumsum(seg * w)])
    z = np.concatenate([[0], np.cumsum(seg * sl)])
    return np.interp(bounds, t_arr, e), np.interp(bounds, t_arr, z)


def aggregate(results, infos, n_slots, slot_s):
    """Per-slot cumulative energy (J), sleep seconds and arrivals over all CPs."""
    day_s = n_slots * slot_s
    bounds = np.arange(1, n_slots + 1) * slot_s
    energy, sleep = np.zeros(n_slots), np.zeros(n_slots)
    arrivals = []
    for r, info in zip(results, infos, strict=True):
        e, z = cum_at(r, info, bounds, day_s)
        energy += e
        sleep += z
        arrivals += r.arrivals
    return energy, sleep, arrivals


def kpi_row(energy_j, ao_energy_j, sleep_s, cp_s, waits) -> dict:
    overhead = energy_j / 3.6e6
    ao = ao_energy_j / 3.6e6
    saved = ao - overhead
    w = np.asarray(waits, dtype=float)
    n = len(w)
    return {
        "overhead_kwh": round(overhead, 3),
        "saved_kwh": round(saved, 3),
        "saved_pct": round(100 * saved / ao, 2) if ao > 0 else 0.0,
        "wait_min_total": round(float(w.sum()), 3),
        "wait_min_mean": round(float(w.mean()), 4) if n else 0.0,
        "wait_min_p95": round(float(np.percentile(w, 95)), 3) if n else 0.0,
        "sessions": n,
        "waited_sessions": int((w > 0).sum()),
        "sleep_share": round(sleep_s / cp_s, 4) if cp_s else 0.0,
    }


def simulate_day(infos, day_idx, runs, n_slots, slot_s):
    """Run (policy, λ) pairs over one synthetic day; returns sessions, p30, per-run CpResults."""
    day_s = n_slots * slot_s
    sessions = [
        gen_sessions(np.random.default_rng([SEED, day_idx, j]), info, day_s)
        for j, info in enumerate(infos)
    ]
    p30 = p30_matrix(infos, sessions, np.random.default_rng([SEED, day_idx, 999]), n_slots, slot_s)
    out = {}
    for policy, lam in runs:
        lam_key = int(round((lam or 0) * 1000))
        res = []
        for j, info in enumerate(infos):
            rng = np.random.default_rng([SEED, day_idx, j, list(PolicyName).index(policy), lam_key])
            cmds = plan_commands(policy, lam, sessions[j], info, p30[:, j], rng, day_s, slot_s)
            res.append(simulate_cp(sessions[j], cmds, info, day_s))
        out[(policy, lam)] = res
    return sessions, p30, out


def build_run_day(
    cps: pd.DataFrame, n_slots: int = 288, day: date = RUN_DATE, day_idx: int = 0
) -> dict:
    """The recorded replay day consumed by api.fixture_store.RecordedDay."""
    cfg = sim_config()
    slot_s = cfg.slot_s
    infos = cp_infos(cps)
    lam = cfg.default_lambda
    runs = [
        (PolicyName.always_on, None),
        (PolicyName.naive_quiet_hours, None),
        (PolicyName.cost_rule, lam),
        (PolicyName.rl_ppo, lam),
    ]
    sessions, p30, out = simulate_day(infos, day_idx, runs, n_slots, slot_s)
    policies = [p.value for p, _ in runs]
    slots = [
        {"slot": k, "kpis": {}, "transitions": {}, "arrivals": {}, "departures": {}, "commands": {}}
        for k in range(n_slots)
    ]
    ao_energy = None
    cp_s = len(infos) * np.arange(1, n_slots + 1) * slot_s
    for (policy, _), res in out.items():
        p = policy.value
        energy, sleep, _ = aggregate(res, infos, n_slots, slot_s)
        if ao_energy is None:
            ao_energy = energy
        per_slot = {k: {"tr": [], "arr": [], "dep": [], "cmd": []} for k in range(n_slots)}
        all_arr = []
        for j, r in enumerate(res):
            for t, s in r.transitions:
                per_slot[t // slot_s]["tr"].append([j, t, s.value])
            for t, w in r.arrivals:
                per_slot[t // slot_s]["arr"].append([j, t, w])
                all_arr.append((t, w))
            for t in r.departures:
                per_slot[t // slot_s]["dep"].append([j, t])
            for t, f, tgt, reason in r.commands:
                per_slot[t // slot_s]["cmd"].append([j, f.value, tgt.value, reason])
        all_arr.sort()
        arr_t = np.array([a[0] for a in all_arr])
        arr_w = [a[1] for a in all_arr]
        for k in range(n_slots):
            n = int(np.searchsorted(arr_t, (k + 1) * slot_s)) if len(arr_t) else 0
            slots[k]["kpis"][p] = kpi_row(energy[k], ao_energy[k], sleep[k], cp_s[k], arr_w[:n])
            ps = per_slot[k]
            for key, name in (
                ("tr", "transitions"), ("arr", "arrivals"), ("dep", "departures"),
                ("cmd", "commands"),
            ):  # fmt: skip
                if ps[key]:
                    rows = ps[key] if key == "cmd" else sorted(ps[key], key=lambda x: (x[1], x[0]))
                    slots[k][name][p] = rows
    for k in range(n_slots):
        slots[k]["p30"] = [float(x) for x in p30[k]]
    intents = []
    rng = np.random.default_rng([SEED, day_idx, 4242])
    for j, sess in enumerate(sessions):
        for a, _ in sess:
            if rng.random() < 0.3:
                ts = a - int(rng.integers(10, 41)) * 60
                eta = a + int(rng.normal(0, 180))
                if ts >= 0:
                    intents.append([j, ts, eta])
    intents.sort(key=lambda x: (x[1], x[0]))
    return {
        "format": 1,
        "date": day.isoformat(),
        "tz": TZ,
        "slot_s": slot_s,
        "n_slots": n_slots,
        "lambda": lam,
        "policies": policies,
        "cp_ids": list(cps["cp_id"]),
        "initial": {p: [State.IDLE.value] * len(infos) for p in policies},
        "intents": intents,
        "slots": slots,
    }


def build_eval(cps: pd.DataFrame, n_slots: int = 288) -> dict:
    cfg = sim_config()
    infos = cp_infos(cps)
    runs = [(PolicyName.always_on, None), (PolicyName.naive_quiet_hours, None)]
    for p in (PolicyName.cost_rule, PolicyName.rl_ppo):
        runs += [(p, lam) for lam in cfg.lambda_grid]
    tot: dict = {r: {"e": 0.0, "z": 0.0, "w": []} for r in runs}
    cp_s = 0
    for day_idx, _ in enumerate(TEST_DAYS):
        _, _, out = simulate_day(infos, day_idx, runs, n_slots, cfg.slot_s)
        cp_s += len(infos) * n_slots * cfg.slot_s
        for r, res in out.items():
            energy, sleep, arrivals = aggregate(res, infos, n_slots, cfg.slot_s)
            tot[r]["e"] += energy[-1]
            tot[r]["z"] += sleep[-1]
            tot[r]["w"] += [w for _, w in arrivals]
    ao = tot[runs[0]]["e"]
    results = []
    for policy, lam in runs:
        t = tot[(policy, lam)]
        row = kpi_row(t["e"], ao, t["z"], cp_s, t["w"])
        results.append({"policy": policy.value, "lambda": lam, **row})
    return {
        "fixture": True,
        "generated_at": GENERATED_AT,
        "test_days": [d.isoformat() for d in TEST_DAYS],
        "results": results,
    }


def build_stations(locs: pd.DataFrame, cps: pd.DataFrame) -> list[dict]:
    def clean(v):
        return None if v is None or (isinstance(v, float) and math.isnan(v)) else v

    by_loc: dict[str, list[dict]] = {}
    for r in cps.itertuples(index=False):
        by_loc.setdefault(r.location_id, []).append(
            {
                "cp_id": r.cp_id,
                "power_class": r.power_class,
                "max_kw": clean(r.max_kw),
                "connectors": list(r.connectors),
                "current_type": r.current_type,
            }
        )
    out = []
    for r in locs.to_dict("records"):
        loc = {k: clean(v) for k, v in r.items()}
        loc["lat"], loc["lon"] = round(loc["lat"], 6), round(loc["lon"], 6)
        loc["charge_points"] = by_loc.get(r["location_id"], [])
        out.append(loc)
    return out


def build_policies() -> list[dict]:
    cfg = sim_config()
    return [
        {"name": "always_on", "lambdas": [], "available": True},
        {"name": "naive_quiet_hours", "lambdas": [], "available": True},
        {"name": "cost_rule", "lambdas": list(cfg.lambda_grid), "available": True},
        {"name": "rl_ppo", "lambdas": [cfg.default_lambda], "available": True},
    ]


def write_run_day(day: dict, path: Path) -> None:
    """One line per slot so diffs stay readable."""

    def dump(v) -> str:
        return json.dumps(v, separators=(",", ":"), ensure_ascii=False)

    lines = ["{"]
    for k, v in day.items():
        if k != "slots":
            lines.append(f"{json.dumps(k)}:{dump(v)},")
    lines.append('"slots":[')
    lines.append(",\n".join(dump(s) for s in day["slots"]))
    lines.append("]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_json(obj, path: Path) -> None:
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def main(out_dir: Path = FIXTURES_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    locs, cps = load_seed_stations()
    write_json(build_stations(locs, cps), out_dir / "stations.json")
    write_json(build_policies(), out_dir / "policies.json")
    write_json(build_eval(cps), out_dir / "eval_results.json")
    write_run_day(build_run_day(cps), out_dir / "run_day.json")
    for f in sorted(out_dir.iterdir()):
        print(f"{f.name}: {f.stat().st_size / 1024:.0f} KiB")


if __name__ == "__main__":
    main()
