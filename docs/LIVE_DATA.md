# Live data: AmpWhere collection and format

How we collect live charger statuses from AmpWhere, and the exact format every part of the app must read and write. If you build anything on live data (live view, simulations on collected data, evaluation, charts), follow this document. Don't write a new parser or a new file layout.

Background decisions: [0003](decisions/0003-live-status-collection.md) (permission and limits), [0016](decisions/0016-live-collector-design.md) (collector design), [0070](decisions/0070-live-mode-shadow-agent.md) and [0072](decisions/0072-live-mode-implementation.md) (live mode), [0082](decisions/0082-impute-live-collection-sleep-gap.md) (gaps and imputation). The interface itself is in [CONTRACTS](CONTRACTS.md).

---

## 1. Ground rules

- **A human starts the collector.** AmpWhere's `robots.txt` disallows AI crawlers and its ToS §4 forbids scraping, so AI agents must not run the collector or fetch AmpWhere themselves (0003). An agent may read the files on disk, check them, and stop the collector.
- **Never commit the data.** Everything lives in `data/raw/live/` (gitignored). That includes CSV exports, samples and screenshots of raw rows. Aggregated numbers in the pitch are fine.
- **Never bypass a block.** On HTTP 403 or 429 the collector stops for good. Don't change the User-Agent, rotate IPs or add proxies to get around it.
- **One collector per data folder.** Two collectors writing to the same `statuses-*.jsonl` interleave polls and break the reader (§5.2).

## 2. Running the collector

### 2.1 Docker (standard, decision 0077)

In `.env`:

```
AMPWHERE_ENABLED=1
COMPOSE_PROFILES=live
```

Then `docker compose up -d ppc-collector`. The service mounts `${DATA_HOST_DIR}/raw/live` and restarts unless stopped. To stop it, run `docker compose stop ppc-collector`.

### 2.2 Laptop, for a fixed window

```
cd ~/PPCnodowntime
mkdir -p data/raw/live
AMPWHERE_ENABLED=1 nohup .venv/bin/python -m dataset.live_collector >/dev/null 2>&1 &
echo $! > data/raw/live/collector.pid
nohup sh -c "sleep 28800; kill -INT $(cat data/raw/live/collector.pid)" >/dev/null 2>&1 &
caffeinate -dimsu -w $(cat data/raw/live/collector.pid) &
```

Lessons from the 2026-11-07/08 overnight run:
- **`mkdir -p` first.** The folder is created only on the first write, so writing the PID file before that fails.
- **Use `nohup` for the stop timer too.** A plain `( sleep …; kill … ) &` dies with the shell that started it, and the collector then runs indefinitely.
- **Keep the lid open, or plug in power and an external display.** A Mac on battery sleeps when the lid closes, and `caffeinate` can't stop that. That's what caused the 46-minute gap.
- **Keep at least 5 GB free on disk.** A full disk fails the poll (`OSError 28`), and 5 failed polls in a row stop the collector.
- **Watch it** with `tail -f data/raw/live/collector.log`, which writes one line per poll.

Flags: `--once` runs a single poll. `--interval N` overrides `LIVE_POLL_S`, which defaults to 300 s. `--adapter file --file <path>` replays a saved JSONL or snapshot instead of hitting AmpWhere, so use it for tests.

## 3. What the collector does

| | |
|---|---|
| Snapshot | `GET https://ampwhere.com/api/locations/ro` (~5.8 MB): every location and charger in Romania, all operators |
| Delta | `GET https://ampwhere.com/api/locations/ro/status?status_updated_at=<cursor>`: chargers whose status changed since the cursor |
| Schedule | A snapshot on start and every `resync_s` (6 h). A delta every `poll_s` (5 min) in between. |
| Forced snapshot | The delta has no `latest_status_updated_at`, can't be parsed, or names an unknown location or charger |
| Politeness | One request at a time, at least 2 s apart, honest User-Agent, no cookies or auth |
| Failures | Network errors and 5xx retry after 5, 10, 20 and 40 s, then the poll fails. Exit after 5 failed polls in a row. HTTP 403 or 429 stops at once. |

Config is in `config/live.yaml` (URLs, timings, `status_map`, `ppc_provider_id: 5`). Code is in `dataset/live_collector.py` and `dataset/live/`.

**A failed poll loses no state.** The delta cursor advances only after a successful parse, so the next poll returns every change since the last good one. What's lost is the timing *inside* the gap (§7).

## 4. Files on disk

All paths are under `data/raw/live/` (`LIVE_DIR` for the API).

