# 0074: Bigger map key; drop run summary metrics from station detail

- Date: 2026-11-08
- Who: tudor + tudor-claude
- Status: accepted
- Supersedes: the run summary metrics bullet of spec 05 §3.4 (from CONTRACTS v0.2)
- Affects: spec 05, frontend map legend and station detail panel

## Context
Tudor found the map legend too small and unclear, and asked to remove current output, expected wait and anomaly score from the station detail panel. Teodora's T38 (PR #10) covered the same ground but switched pins to six exact-state colours; Tudor took the task over to keep the red/green/yellow scheme from 0067.

## Decision
- The map key lists the same four groups as before (idle red, asleep green, charging yellow, offline grey only when present) with larger swatches and a short plain-language hint each ("on, wasting power", "saving power", ...).
- Below it, a red → green bar labelled "all idle" / "all asleep" explains the colour of groups of stations (same RGB interpolation as `levelColor`).
- On narrow screens the hints are hidden.
- The station panel no longer shows current output, expected wait or anomaly score and no longer calls the summary endpoint. The API client function and the contract route stay, unused by the UI.

## Alternatives considered
- Six exact-state colours (PR #10): more precise but harder to read at a glance; rejected by Tudor.

## Consequences
The legend is taller (about 180 px) in the bottom-left corner. The backend summary route can be dropped later if nothing else uses it.
