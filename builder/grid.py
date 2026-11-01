"""The tile grid: web-map tiles at zoom 10 (spec 02 §1, 0051).

1,024 by 1,024 square tiles from 85.0511°N to 85.0511°S (Web
Mercator, EPSG:3857). `x` grows east from 180°W and wraps at
the date line; `y` grows south from the top. Tile ids are
`z10_<x>_<y>` with four digits, for example `z10_0581_0295`
(Nuuksio). Every tile is north-up and square, each side meets
exactly one tile, and a point on a line belongs to the tile
south or east of it. Groups are the 8 by 8 zoom 7 blocks.
Nothing is built beyond ±85°.

The formulas mirror the game's `Grid` (`scripts/grid.gd`)
exactly: same clamping, wrapping and rounding, so both sides
always name the same tile.
"""

from __future__ import annotations

import math
import re

#: Zoom, tiles per side, and the grid's name in ids and paths.
Z = 10
N = 1024
NAME = "z10"

#: Where Web Mercator's square ends; latitudes clamp to it.
MAX_LAT = 85.0511287798

#: The equator in metres, and one tile's side in Mercator metres.
EQUATOR_M = 40075016.686
TILE_M = EQUATOR_M / N

#: Tiles per group side (a group is one zoom 7 block).
GROUP_CELLS = 8

ID_PATTERN = re.compile(r"^z10_([0-9]{4})_([0-9]{4})$")


def cell(lat: float, lon: float) -> tuple[int, int]:
    """The (x, y) cell holding this point.

    Latitudes clamp to ±85.0511° (beyond is the edge row)
    and longitudes wrap at the date line, as the game does.
    """
    clamped = min(max(lat, -MAX_LAT), MAX_LAT)
    rad = math.radians(clamped)
    x = math.floor(((lon + 180.0) % 360.0) / 360.0 * N)
    y = math.floor((1.0 - math.asinh(math.tan(rad)) / math.pi) * 0.5 * N)
    return x, min(max(y, 0), N - 1)


def tile_id(x: int, y: int) -> str:
    """The tile id for this cell, for example `z10_0581_0295`.

    `x` wraps round the world, `y` stops at the edge rows.
    """
    return f"{NAME}_{x % N:04d}_{min(max(y, 0), N - 1):04d}"


def parse_tile(tile: str) -> tuple[int, int]:
    """The (x, y) cell behind a tile id."""
    match = ID_PATTERN.match(tile)
    if match is None:
        raise ValueError(f"bad tile id {tile!r}")
    x, y = int(match.group(1)), int(match.group(2))
    if x >= N or y >= N:
        raise ValueError(f"cell out of range: {x}, {y}")
    return x, y


def tile_at(lat: float, lon: float) -> str:
    """The tile id holding this point."""
    x, y = cell(lat, lon)
    return tile_id(x, y)


def lat_of(y: float) -> float:
    """The latitude of the line between row y - 1 and row y."""
    return math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / N))))


def lon_of(x: float) -> float:
    """The longitude of the line between column x - 1 and column x."""
    return x / N * 360.0 - 180.0


def center(tile: str) -> tuple[float, float]:
    """The cell centre as (lat, lon): its middle on the map."""
    x, y = parse_tile(tile)
    return lat_of(y + 0.5), lon_of(x + 0.5)


def bounds(tile: str) -> tuple[float, float, float, float]:
    """The tile's lat/lon box: (min lat, max lat, min lon, max lon)."""
    x, y = parse_tile(tile)
    return lat_of(y + 1), lat_of(y), lon_of(x), lon_of(x + 1)


def neighbours(tile: str) -> dict[str, str | None]:
    """The eight neighbours by direction, None past the top/bottom."""
    x, y = parse_tile(tile)
    out: dict[str, str | None] = {}
    for name, dx, dy in (
        ("n", 0, -1),
        ("s", 0, 1),
        ("e", 1, 0),
        ("w", -1, 0),
        ("ne", 1, -1),
        ("nw", -1, -1),
        ("se", 1, 1),
        ("sw", -1, 1),
    ):
        ny = y + dy
        if 0 <= ny < N:
            out[name] = tile_id(x + dx, ny)
        else:
            out[name] = None
    return out


def group(tile: str) -> tuple[int, int]:
    """The tile's group: its zoom 7 block (x // 8, y // 8)."""
    x, y = parse_tile(tile)
    return x // GROUP_CELLS, y // GROUP_CELLS


def group_path(tile: str) -> str:
    """The group file path, for example `tiles/v2/z10/072_036.json`."""
    gx, gy = group(tile)
    return f"tiles/v2/{NAME}/{gx:03d}_{gy:03d}.json"