| Path | What | Who reads it |
|---|---|---|
| `statuses-YYYYMMDD.jsonl` | **The canonical live data.** Normalised rows (§5), one file per **UTC** date, append-only | live mode, simulations, evaluation |
| `raw/YYYYMMDD/HHMMSS_N.json.gz` | Every raw AmpWhere response, unchanged | re-parsing after a parser fix |
| `ampwhere_snapshot_latest.json.gz` | Latest full snapshot, replaced atomically at each resync | station import (`python -m stations`, 0029) |
| `collector.log` | One line per poll: kind, row count, status counts, PPC counts, latency, failures | humans |
| `collector.pid` | PID of a laptop run | stop scripts |
| `imputed/` | Synthetic rows for gaps, never mixed with observed data (§7) | visualisation only, by explicit opt-in |

## 5. The canonical row format

### 5.1 Fields

One JSON object per line, UTF-8, written by `LiveStatus.to_dict()` (`dataset/live/models.py`).

| Field | Type | Null? | Meaning |
|---|---|---|---|
| `ts` | ISO 8601 UTC with offset, e.g. `2026-11-07T22:32:55.283135+00:00` | no | When **our poll** ran. Every row of one poll has the identical string. |
| `source` | string | no | `"ampwhere"`. `"file"` for replays, `"imputed"` only in `imputed/` |
| `source_location_id` | string (UUID) | no | AmpWhere location `uid` |
| `source_cp_id` | string | yes | AmpWhere charger `id`. With the location it forms the connector key (§6). |
| `connector` | string | yes | `type2`, `ccs2`, `chademo`, … (lower case, as AmpWhere sends it) |
| `raw_status` | string | no | AmpWhere's word, unchanged (`AVAILABLE`, `UNAVAILABLE`, `CHARGING`, …) |
| `status` | `CHARGING` \| `AVAILABLE` \| `OFFLINE` \| `UNKNOWN` | no | Normalised through `status_map`. Unknown words become `UNKNOWN`. |
| `cp_id` | string | yes | **Always null in the JSONL.** Matching to our stations happens at read time (§6). |
| `name` | string | yes | Location name |
| `lat`, `lon` | float | yes | WGS84 degrees |
| `operator` | string | yes | Provider name, e.g. `"PPC Blue"` |
| `provider_id` | int | yes | AmpWhere provider. **PPC Blue = 5.** Filter on this, not on `operator`. |
| `evse_id` | string | yes | EVSE / QR id, e.g. `RO*PPC*E22XP22T3K` |
| `power_kw` | number | yes | Rated power of the connector |
| `current_type` | `AC` \| `DC` | yes | As AmpWhere sends it, upper-cased. **Unreliable for PPC** (§8). |
| `source_updated_at` | ISO 8601 UTC with `Z` | yes | AmpWhere's **location** `updated_at` at publish time. It isn't per charger. |

Example:

```json
{"ts": "2026-11-07T22:32:55.283135+00:00", "source": "ampwhere", "source_location_id": "74dbf43b-5dff-596a-92bd-c5736901a395", "raw_status": "AVAILABLE", "status": "AVAILABLE", "source_cp_id": "1237", "connector": "type2", "cp_id": null, "name": "Metro Baneasa", "lat": 44.512565, "lon": 26.083169, "operator": "ElDrive", "provider_id": 1, "evse_id": "ROE1*1", "power_kw": 22, "current_type": "AC", "source_updated_at": "2026-11-07T22:32:42.300Z"}
```

### 5.2 File rules every writer must keep

The reader (`sim.live.LiveStatusReader`) depends on these:
1. **Append-only.** Never rewrite, sort or delete lines in a `statuses-*.jsonl`.
2. **Rows of one poll are contiguous and share one `ts` string.** That's how polls are told apart. There is no poll id.
3. **`ts` never goes backwards** within or across files. The reader stops at the first row later than its clock.
4. **The file name is the UTC date of `ts`.** Midnight UTC is 02:00 in Romania in winter, so one local day spans two files. The reader opens both.
5. **Only observed rows.** Synthetic, imputed or edited rows never go in these files.

### 5.3 Snapshot vs delta rows

There is no `kind` field.
- A **snapshot** poll has a row for every charger of every operator, about 15.8k rows. The reader treats a poll that lists at least 90% of the known keys as a snapshot.
- A **delta** poll has rows only for chargers AmpWhere reported, usually 200–600.
- **A delta row is not necessarily a change.** Most re-report an unchanged status. Count real changes by comparing against the connector's previous status. On 2026-11-08, 02:45–03:30 UTC, 2,895 delta rows contained 366 real changes.

