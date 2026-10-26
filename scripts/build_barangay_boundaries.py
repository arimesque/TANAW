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

Only a very small simplification (default 0.00001 deg, about 1 m) is applied
to drop redundant points; real bends and curves are preserved.
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


def to_geometry(shape, tolerance):
    """Group rings into polygons: clockwise rings are outer shells, the
    counter-clockwise rings that follow are holes (ESRI convention)."""
    polygons = []
    for ring in ring_list(shape):
        ring = simplify([tuple(p) for p in ring], tolerance)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("shapefile", help="Path to the national barangay .shp file")
    parser.add_argument("--tolerance", type=float, default=0.00001, help="Simplification in degrees (default 0.00001)")
    parser.add_argument("--output", default=str(OUTPUT))
    args = parser.parse_args()

    collection = build(args.shapefile, args.tolerance)
    if len(collection["features"]) != 21:
        sys.exit(f"Expected 21 Bacolor barangays but found {len(collection['features'])}. "
                 "Check that this is the PSA/NAMRIA barangay shapefile (ADM3_PCODE PH0305404).")

    Path(args.output).write_text(json.dumps(collection, separators=(",", ":")), encoding="utf-8")
    vertices = sum(len(r) for f in collection["features"]
                   for poly in ([f["geometry"]["coordinates"]] if f["geometry"]["type"] == "Polygon" else f["geometry"]["coordinates"])
                   for r in poly)
    print(f"Wrote {len(collection['features'])} barangays ({vertices} vertices) to {args.output}")


if __name__ == "__main__":
    main()
