"""Tile shore grids from real 1:10m shores (#204, spec 02 `shore`).

A tile with a real shore (Natural Earth 1:10m coast or a 1:10m
lake's shore) within reach carries where the water is, finer
than `coast`: 33 by 33 signed distances in game metres, one
byte a point for the lake, and the tile's named lakes. Each
point is worked out from its own world position, so a tile's
east column equals its east neighbour's west column, number
for number, and shores run on across sides.
"""

from __future__ import annotations

import base64
import gzip
import json
import math
import struct
from functools import lru_cache
from pathlib import Path
from typing import Any

from .grid import lat_of, lon_of, parse_tile
from .habitats import LAKES_FILE, SHORE_FILE

#: Game metres between shore points; 33 points a side, both sides.
SPAN = 32

#: Game metres of reach; past it, points take ±REACH by side.
REACH = 512

#: Points per side (1024 / SPAN + 1).
WIDTH = 33

#: Real metres per tile side at the equator (web-mercator z10).
TILE_METRES = 40075016.686 / 1024

#: Real metres per degree, for short equirectangular distances.
LON_METRES = 111319.5
LAT_METRES = 111132.0

#: Lake byte for the sea (or land nearest the sea).
SEA = 0

#: Lake byte for a lake with no name.
NAMELESS = 255

#: Bbox growth that always covers the reach, in degrees: the
#: reach in real metres is longest at the equator (512 game
#: metres are 19.6 km there, 0.176 degrees).
REJECT_DEGREES = 0.2


class ShoreData:
    """Simplified shores and lake names, loaded once per grid dir."""

    def __init__(self, grid_dir: Path) -> None:
        from shapely.geometry import Point, Polygon
        from shapely.strtree import STRtree

        raw = json.loads(
            gzip.open(grid_dir / SHORE_FILE, "rt", encoding="utf-8").read()
        )
        self.names: dict[str, dict[str, str]] = json.loads(
            (grid_dir / LAKES_FILE).read_text(encoding="utf-8")
        )["lakes"]
        land_polys: list[Any] = []
        lake_polys: list[tuple[str, Any]] = []
        segments: list[Any] = []
        lakes: list[str | None] = []
        for rings in raw["land"]:
            poly = Polygon(rings[0], rings[1:])
            if not poly.is_valid or poly.is_empty:
                continue
            land_polys.append(poly)
            for ring in rings:
                _add_ring_segments(ring, None, segments, lakes)
        for ne_id, polys in raw["lakes"].items():
            for rings in polys:
                poly = Polygon(rings[0], rings[1:])
                if poly.is_valid and not poly.is_empty:
                    lake_polys.append((ne_id, poly))
                for ring in rings:
                    _add_ring_segments(ring, ne_id, segments, lakes)
        self._land_geoms = land_polys
        self._land = STRtree(land_polys)
        self._lake_geoms = [poly for _, poly in lake_polys]
        self._lakes = STRtree(self._lake_geoms)
        self._seg_geoms = segments
        self._seg_lakes = lakes
        self._segments = STRtree(segments)
        self._point = Point

    def side(self, lon: float, lat: float) -> bool:
        """Whether the point is in water: the sea or a 1:10m lake."""
        point = self._point(lon, lat)
        for index in self._lakes.query(point):
            if self._lake_geoms[int(index)].covers(point):
                return True
        for index in self._land.query(point):
            if self._land_geoms[int(index)].covers(point):
                return False
        return True

    def nearest(self, lon: float, lat: float) -> tuple[float, str | None]:
        """Real metres to the nearest shore, and its lake (sea: None).

        The point's own longitude and its ±360 twins, so shores
        across the date line count.
        """
        best = math.inf
        lake: str | None = None
        for shift in (-360.0, 0.0, 360.0):
            found = self._segments.nearest(self._point(lon + shift, lat))
            if found is None:
                continue
            index = int(found)
            dist = _seg_metres(self._seg_geoms[index], lon + shift, lat)
            if dist < best:
                best = dist
                lake = self._seg_lakes[index]
        return best, lake


