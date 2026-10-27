"""Country lookup: one point against the vendored admin shapes.

The web app refuses taps outside a country; the worker re-derives
the country from the cell itself and trusts no client field (#41).
Same shapes, same ray-casting as `web/build/app.js`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .schemas import ROOT

SHAPES = (
    ROOT
    / "web"
    / "build"
    / "vendor"
    / "natural-earth"
    / ("ne_110m_admin_0_countries.geojson")
)


def find_shapes() -> Path:
    """The vendored country shapes, or a ValueError."""
    if not SHAPES.is_file():
        raise ValueError(f"no country shapes vendored at {SHAPES}")
    return SHAPES


def country_at(shapes_path: Path, lat: float, lon: float) -> tuple[str, str] | None:
    """The (name, ISO code) under the point, or None outside all shapes."""
    data = json.loads(shapes_path.read_text(encoding="utf-8"))
    for feature in data.get("features", []):
        if _shape_has(feature.get("geometry"), lon, lat):
            props = feature.get("properties", {})
            return (props.get("NAME", "") or "", props.get("ISO_A2", "") or "")
    return None


def _shape_has(geometry: dict[str, Any] | None, lon: float, lat: float) -> bool:
    if not geometry:
        return False
    if geometry.get("type") == "Polygon":
        return _ring_has(geometry["coordinates"][0], lon, lat)
    if geometry.get("type") == "MultiPolygon":
        return any(_ring_has(poly[0], lon, lat) for poly in geometry["coordinates"])
    return False


def _ring_has(ring: list[list[float]], lon: float, lat: float) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside
