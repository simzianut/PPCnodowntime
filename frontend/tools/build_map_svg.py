"""Build the SVG base map of Romania (decision 0041)."""

import argparse
import csv
import json
import math
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
import shapely
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon, box, shape

ROOT = Path(__file__).resolve().parents[2]
NE_DIR = ROOT / "data/raw/naturalearth"
STATIONS = ROOT / "data/seed/stations_open.csv"
OUT = ROOT / "frontend/public/map/romania.svg"

LON_MIN, LON_MAX, LAT_MIN, LAT_MAX = 19.9, 30.1, 43.35, 48.55
WIDTH = 1600.0

CITY_NAMES = {
    "Bucharest": "București",
    "Tirgu Mures": "Târgu Mureș",
    "Alba Lulia": "Alba Iulia",
    "Miercurea Cuic": "Miercurea Ciuc",
    "Piatra-Neamt": "Piatra Neamț",
    "Rimnicu Vilcea": "Râmnicu Vâlcea",
    "Sfintu-Gheorghe": "Sfântu Gheorghe",
}
AREA_LABELS = [
    ("România", 24.95, 46.05, "country-ro"),
    ("Ungaria", 20.95, 47.75, "country"),
    ("Serbia", 20.7, 44.35, "country"),
    ("Bulgaria", 25.3, 43.55, "country"),
    ("Ucraina", 26.6, 48.35, "country"),
    ("Moldova", 28.6, 47.15, "country"),
    ("Marea Neagră", 29.7, 43.95, "sea"),
]


def merc_y(lat: float) -> float:
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


SCALE = WIDTH / math.radians(LON_MAX - LON_MIN)
Y_TOP = merc_y(LAT_MAX)
HEIGHT = (Y_TOP - merc_y(LAT_MIN)) * SCALE


def project(lon: float, lat: float) -> tuple[float, float]:
    return math.radians(lon - LON_MIN) * SCALE, (Y_TOP - merc_y(lat)) * SCALE


def proj_geom(g):
    def f(c: np.ndarray) -> np.ndarray:
        x = np.radians(c[:, 0] - LON_MIN) * SCALE
        y = (Y_TOP - np.log(np.tan(np.pi / 4 + np.radians(c[:, 1]) / 2))) * SCALE
        return np.column_stack([x, y])

    return shapely.transform(g, f)


VIEW = box(LON_MIN - 0.5, LAT_MIN - 0.5, LON_MAX + 0.5, LAT_MAX + 0.5)


def load(name: str):
    return json.loads((NE_DIR / f"{name}.geojson").read_text())["features"]


def clipped(features, tol: float, keep=lambda p: True):
    for f in features:
        if not keep(f["properties"]):
            continue
        g = shape(f["geometry"])
        if not g.intersects(VIEW):
            continue
        g = proj_geom(g.intersection(VIEW)).simplify(tol, preserve_topology=True)
        if not g.is_empty:
            yield f["properties"], g


def fmt(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".")


def path_d(g) -> str:
    parts = []
    if isinstance(g, (Polygon, MultiPolygon)):
        for poly in getattr(g, "geoms", [g]):
            for ring in [poly.exterior, *poly.interiors]:
                parts.append("M" + "L".join(f"{fmt(x)},{fmt(y)}" for x, y in ring.coords) + "Z")
    elif isinstance(g, (LineString, MultiLineString)):
        for line in getattr(g, "geoms", [g]):
            parts.append("M" + "L".join(f"{fmt(x)},{fmt(y)}" for x, y in line.coords))
    elif hasattr(g, "geoms"):
        parts.extend(path_d(sub) for sub in g.geoms)
    return "".join(parts)


def paths(items, attrs=lambda p: "") -> str:
    out = []
    for props, g in items:
        d = path_d(g)
        if d:
            out.append(f'    <path{attrs(props)} d="{d}"/>')
    return "\n".join(out)


METADATA = (
    "Base map: Natural Earth 10m (public domain). Stations: data/seed/stations_open.csv"
    " (OSM ODbL, OCM CC BY 4.0). Projection: decision 0041."
)

STYLE = """
    svg { font-family: Roboto, "Helvetica Neue", Arial, sans-serif; }
    #sea, #lakes path { fill: #44bcea; }
    #neighbours path { fill: #f2f2f2; stroke: #4a4a4a; stroke-width: 0.9; }
    #land-ro path { fill: #f2f2f2; }
    #urban path { fill: #ebebeb; }
    #rivers path { fill: none; stroke: #44bcea; stroke-linecap: round; stroke-linejoin: round; }
    #counties path { fill: none; stroke: #8c8c8c; stroke-width: 0.9; stroke-dasharray: 1.2 2.6;
      stroke-linecap: round; }
    #roads path { fill: none; stroke-linecap: round; stroke-linejoin: round; }
    #roads .casing { stroke: #dddddd; stroke-width: 2.6; }
    #roads .road { stroke: #ffffff; stroke-width: 1.6; }
    #roads .motorway-casing { stroke: #d2d2d2; stroke-width: 3.6; }
    #roads .motorway { stroke: #ffffff; stroke-width: 2.4; }
    #border path { fill: none; stroke: #3c3c3c; stroke-width: 1.3; }
    #labels text { paint-order: stroke; stroke: #ffffff; stroke-width: 3; stroke-linejoin: round; }
    #labels .city { fill: #3c3c3c; font-size: 12px; }
    #labels .capital { fill: #3c3c3c; font-size: 15px; font-weight: 500; }
    #labels .city-dot { fill: #ffffff; stroke: #3c3c3c; stroke-width: 1.2; }
    #labels .country { fill: #616161; font-size: 15px; }
    #labels .country-ro { fill: #616161; font-size: 24px; font-weight: 700; }
    #labels .sea { fill: #ffffff; stroke: none; font-size: 14px; }
    #lines path, #lines line, #lines polyline {
      fill: none; stroke: #0057b8; stroke-width: 2.5; stroke-linecap: round; }
    #stations .low { opacity: 0.55; }
"""