def _add_ring_segments(
    ring: list[list[float]],
    lake: str | None,
    segments: list[Any],
    lakes: list[str | None],
) -> None:
    from shapely.geometry import LineString

    for first, second in zip(ring, ring[1:]):
        if first == second:
            continue
        segments.append(LineString([first, second]))
        lakes.append(lake)


def _seg_metres(segment: Any, lon: float, lat: float) -> float:
    """Real metres from the point to the segment, equirectangular."""
    kx = LON_METRES * math.cos(math.radians(lat))
    (ax, ay), (bx, by) = segment.coords[0], segment.coords[1]
    px, py = (lon - ax) * kx, (lat - ay) * LAT_METRES
    dx, dy = (bx - ax) * kx, (by - ay) * LAT_METRES
    length2 = dx * dx + dy * dy
    t = (px * dx + py * dy) / length2 if length2 > 0 else 0.0
    t = max(0.0, min(1.0, t))
    return math.hypot(px - dx * t, py - dy * t)


def scale(lat: float) -> float:
    """Game metres per real metre at this latitude (1 tile = 1024 m)."""
    return 1024.0 / (TILE_METRES * math.cos(math.radians(lat)))


@lru_cache(maxsize=4)
def load(grid_dir: str) -> ShoreData | None:
    """The grid dir's shores, or None without the shore files."""
    path = Path(grid_dir)
    if not (path / SHORE_FILE).is_file() or not (path / LAKES_FILE).is_file():
        return None
    return ShoreData(path)


def shore_for_tile(tile: str, grid_dir: str | Path | None) -> dict[str, Any] | None:
    """The tile's `shore`, or None with no shore within reach (#204).

    33 by 33 points over the tile, row by row from the north
    side, west to east; `d` the signed distance in game metres
    (+ land, - water, ±REACH past it), `lake` the nearest
    shore's lake per point, `lakes` the named lakes the points
    name, in first-use order. Without the shore files (old
    grids, test fakes) there is no shore.
    """
    if grid_dir is None:
        return None
    data = load(str(grid_dir))
    if data is None:
        return None
    from shapely.geometry import box as _box

    col, row = parse_tile(tile)
    west, east = lon_of(col), lon_of(col + 1)
    north, south = lat_of(row), lat_of(row + 1)
    grown = _box(
        west - REJECT_DEGREES,
        south - REJECT_DEGREES,
        east + REJECT_DEGREES,
        north + REJECT_DEGREES,
    )
    if len(data._segments.query(grown)) == 0:
        return None
    ds: list[int] = []
    lake_bytes: list[int] = []
    lakes: list[dict[str, str]] = []
    used: dict[str, int] = {}
    closest = math.inf
    for j in range(WIDTH):
        lat = lat_of(row + j / SPAN)
        factor = scale(lat)
        for i in range(WIDTH):
            lon = lon_of(col + i / SPAN)
            metres, lake = data.nearest(lon, lat)
            closest = min(closest, metres * factor)
            value = min(round(metres * factor), REACH)
            ds.append(-value if data.side(lon, lat) else value)
            lake_bytes.append(_lake_byte(data, lake, lakes, used))
    if closest > REACH:
        return None
    return {
        "span": SPAN,
        "reach": REACH,
        "d": base64.b64encode(struct.pack(f"<{len(ds)}h", *ds)).decode("ascii"),
        "lake": base64.b64encode(bytes(lake_bytes)).decode("ascii"),
        "lakes": lakes,
    }


def _lake_byte(
    data: ShoreData,
    lake: str | None,
    lakes: list[dict[str, str]],
    used: dict[str, int],
) -> int:
    """The point's lake byte: 0 the sea, k the k-th named, 255 nameless."""
    if lake is None:
        return SEA
    entry = data.names.get(lake) or {}
    if not entry.get("name"):
        return NAMELESS
    if lake not in used:
        used[lake] = len(lakes)
        lakes.append(
            {"en": entry["en"], "fi": entry["fi"], "wikidata": entry["wikidata"]}
        )
    return used[lake] + 1
