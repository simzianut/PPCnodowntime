# 0068: The frontend uses a plain "control room" style, and the energy chart is two total bars

- Date: 2026-11-08
- Who: tudor + tudor-claude
- Status: accepted
- Supersedes: the energy-chart part of [0065](0065-sidebar-two-policies-separate-wait.md) (night/day bars → two total bars) and the visual style of the earlier frontend (T16/T17, Teodora)
- Affects: spec 05 §1, §2, §3, the PRD note on the pitch chart, `frontend/src/` (styles, header, sidebar, map overlays, station detail), `frontend/src/state/useKpiHistory.ts` (removed)

## Context
- Tudor (T34) asked to remove "AI slop" from the whole frontend: generic dashboard tropes that make the app look machine-made and hard to read in a demo.
- Three versions were built in parallel, each then reviewed by a fresh-eyes critic. Tudor chose A with two tweaks (no "Connected" status; no night/day split in the chart).

## Decision
- **Style:** dense grid-ops look. Inter only, neutral greys, one accent blue `#0B5CAD` for the agent, controls and chart bars; map red/green/yellow/grey unchanged. Sizes 11/12/13/16/22 px, tabular numbers, 3 px radius, hairlines instead of cards, no shadows.
- **Tropes removed → replacement:**
  - Mono uppercase eyebrows ("NETWORK OVERVIEW", "REPLAY ·") → sentence-case 13 px titles, grey meta right-aligned.
  - Pills and badges (site count, "−76%", BASELINE/AGENT tags, pending bubble) → plain text, e.g. "29 kW lower (71%)", "4 pending".
  - AI purple plus a blue/teal/indigo mix → one accent; Without agent = grey.
  - Two KPI cards, glow border, "≈ 0 RON" baseline → one comparison table, "—" where a baseline value means nothing.
  - Card-in-card, soft shadows, 6–10 px radii, tinted fills → flat sections with hairline dividers (map overlays too).
  - Decorative icons (tinted-square stats, ⚡ per row) → only functional icons (play/pause, close, chevrons, zoom).
  - Taglines ("Charging intelligence.", "Network operations") → header "PPC | Charging network".
  - Counts in 3 places and an activity log duplicating the map ticker → counts in the header and map cards only; activity log dropped.
  - Unexplained navy lightbulb box → SmartHack logo in grey with its name.
  - Full-screen blur "Starting network view" overlay → header status "Connecting…", table shows "—" until data arrives.
  - Always-visible "Connected" pill → header status only when something is wrong: "Connecting…", "Not connected · showing demo data", "Not connected".
  - Fake precision ("0.00 min", 3 dp anomaly, "1,062.1 kWh") → waits 1 dp, anomaly 2 dp, kWh whole, "—" for missing.
  - Three font families in six tiny sizes → Inter, five sizes.
  - Pulsing dots, slide-ins, hover lifts, fading rows → static UI; map action ripples kept (they carry information).
  - Filler copy, raw reasons ("rl_ppo(action=STANDBY)", "p30=0.02") and mixed vocabulary → "No actions yet.", "2% chance of an arrival within 30 min", "Without agent" / "With RL agent" everywhere.
- **KPI table:** "Totals so far" with the real window (e.g. "Tue 12 Mar, 00:00–02:45"); rows Idle energy, Energy saved, Cost saved, Avg wait per charge; columns Without agent / With RL agent.
- **Energy chart:** "Wasted energy on idle mode", two bars: total idle energy so far (`overhead_kwh`) without the agent (grey) vs with the RL agent (accent), labelled in kWh. No night/day split, so `state/useKpiHistory.ts` is removed.

## Alternatives considered
- B "Plain-language report": a headline sentence, serif type on off-white paper. Reads well once, but too sparse for watching a replay tick.
- C "Utility product": GOV.UK-like, IBM Plex Sans, deep blue, real buttons. Clear, but looks like a form service rather than an operations view.
- Keep the night/day bars from 0065: Tudor found two totals easier to read at a glance.

## Consequences
- Inter is loaded from Google Fonts; Manrope, DM Sans and DM Mono are gone.
- One accent colour; purple is no longer the agent colour anywhere.
- The two policy cards become one KPI table.
- The ACT-mode activity log and the loading overlay are removed; the map ticker is the only action feed. It shows the latest 3 actions and expands to a scrollable list of all of them (kept in memory, up to 20,000).
- Map overlays (swipe cards, ticker, legend, zoom) are restyled flat; dot and cluster colours are unchanged.