## 6. Turning rows into station state

Use the existing code: `sim/live.py` (`LiveStatusReader`, `cp_status`, `obs_code`, `event_kind`). The rules are listed here so that any other consumer, such as a chart or a script, gives the same answers.

1. **Connector key:** `f"{source_location_id}|{source_cp_id}"`. Within a key, the latest row wins.
2. **Matching:** the key equals `source_id` in `data/processed/source_refs.parquet` (`source == "ampwhere"`), which gives `cp_id`. Unmatched keys are other operators or chargers outside our station list, and are ignored. Build the station tables from the same snapshot with `python -m stations` so the ids line up (0080).
3. **Connector → CP:** any connector CHARGING → CHARGING. Else any AVAILABLE → AVAILABLE. Else all OFFLINE → OFFLINE. Else UNKNOWN.
4. **CP → sim state:** CHARGING → `CHARGING`, OFFLINE → `OFFLINE`, AVAILABLE and UNKNOWN → `IDLE`. The agent's sleep states (`STANDBY`, `DEEP_SLEEP`, `WAKING`) are never observed, only simulated.
5. **Events:**
   - AVAILABLE/UNKNOWN → CHARGING is an **arrival**, and CHARGING → AVAILABLE is a **departure**.
   - Changes to or from OFFLINE are state changes, not sessions, and that includes OFFLINE → CHARGING.
6. **Event time:** `source_updated_at`, clamped to lie after the previous poll that saw the connector and no later than this poll's `ts`. An event later than its slot moves to the start of the next slot.
7. **Day and slots:** Europe/Bucharest local day, 5-minute slots. A slot is stepped 60 s after it ends (`LIVE_LAG_S`). A live run is refused when the newest row is more than 30 minutes old.

**Simulating on collected data:**
- Use live mode's demo clock rather than a new pipeline: `LIVE_CLOCK_START=<ISO local time>` and `LIVE_CLOCK_SPEED=<x>` replay a collected window with the agent acting in shadow (0072).
- Observed arrivals drive the engine. The agent's sleep decisions add wake-up waits and save energy against always-on.
- Report these numbers as **shadow estimates** on real statuses.

## 7. Gaps and imputation

- **Finding gaps:** consecutive poll `ts` values more than 360 s apart, or `poll fail` lines in `collector.log`. After a gap, the first poll carries every net change at once, all timed at or near that poll.
- **Known gaps** in the 2026-11-07/08 run are listed in its manifest, [live-runs/2026-11-07-overnight.md](live-runs/2026-11-07-overnight.md), with the archive checksum and per-poll PPC counts.
- **Imputed rows** live only in `imputed/<name>.jsonl` with a `<name>.meta.json` (method, seed, fitted parameters).
  - Each row has the §5.1 fields plus `source: "imputed"`, `imputed: true` and `imputation: "<method>"`, with `raw_status` and `source_updated_at` set to null.
  - The current method is a Markov bridge (0082).
- **Scoring, KPIs and any accuracy claim use observed rows only.** Imputed rows may fill a replay or chart for continuity, and must be shown as imputed. Don't call an imputed window observed in the pitch.

## 8. Known data quirks

| Quirk | What to do |
|---|---|
| AmpWhere reports **every PPC connector as `DC`**, even 22 kW Type 2 | Derive AC/DC from `connector`: `type2` → AC, `ccs2` and `chademo` → DC. Don't use `current_type` for PPC. |
| `source_updated_at` is per location and batch, not per charger | Use it only within the clamp in §6. It can't recover timing inside a gap. |
| Delta rows mostly re-report unchanged statuses | Compare with the previous status before counting changes (§5.3) |
| `UNAVAILABLE` maps to OFFLINE | It's AmpWhere's word for out of service. Check the `status_map` before adding new words. |
| About 2–3% of chargers are `UNKNOWN` | They are treated as IDLE in the sim. Don't drop them. |
| PPC Blue has 1,364 connectors, 671 matched CPs | A live run includes only CPs with an observed status |

## 9. Changing the format

- Any change to the row fields, file layout, status vocabulary or matching rules goes into `docs/CONTRACTS.md` (live JSONL row) and a decision file first, in its own commit on `main`. Then update this document and `sim/live.py` together.
- New fields are **added**, never renamed or retyped, so older files stay readable.
- After a parser fix, rebuild the normalised files from `raw/` into a new folder. Don't edit `statuses-*.jsonl` in place.
