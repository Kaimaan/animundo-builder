"""The tile grid: ids, cells, centres, neighbours, groups. No network."""

from __future__ import annotations

import re

import numpy as np
import pytest
from pyproj import Transformer

from builder.grid import (
    CELL_M,
    CELLS,
    HALF_M,
    cell,
    center,
    grid_north,
    group,
    group_path,
    neighbours,
    parse_tile,
    tile_at,
    tile_id,
)

LAB_SPOTS = {
    "nuuksio": (60.45, 24.40, "n"),
    "lapland": (68.40, 23.60, "n"),
    "amazon": (-1.00, -75.50, "s"),
    "savanna": (-2.33, 34.83, "s"),
    "coast": (52.00, -4.50, "n"),
}


def test_tile_ids_match_the_pattern() -> None:
    for hemi in ("n", "s"):
        for col, row in ((0, 0), (412, 301), (719, 719)):
            tid = tile_id(f"ease2_{hemi}25", col, row)  # type: ignore[arg-type]
            assert re.match(r"^[ns]_[0-9]{3}_[0-9]{3}$", tid), tid
    assert tile_id("ease2_n25", 412, 301) == "n_412_301"


def test_parse_tile_round_trips() -> None:
    assert parse_tile("n_412_301") == ("ease2_n25", 412, 301)
    assert parse_tile("s_014_270") == ("ease2_s25", 14, 270)
    with pytest.raises(ValueError, match="bad tile id"):
        parse_tile("412_301")
    with pytest.raises(ValueError, match="bad tile id"):
        parse_tile("n_4122_301")


def test_lab_spots_land_in_their_hemispheres() -> None:
    for name, (lat, lon, hemi) in LAB_SPOTS.items():
        tid = tile_at(lat, lon)
        assert tid.startswith(hemi + "_"), f"{name} -> {tid}"
        grid, col, row = cell(lat, lon)
        assert tile_id(grid, col, row) == tid


def test_centre_round_trips_to_the_same_cell() -> None:
    for _name, (lat, lon, _hemi) in LAB_SPOTS.items():
        tid = tile_at(lat, lon)
        clat, clon = center(tid)
        assert tile_at(clat, clon) == tid


def test_every_cell_round_trips_both_grids() -> None:
    """Cell to centre and back gives the same cell, all 720 by 720 by 2."""
    cols = np.arange(CELLS)
    rows = np.arange(CELLS)
    cc, rr = np.meshgrid(cols, rows)
    xs = -HALF_M + (cc + 0.5) * CELL_M
    ys = HALF_M - (rr + 0.5) * CELL_M
    for epsg in (6931, 6932):
        back = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
        fwd = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
        lons, lats = back.transform(xs, ys)
        x2, y2 = fwd.transform(lons, lats)
        col2 = ((x2 + HALF_M) // CELL_M).astype(int)
        row2 = ((HALF_M - y2) // CELL_M).astype(int)
        assert bool(np.all(col2 == cc)), f"EPSG:{epsg} cols drift"
        assert bool(np.all(row2 == rr)), f"EPSG:{epsg} rows drift"


def test_grid_north_is_zero_on_the_meridian() -> None:
    tid = tile_at(60, 0)
    assert abs(grid_north(tid)) < 0.5, f"{tid}: {grid_north(tid)}"
    tid = tile_at(-30, 0)
    assert abs(grid_north(tid)) < 0.5, f"{tid}: {grid_north(tid)}"


def test_grid_north_stays_inside_half_a_circle() -> None:
    for lat, lon in ((60, 24.6), (60, -90), (-30, 34.8), (-2.33, 34.83)):
        angle = grid_north(tile_at(lat, lon))
        assert -180 <= angle <= 180


def test_grid_north_matches_the_contract_example() -> None:
    # Spec 02 shows -24.6 for a tile centred at 60.41, 24.62.
    angle = grid_north(tile_at(60.41, 24.62))
    assert abs(angle - -24.6) < 2.0, angle


def test_neighbours_meet_at_sides_and_corners() -> None:
    parts = neighbours("n_412_301")
    assert parts["n"] == "n_412_300"
    assert parts["s"] == "n_412_302"
    assert parts["e"] == "n_413_301"
    assert parts["w"] == "n_411_301"
    assert parts["ne"] == "n_413_300"
    assert parts["sw"] == "n_411_302"


def test_neighbours_are_none_past_the_edge() -> None:
    parts = neighbours("n_000_000")
    assert parts["n"] is None
    assert parts["w"] is None
    assert parts["nw"] is None
    assert parts["e"] == "n_001_000"
    assert parts["s"] == "n_000_001"
    assert parts["se"] == "n_001_001"


def test_group_is_eight_by_eight() -> None:
    assert group("n_412_301") == ("ease2_n25", 51, 37)
    assert group("s_014_270") == ("ease2_s25", 1, 33)
    assert group_path("n_412_301") == "tiles/v1/ease2_n25/51_37.json"


def test_outside_the_grid_raises() -> None:
    with pytest.raises(ValueError, match="outside"):
        cell(0, 0)
    with pytest.raises(ValueError, match="not on Earth"):
        cell(91, 0)
    with pytest.raises(ValueError, match="out of range"):
        tile_id("ease2_n25", 720, 0)
