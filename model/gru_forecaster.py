"""F2 GRU as a service (decisions 0069, 0070): batch forecasts for days and an online Forecaster.

`predict_days` builds forecasts.parquet with the training feature builder; `GRUForecaster` builds
the same features online from what SimState shows at t and implements the Forecaster protocol.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import torch
import yaml

from model import forecast_data as fd
from model import forecast_eval as fe
from model.calibration import IsotonicCalibrator
from model.experiments import f2_gru
from model.features import CONTEXT_TYPE_MAP, FEATURE_COLUMNS, POWER_CLASS_MAP
from sim.forecast import ts_key
from sim.types import FORECAST_COLUMNS, SimState, State

log = logging.getLogger("model.gru_forecaster")
OUT_COLUMNS = ["ts", *FORECAST_COLUMNS]
NS, SLOT_NS, SLOTS = fd.NS, fd.SLOT_NS, fd.SLOTS


@dataclass
class GRUModel:
    """Network, normalisation and calibrators loaded from an f2_gru.pt."""

    net: torch.nn.Module
    config: dict
    features: list[str]
    dyn_names: list[str]
    norm: dict
    calibrators: dict[str, IsotonicCalibrator]
    calibration: dict | None = None  # {"kind", "a", "b"}; None in old checkpoints (isotonic)

    @property
    def window(self) -> int:
        return int(self.config["window_slots"])

    def prep(self, X: np.ndarray):
        """Columns in `features` order -> normalised (dynamic, static) blocks."""
        _, dyn, stat = f2_gru._prep(X, self.features)
        return f2_gru.apply_norm(dyn, stat, self.norm)

    def finish(self, raw: dict) -> dict:
        """Saved calibration, then the cumulative max across horizons (as in training)."""
        if (self.calibration or {}).get("kind") == "temp_bias":
            cal = f2_gru.affine_probs(raw["logits"], self.calibration["a"], self.calibration["b"])
        else:
            cal = {
                k: self.calibrators[f"y{k[1:]}"].predict(raw[k]) if self.calibrators else raw[k]
                for k in ("p15", "p30", "p60")
            }
        (p15, p30, p60), _ = fe.enforce_monotone(cal["p15"], cal["p30"], cal["p60"])
        return {"p15": p15, "p30": p30, "p60": p60, "n60": raw["n60"]}

    @torch.no_grad()
    def forward(self, x: np.ndarray, valid: np.ndarray, stat: np.ndarray) -> dict:
        """Windows x (n, W, n_dyn), valid (n, W), stat (n, 4) -> calibrated p15/p30/p60/n60."""
        t = [torch.from_numpy(np.ascontiguousarray(a, np.float32)) for a in (x, valid, stat)]
        self.net.eval()
        logits, lograte = self.net(*t)
        keys = ("p15", "p30", "p60", "n60")
        res = f2_gru.probs_from(logits, lograte)
        raw = {k: r.double().numpy() for k, r in zip(keys, res, strict=True)}
        return self.finish({**raw, "logits": logits.double().numpy()})


def load_gru(path: str | Path) -> GRUModel:
    ck = torch.load(Path(path), map_location="cpu", weights_only=False)
    net = f2_gru.Net(len(ck["dynamic_features"]), 2, ck["config"])
    net.load_state_dict(ck["state_dict"])
    net.eval()
    cals = {k: IsotonicCalibrator.from_dict(v) for k, v in (ck.get("calibrators") or {}).items()}
    return GRUModel(
        net,
        ck["config"],
        ck["features"],
        ck["dynamic_features"],
        ck["norm"],
        cals,
        ck.get("calibration"),
    )


def model_path(run_dir_or_pt: str | Path) -> Path:
    p = Path(run_dir_or_pt)
    return p / "model" / "f2_gru.pt" if p.is_dir() else p


def _run_config(mpath: Path) -> dict:
    """Config of the run that made `mpath` (data file names), with the seed county_ev default."""
    cfg: dict = {}
    for d in (mpath.parent.parent, mpath.parent):
        if (d / "config.yaml").exists():
            cfg = yaml.safe_load((d / "config.yaml").read_text()) or {}
            break
    cfg = {**cfg, "data": dict(cfg.get("data") or {})}
    if "county_ev" not in cfg["data"] and (fd.data_root().parent / "seed").exists():
        if (fd.data_root().parent / "seed" / "county_ev_stats.csv").exists():
            cfg["data"]["county_ev"] = "../seed/county_ev_stats.csv"
    return cfg


@contextlib.contextmanager
def _data_dir(path: str | Path | None):
    old = os.environ.get("DATA_DIR")
    if path is not None:
        os.environ["DATA_DIR"] = str(path)
    try:
        yield
    finally:
        if path is not None:
            if old is None:
                os.environ.pop("DATA_DIR", None)
            else:
                os.environ["DATA_DIR"] = old


# ---------------------------------------------------------------- batch forecasts


def _as_dates(days: Iterable) -> list[pd.Timestamp]:
    return sorted({pd.Timestamp(d).normalize().tz_localize(None) for d in days})


def predict_days(
    model_path_: str | Path,
    days: Iterable[date | str],
    data_dir: str | Path | None = None,
    out_path: str | Path | None = None,
    chunk_days: int = 7,
    config: dict | None = None,
    batch_size: int = 8192,
) -> pd.DataFrame | int:
    """Forecasts for every active CP x 288 slots of each UTC day, in the forecasts.parquet schema.

    Returns the DataFrame, or the row count when `out_path` is given (written chunk by chunk).
    """
    with _data_dir(data_dir):
        return _predict_days(
            Path(model_path_), _as_dates(days), out_path, chunk_days, config, batch_size
        )


def _predict_days(mpath, dates, out_path, chunk_days, config, batch_size):
    t0 = time.monotonic()
    model = load_gru(mpath)
    cfg = config if config is not None else _run_config(mpath)
    cps = fd.load_cps(cfg).set_index("cp_id", drop=False)
    act = pd.read_parquet(fd._data_file(cfg, "cp_active_days"))
    act = act[act["active"]][["cp_id", "date"]].copy()
    act["date"] = pd.to_datetime(act["date"]).dt.tz_localize(None).dt.normalize()
    act = act[act["date"].isin(dates) & act["cp_id"].isin(cps.index)]
    sess = pd.read_parquet(
        fd._data_file(cfg, "sessions"),
        columns=["cp_id", "location_id", "arrival_ts", "departure_ts"],
    )
    hist_start = fd.history_start(sess)
    by_cp = fd._sessions_by(sess, "cp_id", True)
    by_loc = {k: v[0] for k, v in fd._sessions_by(sess, "location_id", False).items()}
    del sess
    loc_index = {k: i for i, k in enumerate(sorted(cps["location_id"].unique()))}
    d_int = act["date"].to_numpy().astype("datetime64[D]").astype("int64")
    li = act["cp_id"].map(cps["location_id"]).map(loc_index).to_numpy().astype(np.int64)
    sig = fd.load_signal_tables(
        fd._data_file(cfg, "signals"), loc_index, np.unique((li << fd.DAY_SHIFT) | d_int), log
    )
    ctx = fd.ContextGrid(pd.read_parquet(fd._data_file(cfg, "context")))
    empty = (np.zeros(0, np.int64), np.zeros(0, np.int64))
    writer, parts, rows = None, [], 0
    step = max(1, int(chunk_days))
    for a in range(0, len(dates), step):
        chunk = act[act["date"].isin(dates[a : a + step])].sort_values(["cp_id", "date"])
        if chunk.empty:
            continue
        args = (cps, loc_index, by_cp, by_loc, empty, sig, ctx, hist_start)
        df = _predict_chunk(model, chunk, *args, batch_size)
        rows += len(df)
        log.info("chunk %d: %d rows (%.0f s)", a // step, len(df), time.monotonic() - t0)
        if out_path is None:
            parts.append(df)
            continue
        tbl = pa.Table.from_pandas(df, preserve_index=False)
        if writer is None:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            writer = pq.ParquetWriter(out_path, tbl.schema)
        writer.write_table(tbl)
    if writer is not None:
        writer.close()
    if out_path is not None:
        return rows
    return pd.concat(parts, ignore_index=True) if parts else _empty_frame()


def _empty_frame() -> pd.DataFrame:
    df = pd.DataFrame({c: pd.Series(dtype="float32") for c in OUT_COLUMNS})
    df["ts"] = pd.DatetimeIndex([], tz="UTC")
    df["cp_id"] = pd.Series(dtype=object)
    return df


def _predict_chunk(
    model, chunk, cps, loc_index, by_cp, by_loc, empty, sig, ctx, hist_start, batch_size
):
    X = np.empty((len(chunk) * SLOTS, len(FEATURE_COLUMNS)), np.float32)
    ts = np.empty(len(chunk) * SLOTS, np.int64)
    cp_ids = np.repeat(chunk["cp_id"].to_numpy(), SLOTS)
    pos = 0
    for cp_id, g in chunk.groupby("cp_id", sort=False):
        cp = cps.loc[cp_id]
        day = g["date"].to_numpy().astype("datetime64[D]").astype("int64") * 86400 * NS
        t = (day[:, None] + np.arange(SLOTS) * SLOT_NS).reshape(-1)
        loc = cp["location_id"]
        feats = fd._cp_features(
            t,
            cp,
            loc_index[loc],
            by_cp.get(cp_id, empty),
            by_loc.get(loc, empty[0]),
            ctx,
            sig,
            hist_start,
        )
        X[pos : pos + len(t)] = feats[0]
        ts[pos : pos + len(t)] = t
        pos += len(t)
    cols = [FEATURE_COLUMNS.index(c) for c in model.features]
    dyn, stat = model.prep(X[:, cols])
    del X
    n = len(dyn)
    ds = f2_gru.Windows(dyn, stat, np.zeros(n, np.uint8), np.zeros(n, np.float32), model.window)
    out = {k: [] for k in ("p15", "p30", "p60", "n60")}
    for a in range(0, n, batch_size):
        x, valid, st, _, _ = ds[np.arange(a, min(a + batch_size, n))]
        r = model.forward(x.numpy(), valid.numpy(), st.numpy())
        for k in out:
            out[k].append(r[k].astype(np.float32))
    p = {k: np.concatenate(v) for k, v in out.items()}
    df = pd.DataFrame(
        {
            "ts": pd.DatetimeIndex(ts.astype("datetime64[ns]"), tz="UTC"),
            "cp_id": cp_ids,
            "p_arrival_15": p["p15"],
            "p_arrival_30": p["p30"],
            "p_arrival_60": p["p60"],
            "expected_arrivals_60": p["n60"],
        }
    )
    return df.sort_values(["ts", "cp_id"], kind="stable").reset_index(drop=True)


# ---------------------------------------------------------------- online forecaster

_F = {c: i for i, c in enumerate(FEATURE_COLUMNS)}


class GRUForecaster:
    """Forecaster protocol on top of the F2 model, with a rolling per-CP feature history.

    cp_meta: cp_id, county_code, context_type, cps_at_location, county_ev_per_1000 (as
        fd.load_cps). sessions: optional history (cp_id, arrival_ts, departure_ts); only arrivals
        <= t are ever used, so passing the replay day's sessions leaks nothing.
    infer_arrivals: also count arrivals seen as a CP entering CHARGING between two predict()
        calls (live mode); set False when `sessions` already holds every arrival.
    """

    def __init__(
        self,
        model: GRUModel | str | Path,
        cp_meta: pd.DataFrame,
        sessions: pd.DataFrame | None = None,
        infer_arrivals: bool = True,
        slot_s: int = 300,
    ) -> None:
        self.model = load_gru(model) if not isinstance(model, GRUModel) else model
        self.meta = cp_meta.drop_duplicates("cp_id").set_index("cp_id")
        self.infer = infer_arrivals
        self.slot_s = slot_s
        self._arr: dict[str, np.ndarray] = {}
        self._hist_start: int | None = None  # set from sessions, else at the first predict()
        if sessions is not None and len(sessions):
            self._hist_start = fd.history_start(sessions)
            by = fd._sessions_by(sessions, "cp_id", False)
            self._arr = {k: v[0] for k, v in by.items()}
        self._cols = [_F[c] for c in self.model.features]
        self._traffic: dict[str, tuple[int, float]] = {}
        self._ids: tuple[str, ...] = ()
        self._cur_slot: int | None = None
        self._pending: np.ndarray | None = None
        self._prev: list[State | None] = []

    @classmethod
    def from_data(
        cls, model_path_: str | Path, data_dir: str | Path | None = None, **kw
    ) -> GRUForecaster:
        """Load the model, the CP metadata and (unless sessions=None is passed) the history."""
        mpath = model_path(model_path_)
        with _data_dir(data_dir):
            cfg = _run_config(mpath)
            meta = fd.load_cps(cfg)
            if "sessions" not in kw:
                kw["sessions"] = pd.read_parquet(
                    fd._data_file(cfg, "sessions"), columns=["cp_id", "arrival_ts", "departure_ts"]
                )
        return cls(mpath, meta, **kw)

    # -- state binding
    def _bind(self, ids: tuple[str, ...]) -> None:
        w1, nd = self.model.window - 1, len(self.model.dyn_names)
        hist = np.zeros((len(ids), w1, nd), np.float32)
        slots = np.full((len(ids), w1), -1, np.int64)
        prev: list[State | None] = [None] * len(ids)
        if self._ids:
            old = {c: i for i, c in enumerate(self._ids)}
            for i, c in enumerate(ids):
                j = old.get(c)
                if j is not None:
                    hist[i], slots[i], prev[i] = self._hist[j], self._hslot[j], self._prev[j]
            if self._pending is not None:
                pend = np.zeros((len(ids), nd), np.float32)
                for i, c in enumerate(ids):
                    if c in old:
                        pend[i] = self._pending[old[c]]
                self._pending = pend
        self._ids, self._hist, self._hslot, self._prev = ids, hist, slots, prev

    # -- features
    def _features(self, state: SimState, t: int) -> np.ndarray:
        cps = state.cps
        n = len(cps)
        X = np.zeros((n, len(FEATURE_COLUMNS)), np.float32)
        sec = t // NS
        ang = 2.0 * np.pi * ((sec % 86400) / 3600.0) / 24.0
        X[:, _F["hour_sin"]], X[:, _F["hour_cos"]] = np.sin(ang), np.cos(ang)
        X[:, _F["weekday"]] = (sec // 86400 + 3) % 7
        X[:, _F["minute_of_day"]] = (sec % 86400) // 60
        locs = [c.location_id for c in cps]
        ctx_dflt = {"is_holiday": 0.0, "temp_c": 15.0, "precip_mm": 0.0, "wind_kmh": 10.0}
        m = self.meta
        for i, cp in enumerate(cps):
            row = m.loc[cp.cp_id] if cp.cp_id in m.index else None
            county = row["county_code"] if row is not None else None
            cx = {**ctx_dflt, **state.context.get(county, {})}
            X[i, _F["is_holiday"]], X[i, _F["temp_c"]] = float(cx["is_holiday"]), cx["temp_c"]
            X[i, _F["precip_mm"]], X[i, _F["wind_kmh"]] = cx["precip_mm"], cx["wind_kmh"]
            X[i, _F["power_class_code"]] = POWER_CLASS_MAP.get(str(cp.power_class.value), 1)
            X[i, _F["context_type_code"]] = (
                CONTEXT_TYPE_MAP.get(row["context_type"], 0) if row is not None else 0
            )
            X[i, _F["county_ev_per_1000"]] = row["county_ev_per_1000"] if row is not None else 1.5
            X[i, _F["cps_at_location"]] = row["cps_at_location"] if row is not None else 1.0
            X[i, _F["is_charging"]] = cp.state == State.CHARGING
            self._arrival_features(X[i], cp.cp_id, t)
        self._signal_features(X, locs, state.signals, t)
        return X

    def _arrival_features(self, x: np.ndarray, cp_id: str, t: int) -> None:
        arr = self._arr.get(cp_id)
        if arr is None or not len(arr):
            r7, r28, mins = fd.cp_level_features(np.zeros(0, np.int64), np.array([t]), t)
            x[_F["arrivals_cp_7d"]], x[_F["arrivals_cp_28d"]] = r7[0], r28[0]
            x[_F["mins_since_last_arrival"]] = mins[0]
            return
        idx_t = np.searchsorted(arr, t, side="right")
        x[_F["arrivals_cp_1h"]] = idx_t - np.searchsorted(arr, t - 3600 * NS, side="right")
        x[_F["arrivals_cp_3h"]] = idx_t - np.searchsorted(arr, t - 3 * 3600 * NS, side="right")
        s4 = 0.0
        for k in range(1, 5):
            c = t - k * 7 * 86400 * NS
            s4 += np.searchsorted(arr, c + 1800 * NS, side="right")
            s4 -= np.searchsorted(arr, c - 1800 * NS, side="right")
        x[_F["arrivals_same_slot_4w"]] = s4 / 4.0
        r7, r28, mins = fd.cp_level_features(arr, np.array([t]), self._hist_start)
        x[_F["arrivals_cp_7d"]], x[_F["arrivals_cp_28d"]] = r7[0], r28[0]
        x[_F["mins_since_last_arrival"]] = mins[0]

    def _signal_features(self, X: np.ndarray, locs: list[str], sig: pd.DataFrame, t: int) -> None:
        if sig is not None and len(sig):
            ts = fd._ts_ns(sig["ts"])
            kind = sig["signal"].astype(str).to_numpy()
            loc = sig["location_id"].astype(str).to_numpy()
            val = sig["value"].to_numpy(dtype=float)
            near = (ts == t) | (ts == t - SLOT_NS) | (ts == t - 2 * SLOT_NS)
            ev = self._sum_by(loc, val, near & (kind == "maps_ev_count"))
            ice = self._sum_by(loc, val, near & (kind == "maps_ice_count"))
            self._update_traffic(loc, ts, val, kind == "maps_traffic_index")
            it = self._intents(sig, loc, ts, kind, t)
        else:
            ev, ice, it = {}, {}, {}
        for i, lc in enumerate(locs):
            e = ev.get(lc, 0.0)
            X[i, _F["maps_ev_count_15m"]] = e
            X[i, _F["weighted_flow_15m"]] = e + 0.05 * ice.get(lc, 0.0)
            X[i, _F["traffic_index"]] = self._traffic.get(lc, (0, 0.0))[1]
            if lc in it:
                X[i, _F["intent_eta_15"] : _F["intent_eta_60"] + 1] = it[lc]
        # NOTE: ev/ice use exact-timestamp matches on the 5-minute grid, as in training

    @staticmethod
    def _sum_by(loc: np.ndarray, val: np.ndarray, m: np.ndarray) -> dict[str, float]:
        if not m.any():
            return {}
        return pd.Series(val[m]).groupby(loc[m]).sum().to_dict()

    def _update_traffic(self, loc, ts, val, m) -> None:
        if not m.any():
            return
        df = pd.DataFrame({"loc": loc[m], "ts": ts[m], "val": val[m]}).sort_values(["ts", "val"])
        for lc, ts_, v in df.groupby("loc").last().itertuples():
            if (ts_, v) >= self._traffic.get(lc, (-1, 0.0)):
                self._traffic[lc] = (int(ts_), float(v))

    @staticmethod
    def _intents(sig, loc, ts, kind, t) -> dict[str, np.ndarray]:
        if "eta_ts" not in sig:
            return {}
        eta_s = pd.to_datetime(sig["eta_ts"], utc=True)
        ok = (kind == "ppcblue_route_intent") & eta_s.notna().to_numpy() & (ts >= t - 3600 * NS)
        if not ok.any():
            return {}
        lead = fd._ts_ns(eta_s[ok]) - t
        keep = (lead > 0) & (lead <= 3600 * NS)
        cat = np.where(lead <= 900 * NS, 0, np.where(lead <= 1800 * NS, 1, 2))[keep]
        out: dict[str, np.ndarray] = {}
        for lc, c in zip(loc[ok][keep], cat, strict=True):
            out.setdefault(lc, np.zeros(3))[c] += 1
        return out

    # -- arrivals seen online
    def _infer_arrivals(self, state: SimState, t: int) -> None:
        for i, cp in enumerate(state.cps):
            was = self._prev[i]
            if was is not None and was != State.CHARGING and cp.state == State.CHARGING:
                at = t - int(cp.seconds_in_state) * NS
                arr = self._arr.get(cp.cp_id, np.zeros(0, np.int64))
                self._arr[cp.cp_id] = np.insert(arr, np.searchsorted(arr, at, side="right"), at)
            self._prev[i] = cp.state

    # -- Forecaster protocol
    def predict(self, state: SimState) -> pd.DataFrame:
        ids = tuple(c.cp_id for c in state.cps)
        if ids != self._ids:
            self._bind(ids)
        t = ts_key(state.t)
        if self._hist_start is None:
            self._hist_start = t
        slot = t // SLOT_NS
        w = self.model.window
        if slot != self._cur_slot:  # new slot: the previous one joins the history
            if self._pending is not None and self._cur_slot is not None:
                self._hist = np.concatenate([self._hist[:, 1:], self._pending[:, None]], axis=1)
                self._hslot = np.concatenate(
                    [self._hslot[:, 1:], np.full((len(ids), 1), self._cur_slot, np.int64)], axis=1
                )
            self._cur_slot = slot
            if self.infer:
                self._infer_arrivals(state, t)
        X = self._features(state, t)
        dyn, stat = self.model.prep(X[:, self._cols])
        self._pending = dyn
        off = np.arange(-(w - 1), 0)
        ok = (self._hslot == slot + off[None, :]) & ((slot % SLOTS) + off[None, :] >= 0)
        x = np.concatenate([self._hist * ok[..., None], dyn[:, None]], axis=1)
        valid = np.concatenate([ok, np.ones((len(ids), 1), bool)], axis=1)
        r = self.model.forward(x, valid.astype(np.float32), stat)
        return pd.DataFrame(
            {
                "cp_id": list(ids),
                "p_arrival_15": r["p15"],
                "p_arrival_30": r["p30"],
                "p_arrival_60": r["p60"],
                "expected_arrivals_60": r["n60"],
            },
            columns=FORECAST_COLUMNS,
        )


# ---------------------------------------------------------------- CLI


def add_predict_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("model", help="run dir or f2_gru.pt")
    ap.add_argument("--start", required=True, help="first UTC day, YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="last UTC day (inclusive)")
    ap.add_argument("--out", default="data/processed/forecasts.parquet")
    ap.add_argument("--data-dir", default=None, help="default: $DATA_DIR or data")
    ap.add_argument("--chunk-days", type=int, default=7)


def run_predict(a: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    days = pd.date_range(a.start, a.end, freq="D")
    t0 = time.monotonic()
    n = predict_days(model_path(a.model), days, a.data_dir, a.out, a.chunk_days)
    print(f"wrote {n} rows for {len(days)} days to {a.out} in {time.monotonic() - t0:.0f} s")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m model.gru_forecaster")
    add_predict_args(ap)
    run_predict(ap.parse_args(argv))


if __name__ == "__main__":
    main()
