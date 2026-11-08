# Spec 05: Simulator frontend

Owner folder: `frontend/`. A single-page React app that shows the agent running across all of Romania, compares policies, and lets an operator control the agent. It talks only to the M4 API, using the routes in CONTRACTS.md.

## 1. Stack
- React 18 + TypeScript + Vite, dev server on `:5173`.
- Map: an inline SVG base map, `frontend/public/map/romania.svg` (decision 0045), with its own pan, zoom and clustering code in `frontend/src/map/geo.ts` ([decision 0046](../decisions/0046-interactive-svg-map.md)). No tile server, no map library.
- Recharts for the charts. Plain CSS variables for the white-only PPC theme: Inter (Google Fonts), neutral greys, one accent blue `#0B5CAD`, hairlines instead of cards, 3 px radius, no shadows, no decorative motion ([decision 0068](../decisions/0068-deslop-control-room-style.md)).
- API base from `VITE_API_URL` (default `http://localhost:8000`).
- Develop against `API_FIXTURES=1` on the backend, or against an MSW mock built from `fixtures/`.

## 2. Layout

```
┌───────────────────────────────────────────────────────────────┬──────────────────────┐
│ Header: PPC | Charging network · counts · status (only if not connected) · SmartHack │
├───────────────────────────────────────────────────────────────┼──────────────────────┤
│                                                               │ Totals so far (table)│
│                     Romania map                               │  Without / With RL:  │
│        (CP dots coloured by state of the RL agent)            │  idle kWh, saved kWh,│
│                                                               │  cost, avg wait      │
│                                                               │ Wasted energy on idle│
│                                                               │  mode: 2 total bars  │
│                                                               ├──────────────────────┤
│                                                               │ Agent mode Act|Review│
│                                                               │ + recommendations    │
├───────────────────────────────────────────────────────────────┴──────────────────────┤
│ Replay bar: clock · ⟲ · −1h ▶/❚❚ +1h · scrubber w/ hour ticks · speed chips · date · Live │
└───────────────────────────────────────────────────────────────────────────────────────┘
Header: PPC logo and "PPC | Charging network" left, location and charge-point counts, SmartHack logo in grey right. A connection status appears only when something is wrong: "Connecting…", "Not connected · showing demo data" or "Not connected" ([decision 0068](../decisions/0068-deslop-control-room-style.md)). A 32% left sidebar contains the KPI table, the energy chart, recommendations, agent mode and replay controls; the 68% right side is the interactive map. Selecting a station replaces the overview in the sidebar, keeps replay controls visible, and centers the map on that station.
```

On phone width the panels stack under the map, and the replay bar stays pinned to the bottom.

## 3. Features (all must-have)

### 3.1 Romania map with charge-point states
- Each **location** is one flat dot on a quiet base: no state rings, no cluster numbers ([decision 0064](../decisions/0064-map-live-actions-with-swipe.md)).
- Map colours and legend (4 only): idle = red (awake, not charging, incl. WAKING), asleep = green (STANDBY, DEEP_SLEEP), charging = yellow, offline = grey. The map key shows each with a large swatch and a short hint, plus a red → green bar ("all idle" → "all asleep") for groups of stations ([decision 0074](../decisions/0074-bigger-legend-trim-station-detail.md)).
- A selected station is drawn larger and centred in the right ("with agent") half, so the divider handle never covers it. The map remains interactive while the sidebar changes between overview and station details.
- Pan by drag, zoom by wheel, pinch, double-click, the +/− buttons or the keyboard. Nearby locations cluster within 44 px on screen at country zoom, shrinking to 14 px from about 8× zoom so stations separate as you zoom in ([decision 0064](../decisions/0064-map-live-actions-with-swipe.md)). Clicking a cluster zooms in and centres it in the with-agent half. Cluster colour = mean over member stations of asleep / (asleep + idle) CPs, interpolated red → green in RGB (never via yellow); charging and offline CPs are ignored, and an all-charging cluster is yellow. Clicking it zooms in. Locations at the same spot (within 12 px at max zoom) fan out into a tight ring on a faint disc, without legs.
- **Live actions:** a ripple + label per transition (green "−W" on sleep, red on wake, yellow on charge start), an action ticker showing the latest 3 actions, expandable ("Show all N") to a scrollable list of every action since the run started or the last seek back ([decision 0068](../decisions/0068-deslop-control-room-style.md)), and sleep/wake counters. Transitions come from diffing `cpState` between ticks; a seek or policy switch (>50% of CPs changing) resets with no animation.
- **Swipe slider:** left half is the always-on counterfactual (every non-charging CP idle, kW burned), right half the shown policy (kW burned, % less idle power). The divider is draggable and keyboard-movable and does not pan the map. Watts come from `frontend/src/map/energy.ts` ([decision 0067](../decisions/0067-cluster-energy-saved-blobs.md)). `NetworkMap` takes `run` for this.
- The station marker icon is `#station-icon` in the SVG file. Lines drawn in its `#lines` layer show up in the app.
- Tick updates arrive as diffs. Apply them to an in-memory `cpState` map. Only the marker layer re-renders, never the base map. Target: 60 fps panning while a replay runs at 288×.
- Locations with `confidence = low` get a dashed outline.

