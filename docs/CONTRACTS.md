# Contracts

The source of truth for every interface between parts of the project. Change it on `main` in its own commit **before** or together with the code that implements the change, and mention it in your task note.

Version: v0.1, 2026-11-07 (sim types, forecasts table, observation layout, λ unit, run pause; decisions 0017–0025). Specs: `docs/specs/`. Every contract below has a sample in `fixtures/`, committed by the module that produces it.

## Services and ports
| Service | Port | Owner | Notes |
|---|---|---|---|
| Postgres 16 + PostGIS 3 | 5432 | M3 | `docker-compose.yml`, DB name `ppc` |
| FastAPI (`api.main:app`) | 8000 | M4 | REST under `/api`, WebSocket under `/ws` |
| Vite dev server | 5173 | M5 | |

## Environment variables
| Name | Used by | Example | Notes |
|---|---|---|---|
| `DATABASE_URL` | M1, M3, M4 | `postgresql+psycopg://ppc:ppc@localhost:5432/ppc` | |
| `DATA_DIR` | M1, M2, M4 | `./data` | |
| `API_FIXTURES` | M4 | `0` | `1` = serve every route from `fixtures/` |
| `LIVE_POLL_S` | M1 | `300` | Live collector interval |
| `AMPWHERE_ENABLED` | M1 | `0` | Must be explicitly enabled by a human, see decision 0003 |
| `PPC_FEED_URL`, `PPC_FEED_TOKEN` | M1, M3 | — | Only if PPC provides a feed. Never commit the values. |
| `VITE_API_URL` | M5 | `http://localhost:8000` | |
| `TOMTOM_API_KEY` | M1, M2 | — | Live traffic index (decision 0028). Never commit. |
| `MAPBOX_TOKEN` | M1 | — | One-off typical-traffic build (decision 0028). Never commit. |

## Make targets and entry points
`make` targets (spec 00 §4) call one entry point per module. A module provides it by adding the file; until then the target prints "not implemented yet".
| Target | Runs | Owner |
|---|---|---|
| `make seed` | `python -m stations` (`stations/__main__.py`) | M3 |
| `make data` | `python -m dataset` (`dataset/__main__.py`) | M1 |
| `make train` | `python -m model` (`model/__main__.py`) | M2 |
| `make api` | `uvicorn api.main:app --port 8000` | M4 |
| `make web` | `npm run dev` in `frontend/` | M5 |
| `make test` | `pytest` (tests in `<package>/tests/` and `tests/`), then `npm test` in `frontend/` if it exists | all |

Migrations: plain SQL files `db/migrations/NNNN_slug.sql`, applied by `make db` in filename order, each once. Applied files are recorded in table `schema_migrations(filename text primary key, applied_at timestamptz)`. Never edit an applied migration; add a new one.

## Shared enums
```
State        = CHARGING | IDLE | WAKING | STANDBY | DEEP_SLEEP | OFFLINE
TargetState  = IDLE | STANDBY | DEEP_SLEEP                     # what a policy may command
PowerClass   = AC_22 | DC_50 | DC_HPC
ContextType  = urban | retail | highway | airport | other
Confidence   = high | medium | low
Autonomy     = ACT | RECOMMEND
CommandStatus= applied | pending | approved | rejected | expired
PolicyName   = always_on | naive_quiet_hours | cost_rule | rl_ppo
LiveStatus   = CHARGING | AVAILABLE | OFFLINE | UNKNOWN
Signal       = ppcblue_route_intent | maps_ev_count | maps_ice_count | maps_traffic_index
```

## Config files
| File | Owner | Keys |
|---|---|---|
| `config/power_profiles.yaml` | M3 | per `PowerClass`: `idle_w, standby_w, deep_sleep_w, standby_wake_s, deep_sleep_wake_s`; top-level `wake_extra_wh` |
| `config/signals.yaml` | M1 | `p_app, intent_lead_min: [10,40], eta_sd_min, false_positive_rate, ev_tag_rate, w_ice, background_ev, ice_base, context_multiplier{ContextType: float}` |
| `config/live.yaml` | M1 | `poll_s, min_request_gap_s, request_timeout_s, backoff{initial_s, factor, max_s, max_retries}, max_consecutive_failures, status_map{raw_word: LiveStatus}, ampwhere{snapshot_url, delta_url, resync_s, ppc_provider_id, user_agent}` |
| `config/traffic.yaml` | M1 | `tomtom{flow_url, zoom, poll_s}, mapbox{directions_url, route_len_m, request_gap_s}, index_clip: [0, 1]` |
| `config/dataset.yaml` | M1 | Dundee item IDs and filenames, Dundee coordinates, date range, `split_date`, `min_session_s`, `max_session_s`, `max_lanes`, `download_delay_s` |
| `config/sim.yaml` | M4 | `slot_s, lambda_grid, default_lambda, naive_quiet_hours_per_day, min_awake_per_location, recommendation_ttl_slots, eval_days` |

