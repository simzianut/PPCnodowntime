# Contracts

The source of truth for every interface between parts of the project. Change it on `main` in its own commit **before** or together with the code that implements the change, and mention it in your task note.

Version: v0.4, 2026-11-07 (per-CP power from the capacity table, RON KPIs, run history in the DB; decision 0049). Specs: `docs/specs/`. Every contract below has a sample in `fixtures/`, committed by the module that produces it.

## Services and ports
| Service | Port | Owner | Notes |
|---|---|---|---|
| Postgres 16 + PostGIS 3 | 5432 | M3 | compose service `ppc-db`, DB name `ppc`. Locally bound to `127.0.0.1:5432`; not published on the server (decision 0066) |
| FastAPI (`api.main:app`) | 8000 | M4 | REST under `/api`, WebSocket under `/ws`. Compose service `ppc-api`, 1 uvicorn worker; not published, reached through `ppc-web` |
| nginx (`ppc-web`) | 80 in the container; host `HTTP_PORT` (8080) locally | deploy | The only entry point: built SPA at `/`, proxies `/api` and `/ws` to `ppc-api:8000`. On the server Traefik routes to it over `TRAEFIK_NETWORK` (decision 0066) |
| Vite dev server | 5173 | M5 | |

Compose service names carry a `ppc-` prefix (`ppc-db`, `ppc-migrate`, `ppc-seed`, `ppc-api`, `ppc-web`, `ppc-collector` under profile `live`) because they are DNS names on Traefik's shared network. Don't rename them, and don't set `container_name:`. A new service is `ppc-<name>`, added to both compose files and this table in one commit (decision 0077).

Frontend → backend: only via `API_BASE` (`frontend/src/api/client.ts`) with `/api/...` and `/ws/...` paths. `VITE_API_URL` is empty in the Docker build (same origin through `ppc-web`) and defaults to `http://localhost:8000` only for the Vite dev server. No other host, port or service name in frontend code.

