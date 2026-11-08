"""Run clock (decision 0091): one tick per simulated minute; speed = sim seconds per wall second."""

from __future__ import annotations

import math

from fastapi import HTTPException

from api.models import SPEEDS
from sim.engine import TICK_S

WALL_SCALE = 1.0  # tests shrink it so replays finish fast


def tick_wall_s(speed: float) -> float:
    """Wall seconds between two minute ticks at `speed`."""
    return TICK_S / speed * WALL_SCALE


def check_speed(v: object) -> int:
    if isinstance(v, bool) or v not in SPEEDS:
        raise HTTPException(400, f"speed must be one of {list(SPEEDS)}")
    return int(v)


def check_minute(v: object) -> int:
    ok = isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)
    if not ok or v != int(v):
        raise HTTPException(400, "seek value must be a whole minute of the day")
    return int(v)


def clamp(minute: int, window: tuple[int, int]) -> int:
    """A seek target inside the window, leaving at least one minute to play."""
    return min(max(minute, window[0]), window[1] - 1)