STATION_ICON = """    <g id="station-icon">
      <circle r="13" fill="#9fbf9f" fill-opacity="0.55"/>
      <circle r="10" fill="#0b1a2a"/>
    </g>"""


def build(out: Path) -> None:
    countries = list(load("ne_10m_admin_0_countries"))
    neighbours = clipped(countries, 0.4, lambda p: p["ADMIN"] != "Romania")
    romania = list(clipped(countries, 0.3, lambda p: p["ADMIN"] == "Romania"))
    admin1 = load("ne_10m_admin_1_states_provinces")
    counties = clipped(admin1, 0.3, lambda p: p["admin"] == "Romania")
    ro_shape = romania[0][1]
    urban = [(p, g.intersection(ro_shape)) for p, g in clipped(load("ne_10m_urban_areas"), 0.4)]
    lakes = clipped(load("ne_10m_lakes"), 0.3)
    rivers = [
        *clipped(load("ne_10m_rivers_lake_centerlines"), 0.4),
        *clipped(load("ne_10m_rivers_europe"), 0.4, lambda p: (p.get("scalerank") or 99) <= 10),
    ]
    road_feats = list(
        clipped(
            load("ne_10m_roads"), 0.4, lambda p: p["type"] != "Ferry Route" and p["scalerank"] <= 8
        )
    )
    minor = [(p, g) for p, g in road_feats if not p["expressway"]]
    motor = [(p, g) for p, g in road_feats if p["expressway"]]

    def river_attrs(p):
        rank = p.get("scalerank") or 10
        width = 3.5 if rank <= 2 else 1.4 if rank <= 9 else 0.9
        return f' stroke-width="{fmt(width)}"'

    labels = []
    for f in load("ne_10m_populated_places_simple"):
        p = f["properties"]
        if p["adm0name"] != "Romania":
            continue
        x, y = project(*f["geometry"]["coordinates"])
        name = escape(CITY_NAMES.get(p["name"], p["name"]))
        cls = "capital" if p["name"] == "Bucharest" else "city"
        r = 4 if cls == "capital" else 2.6
        labels.append(f'    <circle class="city-dot" cx="{fmt(x)}" cy="{fmt(y)}" r="{r}"/>')
        tx, ty = fmt(x + r + 3), fmt(y + 4)
        labels.append(f'    <text class="{cls}" x="{tx}" y="{ty}">{name}</text>')
    for name, lon, lat, cls in AREA_LABELS:
        x, y = project(lon, lat)
        labels.append(
            f'    <text class="{cls}" x="{fmt(x)}" y="{fmt(y)}" text-anchor="middle">'
            f"{escape(name)}</text>"
        )

    stations = []
    with STATIONS.open(newline="", encoding="utf-8") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=1):
            x, y = project(float(row["lon"]), float(row["lat"]))
            cls = ' class="low"' if row["confidence"] == "low" else ""
            stations.append(
                f'    <use id="st-{i:03d}" href="#station-icon" x="{fmt(x)}" y="{fmt(y)}"{cls}'
                f' data-name="{escape(row["name"], {chr(34): "&quot;"})}"'
                f' data-county="{escape(row["county"])}"'
                f' data-lat="{row["lat"]}" data-lon="{row["lon"]}"'
                f' data-confidence="{row["confidence"]}"/>'
            )

    w, h = fmt(WIDTH), fmt(HEIGHT)
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}"
     data-projection="web-mercator" data-bounds="{LON_MIN},{LAT_MIN},{LON_MAX},{LAT_MAX}">
  <title>Romania charging network</title>
  <metadata>{METADATA}</metadata>
  <defs>
    <style>{STYLE}    </style>
{STATION_ICON}
  </defs>
  <rect id="sea" x="0" y="0" width="{w}" height="{h}"/>
  <g id="neighbours">
{paths(neighbours)}
  </g>
  <g id="land-ro">
{paths(romania)}
  </g>
  <g id="urban">
{paths(urban)}
  </g>
  <g id="lakes">
{paths(lakes)}
  </g>
  <g id="rivers">
{paths(rivers, river_attrs)}
  </g>
  <g id="counties">
{paths(counties)}
  </g>
  <g id="roads">
{paths(minor, lambda p: ' class="casing"')}
{paths(minor, lambda p: ' class="road"')}
{paths(motor, lambda p: ' class="motorway-casing"')}
{paths(motor, lambda p: ' class="motorway"')}
  </g>
  <g id="border">
{paths(romania)}
  </g>
  <g id="labels">
{chr(10).join(labels)}
  </g>
  <g id="lines">
  </g>
  <g id="stations">
{chr(10).join(stations)}
  </g>
</svg>
"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg, encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB, {len(stations)} stations, {w}x{h})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--project", nargs=2, type=float, metavar=("LAT", "LON"), help="print SVG x,y")
    args = ap.parse_args()
    if args.project:
        x, y = project(args.project[1], args.project[0])
        print(f"x={fmt(x)} y={fmt(y)}")
    else:
        build(args.out)