## Environment variables
| Name | Used by | Example | Notes |
|---|---|---|---|
| `DATABASE_URL` | M1, M3, M4 | `postgresql+psycopg://ppc:ppc@localhost:5432/ppc` | |
| `DATA_DIR` | M1, M2, M4 | `./data` | |
| `API_FIXTURES` | M4 | `0` | `1` = serve every route from `fixtures/`. `0` = in-memory replay runs on `sim.Engine` (decision 0047) |
| `LIVE_POLL_S` | M1 | `300` | Live collector interval |
| `AMPWHERE_ENABLED` | M1 | `0` | Must be explicitly enabled by a human, see decision 0003 |
| `PPC_FEED_URL`, `PPC_FEED_TOKEN` | M1, M3 | — | Only if PPC provides a feed. Never commit the values. |
| `VITE_API_URL` | M5 | `http://localhost:8000` | Build time. Unset = `http://localhost:8000` (dev). Empty string = same origin (`/api`, `ws(s)://<page host>/ws`), used by the `ppc-web` image (decision 0066) |
| `TOMTOM_API_KEY` | M1, M2 | — | Live traffic index (decision 0028). Never commit. |
| `MAPBOX_TOKEN` | M1 | — | One-off typical-traffic build (decision 0028). Never commit. |
| `GPU_ID` | M2 training (Docker) | `1` | Host GPU index (as in `nvidia-smi`) the training container may see. Default `0`. Inside the container it is always `cuda:0` (decision 0051) |
| `RL_AGENT` | M4 API, evaluator | `auto` | Which agent serves `rl_ppo`: `gru` (cost rule driven by F2 GRU forecasts, decision 0069), `ppo`, `maskppo`, `ddqn`, or `auto` = gru if `artifacts/f2_gru.pt` exists, else ppo |
| `LIVE_DIR` | M4 API | `./data/raw/live` | Where live runs read the collector's `statuses-YYYYMMDD.jsonl` (default `$DATA_DIR/raw/live`, decision 0072) |
| `LIVE_LAG_S` | M4 API | `60` | Seconds after a slot ends before a live run steps it |
| `LIVE_CLOCK_START`, `LIVE_CLOCK_SPEED` | M4 API | `2026-11-07T21:00:00+02:00`, `60` | Demo only: live runs replay collected statuses from that time at that speed. Unset = real time |
| `LIVE_F2_MODEL`, `LIVE_F0_RATES` | M4 API | `artifacts/f2_gru_live.pt` if present else `artifacts/f2_gru.pt`, `artifacts/f0_rates.parquet` | Override the live forecaster and live-signal rate files. `f2_gru_live.pt` is the honest live model (no mock-signal features, decision 0084) |
| `LIVE_SIGNALS` | M4 API | `mock` | Live runs: `mock` samples PPC Blue intents and Maps counts from the F0 rates (decision 0070); `off` feeds no signals, so the policy's intent override never fires on mocked data (decision 0084). Reported as `signals` in the run info |
| `LIVE_F2_CALIBRATION` | M4 API | `artifacts/f2_live_calibration.json` | Affine hazard calibration fitted on real AmpWhere arrivals (`python -m model.live_calibration`, decision 0084). Applied only when its `model` field equals the live model's file name |
| `RUNS_DIR` | M2 training | `./runs` | Where training runs are written. Gitignored |
| `POSTGRES_PASSWORD` | compose | `ppc` | Password for `ppc-db`; the compose `DATABASE_URL` is built from it. Must be a real secret on the server (decision 0066). Never commit |
| `HTTP_PORT` | compose | `8080` | Host port of `ppc-web` locally. Not used on the server |
| `TRAEFIK_NETWORK` | compose (server) | `traefik` | Existing external Docker network of the server's Traefik; becomes the stack's default network |
| `PPC_HOST` | compose (server) | `ppc.example.com` | Host rule of the Traefik router on `ppc-web` |
| `TRAEFIK_ENTRYPOINT` | compose (server) | `websecure` | Traefik entrypoint for the router |
| `PPC_TRAEFIK_LABELS` | compose (server) | `true` | `false` = `ppc-web` carries `traefik.enable=false`, for routing through a route in Traefik's file provider (`dynamic.yaml`) instead of Docker labels; the file route's service URL is `http://ppcnodowntime-ppc-web-1:80` (decision 0088) |
| `COMPOSE_PROJECT_NAME` | compose (server) | `ppcnodowntime` | Fixes container names (`ppcnodowntime-ppc-web-1`) whatever the folder is called |
| `COMPOSE_FILE`, `COMPOSE_PROFILES` | compose | `docker-compose.yml:docker-compose.server.yml`, `live` | Set in the server `.env` so plain `docker compose up -d` uses the server override; `live` adds `ppc-collector` |
| `PPC_AUTH` | M4 API | `on` | `on` = every `/api` route (except `/api/health` and `/api/auth/*`) and the `/ws` handshake need the access cookie. Default `off` in code (host dev); compose sets `on` (decision 0087) |
| `PPC_ACCESS_KEY` | M4 API | — | The access key. Setting it also turns auth on. Unset with `PPC_AUTH=on`: `ppc-api` generates one at start (`secrets.token_urlsafe(32)`) and logs it once (`docker compose logs ppc-api`); it changes on every restart. Never commit |
| `API_CPUS`, `API_MEM_LIMIT` | compose | `4`, `8g` | CPU and memory caps of the API-image services (`ppc-api`, `ppc-seed`, `ppc-collector`). `API_CPUS` must be a whole number: it also sets `OMP_NUM_THREADS`/`MKL_NUM_THREADS` (decision 0087) |
| `DB_CPUS`, `DB_MEM_LIMIT`, `WEB_CPUS`, `WEB_MEM_LIMIT` | compose | `1`, `1g`, `0.5`, `256m` | Caps of `ppc-db` and `ppc-web` |

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

## Training runs (M2, decision 0051)
- **Entry point:** `python -m model.experiments <command>`
  - `run <config.yaml> [--seed N] [--set key.path=value ...] [--overwrite]`: one training run.
  - `suite <suite.yaml>`: several runs (e.g. ablation, seeds), plus one summary table.
  - `promote <runs/run_id> [--as <file>]`: copies that run's model into `artifacts/` under the existing artifact names (`--as f2_gru_live.pt` for the live model). Never automatic.
  - `ensemble <runs/a> <runs/b> ... --name <name>`: averages the hazard logits of F2 runs that share features and normalisation, calibrates on the shared val split and writes a new run dir (`runs/<name>-<sha8>-s0/`, checkpoint key `members`) that `promote` accepts (decision 0084).
- **Docker:** `docker/train.Dockerfile`, `docker-compose.train.yml`.
  - Example: `GPU_ID=1 docker compose -f docker-compose.train.yml run --rm train run config/train/f2_gru.yaml --seed 0`
  - Make: `make train-docker CONFIG=config/train/f2_gru.yaml SEED=0 GPU_ID=1`
  - Mounts: `./data` → `/app/data` (read-only), `./runs` → `/app/runs`, `./artifacts` → `/app/artifacts`.
