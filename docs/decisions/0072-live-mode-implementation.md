# 0072: Live mode implementation details

- Date: 2026-11-08
- Who: vlad + vlad-claude
- Status: accepted
- Supersedes: — (refines 0070)
- Affects: CONTRACTS (env vars), api/live_mode.py, sim/live.py, api/engine_mode.py, model/live_signals.py

## Context
Building the shadow live mode (0070) forced choices that 0070 left open.

## Decision
- **Source:** the collector's JSONL only, not the DB `live_status` table.
  - Rows match `source_refs` by `source_location_id|source_cp_id`, and the latest row per connector wins.
  - Connectors roll up to a CP: any CHARGING → CHARGING, else any AVAILABLE → IDLE, else all OFFLINE → OFFLINE, else UNKNOWN → IDLE.
  - Only CPs with an observed status are in a live run. On the 2026-11-07 file that is all 671 CPs, since every PPC connector row matched.
- **Events:** AVAILABLE/UNKNOWN → CHARGING is an arrival and CHARGING → AVAILABLE is a departure. Changes to and from OFFLINE, including OFFLINE → CHARGING, are state changes, not arrivals. A change is timed by `source_updated_at`, kept between the previous and current poll; late events move to the start of the next slot.
- **Timing:** a slot is stepped once its statuses are in, 60 s after it ends (`LIVE_LAG_S`). The first tick is the **last complete slot**, not the current one as 0070 said. A run ends at local midnight.
- **Engine reuse:** `LiveEngine(Engine)` feeds observed events into the Engine's arrival queue. An arrival on a sleeping CP waits that state's wake time (10 s / 180 s). Energy, waits, KPIs, ACT/RECOMMEND and TTL all reuse the Engine's own code. The forecaster runs once per slot on the observed states, so it never sees its own policy's sleep states.
- **Forecaster:** `GRUForecaster.from_data(artifacts/f2_gru.pt, sessions=None)`, with arrivals inferred from CHARGING transitions. If it can't load, live falls back to F1, then to a constant stub; the API logs which one it used.
- **Signals:**
  - Intents are drawn per slot at `p_app × F0 rate × 5/60` with relative false positives, via the new `intent_rate` argument of `LiveSignalSampler.sample`. The old per-call behaviour gave about 12× too many intents per hour when called every 5 minutes, and stays the default for other callers.
  - Traffic is the Mapbox typical index. Weather context is empty for today.
- **Demo clock:** `LIVE_CLOCK_START` (ISO) and `LIVE_CLOCK_SPEED` replay a collected day at a chosen speed. Without them the clock is real time.
- **`/api/stations` state** still falls back to the newest replay run when no live run exists, so the replay map keeps working (CONTRACTS said "only when a live run exists").
- `naive_quiet_hours` is allowed live (it only uses training sessions); `oracle` is not.

## Consequences
- **First live shadow numbers:** replayed clock from 2026-11-07 20:50 to 21:40, 34 arrivals:

  | Policy | Saved | Waited sessions | Total wait |
  |---|---|---|---|
  | agent | 44.9% | 11 | 18.8 min |
  | always-on | 0% | 0 | 0 |

  Waits are higher than in replay because F2's history starts empty at run start and the live intents are mocked from F0 rates. These numbers are labelled as shadow estimates.
- A real-time run returns 400 when the newest status is older than 30 minutes. The collector stopped on 2026-11-07 around 21:30 UTC because of DNS errors, and must be running for live demos.
