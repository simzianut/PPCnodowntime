# 0044: The map view is an interactive inline SVG, replacing MapLibre

- Date: 2026-11-07
- Who: tudor + tudor-claude
- Status: accepted
- Supersedes: — (changes spec 05 §1 and §3.1; T16 built the MapLibre version)
- Affects: spec 05, `frontend/src/components/NetworkMap.tsx`, `frontend/src/map/geo.ts`

## Context
- Tudor wants the simulator map to be the PPC-style SVG from decision 0043. The station icon comes from that file, and lines can be drawn in it by hand.
- MapLibre can't render that file as-is. Its tiles also come from a third-party server.

## Decision
- **Loading:** `NetworkMap` fetches `/map/romania.svg` and inlines it, so the file's `<style>`, `#lines` layer and `#station-icon` apply in the app.
  - Projection and size come from the file's `data-bounds` and `viewBox`. Regenerating the map with other bounds needs no code change.
  - The file's own placeholder `#stations` are removed. Stations come from the app's `locations`: the API, or the seed demo from decision 0041.
- **View:** one `translate(tx ty) scale(k)` on the base map. `k` runs from 1× to 40× (`MIN_K`, `MAX_K` in `geo.ts`).
  - The map fills the panel (`preserveAspectRatio slice`) and is clamped, so you can't pan off the map.
- **Controls:**
  - drag to pan; wheel, trackpad pinch or touch pinch to zoom at the pointer
  - double-click to zoom in; +, − and reset buttons
  - keyboard: arrows pan, +/− zoom, 0 resets
  - eased animation for the buttons, cluster clicks and selection
- **Constant screen size:** markers are drawn on top in viewBox units scaled by 1/fitScale, so they keep their size while zooming. Labels, borders and lines keep theirs through `--s` (screen px per map unit) and `vector-effect: non-scaling-stroke`.
- **Clustering:** greedy, with a 34 px screen radius, recomputed at quarter-octave zoom steps. Panning never changes the clusters.
  - Clusters are black bubbles with a location count, as on the PPC map. An indigo badge shows the number of asleep CPs (STANDBY + DEEP_SLEEP).
  - Clicking a cluster zooms to fit its members, at least 2× the current zoom.
  - Locations too close to separate even at 40× fan out on a ring from 12× zoom.
- **Station marker:** a state-share ring (spec 05 colours) around `<use href="#station-icon">`, scaled to 0.62. Custom icons should fit within radius 13 around (0, 0).
  - A selected station gets a black ring and the map flies to it at 6× or more. Low confidence gets a dashed ring. Clicking empty map deselects.
- **Dependency:** `maplibre-gl` is removed.

## Alternatives considered
- MapLibre with the SVG as an image overlay: double rendering, and the in-file icon and lines wouldn't drive the markers.
- d3-zoom or panzoom libraries: the needed math is about 100 lines and tested in `geo.test.ts`. No extra dependency.
- Supercluster: overkill for about 450 locations.

## Consequences
- Natural Earth detail is coarse past about 10×. Streets are not shown. If street-level detail is needed, regenerate the map from OSM extracts for the cities.
- Ticks re-render only the marker layer. The 364 KB base markup is set once.
- Each tick also re-runs `stateCounts` per marker. That is fine for about 450 locations; re-check at 288× if it ever stutters.