- **Configs:** `config/train/*.yaml` (one per model) and `config/train/suites/*.yaml`. Each has `name`, `kind` (`forecaster` | `rl` | `bound`), `model`, `seed`, `device` (`auto` | `cpu` | `cuda`), `data` (file names under `DATA_DIR/processed`, incl. `signals`), and model-specific `params`.
- **Run directory** `runs/<run_id>/`, where `run_id` = `<name>-<first 8 hex of sha256(resolved config)>-s<seed>`:
  | File | Content |
  |---|---|
  | `config.yaml` | Fully resolved config |
  | `manifest.json` | `run_id, config_sha256, seed, deterministic, git_sha, git_dirty, data{path: {sha256, bytes}}, packages{name: version}, python, torch, cuda, cudnn, gpu_name, driver, host, started_at, finished_at, duration_s, status` |
  | `metrics.json` | Model metrics (forecasters: per horizon and power_class; RL: validation episodes) |
  | `model/` | Weights in the model's native format |
  | `forecasts.parquet` | Forecasters only. Same schema as `data/processed/forecasts.parquet` |
  | `train.log` | Full log |
  | `checkpoints/` | RL only: best and last |
- The same config, seed, data and image must give the same `metrics.json` on the same GPU model.

## Shared enums
```
State        = CHARGING | IDLE | WAKING | STANDBY | DEEP_SLEEP | OFFLINE
TargetState  = IDLE | STANDBY | DEEP_SLEEP                     # what a policy may command
PowerClass   = AC_22 | DC_50 | DC_HPC
ContextType  = urban | retail | highway | airport | other
Confidence   = high | medium | low
Autonomy     = ACT | RECOMMEND
CommandStatus= applied | pending | approved | rejected | expired
PolicyName   = always_on | naive_quiet_hours | cost_rule | rl_ppo | rl_maskppo | rl_ddqn | oracle
               # rl_ppo is the agent RL_AGENT picks: F2 GRU + cost rule by default (decisions 0061, 0069); oracle is replay-only
LiveStatus   = CHARGING | AVAILABLE | OFFLINE | UNKNOWN
Signal       = ppcblue_route_intent | maps_ev_count | maps_ice_count | maps_traffic_index
```

## Config files
| File | Owner | Keys |
|---|---|---|
| `config/power_profiles.yaml` | M3 | per `PowerClass`: `idle_w, standby_w, deep_sleep_w, standby_wake_s, deep_sleep_wake_s`; top-level `wake_extra_wh`. Per-CP watts come from `stations.store.get_power_profile(power_class, max_kw)` (capacity table, decision 0048); the sim uses that (decision 0049) |
| `config/signals.yaml` | M1 | `p_app, intent_lead_min: [10,40], eta_sd_min, false_positive_rate, ev_tag_rate, w_ice, background_ev, ice_base, rain_boost, traffic_noise_sd, seed, context_multiplier{ContextType: float}` (0039) |
| `config/live.yaml` | M1 | `poll_s, min_request_gap_s, request_timeout_s, backoff{initial_s, factor, max_s, max_retries}, max_consecutive_failures, status_map{raw_word: LiveStatus}, ampwhere{snapshot_url, delta_url, resync_s, ppc_provider_id, user_agent}` |
| `config/traffic.yaml` | M1 | `tomtom{flow_url, zoom, poll_s, max_live_locations, request_gap_s, timeout_s}, mapbox{directions_url, free_flow_url, route_len_m, min_snap_m, request_gap_s, timeout_s, workers, max_rps, sample_every_h}, index_clip: [0, 1]` (0043) |
| `config/dataset.yaml` | M1 | Dundee item IDs and filenames, Dundee coordinates, date range, `split_date`, `min_session_s`, `max_session_s`, `max_lanes`, `download_delay_s` |
| `config/sim.yaml` | M4 | `slot_s, lambda_grid, default_lambda, naive_quiet_hours_per_day, min_awake_per_location, recommendation_ttl_slots, eval_days` |

## Data schemas / shared types

### DB tables (M3 owns; full DDL in spec 03 §3 and `db/migrations/0001_stations_schema.sql`, decision 0048)
`locations`, `charge_points`, `source_refs`, `cp_state`, `state_events`, `commands` (includes `expires_at timestamptz`), `settings`, `live_status` (includes nullable `source_location_id, source_cp_id, connector`, written by M1). Accessed via `stations.store`.

