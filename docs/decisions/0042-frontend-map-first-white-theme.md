# 0042: Map-first PPC dashboard presentation

- Date: 2026-11-07
- Who: teodora
- Status: accepted
- Supersedes: spec 05 layout and theme requirements
- Affects: frontend module, spec 05

## Context
The approved design brief calls for a PPC-branded, white-only map-first experience with an always-visible interactive map and a contextual station panel.

## Decision
Use a 32% overview/station sidebar and a 68% OpenFreeMap map on desktop. Selecting a station replaces the overview content and centers the map, while replay controls stay in the sidebar. On narrow screens, stack the map above the sidebar. Bundle PPC's positive logo from its official press kit and the SmartHack logo from its official site (user confirmed asset-use approval). Status marker colors follow the approved six-color palette; marker tooltips expose state shares and selection uses a black ring. Station current output, predicted wait, and anomaly score come only from the run-scoped summary contract and display unavailable when absent.

## Alternatives considered
- Keep the map in a card beside analysis: it gives the map less room and station details overlay it.
- Keep the dark theme option: the design approval specifies white only.
- Infer current kW from charger rating: rated capacity is not a live meter reading.

## Consequences
Desktop maps use the remaining workspace; mobile users get a stacked map-first layout. The summary panel remains useful before producers expose values by showing unavailable fields. Official assets are bundled locally for predictable rendering.

## Sources
- PPC press kit: https://www.ppcenergy.ro/media/birou-de-presa/press-kit/
- SmartHack official site: https://smarthack.asmi.ro/