## Data schemas / shared types

### DB tables (M3 owns; full DDL in spec 03 §3)
`locations`, `charge_points`, `source_refs`, `cp_state`, `state_events`, `commands`, `settings`, `live_status` (written by M1).

### Station files (M3, T4, under `data/processed/`, decision 0031; mirror the DB tables until T5 lands)
| File | Columns |
|---|---|
| `locations.parquet` | `location_id str, name, operator, partner?, address?, county_code, lat float, lon float, context_type ContextType, primary_source, confidence Confidence, cp_count_estimated bool` |
| `charge_points.parquet` | `cp_id str (<location_id>-<n>), location_id, power_class PowerClass, max_kw float, connectors list[str], current_type AC|DC, cp_count_estimated bool`. One row per physical unit. |
| `source_refs.parquet` | `source (ampwhere|osm|ocm), source_id, location_id, cp_id?`. AmpWhere connector rows use `source_id = "<uid>|<charger id>"`, which matches live JSONL `source_location_id|source_cp_id`. |
| `other_chargers.parquet` | `uid, provider_id int, operator, name, lat, lon, county_code, n_connectors int, max_kw float, power_class` (non-PPC operators, context only) |

### Parquet files (M1 owns, under `data/processed/`)
| File | Columns |
|---|---|
| `sessions.parquet` | `session_id str, cp_id str, location_id str, arrival_ts ts[UTC], departure_ts ts[UTC], energy_kwh float, donor_charger_id str, split {train,test}` |
| `signals.parquet` | `ts ts[UTC], location_id str, signal Signal, value float, eta_ts ts[UTC]?, session_id str? (eval only, never a feature)` |
| `context.parquet` | `ts_hour ts[UTC], county_code str, temp_c float, precip_mm float, wind_kmh float, is_holiday bool` |
| `forecasts.parquet` (M2 writes, M4 reads; decision 0021) | `ts ts[UTC] (slot start), cp_id str, p_arrival_15 float, p_arrival_30 float, p_arrival_60 float, expected_arrivals_60 float` |
| `donor_sessions.parquet` | `donor_session_id, donor_charger_id, arrival_ts, departure_ts, energy_kwh, temp_c, precip_mm, wind_kmh, is_holiday_gb, split` |
| `donor_chargers.parquet` | `donor_charger_id (<cp>#<lane>), site, power_class, sessions_per_day, util_quantile`, plus `base_cp_id, lane, n_sessions_train, active_days_train, first_ts, last_ts, util_basis {train,test}` (decision 0027) |
| `donor_occupancy.parquet` | `donor_charger_id, hour_of_week int 0..167 (local, Mon 00h = 0), occupancy_share float` |
| `data/seed/county_ev_stats.csv` | `county_code, county_name, ev_count, population, ev_per_1000`, plus `ev_per_1000_raw, pool_group` (B and IF share a pooled `ev_per_1000`, decision 0030) |
| `data/processed/traffic_typical.parquet` | `location_id str, hour_of_week int 0..167 (local, Mon 00h = 0), typical_index float 0..1, source str` (decision 0028) |
| `data/raw/live/ampwhere_snapshot_latest.json.gz` (gitignored, decision 0029) | the latest raw AmpWhere snapshot, overwritten at every resync; read by the T4 importer |
| `data/raw/live/statuses-YYYYMMDD.jsonl` (gitignored, decision 0016) | one JSON object per status: `ts ISO UTC, source, source_location_id, source_cp_id?, connector?, raw_status, status LiveStatus, cp_id? (null until matched), name?, lat?, lon?, operator?, provider_id? int, evse_id?, power_kw?, current_type? AC|DC, source_updated_at? ISO`. After an AmpWhere snapshot: one row per charger of every provider; after a delta: rows only for changed locations (decision 0016) |