### Station files (M3, T4, under `data/processed/`, decision 0033; mirror the DB tables until T5 lands)
| File | Columns |
|---|---|
| `locations.parquet` | `location_id str, name, operator, partner?, address?, county_code, lat float, lon float, context_type ContextType, primary_source, confidence Confidence, cp_count_estimated bool` |
| `charge_points.parquet` | `cp_id str (<location_id>-<n>), location_id, power_class PowerClass, max_kw float, connectors list[str], current_type AC|DC, cp_count_estimated bool`. One row per physical unit. |
| `source_refs.parquet` | `source (ampwhere|osm|ocm), source_id, location_id, cp_id?`. AmpWhere connector rows use `source_id = "<uid>|<charger id>"`, which matches live JSONL `source_location_id|source_cp_id`. |
| `other_chargers.parquet` | `uid, provider_id int, operator, name, lat, lon, county_code, n_connectors int, max_kw float, power_class` (non-PPC operators, context only) |

The sim and the API read stations through `sim.stations_seed.load_stations()` (decision 0047), in this order: `locations.parquet` + `charge_points.parquet`; else the CSV export `ppc_locations.csv` + `ppc_charge_points.csv` (same IDs, `connectors` joined with `+`); else the committed open seed `data/seed/stations_open.csv`. All of these except the seed are AmpWhere-derived and stay local.

### Parquet files (M1 owns, under `data/processed/`)
| File | Columns |
|---|---|
| `sessions.parquet` | `session_id str, cp_id str, location_id str, arrival_ts ts[UTC], departure_ts ts[UTC], energy_kwh float, donor_charger_id str, split {train,test}` |
| `signals.parquet` | `ts ts[UTC], location_id str, signal Signal, value float, eta_ts ts[UTC]?, session_id str? (eval only, never a feature)`. Resolution (decision 0039): intents are events; `maps_traffic_index` and `maps_ice_count` are hourly (use the latest value at or before t); `maps_ev_count` is 5-min, non-zero rows only (a missing row means 0) |
| `cp_donor_map.parquet` | `cp_id, power_class, demand, demand_quantile, donor_charger_id, donor_fallback bool, second_donor_id, target_rate, donor_rate, ratio, capped bool, active_from ts[UTC], active_to ts[UTC]` (decision 0038) |
| `cp_active_days.parquet` | `cp_id, date (local), active bool`. On inactive days the donor did not exist: treat them as unknown and exclude them from training and evaluation (decision 0038) |
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
    max_kw: float | None = None     # for per-CP power via stations.store.get_power_profile (decision 0049)

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
     "overhead_ron": 0, "saved_ron": 0,
     "wait_min_total": 0, "wait_min_mean": 0, "wait_min_p95": 0, "sessions": 0, "waited_sessions": 0, "sleep_share": 0}
  ]
}
```
The curve is built from the rows that share a `policy` and differ in `lambda`. Baselines have `lambda: null`.

## API (M4 owns, base `http://localhost:8000`)

