# 0063: Map shows live agent actions on a quiet base, with a without/with-agent swipe slider

- Date: 2026-11-07
- Who: tudor + tudor-claude
- Status: accepted
- Supersedes: the marker and cluster parts of [0046](0046-interactive-svg-map.md) (6-state rings, numbered clusters) and of [0062](0062-cluster-energy-saved-blobs.md) (energy-saved blobs, red → yellow → green)
- Affects: spec 05, `frontend/src/components/NetworkMap.tsx`, `frontend/src/map/energy.ts`, `frontend/src/App.css`

## Context
- Tudor asked for a simpler map that is easy to read and makes the agent's effect obvious.
- Four variants were prototyped behind a tab switcher (`?map=a..d`, T30). Tudor chose D plus only the swipe slider from B. The scratchpad and the other variants are deleted.

## Decision
- **Quiet base:** flat dots. No state rings, no cluster numbers.
- **Colours (4, legend shows only these):** idle = red (awake, not charging, incl. WAKING), asleep = green (STANDBY, DEEP_SLEEP), charging = yellow, offline = grey.
- **Clustering:** screen radius ~44 px (was 34). Cluster colour = arithmetic mean over member stations of asleep / (asleep + idle) CPs, interpolated straight red → green in RGB so it never passes through yellow. Charging and offline CPs are ignored; a cluster where everything is charging is yellow.
- **Live actions:** ripple + label on each transition: green "−W" when CPs go to sleep, red when they wake, yellow when a charge starts. An action ticker shows the last 5 actions, plus sleep/wake counters.
- **Swipe slider:** left half is the always-on counterfactual (every non-charging CP idle/red, kW burned); right half is the shown policy (kW burned, % less idle power). The divider is draggable and keyboard-movable and does not pan the map.
- **Props:** `NetworkMap` also takes `run`.

## Alternatives considered
- A, 3-colour waste/saved pies: readable, but static; it doesn't show the agent acting.
- B, full without/with-agent swipe compare: strong before/after story, but alone it shows no actions. Its slider is kept.
- C, county choropleth: clean, but hides individual stations and blurs where actions happen.
- D, live actions with ripples, ticker and headline card: makes the agent's effect visible as it happens. Chosen.

## Consequences
- A selected station is centred in the right ("with agent") half, so the divider handle never covers it.
- Transitions are found by diffing `cpState` between ticks. A seek or policy switch (>50% of CPs changing at once) is treated as a reset, with no animations.
- Watts still come from the hand-copied profiles in `frontend/src/map/energy.ts` (placeholder from 0062, must be kept in sync with `config/power_profiles.yaml`).
- The 6 per-state colours are no longer on the map; WAKING vs IDLE and STANDBY vs DEEP_SLEEP are not distinguished there.
