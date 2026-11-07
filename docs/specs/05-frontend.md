# Spec 05: Simulator frontend

Owner folder: `frontend/`. A single-page React app that shows the agent running across all of Romania, compares policies, and lets an operator control the agent. It talks only to the M4 API, using the routes in CONTRACTS.md.

## 1. Stack
- React 18 + TypeScript + Vite, dev server on `:5173`.
- MapLibre GL JS with the OpenFreeMap "positron" style (no key needed).
  - Alternative under review: a static SVG base map, `frontend/public/map/romania.svg`, with a swappable station icon and a layer for hand-drawn lines ([decision 0041](../decisions/0041-svg-base-map.md)).
- Recharts for the charts. Plain CSS variables for theming; light and dark themes.
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
Station detail: right-side drawer over the KPI panel when a location is clicked.
```

On phone width the panels stack under the map, and the replay bar stays pinned to the bottom.

## 3. Features (all must-have)

### 3.1 Romania map with charge-point states
- Each **location** is one marker. Its colour shows the state of its CPs, split by share when the CPs are in mixed states.
- State colours and legend: CHARGING green, IDLE blue, WAKING amber, STANDBY light grey, DEEP_SLEEP dark grey, OFFLINE red.
- Clusters form below zoom level 8. A cluster's badge shows how many CPs are asleep.
- Tick updates arrive as diffs. Apply them to an in-memory `cpState` map and update the GeoJSON source without re-creating layers. Target: 60 fps panning while a replay runs at 288×.
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

## 4. State management
- One WebSocket per run, with auto-reconnect. On reconnect, call `GET /api/runs/{id}` to resync.
- Store: Zustand or React context. The keys are `run`, `kpis`, `cpState`, `recommendations`, `settings`.
- No `localStorage` for app state. It may remember UI preferences only, such as the theme and which panel is collapsed.

## 5. Must-have vs stretch

| Must-have | Stretch |
|---|---|
| The 4 features above, on fixtures, then on the real API | "Explain" button on a command (LLM explainer from M2) |
| Light and dark themes, phone-width layout | 1,400-CP annual extrapolation card |
| | Heatmap layer of predicted demand |

## 6. Acceptance criteria
- Works end to end with `API_FIXTURES=1` before the engine exists.
- A replay at 288× of a full day on all CPs stays smooth and the numbers match `GET /api/runs/{id}` at the end.
- Approving a recommendation changes that CP's colour on the next tick.
- Every payload type is generated from or checked against CONTRACTS.md (hand-written TS types in `frontend/src/api/types.ts`, kept in sync).