Request validation errors return **400** `{"detail": ...}` on every route (not FastAPI's default 422).

### Shared payloads
```ts
type Location = { location_id: string; name: string; partner: string|null; address: string|null;
  county_code: string; lat: number; lon: number; context_type: ContextType;
  primary_source: string; confidence: Confidence; charge_points: ChargePoint[] };
type ChargePoint = { cp_id: string; power_class: PowerClass; max_kw: number|null;
  connectors: string[]; current_type: "AC"|"DC"; state?: State };
type Kpis = { overhead_kwh: number; saved_kwh: number; saved_pct: number;
  overhead_ron: number; saved_ron: number;
  wait_min_total: number; wait_min_mean: number; wait_min_p95: number;
  sessions: number; waited_sessions: number; sleep_share: number };
type StationRunSummary = { location_id: string; current_kw: number|null;
  expected_wait_min: number|null; anomaly_score: number|null };
// saved_pct is a percentage (0–100) of always_on's overhead in the same run; sleep_share is a fraction (0–1) of all CP time.
// *_ron = the matching *_kwh × the net B2B tariff from stations.store.get_energy_tariffs() (1.15 RON/kWh, decision 0049).
type Recommendation = { id: number; ts: string; cp_id: string; policy: PolicyName;
  from_state: State; target_state: TargetState; reason: string; status: CommandStatus; expires_at: string };
```

### GET /api/health
Response 200: `{"ok": true}`

### Access key (decision 0087)
When auth is on (`PPC_AUTH=on` or `PPC_ACCESS_KEY` set), any other `/api` route without a valid cookie returns **401** `{"detail": "access key required"}`, and a `/ws` handshake without one is refused (close code 4401, before accept). `/api/health` and `/api/auth/*` stay open.
- `GET /api/auth/status` → 200 `{"required": bool, "authenticated": bool}`
- `POST /api/auth/login` `{"key": "..."}` → 200 `{"authenticated": true}` and sets cookie `ppc_access` (HttpOnly, SameSite=Strict, Path=/, Max-Age 30 days, Secure when `X-Forwarded-Proto: https`). The cookie holds an HMAC derived from the key, never the key. Wrong key → 401. Auth off → 200 with no cookie.
- `POST /api/auth/logout` → 200, clears the cookie.
The key never goes in a URL. A new key (restart without `PPC_ACCESS_KEY`) invalidates all cookies.

### GET /api/stations?bbox=minLon,minLat,maxLon,maxLat&county=B
Response 200: `Location[]`. `state` is included only when a live run exists.

### GET /api/stations/{location_id}
Response 200: `Location`. Errors: 404.

### GET /api/policies
Response 200: `[{"name": "rl_ppo", "lambdas": [0.1], "available": true}, ...]`
Every PolicyName is listed. A policy whose artifact is missing has `available: false` (rl_maskppo needs `artifacts/maskppo_policy.zip`, rl_ddqn needs `artifacts/ddqn_policy.zip`). `oracle` is available in replay only; a live run that requests it gets 400 (decision 0061).

### GET /api/settings/autonomy · PUT /api/settings/autonomy
Body / response: `{"mode": "ACT" | "RECOMMEND"}`. Stored through `stations.store` (the `settings` table when the DB is up).

Run history (decision 0049): when the DB is reachable, each engine run writes its focus policy's commands (at their final status) and state changes to `commands` and `state_events` with its `run_id`, through `stations.store.record_run_history`. Without a DB nothing is written.

### POST /api/runs
Request: `{"mode": "replay"|"live", "date": "2024-03-12"|null, "policies": PolicyName[], "focus_policy": PolicyName, "lambda": 0.1, "speed": 60}`
Response 201: `{"run_id": "uuid"}`. Errors: 400 (unknown or unavailable policy, date not a replayable day, `oracle` in a live run or on a collected day, live without a running collector feed).
`date: null` in a replay means the first day of `GET /api/eval` `test_days`.
Replay `date` must be a test day covered by `data/processed/forecasts.parquet` when that file exists (the 60-day F2 window, decision 0069), or a **collected day** (decision 0090).
A collected day is a Europe/Bucharest local day with at least one whole 5-minute slot between the first and last poll in `LIVE_DIR/statuses-*.jsonl`. Its replay runs the live engine and reader on those statuses: the window is slot `ceil(first poll)` to slot `floor(last poll)`, the run starts at the window's first slot and is `done` at its last. `play`, `pause`, `speed` and `seek` work as in any replay, and `seek` is clamped to the window. Signals are mocked from the F0 rates as in live mode. `oracle` returns 400 on a collected day (it needs sessions), and so does a day with no status matched through `source_refs`. Live runs (decision 0070) step once per wall-clock 5-minute slot on real statuses; KPIs and WS ticks have the replay shapes and are shadow estimates. Defaults: `policies` = all four, `focus_policy` = `rl_ppo` if requested else the last listed policy (it must be one of `policies`, else 400), `lambda` = `default_lambda`, `speed` = 60. A live run uses today's date and ignores `date`; `speed` and `seek` return 400 in live mode.

### GET /api/runs/{run_id}
Response 200: `{"run_id", "mode", "status": "running"|"paused"|"done", "paused_reason": "user"|"recommendations"|null, "sim_time": "ISO", "slot": 0, "speed": 60, "focus_policy", "kpis": {PolicyName: Kpis}}`

In RECOMMEND mode only `focus_policy`'s commands become recommendations; other policies act. A replay pauses itself with `paused_reason: "recommendations"` when new pending recommendations appear; `play` resumes it. Recommendations expire after `recommendation_ttl_slots` of sim time. Approved commands apply at the start of the next slot (decision 0025).

### GET /api/runs/{run_id}/locations/{location_id}/summary
Response 200: `StationRunSummary`. Errors: 404 (run or location not found).
`current_kw` is measured charging output, never charger `max_kw`; it is null without a metered value. `expected_wait_min` is in minutes. `anomaly_score` is a unitless model score with no client-side thresholds. Either model-backed field is null until its producer supplies a value.

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
 "recommendations": [/* Recommendation, only new or changed ones. In ACT mode the focus policy's applied commands also appear here with status "applied", for the activity log. */]}
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
In real mode `test_days` is replaced by the days the engine can replay (loaded test days covered by `forecasts.parquet`), so the frontend never offers a day `POST /api/runs` rejects (decision 0083).
Collected days come first, oldest first, followed by the loaded test days only when they are real sessions. Synthetic test days are listed only when there is no collected day and no real data (decision 0090).
