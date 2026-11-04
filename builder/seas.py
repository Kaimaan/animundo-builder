"""Which named sea covers an open-ocean cell (#177.2).

The lookup is baked once from Natural Earth's marine areas
(`ne_10m_geography_marine_polys`) into `data/seas.json.gz`:
`{tile: sea id}` for every open-ocean grid cell a named sea
covers. The tile maker reads the baked file; a cell with no
entry stays blocked `open_water`.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

SOURCE = "naturalearth:ne_10m_geography_marine_polys:5.1.0"
NAMES_SOURCE = "naturalearth:ne_10m_geography_marine_polys:5.1.2"


def load_seas(path: Path) -> dict[str, str]:
    """The baked `{tile: sea id}` table."""
    data = json.loads(gzip.open(path, "rt", encoding="utf-8").read())
    return dict(data.get("cells", {}))


def require_seas(builder_root: Path) -> None:
    """The baked sea names exist, else loud at startup (#272.5).

    `data/seas.json.gz` is builder data, not a grid sibling, so
    it gets its own gate beside `require_grid`: without it every
    open-sea tile would block as `open_water`, looking real.
    """
    path = builder_root / "data" / "seas.json.gz"
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} is missing: open-sea tiles cannot name their sea"
        )


def sea_at(cells: dict[str, str], tile: str) -> str | None:
    """The named sea covering the cell, or nothing when unnamed."""
    return cells.get(tile)


def bake_seas(
    grid_dir: Path, shapes: Path, names: dict[str, Any], out_path: Path
) -> dict[str, Any]:
    """Bake `{tile: sea id}` for every named open-ocean cell.

    `grid_dir` holds the baked `z10.json.gz` grid, `shapes`
    the marine areas shapefile, `names` the world's `seas/names.json`
    `seas` table. A cell's sea is the table id whose `ne_ids` hold
    the covering area; the smallest area wins when areas overlap.
    Every table id must exist in the shapes, or the bake refuses.
    Returns the written document.
    """
    import fiona
    from shapely.geometry import shape
    from shapely.strtree import STRtree

    want: dict[int, str] = {}
    for sea_id, entry in names.items():
        for ne_id in entry.get("ne_ids", []) or []:
            want[int(ne_id)] = sea_id
    geoms = []
    ne_ids = []
    for feature in fiona.open(shapes):
        ne_ids.append(int(float(feature["properties"]["ne_id"])))
        geoms.append(shape(feature["geometry"]))
    missing = sorted(set(want) - set(ne_ids))
    if missing:
        raise ValueError(f"{len(missing)} table seas miss their shapes: {missing[:5]}")
    tree = STRtree(geoms)
    cells: dict[str, str] = {}
    path = grid_dir / "z10.json.gz"
    if path.is_file():
        cells.update(_bake_grid(path, tree, geoms, ne_ids, want))
    doc = {
        "source": SOURCE,
        "names": NAMES_SOURCE,
        "cells": cells,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_path, "wt", encoding="utf-8") as handle:
        json.dump(doc, handle, sort_keys=True)
    return doc


def _bake_grid(
    path: Path,
    tree: Any,
    geoms: list[Any],
    ne_ids: list[int],
    want: dict[int, str],
) -> dict[str, str]:
    from shapely.geometry import Point

    from .grid import center, tile_id
    from .habitats import HabitatGrid

    cells: dict[str, str] = {}
    baked = HabitatGrid.load(path)
    for key in baked.cells():
        col, row = (int(v) for v in key.split(","))
        try:
            habitat = baked.habitat(col, row)
        except (KeyError, ValueError):
            continue
        if habitat != "open_ocean":
            continue
        tile = tile_id(col, row)
        lat, lon = center(tile)
        point = Point(lon, lat)
        hits = [
            (geoms[i].area, ne_ids[i])
            for i in tree.query(point)
            if geoms[i].intersects(point) and ne_ids[i] in want
        ]
        if not hits:
            continue
        hits.sort(key=lambda hit: hit[0])
        cells[tile] = want[hits[0][1]]
    return cells
