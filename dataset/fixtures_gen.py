"""Synthetic fixtures for sessions, signals and context. Run: python -m dataset.fixtures_gen."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "fixtures"
SEED = 42
TZ = "Europe/Bucharest"
DAYS = ["2024-03-12", "2024-03-13"]
# Fixture-only placeholders until T8 sets real values in config/signals.yaml.
BACKGROUND_EV = 2.0
ICE_BASE = 40.0

LOCATIONS = [
    ("L00001", "B", "urban", ["AC_22", "AC_22", "DC_50"]),
    ("L00002", "CJ", "retail", ["DC_50", "DC_50", "AC_22"]),
    ("L00003", "PH", "highway", ["DC_HPC", "DC_HPC", "DC_HPC", "DC_50"]),
    ("L00004", "IF", "airport", ["AC_22", "DC_HPC"]),
    ("L00005", "TM", "other", ["AC_22"]),
]
# Sessions per CP per day (min, max) by context, halved for AC (long stays).
COUNTS = {
    "urban": (8, 12),
    "retail": (6, 10),
    "highway": (4, 8),
    "airport": (5, 9),
    "other": (3, 6),
}
DURATION_MIN = {"AC_22": (60, 240), "DC_50": (20, 50), "DC_HPC": (12, 30)}
POWER_KW = {"AC_22": (7, 11), "DC_50": (30, 45), "DC_HPC": (80, 150)}
PAD = pd.Timedelta(minutes=2)


def _arrival_hour(rng: np.random.Generator, ctx: str) -> float:
    """Local-clock arrival hour in [0, 24) for a context type."""
    if ctx == "urban":
        h = rng.normal(8, 1.2) if rng.random() < 0.5 else rng.normal(18, 1.5)
    elif ctx == "retail":
        h = rng.normal(13, 2.5)
    elif ctx == "highway":
        h = rng.uniform(6, 22)
    elif ctx == "airport":
        h = rng.uniform(5, 23) if rng.random() < 0.5 else rng.normal(14, 4)
    else:
        h = rng.normal(12, 4)
    return float(np.clip(h, 0.0, 23.9))


def gen_locations() -> pd.DataFrame:
    rows = [
        (loc, county, ctx, f"{loc}-{k}", pc)
        for loc, county, ctx, pcs in LOCATIONS
        for k, pc in enumerate(pcs, 1)
    ]
    cols = ["location_id", "county_code", "context_type", "cp_id", "power_class"]
    return pd.DataFrame(rows, columns=cols)


def gen_sessions(rng: np.random.Generator, locs: pd.DataFrame) -> pd.DataFrame:
    rows = []
    n = 0
    for r in locs.itertuples():
        lo, hi = COUNTS[r.context_type]
        for day in DAYS:
            midnight = pd.Timestamp(day, tz=TZ)
            day_end = midnight + pd.Timedelta(days=1)
            target = int(rng.integers(lo, hi + 1))
            if r.power_class == "AC_22":
                target = max(3, target // 2)
            taken: list[tuple[pd.Timestamp, pd.Timestamp]] = []
            for _ in range(target):
                dmin = rng.uniform(*DURATION_MIN[r.power_class])
                for _try in range(30):
                    hour = _arrival_hour(rng, r.context_type)
                    arr = midnight + pd.Timedelta(seconds=int(hour * 3600))
                    dep = arr + pd.Timedelta(seconds=int(dmin * 60))
                    free = all(dep + PAD <= a or arr >= d + PAD for a, d in taken)
                    if dep < day_end and free:
                        taken.append((arr, dep))
                        kwh = rng.uniform(*POWER_KW[r.power_class]) * dmin / 60
                        n += 1
                        rows.append(
                            (
                                f"S{n:06d}",
                                r.cp_id,
                                r.location_id,
                                arr.tz_convert("UTC"),
                                dep.tz_convert("UTC"),
                                round(float(kwh), 2),
                            )
                        )
                        break
    cols = ["session_id", "cp_id", "location_id", "arrival_ts", "departure_ts", "energy_kwh"]
    df = pd.DataFrame(rows, columns=cols)
    df["donor_charger_id"] = "synthetic"
    df["split"] = "test"
    df = df.sort_values(["arrival_ts", "cp_id"]).reset_index(drop=True)
    for c in ("arrival_ts", "departure_ts"):
        df[c] = df[c].astype("datetime64[ns, UTC]")
    return df


def gen_context(rng: np.random.Generator, counties: list[str]) -> pd.DataFrame:
    hours = pd.date_range(pd.Timestamp(DAYS[0], tz=TZ), periods=48, freq="h").tz_convert("UTC")
    local_h = hours.tz_convert(TZ).hour.to_numpy()
    frames = []
    for county in counties:
        base = rng.uniform(5, 9)
        temp = base + 4 * np.sin((local_h - 9) / 24 * 2 * np.pi) + rng.normal(0, 0.5, 48)
        rainy = rng.random(48) < 0.15
        precip = np.where(rainy, rng.uniform(0.1, 2.5, 48), 0.0)
        wind = np.clip(rng.normal(14, 5, 48), 2, 35)
        frames.append(
            pd.DataFrame(
                {
                    "ts_hour": hours,
                    "county_code": county,
                    "temp_c": temp.round(1),
                    "precip_mm": precip.round(1),
                    "wind_kmh": wind.round(1),
                    "is_holiday": False,
                }
            )
        )
    df = pd.concat(frames, ignore_index=True)
    df["ts_hour"] = df["ts_hour"].astype("datetime64[ns, UTC]")
    return df


def _traffic_curve(local_hour: np.ndarray, ctx: str) -> np.ndarray:
    def g(mu: float, sd: float) -> np.ndarray:
        return np.exp(-0.5 * ((local_hour - mu) / sd) ** 2)

    if ctx == "highway":
        return 0.25 + 0.5 * g(13, 4) + 0.2 * g(17, 2)
    return 0.15 + 0.6 * g(8, 1.5) + 0.65 * g(17.5, 2) + 0.2 * g(13, 3)


def gen_signals(
    rng: np.random.Generator,
    cfg: dict,
    sessions: pd.DataFrame,
    context: pd.DataFrame,
) -> pd.DataFrame:
    bg = cfg.get("background_ev") or BACKGROUND_EV
    ice = cfg.get("ice_base") or ICE_BASE
    parts = []
    lead_lo, lead_hi = cfg["intent_lead_min"]
    start = pd.Timestamp(DAYS[0], tz=TZ).tz_convert("UTC")
    end = start + pd.Timedelta(days=len(DAYS))

    # Route intents: one per app-using session, plus false positives with no session.
    intent_rows = []
    for s in sessions.itertuples():
        if rng.random() < cfg["p_app"]:
            lead = pd.Timedelta(seconds=int(rng.uniform(lead_lo, lead_hi) * 60))
            err = pd.Timedelta(seconds=int(rng.normal(0, cfg["eta_sd_min"]) * 60))
            intent_rows.append(
                (s.arrival_ts - lead, s.location_id, s.arrival_ts + err, s.session_id)
            )
    for loc, g in sessions.groupby("location_id"):
        for _ in range(int(rng.binomial(len(g), cfg["false_positive_rate"]))):
            ts = start + pd.Timedelta(seconds=int(rng.uniform(0, 48 * 3600)))
            eta = ts + pd.Timedelta(seconds=int(rng.uniform(lead_lo, lead_hi) * 60))
            intent_rows.append((ts, loc, eta, None))
    intents = pd.DataFrame(intent_rows, columns=["ts", "location_id", "eta_ts", "session_id"])
    intents["signal"] = "ppcblue_route_intent"
    intents["value"] = 1.0
    parts.append(intents)

    slots = pd.date_range(start, end, freq="5min", inclusive="left")
    local = slots.tz_convert(TZ)
    local_hour = (local.hour + local.minute / 60).to_numpy()
    for loc, county, ctx, _ in LOCATIONS:
        precip = (
            context[context.county_code == county]
            .set_index("ts_hour")["precip_mm"]
            .reindex(slots.floor("h"))
            .to_numpy()
        )
        traffic = _traffic_curve(local_hour, ctx)
        traffic = traffic * (1 - 0.1 * np.minimum(precip, 2)) * rng.normal(1, 0.05, len(slots))
        traffic = np.clip(traffic, 0, 1)
        arr = pd.DatetimeIndex(sessions.loc[sessions.location_id == loc, "arrival_ts"])
        nxt = np.array([((arr >= t) & (arr < t + pd.Timedelta(minutes=30))).sum() for t in slots])
        vals = {
            "maps_traffic_index": traffic,
            "maps_ev_count": rng.poisson(cfg["ev_tag_rate"] * (nxt + bg * traffic)),
            "maps_ice_count": rng.poisson(ice * traffic),
        }
        for name, v in vals.items():
            parts.append(
                pd.DataFrame(
                    {
                        "ts": slots,
                        "location_id": loc,
                        "signal": name,
                        "value": np.asarray(v, dtype="float64"),
                        "eta_ts": pd.NaT,
                        "session_id": None,
                    }
                )
            )
    df = pd.concat(parts, ignore_index=True)
    df = df[["ts", "location_id", "signal", "value", "eta_ts", "session_id"]]
    for c in ("ts", "eta_ts"):
        df[c] = pd.to_datetime(df[c], utc=True).astype("datetime64[ns, UTC]")
    df["value"] = df["value"].astype("float64")
    df["session_id"] = df["session_id"].astype(object)
    return df.sort_values(["ts", "location_id", "signal"]).reset_index(drop=True)


def main() -> None:
    cfg = yaml.safe_load((ROOT / "config" / "signals.yaml").read_text())
    rng = np.random.default_rng(SEED)
    OUT.mkdir(exist_ok=True)
    locs = gen_locations()
    sessions = gen_sessions(rng, locs)
    context = gen_context(rng, sorted(locs.county_code.unique()))
    signals = gen_signals(rng, cfg, sessions, context)
    locs.to_csv(OUT / "locations_sample_m1.csv", index=False)
    sessions.to_parquet(OUT / "sessions_sample.parquet", index=False)
    signals.to_parquet(OUT / "signals_sample.parquet", index=False)
    context.to_parquet(OUT / "context_sample.parquet", index=False)
    print(
        f"locations={len(locs)} sessions={len(sessions)} "
        f"signals={len(signals)} context={len(context)}"
    )


if __name__ == "__main__":
    main()
