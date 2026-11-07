# 0063: Map clusters are energy-saved colour blobs, not numbered bubbles

- Date: 2026-11-07
- Who: tudor + tudor-claude
- Status: accepted
- Supersedes: the cluster-bubble part of [0046](0046-interactive-svg-map.md) (black bubble with location count and asleep badge)
- Affects: spec 05, `frontend/src/components/NetworkMap.tsx`, `frontend/src/map/energy.ts`, `frontend/src/App.css`

## Context
- Tudor wants clusters to show at a glance how much energy each area is saving, instead of a count.
- The API has no per-station energy-saved figure. Kpis (`saved_pct`) are per policy and network-wide.
- The frontend already streams each CP's state for the shown policy.

## Decision
- **Look:** a cluster is a soft glowing blob: radial gradient, opaque centre fading to transparent, `mix-blend-mode: multiply`. No number.
  - Radius 18 + 4·log2(n) px, capped at 34. Grows 12% on hover. Clicking still zooms in.
  - `aria-label`/`title` give location count, CP count and average saved percentage.
  - The legend gets a "Cluster energy saved low → high" gradient bar. Single-station markers are unchanged.
- **Station level (0–1)** in `map/energy.ts`: sum over its CPs of watts saved vs IDLE in the current state ÷ sum of the maximum possible saving (every CP in its deepest sleep).
  - STANDBY saves idle_w − standby_w; DEEP_SLEEP saves idle_w − deep_sleep_w (AC_22 has none, so it uses standby_w). IDLE, WAKING, CHARGING and OFFLINE save 0.
  - Normalised as "share of possible saving realised", so a fully asleep station reads 100% whatever its hardware.
- **Cluster level:** plain arithmetic mean of member station levels, unweighted by CP count (Tudor's choice).
- **Colour:** hsl(level·125, 82%, lightness 50→42%): red at 0, yellow at 0.5, green at 1.
- **Watts:** copied from `config/power_profiles.yaml` into `energy.ts` (AC_22 25/9/–, DC_50 105/35/20, DC_HPC 165/50/25.5). Unknown power class falls back to DC_50.

## Alternatives considered
- Mean weighted by CP count: big hubs would dominate; Tudor preferred each location to count once.
- Per-policy `saved_pct` from Kpis: network-wide only, so every blob would be the same colour.

## Consequences
- The duplicated watts are a placeholder shortcut. They must be kept in sync with `config/power_profiles.yaml` by hand; the proper fix is an API route exposing the power profiles.
- Before a replay starts, or under `always_on`, nothing is asleep, so every blob is red.
- The asleep-CP count is no longer shown on clusters. Each station marker still shows its asleep CPs in its state ring, and the sidebar shows the network total.
