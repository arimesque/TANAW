"""Rebuild static/data/bacolor_barangays.geojson from the FULL-RESOLUTION
NAMRIA/PSA barangay shapefile.

Why this exists: the file TANAW shipped with was extracted from a national
GeoJSON that had been simplified with a 0.005 degree (~500 m) tolerance, which
reduces each barangay to 4-9 points. The unsimplified shapefile keeps the real
outlines.

How to get the source (needs normal internet access and git-lfs):
    git clone https://github.com/altcoder/philippines-psgc-shapefiles
    cd philippines-psgc-shapefiles && git lfs pull --include="data/2023/BgySubMuns/*"
The .shp is a ~270 MB Git LFS file. Then run, from the TANAW folder:
    pip install pyshp
    python scripts/build_barangay_boundaries.py path/to/BgySubMuns.shp

By default NO simplification is applied (--tolerance 0). Each barangay ring is
simplified on its own, so any tolerance above 0 can move the points along a
border that two neighbouring barangays share, leaving small gaps or overlaps
between them. With --tolerance 0 neighbours keep exactly the same border points.
Coordinates are rounded to 6 decimals (about 0.1 m), which keeps shared points
identical and makes the file smaller. If the file turns out too big for phones,
try a small tolerance such as 0.000005 and check the borders on the map.

Windows PowerShell example:
    pip install pyshp
    python scripts/build_barangay_boundaries.py C:\\path\\to\\BgySubMuns.shp --tolerance 0
Then start the app, open the map, switch on "Barangay boundaries" and check that
all 21 barangays are drawn, the outlines are smooth, and neighbours touch.
"""

import argparse
import json
import sys
from pathlib import Path

import shapefile  # pyshp

BACOLOR_PCODE = "PH0305404"  # PSGC municipality code for Bacolor, Pampanga
OUTPUT = Path(__file__).resolve().parent.parent / "static" / "data" / "bacolor_barangays.geojson"


def perpendicular_distance(point, start, end):
    (x, y), (x1, y1), (x2, y2) = point, start, end
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return ((x - x1) ** 2 + (y - y1) ** 2) ** 0.5
    return abs(dy * x - dx * y + x2 * y1 - y2 * x1) / (dx * dx + dy * dy) ** 0.5


def simplify(points, tolerance):
    """Douglas-Peucker (iterative, safe for very long rings)."""
    if tolerance <= 0 or len(points) < 5:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        far_index, far_dist = None, 0.0
        for i in range(first + 1, last):
            d = perpendicular_distance(points[i], points[first], points[last])
            if d > far_dist:
                far_index, far_dist = i, d
        if far_index is not None and far_dist > tolerance:
            keep[far_index] = True
            stack.extend([(first, far_index), (far_index, last)])
    return [p for p, k in zip(points, keep) if k]


def ring_list(shape):
    """Split a shapefile polygon into rings; pyshp marks starts in .parts."""
    bounds = list(shape.parts) + [len(shape.points)]
    return [shape.points[bounds[i]:bounds[i + 1]] for i in range(len(shape.parts))]


def signed_area(ring):
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1])) / 2


def clean_ring(ring, tolerance):
    """Simplify (optional), round to 6 decimals, and drop repeated points."""
    points = simplify([tuple(p) for p in ring], tolerance)
    rounded = []
    for x, y in points:
        point = (round(x, 6), round(y, 6))
        if not rounded or rounded[-1] != point:
            rounded.append(point)
    return rounded


def to_geometry(shape, tolerance):
    """Group rings into polygons: clockwise rings are outer shells, the
    counter-clockwise rings that follow are holes (ESRI convention)."""
    polygons = []
    for ring in ring_list(shape):
        ring = clean_ring(ring, tolerance)
        if len(ring) < 4:
            continue
        if signed_area(ring) < 0:  # clockwise = outer shell
            polygons.append([[list(p) for p in ring[::-1]]])  # GeoJSON wants CCW outers
        elif polygons:
            polygons[-1].append([list(p) for p in ring[::-1]])  # holes are CW in GeoJSON
    if not polygons:
        return None
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def build(source, tolerance):
    reader = shapefile.Reader(str(source))
    fields = [f[0] for f in reader.fields[1:]]
    features = []
    for record, shape in zip(reader.iterRecords(), reader.iterShapes()):
        props = dict(zip(fields, record))
        if props.get("ADM3_PCODE") != BACOLOR_PCODE:
            continue
        geometry = to_geometry(shape, tolerance)
        if geometry:
            keep = {k: props[k] for k in ("ADM4_EN", "ADM4_PCODE", "ADM3_EN", "ADM3_PCODE", "ADM2_EN") if k in props}
            features.append({"type": "Feature", "properties": keep, "geometry": geometry})
    return {"type": "FeatureCollection", "features": features}


def check_collection(collection):
    """Return a list of problems found in the built GeoJSON (empty = fine)."""
    problems = []
    for feature in collection["features"]:
        name = feature["properties"].get("ADM4_EN", "?")
        geometry = feature["geometry"]
        polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        for polygon in polygons:
            for ring in polygon:
                if len(ring) < 4 or ring[0] != ring[-1]:
                    problems.append(f"{name}: a ring is not closed or has fewer than 4 points")
                for lon, lat in ring:
                    # Bacolor, Pampanga is near 15.0 N, 120.65 E.
                    if not (14.8 <= lat <= 15.2 and 120.5 <= lon <= 120.8):
                        problems.append(f"{name}: point ({lon}, {lat}) is outside the expected Bacolor area")
                        break
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("shapefile", help="Path to the national barangay .shp file")
    parser.add_argument("--tolerance", type=float, default=0.0,
                        help="Simplification in degrees (default 0 = none, keeps shared borders exact)")
    parser.add_argument("--output", default=str(OUTPUT))
    args = parser.parse_args()

    collection = build(args.shapefile, args.tolerance)
    if len(collection["features"]) != 21:
        sys.exit(f"Expected 21 Bacolor barangays but found {len(collection['features'])}. "
                 "Check that this is the PSA/NAMRIA barangay shapefile (ADM3_PCODE PH0305404).")

    problems = check_collection(collection)
    if problems:
        sys.exit("The result looks wrong, nothing was written:\n  " + "\n  ".join(problems[:10]))

    Path(args.output).write_text(json.dumps(collection, separators=(",", ":")), encoding="utf-8")
    vertices = sum(len(r) for f in collection["features"]
                   for poly in ([f["geometry"]["coordinates"]] if f["geometry"]["type"] == "Polygon" else f["geometry"]["coordinates"])
                   for r in poly)
    size_kb = Path(args.output).stat().st_size / 1024
    print(f"Wrote {len(collection['features'])} barangays ({vertices} vertices, {size_kb:,.0f} KB) to {args.output}")


if __name__ == "__main__":
    main()
