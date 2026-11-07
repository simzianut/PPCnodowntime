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
  - The file's `<style>` rules are prefixed with `.base-map` on load, so they can't restyle the rest of the app.
  - Projection and size come from the file's `data-bounds` and `viewBox`. Regenerating the map with other bounds needs no code change.
  - The file's own placeholder `#stations` are removed. Stations come from the app's `locations`: the API, or the seed demo from decision 0041.
- **View:** one `translate(tx ty) scale(k)` on the base map. `k` runs up to 32× (`MAX_K` in `geo.ts`).
  - The map fills the panel (`preserveAspectRatio slice`).
  - Pan limits use the part of the map actually on screen. You can zoom out until the whole map fits, so on a phone every station is reachable.
  - The home view and the reset button fit all stations.
- **Controls:**
  - drag to pan; wheel, trackpad pinch (including Safari gesture events) or touch pinch to zoom at the pointer
  - double-click to zoom in; +, − and reset buttons
  - keyboard: arrows pan, +/− zoom, 0 resets, Escape deselects
  - eased animation for the buttons, cluster clicks and selection
- **Constant screen size:** markers are drawn on top in viewBox units scaled by 1/fitScale, so they keep their size while zooming. Labels, borders and lines keep theirs through `--s` (screen px per map unit) and `vector-effect: non-scaling-stroke`.
- **Clustering:** greedy, with a 34 px screen radius, recomputed at quarter-octave zoom steps. Panning never changes the clusters.
  - Clusters are black bubbles with a location count, as on the PPC map. An indigo badge shows the number of asleep CPs (STANDBY + DEEP_SLEEP).
  - Clicking a cluster zooms to fit its members, at least 2× the current zoom.
  - Locations that would still cluster at 32× fan out on a ring from 12× zoom, so every station can be selected.
  - The selected station is never clustered.
- **Station marker:** a state-share ring (spec 05 colours) around `<use href="#station-icon">`, scaled to 0.62. Custom icons should fit within radius 13 around (0, 0).
  - A selected station gets a black ring and the map flies to it at 6× or more. Low confidence gets a dashed ring. A single click on empty map deselects, after a 260 ms delay so that a double-click zoom keeps the selection.
- **Dependency:** `maplibre-gl` is removed.

## Alternatives considered
- MapLibre with the SVG as an image overlay: double rendering, and the in-file icon and lines wouldn't drive the markers.
- d3-zoom or panzoom libraries: the needed math is about 100 lines and tested in `geo.test.ts`. No extra dependency.
- Supercluster: overkill for about 450 locations.

## Consequences
- A drag survives the pointer leaving the map: `pointerup` is watched on `window`, and a mouse move with no button held ends it.
- Natural Earth detail is coarse past about 10×. Streets are not shown. If street-level detail is needed, regenerate the map from OSM extracts for the cities.
- Ticks re-render only the marker layer. The 364 KB base markup is set once.
- Each tick also re-runs `stateCounts` per marker. That is fine for about 450 locations; re-check at 288× if it ever stutters.
