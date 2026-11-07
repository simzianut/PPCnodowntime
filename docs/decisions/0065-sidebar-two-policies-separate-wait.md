# 0065: The sidebar shows two policies, a night/day before-after energy chart, average wait as a number, and a simpler replay bar

- Date: 2026-11-08
- Who: tudor + tudor-claude
- Status: accepted
- Supersedes: — (changes spec 05 §2, §3.2 and §3.3; T17 (Teodora) built the previous version)
- Affects: spec 05, `frontend/src/components/sidebar/` (PolicyCards, ReplayCharts, ReplayBar, shared), `frontend/src/App.tsx`, `frontend/src/state/`

## Context
- The sidebar showed 4 policy cards, a "Map view" (focus policy) dropdown and the λ energy-vs-added-wait trade-off scatter. Tudor (T33) found it too much to read during a demo, and the trade-off scatter too hard to explain.
- The replay bar was a row of small controls that was hard to use quickly.

## Decision
- **Two policies:** the frontend shows and requests only Always on (baseline) and RL agent (`rl_ppo`). Quiet hours (naive) and Cost rule are no longer in the UI or in the `policies` of runs it starts. The backend and evaluator still support them; the `PolicyName` type is unchanged.
- **No map policy selector:** the "Map view" dropdown is removed. `focus_policy` is always `rl_ppo`.
- **Energy chart:** the trade-off scatter and the λ curve are gone from the UI. One bar chart, "Energy before vs after the agent": idle energy used at night (22:00–06:00) and by day (06:00–22:00), a before and an after bar for each. Before = Always on `overhead_kwh` (grey), after = RL agent `overhead_kwh` (purple), as night/day deltas of the cumulative KPI built client-side from ticks. The card header gives totals, e.g. "41 kWh before → 11 kWh with RL agent (−73%)".
- **Wait as a number, no chart:** added wait is shown only as the average added wait per charge, `Kpis.wait_min_mean` (wait_min_total / sessions, over all sessions including zero waits), on the RL agent card, e.g. "Avg wait per charge 0.4 min". Always on is 0 by definition.
- **Replay bar:** big clock, big play/pause, restart, ±1 h skip, scrubber with hour ticks, speed chips instead of a select, keyboard shortcuts (Space plays/pauses, ←/→ skip), a compact date pill, and a less prominent Live toggle.
- **Code layout:** the sidebar moved out of `App.tsx` into `frontend/src/components/sidebar/`.

## Alternatives considered
- Keep the 4 policies behind a "show all policies" toggle: more state and layout cases for a view nobody uses in the demo.
- Keep the trade-off curve in a collapsible "advanced" section: still needs explaining when opened, and it keeps the `/api/eval` chart code alive for little gain.
- Cumulative "energy saved" line chart and a separate "added wait" chart (first version of this decision, changed by Tudor on 2026-11-08): the night/day before-after bars show the saving at a glance, and the wait is near zero, so a chart of it says less than one number.
- Hourly before/after bars (24 pairs): too much detail for a sidebar; Tudor asked for night and day only.

## Consequences
- The λ curve and the naive and cost-rule results are only visible via `GET /api/eval` and `eval_results.json`.
- The PRD's "energy-vs-wait curve" pitch chart and demo step 7 become the before/after energy bars plus the average-wait number.
