"""Country lookup: ray-casting over admin shapes. No network."""

from __future__ import annotations

import json
from pathlib import Path

from builder.countries import country_at, find_shapes

SQUARE = [
    [20.0, 60.0],
    [30.0, 60.0],
    [30.0, 70.0],
    [20.0, 70.0],
    [20.0, 60.0],
]
ISLAND = [
    [0.0, 0.0],
    [1.0, 0.0],
    [1.0, 1.0],
    [0.0, 1.0],
    [0.0, 0.0],
]


def _shapes(tmp_path: Path) -> Path:
    path = tmp_path / "admin.geojson"
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"NAME": "Testland", "ISO_A2": "TT"},
                        "geometry": {"type": "Polygon", "coordinates": [SQUARE]},
                    },
                    {
                        "type": "Feature",
                        "properties": {"NAME": "Isle", "ISO_A2": ""},
                        "geometry": {
                            "type": "MultiPolygon",
                            "coordinates": [[ISLAND]],
                        },
                    },
                ],
            }
        )
    )
    return path


def test_country_inside_polygon(tmp_path: Path) -> None:
    assert country_at(_shapes(tmp_path), 65.0, 25.0) == ("Testland", "TT")


def test_country_inside_multipolygon(tmp_path: Path) -> None:
    assert country_at(_shapes(tmp_path), 0.5, 0.5) == ("Isle", "")


def test_country_outside_all_shapes(tmp_path: Path) -> None:
    assert country_at(_shapes(tmp_path), 0.0, -30.0) is None


def test_find_shapes_is_vendored() -> None:
    path = find_shapes()
    assert path.is_file()
    assert country_at(path, 61.5, 26.0) == ("Finland", "FI")
