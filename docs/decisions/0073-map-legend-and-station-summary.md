# 0073: Map legend and compact station summary

- Date: 2026-11-08
- Who: teodora-codex
- Status: accepted
- Supersedes: marker and station-level display portions of [0067](0067-cluster-energy-saved-blobs.md)
- Affects: spec 05, frontend map and station detail panel

## Context
The map uses a cluster gradient based on mean asleep share, while individual markers and station details need an explicit six-state status key. Operators also asked to simplify station details and remove values that are not needed for this view. The base SVG contained incomplete empty path commands that generated browser console errors.

## Decision
- Keep cluster color as mean per-station asleep share over IDLE/WAKING and STANDBY/DEEP_SLEEP charge points; exclude CHARGING and OFFLINE. If no eligible points exist, retain the existing charging-yellow/offline-grey fallback.
- Color each individual station pin by its most common exact charge-point state. Resolve ties in the displayed state palette order: CHARGING, IDLE, STANDBY, WAKING, DEEP_SLEEP, OFFLINE. Tooltips show all state counts.
- Show a prominent two-part legend with the asleep-share cluster gradient and six exact state colors.
- Remove malformed `MZ` empty path placeholders from the base SVG; they draw nothing and produce SVG path parse errors.
- Limit selected-station details to name, location, status mix, and energy saved percentage. Compute energy saved against idle draw for charge points other than CHARGING and OFFLINE, matching the network comparison. Show unavailable when the baseline is zero.
- Omit current output, expected wait, anomaly score, partner/source metadata, and CP timelines/charts/commands from the panel.

## Alternatives considered
- Label the cluster gradient as energy saved: that would contradict the current cluster calculation and misstate what marker colors mean.
- Use a segmented marker for mixed charge-point states: the dominant state keeps pins simple while the tooltip preserves the full mix.
- Keep the detailed CP timeline panel: the requested station summary prioritizes location status and energy savings.

## Consequences
The legend explains both map encodings without changing the asleep-share cluster algorithm. Station energy saved uses the same eligibility rules as the network-level idle draw comparison.
