"""Named seas per open-ocean cell: bake and lookup. No network."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from builder.grid import center, tile_id
from builder.seas import bake_seas, load_seas, sea_at


def _grid(tmp_path: Path, cells: dict[str, list]) -> Path:
    grid_dir = tmp_path / "grids"
    grid_dir.mkdir(exist_ok=True)
    with gzip.open(grid_dir / "ease2_n25.json.gz", "wt", encoding="utf-8") as handle:
        json.dump({"cells": cells}, handle)
    return grid_dir


def _shapes(tmp_path: Path, polys: list[tuple[int, list]]) -> Path:
    import fiona

    path = tmp_path / "marine.shp"
    schema = {"geometry": "Polygon", "properties": {"ne_id": "int"}}
    with fiona.open(path, "w", driver="ESRI Shapefile", schema=schema) as out:
        for ne_id, ring in polys:
            out.write(
                {
                    "geometry": {"type": "Polygon", "coordinates": [ring]},
                    "properties": {"ne_id": ne_id},
                }
            )
    return path


def test_bake_names_the_covered_cell(tmp_path: Path) -> None:
    """#177.2: the sea whose area holds the centre names the cell."""
    col, row = 415, 480
    lat, lon = center(tile_id("ease2_n25", col, row))
    ring = [
        (lon - 2, lat - 2),
        (lon + 2, lat - 2),
        (lon + 2, lat + 2),
        (lon - 2, lat + 2),
        (lon - 2, lat - 2),
    ]
    grid_dir = _grid(
        tmp_path,
        {
            f"{col},{row}": [None, 0, None, 0, None, 0.03, 0.0, -5],
            "1,1": [1, 1.0, None, 0, 4, 0.9, 0.0, 100],
        },
    )
    shapes = _shapes(tmp_path, [(7, ring)])
    names = {"test_sea": {"ne_ids": [7]}}
    doc = bake_seas(grid_dir, shapes, names, tmp_path / "seas.json.gz")
    assert doc["cells"] == {tile_id("ease2_n25", col, row): "test_sea"}
    assert doc["source"].startswith("naturalearth:")
    assert sea_at(load_seas(tmp_path / "seas.json.gz"), tile_id("ease2_n25", col, row))
    assert sea_at(doc["cells"], "n_1_1") is None


def test_bake_refuses_a_table_id_with_no_shape(tmp_path: Path) -> None:
    grid_dir = _grid(tmp_path, {"1,1": [1, 1.0, None, 0, 4, 0.9, 0.0, 100]})
    shapes = _shapes(tmp_path, [(7, [(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])])
    with pytest.raises(ValueError, match="miss their shapes"):
        bake_seas(
            grid_dir, shapes, {"test_sea": {"ne_ids": [8]}}, tmp_path / "seas.json.gz"
        )
