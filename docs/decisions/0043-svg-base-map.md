# 0043: SVG base map of Romania for the map view, in a PPC Blue map style

- Date: 2026-11-07
- Who: tudor + tudor-claude
- Status: accepted
- Supersedes: —
- Affects: spec 05 §1 and §3.1, T16, T21

## Context
- Tudor wants the map view to look like the public map at ppcblue.ro/ro/retea-incarcare/harta-statii-publice, with two changes:
  - our own station icons instead of PPC's logos
  - lines drawn directly in the map file
- An embed or a tile map can't do either, so we need our own map file.
- ppcblue.ro answers automated requests with an Imperva bot wall, which decision 0003 forbids bypassing.
- At Tudor's request, the agent opened the page in Tudor's own Chrome to look at it. It is Google Maps with a custom grey style.
  - The stations come from PPC's private API (`api.ppcblue.ro/api/partners/charge-points/clustered`), which decision 0014 rejects.
  - The agent's one attempt to call that API directly was blocked as access circumvention. No PPC station data was taken.

## Decision
- **One static SVG**, `frontend/public/map/romania.svg`, built by `frontend/tools/build_map_svg.py`.
- **Base data:** Natural Earth 10m, which is public domain: countries, Romanian counties, urban areas, lakes, rivers, roads and cities.
  - The raw files stay in `data/raw/naturalearth/` (gitignored). Get them from github.com/nvkelso/natural-earth-vector, `geojson/`.
- **Projection:** Web Mercator, as Google-style web maps use.
  - Bounds: lon 19.9–30.1, lat 43.35–48.55. Width 1600 units, height 1174.4.
  - `x = rad(lon − 19.9) · S` and `y = (merc(48.55) − merc(lat)) · S`, where `S = 1600 / rad(10.2)` and `merc(φ) = ln tan(π/4 + φ/2)`.
  - `python frontend/tools/build_map_svg.py --project LAT LON` prints x, y.
- **Style:** copied by eye, with colours sampled from a screenshot of the PPC map:
  - land `#f2f2f2`, the same for neighbouring countries, with no shading
  - water `#44bcea`
  - country borders solid dark grey; county borders dotted grey
  - roads white with a light grey casing
  - cities as hollow dots, dark grey text with a white halo
  - Romanian labels (Google shows English and local names)
- **Layers** are `<g>` elements with ids, bottom to top: `sea`, `neighbours`, `land-ro`, `urban`, `lakes`, `rivers`, `counties`, `roads`, `border`, `labels`, `lines`, `stations`.
  - `lines` is empty, for hand-drawn paths. Default stroke: PPC blue `#0057b8`.
- **Stations:** each one is `<use href="#station-icon">`, with its anchor point at x, y and `data-*` attributes (name, county, lat, lon, confidence).
  - Replacing `<g id="station-icon">` in `<defs>` replaces every marker. Draw the new icon centred on (0, 0).
  - The placeholder icon is a plain navy disc with a pale green halo, the shape of PPC's marker without its logo.
  - Low-confidence stations get `class="low"`.
- **Placeholder station list:** `data/seed/stations_open.csv` (116 locations), not ppcblue.ro. The real list comes in a later PR.

## Alternatives considered
- Embed the PPC Blue map: we couldn't swap its icons or draw on it.
- MapLibre with the positron style (spec 05 §1): lines and icons would live in code, not in a file you can edit by hand.
- PPC stations from its private API, read through Tudor's browser: blocked as access circumvention (see Context), and decision 0014 rejects it anyway.
- OSM roads at full detail: far heavier for a country-wide view. Natural Earth is enough at this scale.

## Consequences
- Natural Earth roads are sparse and some motorway segments are out of date.
- A static SVG has no zoom levels. The PPC map's black count bubbles and the clustering in spec 05 §3.1 must be done in app code if we keep this map.
- Whether the app's map view (T16, MapLibre today) switches to this SVG is still open. Tudor and Teodora decide.
