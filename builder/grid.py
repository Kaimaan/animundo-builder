"""The tile grid: EASE-Grid 2.0 at 25 km, both hemispheres.

Two published equal-area grids from NSIDC: `ease2_n25` (EPSG:6931,
north) and `ease2_s25` (EPSG:6932, south). Each is 720 by 720 cells
of exactly 25,000 m, with its own corner at (-9,000,000 m,
9,000,000 m). Column grows with projected x; row grows downwards
from the top (row 0 at y = +9,000,000 m). Tile ids are
`<n|s>_<col>_<row>` with three digits, for example `n_412_301`.

The equator maps just outside both grids (about 10 km beyond the
edge), so it belongs to neither: `cell()` raises for it, and
crossing it stays a journey until a later grid covers it.
"""

from __future__ import annotations

import re
from typing import Literal

from pyproj import Geod, Transformer

GridName = Literal["ease2_n25", "ease2_s25"]
Hemisphere = Literal["n", "s"]

CELL_M = 25_000
CELLS = 720
HALF_M = 9_000_000
GROUP_CELLS = 8

TILE_PATTERN = re.compile(r"^([ns])_([0-9]{3})_([0-9]{3})$")

_EPSG: dict[GridName, int] = {"ease2_n25": 6931, "ease2_s25": 6932}
_HEMI_GRID: dict[Hemisphere, GridName] = {"n": "ease2_n25", "s": "ease2_s25"}
_GRID_HEMI: dict[GridName, Hemisphere] = {"ease2_n25": "n", "ease2_s25": "s"}

_to_grid: dict[GridName, Transformer] = {
    name: Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    for name, epsg in _EPSG.items()
}
_from_grid: dict[GridName, Transformer] = {
    name: Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    for name, epsg in _EPSG.items()
}
_GEOD = Geod(ellps="WGS84")


def _grid_for_lat(lat: float) -> GridName:
    return "ease2_n25" if lat >= 0 else "ease2_s25"


def cell(lat: float, lon: float) -> tuple[GridName, int, int]:
    """The grid cell holding this point.

    Raises ValueError when the point falls outside both grids (the
    equator seam and beyond).
    """
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError(f"not on Earth: {lat}, {lon}")
    grid = _grid_for_lat(lat)
    x, y = _to_grid[grid].transform(lon, lat)
    col = int((x + HALF_M) // CELL_M)
    row = int((HALF_M - y) // CELL_M)
    if not 0 <= col < CELLS or not 0 <= row < CELLS:
        raise ValueError(f"outside the {grid} grid: {lat}, {lon}")
    return grid, col, row


def tile_id(grid: GridName, col: int, row: int) -> str:
    """The tile id for this cell, for example `n_412_301`."""
    if grid not in _GRID_HEMI:
        raise ValueError(f"unknown grid {grid!r}")
    if not 0 <= col < CELLS or not 0 <= row < CELLS:
        raise ValueError(f"cell out of range: {col}, {row}")
    return f"{_GRID_HEMI[grid]}_{col:03d}_{row:03d}"


def parse_tile(tile: str) -> tuple[GridName, int, int]:
    """The grid and cell behind a tile id."""
    match = TILE_PATTERN.match(tile)
    if match is None:
        raise ValueError(f"bad tile id {tile!r}")
    hemi = match.group(1)
    assert hemi in ("n", "s")
    return _HEMI_GRID[hemi], int(match.group(2)), int(match.group(3))


def tile_at(lat: float, lon: float) -> str:
    """The tile id holding this point."""
    grid, col, row = cell(lat, lon)
    return tile_id(grid, col, row)


def _cell_xy(grid: GridName, col: int, row: int) -> tuple[float, float]:
    """The projected centre of this cell."""
    x = -HALF_M + (col + 0.5) * CELL_M
    y = HALF_M - (row + 0.5) * CELL_M
    return x, y


def center(tile: str) -> tuple[float, float]:
    """The cell centre as (lat, lon)."""
    grid, col, row = parse_tile(tile)
    x, y = _cell_xy(grid, col, row)
    lon, lat = _from_grid[grid].transform(x, y)
    return lat, lon


def grid_north(tile: str) -> float:
    """The angle from grid up to true north, in degrees.

    Grid up is row - 1. Zero on the 0-degree meridian; negative
    west of it (turn left to face north), positive east of it.
    """
    grid, col, row = parse_tile(tile)
    lat0, lon0 = _latlon(grid, col, row)
    lat1, lon1 = _latlon(grid, col, row - 1)
    forward, _back, _dist = _GEOD.inv(lon0, lat0, lon1, lat1)
    return -((forward + 180) % 360 - 180)


def _latlon(grid: GridName, col: int, row: int) -> tuple[float, float]:
    """The lat/lon of a cell centre, clamping the row inside the grid."""
    row = min(max(row, 0), CELLS - 1)
    x, y = _cell_xy(grid, col, row)
    lon, lat = _from_grid[grid].transform(x, y)
    return lat, lon


def neighbours(tile: str) -> dict[str, str | None]:
    """The eight neighbours by direction, None past the grid edge."""
    grid, col, row = parse_tile(tile)
    out: dict[str, str | None] = {}
    for name, dcol, drow in (
        ("n", 0, -1),
        ("s", 0, 1),
        ("e", 1, 0),
        ("w", -1, 0),
        ("ne", 1, -1),
        ("nw", -1, -1),
        ("se", 1, 1),
        ("sw", -1, 1),
    ):
        ncol, nrow = col + dcol, row + drow
        if 0 <= ncol < CELLS and 0 <= nrow < CELLS:
            out[name] = tile_id(grid, ncol, nrow)
        else:
            out[name] = None
    return out


def group(tile: str) -> tuple[GridName, int, int]:
    """The tile's group: its grid and group column and row (8 by 8)."""
    grid, col, row = parse_tile(tile)
    return grid, col // GROUP_CELLS, row // GROUP_CELLS


def group_path(tile: str) -> str:
    """The group file path, for example `tiles/v1/ease2_n25/51_37.json`."""
    grid, gcol, grow = group(tile)
    return f"tiles/v1/{grid}/{gcol:02d}_{grow:02d}.json"