### 3.2 Policy comparison KPIs
Two policies only: Always on (baseline) and RL agent (`rl_ppo`). Runs request just these two; naive and cost rule stay in the backend and `GET /api/eval` but are not shown ([decision 0065](../decisions/0065-sidebar-two-policies-separate-wait.md)). Code: `frontend/src/components/sidebar/`.
- One comparison table, "Totals so far" with the real window (e.g. "Tue 12 Mar, 00:00–02:45"), updated every tick ([decision 0068](../decisions/0068-deslop-control-room-style.md)). Rows: Idle energy, Energy saved, Cost saved, Avg wait per charge; columns: Without agent, With RL agent. Avg wait per charge = `Kpis.wait_min_mean` (wait_min_total / sessions, zero waits included), 1 dp. "—" where a baseline value means nothing or data has not arrived. No wait chart.
- One bar chart, "Wasted energy on idle mode": two bars, total idle energy so far (`overhead_kwh`) without the agent (grey) and with the RL agent (accent blue), labelled in whole kWh. No night/day split ([decision 0068](../decisions/0068-deslop-control-room-style.md), superseding that part of 0065).
- No energy-vs-wait trade-off curve and no map policy selector: `focus_policy` is always `rl_ppo`.

### 3.3 Replay controls + autonomy switch
- Replay bar ([decision 0065](../decisions/0065-sidebar-two-policies-separate-wait.md)): big sim clock, big play/pause, restart, ±1 h skip, a scrubber with hour ticks that sends `seek`, speed chips, and a compact date pill limited to the test days from `/api/eval`. Keyboard: Space plays/pauses, ←/→ skip.
- Live mode toggle (low-key, in the bar): starts a `mode: live` run. The scrubber is hidden in live mode.
- **Autonomy switch** (`PUT /api/settings/autonomy`):
  - Shown as an "Agent mode: Act | Review" segmented control.
  - **ACT:** commands apply immediately; the map's action ticker is the only feed (no separate activity log, [decision 0068](../decisions/0068-deslop-control-room-style.md)).
  - **RECOMMEND:** pending recommendations appear in the feed, each with the CP, `from → to` states, the reason and the time left before it expires, plus Approve and Reject buttons.

### 3.4 Station detail panel
Opens when a location is clicked (`GET /api/runs/{run_id}/cp/{cp_id}/timeline` for each CP). It shows:
- Header: name, partner, address, county, context, sources and confidence.
- Per CP: power class, connectors, and a **state timeline bar** for the day.
- A chart of the forecast `p30` as a line against actual arrivals as ticks, with route-intent markers.
- The list of the agent's commands for this location, each with its reason and status.
- No run summary metrics (current output, expected wait, anomaly score) are shown ([decision 0074](../decisions/0074-bigger-legend-trim-station-detail.md)).

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