### Python interfaces (M2 ↔ M4)
Module: `sim/types.py` (M4 owns, decision 0017). `model/` imports these types and the enums from there.
```python
@dataclass
class CpView:            # one charge point, as a policy sees it
    cp_id: str; location_id: str; power_class: PowerClass; connectors: list[str]
    state: State; seconds_in_state: int

@dataclass
class SimState:
    t: datetime                       # slot start, tz-aware Europe/Bucharest
    cps: list[CpView]
    signals: pd.DataFrame             # signals with t - 60 min < ts <= t (signals.parquet schema; decision 0021)
    context: dict[str, dict]          # county_code -> weather/holiday at t
    forecast: pd.DataFrame | None     # ForecastOutput, filled by engine if a forecaster is set

@dataclass
class Command:
    cp_id: str; target_state: TargetState; reason: str

class Policy(Protocol):
    name: PolicyName
    def decide(self, state: SimState) -> list[Command]: ...

class Forecaster(Protocol):
    def predict(self, state: SimState) -> pd.DataFrame: ...
    # ForecastOutput columns: cp_id, p_arrival_15, p_arrival_30, p_arrival_60, expected_arrivals_60
```
Policies and forecasters receive their history (training sessions, models) in their constructor. `SimState` holds only what is visible at `t`. For training and evaluation the engine uses `sim.forecast.PrecomputedForecaster`, a lookup into `forecasts.parquet`; online `predict()` is for live mode (decision 0021).

**λ** (`lambda_wait`, `lambda`, `lambda_grid`) is in **kWh per minute of added wait** (decision 0020).

`sim.gym_env.ChargePointEnv(split, lambda_wait, sibling_policy=None, seed=0, days=None, data=None)` is a Gymnasium environment (decision 0023):
- `split`: `train` | `test`. `days`: optional list of dates that restricts episode sampling (for validation hold-outs). `data`: optional `sim.data.SimData`; default loads `data/processed/`.
- `sibling_policy=None` means `naive_quiet_hours` until `cost_rule` exists, then `cost_rule`.
- Action `Discrete(3)` = `[IDLE, STANDBY, DEEP_SLEEP]`. Invalid actions keep the current state. `env.action_masks() -> np.ndarray[bool]` for MaskablePPO.
- Reward per slot: `−(overhead_kwh + λ · wait_min_caused) / (idle_w × slot_s / 3.6e6)`.

Observation: `sim.obs.build_obs(state: SimState, cp_ids: list[str]) -> np.ndarray[float32]` of shape `(len(cp_ids), 31)`, used by both the env and `rl_ppo` (decision 0017). Order:
| Index | Feature | Scaling |
|---|---|---|
| 0–2 | `p_arrival_15`, `p_arrival_30`, `p_arrival_60` | as is |
| 3 | `expected_arrivals_60` | `log1p` |
| 4–6 | route intents at the location with `eta` in `(t, t+15]`, `(t+15, t+30]`, `(t+30, t+60]` min | `min(n, 5) / 5` |
| 7 | weighted EV flow: `maps_ev_count + w_ice × maps_ice_count`, last 15 min | `log1p` |
| 8 | `maps_traffic_index`, latest | as is |
| 9–10 | hour of day sin, cos (local time, minute precision) | |
| 11–17 | weekday one-hot, Monday first | |
| 18 | `is_holiday`: 1 if any county in `state.context` is a holiday (RO public holidays are national) | 0/1 |
| 19–24 | state one-hot: CHARGING, IDLE, WAKING, STANDBY, DEEP_SLEEP, OFFLINE | |
| 25 | seconds in state | `min(s / 3600, 1)` |
| 26 | siblings IDLE / siblings total (0 if no siblings) | |
| 27 | siblings total | `min(n / 10, 1)` |
| 28–30 | `power_class` one-hot: AC_22, DC_50, DC_HPC | |

### `artifacts/eval_results.json` (M4 writes, M5 reads)
```json
{
  "generated_at": "2026-11-07T12:00:00Z",
  "test_days": ["2024-03-12", "..."],
  "results": [
    {"policy": "rl_ppo", "lambda": 0.1, "overhead_kwh": 0, "saved_kwh": 0, "saved_pct": 0,
     "wait_min_total": 0, "wait_min_mean": 0, "wait_min_p95": 0, "sessions": 0, "waited_sessions": 0, "sleep_share": 0}
  ]
}
```
The curve is built from the rows that share a `policy` and differ in `lambda`. Baselines have `lambda: null`.

## API (M4 owns, base `http://localhost:8000`)

