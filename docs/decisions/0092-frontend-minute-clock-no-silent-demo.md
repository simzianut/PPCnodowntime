# 0092: Frontend follows the server's minute clock; demo data only on explicit opt-in

- Date: 2026-11-08
- Who: tudor + tudor-claude
- Status: accepted
- Supersedes: [0076](0076-stagger-actions-within-slot.md), [0078](0078-replay-clock-ticks-per-minute.md); the "falls back to demo data without a backend" part of [0075](0075-demo-mode-day-simulation.md)
- Affects: spec 05 §2, §3.1, §3.3; `frontend/src/state/useDashboard.ts`, `components/sidebar/ReplayBar.tsx`, `shared.ts`, `PolicyCards.tsx`, `NetworkMap.tsx`, `api/types.ts`, `api/mock.ts`; T54

## Context
- T54: the replay plays the AmpWhere day collected on 2026-11-08 (00:35–11:25 local), with real decisions from the model. Tudor asked for minute-by-minute ticks, speeds 1×/15×/60×/300×, and "nothing mocked".
- The backend (T54a, in parallel) now sends one tick per simulated minute with `minute` (0..1439), `slot = minute // 5`, a `window` on `GET /api/runs/{id}`, `seek` in minutes and `speed` as a real-time multiple. The agent still decides every 5 minutes.
- Before this, the frontend faked minute resolution: it spread each 5-minute tick's changes over random per-CP timers and advanced a separate `actionClock` (0076, 0078). With real minute ticks those timers would invent timing the model never chose.
- When any request failed with a network error, the app silently switched to the seed-data demo (`mock.ts`, `demoSim.ts`) with a rule-based stand-in agent. In the Docker app that looks like real model output, which is exactly what "nothing mocked" rules out.

## Decision
- **Clock.** The replay clock shows `HH:MM` from the run's `minute`, falling back to `sim_time` if a message has none. The stagger timers and `actionClock` are removed. A CP's state changes when the tick carrying it arrives, and the map's action ticker stamps it with that tick's `sim_time`.
- **Timeline.** The scrubber runs in minutes over `run.window` (default 0..1440 when absent), so the 11-hour collected window fills the bar. Hour ticks are drawn inside the window, labels every 1/2/3/4/6/12 h (whichever gives fewer than 6), and both edges labelled (`00:35` … `11:25`). ±1 h skips ±60 minutes clamped to the window. Restart and "Replay again" seek to the window start. "Totals so far" shows `window start–now`.
- **Speeds.** 1×, 15×, 60× and 300× real time (values 1/15/60/300; hints: real time, 1 h in 4 min, 1 h in 1 min, 1 h in 12 s). New runs start at 60×.
- **No silent demo.** If the API can't be reached at start, the app shows "Backend unreachable" in the header and an error banner, shows no stations and no run, and retries every 5 s. A failed run start, timeline or control call shows an error and keeps the current state. The station panel no longer falls back to a made-up timeline.
- **Demo only on opt-in.** The seed-data demo runs only when the build sets `VITE_DEMO=1` or the URL has `?demo`. It then starts straight away without calling the API, and the header says "Demo mode · simulated seed data". The demo now advances one minute per tick (changes at slot boundaries) and takes seeks in minutes, so it follows the same contract.

## Alternatives considered
- **Keep the stagger and feed it minute ticks:** it would still randomise when an action appears inside the slot, which the model did not decide.
- **Keep the automatic fallback but label it:** a banner is easy to miss in a demo, and the map would still show stand-in decisions as if they came from the model.
- **Demo only through `VITE_DEMO`:** a URL flag also lets someone show the UI from the Docker build without rebuilding.

## Consequences
- With the backend down, the Docker app is empty until it comes back. That is intended.
- `VITE_DEMO` is a new build-time env var. It belongs in the CONTRACTS env var table, which the backend agent owns for T54, so it is not added there in this change.
- The frontend assumes every tick, snapshot and status message carries `minute`, and `GET /api/runs/{id}` carries `window`. Without them the clock falls back to `sim_time` and the bar to the full day.
- At 300× the socket delivers 5 messages a second, and each one re-renders the sidebar. That was fine in tests. If it stutters, ticks without changes could skip the trend update.
