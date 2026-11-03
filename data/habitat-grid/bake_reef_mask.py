#!/usr/bin/env python3
"""Bake 16x16 reef-cover masks per z10 cell (#255.1).

The reef map (`reef.json.gz`) carries one share per cell. The game
needs to know WHERE in the cell the reef sits, so this bakes a
16x16 grid of reef shares per cell in the reef extent: each value
is the exact reef-polygon clip over that subcell's box, rounded to
0.05, row-major with the first row on the north side. Cells with no
reef get no entry (a missing mask means no reef, like a missing
share); the mean of a mask matches the cell's share within 0.01.

Source: the Complete GBR Reef and Island Features V1b shapefile
(eAtlas, CC BY 3.0 AU; see ../README), `LEVEL_1 == 'Reef'`
polygons in lon/lat degrees, the same polygons and projection
#226 baked the shares from. Only the masks are committed, never
the shapefile.

Usage (from the repo root)::

    uv run python data/habitat-grid/bake_reef_mask.py \\
        --shapefile /tmp/reef-staging/gbr/features.shp
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

GRID = Path(__file__).resolve().parent
sys.path.insert(0, str(GRID.parent.parent))

from builder.grid import bounds  # noqa: E402

N = 16
STEP = 0.05


def load_reef_polys(shapefile: Path) -> list:
    """The LEVEL_1 == Reef polygons as shapely geometries."""
    import fiona
    from shapely.geometry import shape

    polys = []
    with fiona.open(shapefile) as source:
        for feature in source:
            if (feature["properties"] or {}).get("LEVEL_1") != "Reef":
                continue
            geometry = shape(feature["geometry"])
            if not geometry.is_empty:
                polys.append(geometry)
    if not polys:
        raise SystemExit(f"no LEVEL_1 == Reef polygons in {shapefile}")
    return polys


def bake_cell(polys: list, index: object, col: int, row: int) -> list[float] | None:
    """The cell's 16x16 reef shares, or nothing when reef-free."""
    from shapely.geometry import box

    min_lat, max_lat, min_lon, max_lon = bounds(f"z10_{col:04d}_{row:04d}")
    d_lon = (max_lon - min_lon) / N
    d_lat = (max_lat - min_lat) / N
    hits = index.query(box(min_lon, min_lat, max_lon, max_lat))
    if not len(hits):
        return None
    near = [polys[i] for i in hits]
    mask = [0.0] * (N * N)
    for r in range(N):
        top = max_lat - r * d_lat
        bottom = top - d_lat
        for c in range(N):
            left = min_lon + c * d_lon
            sub = box(left, bottom, left + d_lon, top)
            area = 0.0
            for poly in near:
                clipped = poly.intersection(sub)
                if not clipped.is_empty:
                    area += clipped.area
            share = area / sub.area if sub.area else 0.0
            mask[r * N + c] = round(min(1.0, max(0.0, share)) / STEP) * STEP
    if not any(mask):
        return None
    return [round(v, 3) for v in mask]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shapefile", type=Path, required=True)
    parser.add_argument("--reef", type=Path, default=GRID / "reef.json.gz")
    args = parser.parse_args()

    from shapely.strtree import STRtree

    reef = json.loads(gzip.open(args.reef, "rt", encoding="utf-8").read())
    extent = reef["extent"]
    (x0, x1), (y0, y1) = extent["x"], extent["y"]
    cells = reef.get("cells", {})
    print(f"extent x {x0}-{x1}, y {y0}-{y1}; {len(cells)} shared cells")

    polys = load_reef_polys(args.shapefile)
    print(f"{len(polys)} reef polygons")
    index = STRtree(polys)

    masks: dict[str, list[float]] = {}
    for col in range(x0, x1):
        for row in range(y0, y1):
            mask = bake_cell(polys, index, col, row)
            if mask is not None:
                masks[f"{col},{row}"] = mask
    print(f"{len(masks)} masked cells")

    worst = 0.0
    for key, mask in masks.items():
        mean = sum(mask) / len(mask)
        diff = abs(mean - cells.get(key, 0.0))
        worst = max(worst, diff)
        if diff > 0.01:
            raise SystemExit(f"{key}: mask mean {mean:.4f} vs share {cells.get(key)}")
    missing = [k for k in cells if cells[k] > 0 and k not in masks]
    if missing:
        raise SystemExit(f"shared cells without masks: {missing[:5]}")
    print(f"mask means match shares (worst {worst:.4f})")

    reef["masks"] = {key: masks[key] for key in sorted(masks)}
    with gzip.open(args.reef, "wt", encoding="utf-8") as handle:
        json.dump(reef, handle, separators=(",", ":"))
    print(f"wrote {args.reef}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