### Shared payloads
```ts
type Location = { location_id: string; name: string; partner: string|null; address: string|null;
  county_code: string; lat: number; lon: number; context_type: ContextType;
  primary_source: string; confidence: Confidence; charge_points: ChargePoint[] };
type ChargePoint = { cp_id: string; power_class: PowerClass; max_kw: number|null;
  connectors: string[]; current_type: "AC"|"DC"; state?: State };
type Kpis = { overhead_kwh: number; saved_kwh: number; saved_pct: number;
  wait_min_total: number; wait_min_mean: number; wait_min_p95: number;
  sessions: number; waited_sessions: number; sleep_share: number };
// saved_pct is a percentage (0–100) of always_on's overhead in the same run; sleep_share is a fraction (0–1) of all CP time.
type Recommendation = { id: number; ts: string; cp_id: string; policy: PolicyName;
  from_state: State; target_state: TargetState; reason: string; status: CommandStatus; expires_at: string };
```

### GET /api/health
Response 200: `{"ok": true}`

### GET /api/stations?bbox=minLon,minLat,maxLon,maxLat&county=B
Response 200: `Location[]`. `state` is included only when a live run exists.

### GET /api/stations/{location_id}
Response 200: `Location`. Errors: 404.

### GET /api/policies
Response 200: `[{"name": "rl_ppo", "lambdas": [0.1], "available": true}, ...]`

### GET /api/settings/autonomy · PUT /api/settings/autonomy
Body / response: `{"mode": "ACT" | "RECOMMEND"}`

### POST /api/runs
Request: `{"mode": "replay"|"live", "date": "2024-03-12"|null, "policies": PolicyName[], "focus_policy": PolicyName, "lambda": 0.1, "speed": 60}`
Response 201: `{"run_id": "uuid"}`. Errors: 400 (unknown policy, date not a test day).

### GET /api/runs/{run_id}
Response 200: `{"run_id", "mode", "status": "running"|"paused"|"done", "paused_reason": "user"|"recommendations"|null, "sim_time": "ISO", "slot": 0, "speed": 60, "focus_policy", "kpis": {PolicyName: Kpis}}`

In RECOMMEND mode only `focus_policy`'s commands become recommendations; other policies act. A replay pauses itself with `paused_reason: "recommendations"` when new pending recommendations appear; `play` resumes it. Recommendations expire after `recommendation_ttl_slots` of sim time. Approved commands apply at the start of the next slot (decision 0025).

### POST /api/runs/{run_id}/control
Request: `{"action": "play"|"pause"|"speed"|"seek"|"focus", "value": number|string|null}`. Response 200: same body as GET run.

### WS /ws/runs/{run_id}
The server sends one message per engine step:
```json
{"type": "tick", "run_id": "uuid", "sim_time": "2024-03-12T03:05:00+02:00", "slot": 37,
 "kpis": {"always_on": {}, "naive_quiet_hours": {}, "cost_rule": {}, "rl_ppo": {}},
 "focus_policy": "rl_ppo",
 "changes": [{"cp_id": "L00012-1", "state": "STANDBY"}],
 "events": [{"type": "arrival", "cp_id": "L00012-2", "ts": "ISO", "wait_min": 3.0, "moved_from": null, "queued": false},
            {"type": "departure", "cp_id": "L00012-1", "ts": "ISO"}],
 "recommendations": [/* Recommendation, only new or changed ones */]}
```
The first message after connecting is `{"type": "snapshot", ...}`: the same as a tick, but `changes` lists every CP. The last message is `{"type": "done", "kpis": {...}}`.
Whenever the run's status, speed or focus changes, the server sends `{"type": "status", "run_id", "status", "paused_reason", "speed", "focus_policy", "slot", "sim_time"}`. After a `seek` or `focus`, it sends a new `snapshot`.

### GET /api/runs/{run_id}/cp/{cp_id}/timeline
Response 200:
```json
{"cp_id": "L00012-1", "policy": "rl_ppo",
 "states": [{"from": "ISO", "to": "ISO", "state": "IDLE"}],
 "arrivals": [{"ts": "ISO", "wait_min": 0}],
 "intents": [{"ts": "ISO", "eta_ts": "ISO"}],
 "forecast": [{"ts": "ISO", "p_arrival_30": 0.12}],
 "commands": [{"ts": "ISO", "target_state": "STANDBY", "reason": "p30=0.02", "status": "applied"}]}
```

### GET /api/recommendations?run_id=&status=pending
Response 200: `Recommendation[]`

### POST /api/recommendations/{id}
Request: `{"decision": "approve"|"reject"}`. Response 200: `Recommendation`. Errors: 404; 409 if the recommendation has expired or was already decided.

### GET /api/eval
Response 200: the contents of `artifacts/eval_results.json`. Errors: 404 if the file doesn't exist yet.
