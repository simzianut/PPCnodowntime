# Deslop A: "Control room"

Neutral greys, one accent (#0B5CAD) for controls and the agent, map red/green/yellow kept. Inter, 13 px body, 12 px meta, tabular numbers, radius 3 px, hairlines instead of cards.

| Trope found | Replaced with |
|---|---|
| Monospace uppercase eyebrows (NETWORK OVERVIEW, PERFORMANCE, REPLAY ·, DIRECT CONTROL, AGENT ACTIONS, STATION OVERVIEW) | Sentence-case 13 px section titles, meta text right-aligned in grey |
| Pills and badges: "Romania \| 445 sites", "API connected", "−41 kW · 80% less idle power", "−76%", "Active" pulsing dot, BASELINE/AGENT tags, pending-count bubble | Plain text: "445 locations · 671 charge points", status dot + "Connected", "29 kW lower (71%)", "4 pending" |
| AI purple (#8369df) for the agent, plus a blue/teal/indigo mix | One accent blue for the agent column, chart bars, play button and active toggles; always on = grey |
| Two KPI cards, agent card with glow border, "≈ 0 RON" baseline full of zeros | One comparison table (Idle energy, Energy saved, Cost saved, Avg wait per charge × Always on / RL agent), "—" where a baseline value means nothing |
| Card-in-card, soft shadows, 6–10 px radii, tinted backgrounds | Flat sections with hairline dividers, 3 px radius, no shadows (map overlays included) |
| Icon-in-tinted-square quick stats, ⚡ on every activity row, calendar/radio/zap/command icons | Removed; only functional icons kept (play/pause, close, chevrons, zoom) |
| "Charging intelligence" ×2 with decorative period, "Network control / CHARGING INTELLIGENCE" lockup, landing-page tagline, "Network operations / Replay analysis" | Header reads "PPC \| Charging network"; no taglines |
| Stat counts in 3 places; activity log duplicating the map ticker with raw ids ("L00231-2 moved to IDLE") | Counts live only in the header and map cards; activity log dropped (ticker covers it) |
| Navy box with a white lightbulb of unclear purpose | SmartHack logo in grey with its name next to it |
| Full-screen blur "Starting network view" overlay with pulsing tile | Header status reads "Connecting…"; table shows "—" until data arrives |
| Fake precision: "0.00 min", anomaly to 3 dp, "Unavailable" | 1 dp waits, 2 dp anomaly, "—" |
| 8–11 px text in six sizes, three font families (Manrope, DM Sans, DM Mono) | One family, sizes 11 (axis ticks) / 12 / 13 / 16 / 22 |
| Gratuitous motion: pulsing live dots, slide-in panel, hover lift on play, fading ticker rows | Static; map action ripples kept (they carry information) |
| Filler copy: "All caught up", "Commands will show here", "Waiting for the agent to act…", "burned" | "Nothing to review…", "No actions yet.", "idle draw" |
| Raw "rl_ppo(action=STANDBY)" reasons repeating the target state | Hidden when they only repeat the transition |
| Station detail: card grid of tiny meta, mono ids, state colours different from the map, source shown twice | One key/value list, map colours for state dots and timeline, coordinates in the list |
| ACT/Review toggle with icons and orange/blue tints | "Agent mode: Act \| Review" segmented control, accent fill on the active one |

## Critic pass
- Map cards: dropped the red/blue 3 px top strips (decorative colour coding) and the clock that repeated the replay clock.
- One vocabulary: the table, chart key and map all say "Without agent" / "With RL agent" (was "Always on" in the sidebar); table title "Totals so far" with the real window "Tue 12 Mar, 00:00–02:45".
- Precision: kWh totals to whole units (was "1,062.1 kWh"), waits always 1 dp ("0.0 min" next to "1.7 min", not "0 min").
- Station detail: forecast x-axis repeated "00:00 00:00 01:00…"; now real HH:MM ticks. Raw "p30=0.02" reasons read "2% chance of an arrival within 30 min"; command status capitalised.
- Jargon: ticker "1 CP → sleep" now "1 charge point → sleep".
- Contrast: --quiet #8a9099 (3.2:1) to #737a83 (≈4.5:1) for 11–12 px meta; SmartHack credit in muted text, logo stays visible below 1100 px (text hides, was the whole credit).
