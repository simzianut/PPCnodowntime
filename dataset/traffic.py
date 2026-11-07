"""Real traffic index: TomTom flow (live) and Mapbox typical travel times (training/replay)."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yaml

USER_AGENT = "PPCnodowntime-hackathon"
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "traffic.yaml"
SECRET_PARAMS = ("key", "access_token")
DAYTYPES = ("weekday", "weekend")


class TrafficAuthError(RuntimeError):
    """401/403: the API key or token is wrong or not allowed."""


class TrafficQuotaError(RuntimeError):
    """429: the API quota is used up."""


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def redact(url: str) -> str:
    """Replace secret query values so a URL is safe to log."""
    pattern = r"([?&](?:" + "|".join(SECRET_PARAMS) + r")=)[^&]*"
    return re.sub(pattern, r"\1***", url)


def _urlopen(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _get_json(url: str, timeout: float = 30, retry_wait_s: float = 2.0) -> dict:
    """GET JSON. Clear errors on 401/403/429, one retry on 5xx, up to 4 retries on network loss."""
    server_retried, net_fails = False, 0
    while True:
        try:
            return _urlopen(url, timeout)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise TrafficAuthError(f"HTTP {e.code}: bad or unauthorised key/token") from None
            if e.code == 429:
                raise TrafficQuotaError(f"HTTP 429: quota exceeded for {redact(url)}") from None
            if e.code >= 500 and not server_retried:
                server_retried = True
                time.sleep(retry_wait_s)
                continue
            raise RuntimeError(f"HTTP {e.code} for {redact(url)}") from None
        except (urllib.error.URLError, OSError) as e:
            net_fails += 1
            if net_fails > 4:
                raise RuntimeError(f"network error {type(e).__name__} for {redact(url)}") from None
            time.sleep(retry_wait_s * net_fails)


def _env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise TrafficAuthError(f"{name} is not set (put it in .env)")
    return val


def clip_index(x: float, clip: tuple[float, float] = (0.0, 1.0)) -> float:
    return float(min(max(x, clip[0]), clip[1]))


def flow_to_index(current_speed: float, free_flow_speed: float) -> float:
    """clip(1 - current / free_flow, 0, 1)."""
    if not free_flow_speed or free_flow_speed <= 0:
        raise ValueError("free_flow_speed must be positive")
    return clip_index(1 - current_speed / free_flow_speed)


def duration_to_index(duration: float, free_flow: float) -> float:
    """clip(duration / free_flow - 1, 0, 1)."""
    if not free_flow or free_flow <= 0:
        raise ValueError("free_flow must be positive")
    return clip_index(duration / free_flow - 1)


# ---------- TomTom live ----------


def tomtom_flow(lat: float, lon: float, cfg: dict | None = None) -> dict:
    """Flow of the road segment nearest to the point."""
    t = (cfg or load_config())["tomtom"]
    q = urllib.parse.urlencode(
        {"point": f"{lat},{lon}", "unit": "KMPH", "key": _env("TOMTOM_API_KEY")}
    )
    d = _get_json(f"{t['flow_url']}?{q}", t.get("timeout_s", 30))["flowSegmentData"]
    return {
        "current_speed": d["currentSpeed"],
        "free_flow_speed": d["freeFlowSpeed"],
        "confidence": d.get("confidence"),
        "frc": d.get("frc"),
    }


def segment_usable(f: dict, max_frc: int = 5, min_conf: float = 0.5, min_free: float = 15) -> bool:
    """False for minor roads (worse than FRC5), low confidence or very low free-flow speed."""
    frc = str(f.get("frc") or "")
    frc_n = int(frc[3:]) if frc[3:].isdigit() else 0
    if frc_n > max_frc:
        return False
    if (f.get("confidence") or 0) < min_conf:
        return False
    return (f.get("free_flow_speed") or 0) >= min_free


def live_index(lat: float, lon: float, cfg: dict | None = None) -> float | None:
    """Current traffic index in 0..1, or None on a non-fatal error. Auth/quota errors raise."""
    try:
        f = tomtom_flow(lat, lon, cfg)
        if not segment_usable(f):
            return None
        return flow_to_index(f["current_speed"], f["free_flow_speed"])
    except (TrafficAuthError, TrafficQuotaError):
        raise
    except Exception as e:  # noqa: BLE001
        print(f"live_index failed at {lat:.4f},{lon:.4f}: {type(e).__name__}")
        return None


def live_indices(locations_df: pd.DataFrame, max_n: int | None = None) -> dict[str, float | None]:
    """Poll at most max_n locations, with a gap between requests. Returns location_id -> index."""
    cfg = load_config()
    n = cfg["tomtom"]["max_live_locations"] if max_n is None else max_n
    out: dict[str, float | None] = {}
    for _, row in locations_df.head(n).iterrows():
        out[row["location_id"]] = live_index(row["lat"], row["lon"], cfg)
        time.sleep(cfg["tomtom"]["request_gap_s"])
    return out


# ---------- Mapbox typical ----------


def offset_point(lat: float, lon: float, bearing_deg: float, dist_m: float) -> tuple[float, float]:
    """Point dist_m away along a bearing (flat-earth, fine for a few hundred metres)."""
    b = math.radians(bearing_deg)
    dlat = dist_m * math.cos(b) / 111_320
    dlon = dist_m * math.sin(b) / (111_320 * math.cos(math.radians(lat)))
    return lat + dlat, lon + dlon


def next_weekday(today: date, weekday: int) -> date:
    """Next date strictly after today with the given weekday (Mon=0)."""
    return today + timedelta(days=(weekday - today.weekday() - 1) % 7 + 1)


def depart_at(day: date, hour: int) -> str:
    return f"{day.isoformat()}T{hour:02d}:00"


def _cache_file(location_id: str, tag: str, data_dir: Path) -> Path:
    return data_dir / "raw" / "traffic" / "mapbox" / f"{location_id}_{tag}.json"


class RateLimiter:
    """Thread-safe global limiter: at most max_rps calls per second across all threads."""

    def __init__(self, max_rps: float, clock=time.monotonic, sleep=time.sleep):
        self.interval = 1.0 / max_rps
        self.clock, self.sleep = clock, sleep
        self.lock = threading.Lock()
        self.next_at = 0.0
        self.count = 0

    def wait(self) -> None:
        with self.lock:
            now = self.clock()
            start = max(now, self.next_at)
            self.next_at = start + self.interval
            self.count += 1
        if start > now:
            self.sleep(start - now)


_LIMITER: RateLimiter | None = None


def get_limiter(cfg: dict) -> RateLimiter:
    global _LIMITER
    if _LIMITER is None:
        m = cfg["mapbox"]
        _LIMITER = RateLimiter(m.get("max_rps") or 1 / m["request_gap_s"])
    return _LIMITER


def mapbox_routes(a: tuple, b: tuple, depart: str | None, cfg: dict, free_flow: bool = False):
    """Directions call between two (lat, lon) points. Returns the list of routes."""
    m = cfg["mapbox"]
    coords = f"{a[1]:.6f},{a[0]:.6f};{b[1]:.6f},{b[0]:.6f}"
    params = {"access_token": _env("MAPBOX_TOKEN"), "overview": "false", "alternatives": "false"}
    if depart and not free_flow:
        params["depart_at"] = depart
    base = m["free_flow_url"] if free_flow else m["directions_url"]
    get_limiter(cfg).wait()
    resp = _get_json(f"{base}/{coords}?{urllib.parse.urlencode(params)}", m.get("timeout_s", 30))
    return resp.get("routes") or []


def _raw_sample(lat: float, lon: float, day: date, hour: int, cfg: dict) -> dict:
    """One sample: east-west route, then north-south if both ends snap to one point."""
    m = cfg["mapbox"]
    half = m["route_len_m"] / 2
    for bearing in (90, 0):
        a = offset_point(lat, lon, bearing + 180, half)
        b = offset_point(lat, lon, bearing, half)
        routes = mapbox_routes(a, b, depart_at(day, hour), cfg)
        if routes and routes[0].get("distance", 0) >= m.get("min_snap_m", 100):
            r = routes[0]
            return {
                "bearing": bearing,
                "duration": r["duration"],
                "duration_typical": r.get("duration_typical"),
                "distance": r["distance"],
                "endpoints": [list(a), list(b)],
            }
    return {"bearing": None, "duration": None, "duration_typical": None, "distance": None}


def _cached(path: Path, make) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    raw = make()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw), encoding="utf-8")
    return raw


def interpolate_circular(vals: list[float | None]) -> list[float | None]:
    """Fill None gaps linearly between known neighbours, wrapping the last hour to the first."""
    n = len(vals)
    known = [i for i, v in enumerate(vals) if v is not None]
    if not known:
        return list(vals)
    out = list(vals)
    for i in range(n):
        if vals[i] is not None:
            continue
        prev = max((k for k in known if k < i), default=known[-1] - n)
        nxt = min((k for k in known if k > i), default=known[0] + n)
        w = (i - prev) / (nxt - prev)
        out[i] = vals[prev % n] * (1 - w) + vals[nxt % n] * w
    return out


def location_profile(
    loc_id: str, lat: float, lon: float, cfg: dict, data_dir: Path, today: date | None = None
) -> dict:
    """48 cached samples (resumable) plus one `driving` free-flow call.

    Returns {"weekday": [24 durations], "weekend": [24], "driving": float | None}.
    """
    today = today or date.today()
    days = {"weekday": next_weekday(today, 1), "weekend": next_weekday(today, 5)}
    out: dict = {}
    first = None
    step = cfg["mapbox"].get("sample_every_h", 1)
    for daytype in DAYTYPES:
        durs: list[float | None] = []
        for hour in range(24):
            path = _cache_file(loc_id, f"{daytype}_{hour:02d}", data_dir)
            if not path.exists() and hour % step:
                durs.append(None)  # odd hour: filled by interpolation below
                continue
            raw = _cached(path, lambda d=days[daytype], h=hour: _raw_sample(lat, lon, d, h, cfg))
            first = first or (raw if raw.get("endpoints") else None)
            durs.append(raw.get("duration_typical") or raw.get("duration"))
        out[daytype] = interpolate_circular(durs)
    out["driving"] = None
    if first:
        a, b = first["endpoints"]

        def make() -> dict:
            routes = mapbox_routes(tuple(a), tuple(b), None, cfg, free_flow=True)
            return {"duration": routes[0]["duration"] if routes else None}

        out["driving"] = _cached(_cache_file(loc_id, "driving", data_dir), make)["duration"]
    return out


def free_flow_duration(profile: dict) -> float | None:
    """Min of the 48 sample durations and the `driving` (no live traffic) duration."""
    vals = [d for k in DAYTYPES for d in profile[k] if d]
    if profile.get("driving"):
        vals.append(profile["driving"])
    return min(vals) if vals else None


def profile_to_week(
    profile: dict, free_flow: float, clip: tuple[float, float] = (0.0, 1.0)
) -> list[float]:
    """Expand weekday/weekend hourly durations to 168 indices (Mon 00h = 0)."""
    idx = {
        k: [clip_index(d / free_flow - 1, clip) if d else 0.0 for d in profile[k]] for k in DAYTYPES
    }
    return [idx["weekday" if dow < 5 else "weekend"][h] for dow in range(7) for h in range(24)]


def _location_rows(r, cfg: dict, data_dir: Path, today: date | None) -> list[tuple]:
    prof = location_profile(r.location_id, r.lat, r.lon, cfg, data_dir, today)
    ff = free_flow_duration(prof)
    if not ff:
        return fallback_rows(r.location_id)
    week = profile_to_week(prof, ff, tuple(cfg["index_clip"]))
    return [(r.location_id, h, week[h], "mapbox_typical") for h in range(168)]


def fallback_rows(location_id: str) -> list[tuple]:
    return [(location_id, h, 0.0, "fallback_synthetic") for h in range(168)]


def _write_parquet(rows: list[tuple], path: Path) -> None:
    df = pd.DataFrame(rows, columns=["location_id", "hour_of_week", "typical_index", "source"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


def build_typical(
    locations: pd.DataFrame,
    cfg: dict,
    data_dir: Path,
    today: date | None = None,
    partial_path: Path | None = None,
) -> pd.DataFrame:
    """Concurrent build. Failed locations get fallback_synthetic; auth/quota errors abort."""
    locs = list(locations.itertuples(index=False))
    total, workers = len(locs), cfg["mapbox"].get("workers", 1)
    by_id: dict[str, list[tuple]] = {}
    fatal: Exception | None = None
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_location_rows, r, cfg, data_dir, today): r for r in locs}
        for done, fut in enumerate(as_completed(futs), 1):
            r = futs[fut]
            try:
                by_id[r.location_id] = fut.result()
            except (TrafficAuthError, TrafficQuotaError) as e:
                fatal = fatal or e
                for f in futs:
                    f.cancel()
            except Exception as e:  # noqa: BLE001
                print(f"{r.location_id} failed: {type(e).__name__}", flush=True)
                by_id[r.location_id] = fallback_rows(r.location_id)
            if done % 25 == 0 or done == total:
                el = time.monotonic() - t0
                print(
                    f"done={done}/{total} requests={get_limiter(cfg).count} elapsed={el:.0f}s",
                    flush=True,
                )
            if partial_path and done % 50 == 0:
                _write_parquet([x for v in by_id.values() for x in v], partial_path)
    rows = [x for r in locs if r.location_id in by_id for x in by_id[r.location_id]]
    if fatal:
        if partial_path:
            _write_parquet(rows, partial_path)
        raise fatal
    return pd.DataFrame(rows, columns=["location_id", "hour_of_week", "typical_index", "source"])


def main(argv: list[str] | None = None) -> None:
    from dotenv import load_dotenv

    load_dotenv()
    p = argparse.ArgumentParser(prog="python -m dataset.traffic")
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("typical")
    t.add_argument("--limit", type=int, default=None)
    lv = sub.add_parser("live")
    lv.add_argument("--n", type=int, default=5)
    args = p.parse_args(argv)
    data_dir = Path(os.environ.get("DATA_DIR", "./data"))
    locs = pd.read_parquet(data_dir / "processed" / "locations.parquet")
    if args.cmd == "live":
        for k, v in live_indices(locs, args.n).items():
            print(k, v)
        return
    out = data_dir / "processed" / "traffic_typical.parquet"
    partial = out.with_name("traffic_typical.partial.parquet")
    sel = locs.head(args.limit) if args.limit else locs
    df = build_typical(sel, load_config(), data_dir, partial_path=partial)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    print(f"wrote {len(df)} rows to {out}")


if __name__ == "__main__":
    main()
