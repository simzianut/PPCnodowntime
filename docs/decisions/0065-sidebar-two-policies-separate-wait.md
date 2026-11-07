# 0065: The sidebar shows two policies, separate energy and wait charts, and a simpler replay bar

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
- **Separate charts:** the trade-off scatter and the λ curve are gone from the UI. Two charts sit side by side:
  - "Energy saved through the day" (cumulative kWh per policy).
  - "Added wait through the day": cumulative `wait_min_total` per policy per 5-min slot, built client-side from tick KPIs.
- **Replay bar:** big clock, big play/pause, restart, ±1 h skip, scrubber with hour ticks, speed chips instead of a select, keyboard shortcuts (Space plays/pauses, ←/→ skip), a compact date pill, and a less prominent Live toggle.
- **Code layout:** the sidebar moved out of `App.tsx` into `frontend/src/components/sidebar/`.

## Alternatives considered
- Keep the 4 policies behind a "show all policies" toggle: more state and layout cases for a view nobody uses in the demo.
- Keep the trade-off curve in a collapsible "advanced" section: still needs explaining when opened, and it keeps the `/api/eval` chart code alive for little gain.

## Consequences
- The λ curve and the naive and cost-rule results are only visible via `GET /api/eval` and `eval_results.json`.
- The PRD's "energy-vs-wait curve" pitch chart and demo step 7 become the two separate charts above.
