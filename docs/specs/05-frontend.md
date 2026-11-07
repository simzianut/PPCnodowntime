# Spec 05: Simulator frontend

Owner folder: `frontend/`. A single-page React app that shows the agent running across all of Romania, compares policies, and lets an operator control the agent. It talks only to the M4 API, using the routes in CONTRACTS.md.

## 1. Stack
- React 18 + TypeScript + Vite, dev server on `:5173`.
- Map: an inline SVG base map, `frontend/public/map/romania.svg` (decision 0045), with its own pan, zoom and clustering code in `frontend/src/map/geo.ts` ([decision 0046](../decisions/0046-interactive-svg-map.md)). No tile server, no map library.
- Recharts for the charts. Plain CSS variables for the white-only PPC theme.
- API base from `VITE_API_URL` (default `http://localhost:8000`).
- Develop against `API_FIXTURES=1` on the backend, or against an MSW mock built from `fixtures/`.

## 2. Layout

```
┌───────────────────────────────────────────────────────────────┬──────────────────────┐
│ Header: PPCnodowntime · mode [Replay|Live] · Autonomy [ACT|RECOMMEND] · policy shown ▾│
├───────────────────────────────────────────────────────────────┼──────────────────────┤
│                                                               │ KPI panel            │
│                     Romania map                               │  per policy:         │
│        (CP dots coloured by state of the shown policy)        │  energy saved kWh/%  │
│                                                               │  added wait tot/p95  │
│                                                               │  line chart over day │
│                                                               │  energy-vs-wait curve│
│                                                               ├──────────────────────┤
│                                                               │ Recommendations feed │
│                                                               │ (RECOMMEND mode)     │
├───────────────────────────────────────────────────────────────┴──────────────────────┤
│ Replay bar: date ▾ · ▶/❚❚ · speed 1×…288× · timeline scrubber · sim clock             │
└───────────────────────────────────────────────────────────────────────────────────────┘
Header: PPC logo left, SmartHack logo right. A 32% left sidebar contains overview statistics, policy analysis, recommendations, autonomy, and replay controls; the 68% right side is the interactive map. Selecting a station replaces the overview in the sidebar, keeps replay controls visible, and centers the map on that station.
```

On phone width the panels stack under the map, and the replay bar stays pinned to the bottom.

## 3. Features (all must-have)

### 3.1 Romania map with charge-point states
- Each **location** is one flat dot on a quiet base: no state rings, no cluster numbers ([decision 0056](../decisions/0056-map-live-actions-with-swipe.md)).
- Map colours and legend (4 only): idle = red (awake, not charging, incl. WAKING), asleep = green (STANDBY, DEEP_SLEEP), charging = yellow, offline = grey.
- A selected station is drawn larger and centred in the right ("with agent") half, so the divider handle never covers it. The map remains interactive while the sidebar changes between overview and station details.
- Pan by drag, zoom by wheel, pinch, double-click, the +/− buttons or the keyboard. Nearby locations cluster within ~44 px on screen. Cluster colour = mean over member stations of asleep / (asleep + idle) CPs, interpolated red → green in RGB (never via yellow); charging and offline CPs are ignored, and an all-charging cluster is yellow. Clicking it zooms in. Locations at the same spot fan out at high zoom.
- **Live actions:** a ripple + label per transition (green "−W" on sleep, red on wake, yellow on charge start), an action ticker of the last 5 actions, and sleep/wake counters. Transitions come from diffing `cpState` between ticks; a seek or policy switch (>50% of CPs changing) resets with no animation.
- **Swipe slider:** left half is the always-on counterfactual (every non-charging CP idle, kW burned), right half the shown policy (kW burned, % less idle power). The divider is draggable and keyboard-movable and does not pan the map. Watts come from `frontend/src/map/energy.ts` ([decision 0055](../decisions/0055-cluster-energy-saved-blobs.md)). `NetworkMap` takes `run` for this.
- The station marker icon is `#station-icon` in the SVG file. Lines drawn in its `#lines` layer show up in the app.
- Tick updates arrive as diffs. Apply them to an in-memory `cpState` map. Only the marker layer re-renders, never the base map. Target: 60 fps panning while a replay runs at 288×.
- Locations with `confidence = low` get a dashed outline.

### 3.2 Policy comparison KPIs
- One card per policy (always-on, naive, cost rule, RL agent) showing energy saved (kWh and %) and added wait (total and p95 minutes). The values update with every tick.
- A line chart of cumulative energy saved over the simulated day, one line per policy.
- An energy-vs-wait curve read from `GET /api/eval`: one curve per λ-swept policy, with the baselines as single points.
- "Shown policy" selector: picks which policy's states colour the map. It sets `focus_policy` on the run.

### 3.3 Replay controls + autonomy switch
- Date picker, limited to the test days from `/api/eval`; play/pause; speed; a scrubber that sends `seek`.
- Live mode toggle: starts a `mode: live` run. The scrubber is hidden in live mode.
- **Autonomy switch** (`PUT /api/settings/autonomy`):
  - **ACT:** commands apply immediately, shown as a toast-style activity log.
  - **RECOMMEND:** pending recommendations appear in the feed, each with the CP, `from → to` states, the reason and the time left before it expires, plus Approve and Reject buttons.

### 3.4 Station detail panel
Opens when a location is clicked (`GET /api/runs/{run_id}/cp/{cp_id}/timeline` for each CP). It shows:
- Header: name, partner, address, county, context, sources and confidence.
- Per CP: power class, connectors, and a **state timeline bar** for the day.
- A chart of the forecast `p30` as a line against actual arrivals as ticks, with route-intent markers.
- The list of the agent's commands for this location, each with its reason and status.
- Run summary metrics from `GET /api/runs/{run_id}/locations/{location_id}/summary`: current metered kW, expected wait in minutes, and a unitless anomaly score. Null or unavailable fields display as “Unavailable”; rated `max_kw` is never used as current output.

## 4. State management
- One WebSocket per run, with auto-reconnect. On reconnect, call `GET /api/runs/{id}` to resync.
- Store: Zustand or React context. The keys are `run`, `kpis`, `cpState`, `recommendations`, `settings`.
- No `localStorage` for app state. It may remember UI preferences only, such as the theme and which panel is collapsed.

## 5. Must-have vs stretch

| Must-have | Stretch |
|---|---|
| The 4 features above, on fixtures, then on the real API | "Explain" button on a command (LLM explainer from M2) |
| White PPC theme, phone-width layout | 1,400-CP annual extrapolation card |
| | Heatmap layer of predicted demand |

## 6. Acceptance criteria
- Works end to end with `API_FIXTURES=1` before the engine exists.
- A replay at 288× of a full day on all CPs stays smooth and the numbers match `GET /api/runs/{id}` at the end.
- Approving a recommendation changes that CP's colour on the next tick.
- Every payload type is generated from or checked against CONTRACTS.md (hand-written TS types in `frontend/src/api/types.ts`, kept in sync).
