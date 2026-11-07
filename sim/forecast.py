"""Forecasters for the engine: a lookup into forecasts.parquet, and zeros (decision 0021)."""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from sim.config import TZ
from sim.types import FORECAST_COLUMNS, SimState

VALUE_COLUMNS = FORECAST_COLUMNS[1:]


def ts_key(t: datetime | pd.Timestamp) -> int:
    """UTC nanoseconds of a timestamp; naive values are taken as Europe/Bucharest."""
    ts = pd.Timestamp(t)
    if ts.tzinfo is None:
        ts = ts.tz_localize(TZ)
    return int(ts.tz_convert("UTC").as_unit("ns").value)


def _frame(cp_ids: list[str], values: np.ndarray) -> pd.DataFrame:
    data = {"cp_id": cp_ids}
    for j, c in enumerate(VALUE_COLUMNS):
        data[c] = values[:, j]
    return pd.DataFrame(data, columns=FORECAST_COLUMNS)


class ZeroForecaster:
    """Predicts zero arrivals everywhere."""

    def predict(self, state: SimState) -> pd.DataFrame:
        ids = [cp.cp_id for cp in state.cps]
        return _frame(ids, np.zeros((len(ids), len(VALUE_COLUMNS))))


class PrecomputedForecaster:
    """Looks up the forecasts.parquet row of each CP at the slot start state.t."""

    def __init__(self, forecasts: pd.DataFrame) -> None:
        df = forecasts
        ts = df["ts"]
        if ts.dt.tz is None:
            ts = ts.dt.tz_localize("UTC")
        keys = ts.to_numpy(dtype="datetime64[ns]").view("int64")
        codes, uniques = pd.factorize(df["cp_id"])
        self._cp_code = {c: i for i, c in enumerate(uniques)}
        self._n_known = len(uniques)
        order = np.argsort(keys, kind="stable")
        keys = keys[order]
        self._codes = codes[order]
        self._values = np.nan_to_num(df[VALUE_COLUMNS].to_numpy(dtype=np.float64))[order]
        bounds = np.flatnonzero(np.diff(keys)) + 1
        starts = np.concatenate([[0], bounds])
        ends = np.concatenate([bounds, [len(keys)]])
        self._slices = {
            int(keys[s]): (int(s), int(e)) for s, e in zip(starts, ends, strict=True) if e > s
        }
        self._req_ids: tuple[str, ...] | None = None
        self._pos: np.ndarray = np.empty(0, dtype=np.int64)

    def _positions(self, ids: list[str]) -> np.ndarray:
        """pos[code] = row of that CP in the requested order, -1 if not requested."""
        key = tuple(ids)
        if key != self._req_ids:
            pos = np.full(self._n_known, -1, dtype=np.int64)
            for r, c in enumerate(ids):
                code = self._cp_code.get(c)
                if code is not None:
                    pos[code] = r
            self._req_ids, self._pos = key, pos
        return self._pos

    def predict(self, state: SimState) -> pd.DataFrame:
        ids = [cp.cp_id for cp in state.cps]
        out = np.zeros((len(ids), len(VALUE_COLUMNS)))
        sl = self._slices.get(ts_key(state.t))
        if sl is not None:
            s, e = sl
            rows = self._positions(ids)[self._codes[s:e]]
            m = rows >= 0
            out[rows[m]] = self._values[s:e][m]
        return _frame(ids, out)
