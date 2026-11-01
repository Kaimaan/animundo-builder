"""The tile grid: ids, cells, centres, neighbours, groups. No network."""

from __future__ import annotations

import re

import pytest

from builder.grid import (
    N,
    bounds,
    cell,
    center,
    group,
    group_path,
    lat_of,
    lon_of,
    neighbours,
    parse_tile,
    tile_at,
    tile_id,
)

#: The game's points from tools/check_grid.gd: (lat, lon, tile).
GAME_POINTS = [
    (60.3007, 24.5263, "z10_0581_0295"),
    (62.115, 28.508, "z10_0593_0284"),
    (-16.759, 145.973, "z10_0927_0560"),
    (0.0, 0.0, "z10_0512_0512"),
    (85.05, -180.0, "z10_0000_0000"),
    (-85.05, 179.999, "z10_1023_1023"),
    (85.0, 0.0, "z10_0512_0001"),
    (-74.65, 165.4, "z10_0982_0838"),
]


def test_tile_ids_match_the_pattern() -> None:
    for x, y in ((0, 0), (581, 295), (1023, 1023)):
        tid = tile_id(x, y)
        assert re.match(r"^z10_[0-9]{4}_[0-9]{4}$", tid), tid
    assert tile_id(581, 295) == "z10_0581_0295"


def test_parse_tile_round_trips() -> None:
    assert parse_tile("z10_0581_0295") == (581, 295)
    assert parse_tile("z10_1023_1023") == (1023, 1023)
    with pytest.raises(ValueError, match="bad tile id"):
        parse_tile("581_295")
    with pytest.raises(ValueError, match="bad tile id"):
        parse_tile("z10_581_295")
    with pytest.raises(ValueError, match="bad tile id"):
        parse_tile("n_412_301")
    with pytest.raises(ValueError, match="out of range"):
        parse_tile("z10_1024_0000")


def test_game_points_land_in_their_tiles() -> None:
    """#182.1: every point from the game's check names its tile."""
    for lat, lon, want in GAME_POINTS:
        assert tile_at(lat, lon) == want, (lat, lon)
        assert cell(lat, lon) == parse_tile(want)


def test_the_date_line_wraps() -> None:
    assert tile_at(10.0, 180.0) == tile_at(10.0, -180.0)
    assert tile_at(10.0, 180.0) == "z10_0000_0483"
    assert tile_id(1024, 100) == "z10_0000_0100"
    assert tile_id(-1, 100) == "z10_1023_0100"


def test_beyond_85_is_the_edge_row() -> None:
    assert tile_at(89.9, 0.0) == "z10_0512_0000"
    assert tile_at(-89.9, 0.0) == "z10_0512_1023"
    assert tile_id(512, -5) == "z10_0512_0000"
    assert tile_id(512, 2000) == "z10_0512_1023"


def test_a_point_on_a_line_belongs_south_or_east() -> None:
    """#182.1: the line's own tile is south or east of it."""
    tid = "z10_0581_0295"
    south, north, west, east = bounds(tid)
    assert tile_at(north, west) == tid
    assert tile_at(south, east) == tile_id(582, 296)
    assert tile_at(north, (west + east) / 2) == tid
    assert tile_at((south + north) / 2, west) == tid


def test_centre_round_trips_to_the_same_cell() -> None:
    for lat, lon, want in GAME_POINTS:
        assert tile_at(*center(want)) == want, (lat, lon)


def test_centres_round_trip_across_the_grid() -> None:
    """400 sampled centres each fall back in their own tile."""
    import random

    rng = random.Random(51)
    for _ in range(400):
        tid = tile_id(rng.randrange(N), rng.randrange(N))
        assert tile_at(*center(tid)) == tid, tid


def test_centre_is_the_middle_on_the_map() -> None:
    lat, lon = center("z10_0581_0295")
    assert lat == pytest.approx(lat_of(295.5))
    assert lon == pytest.approx(lon_of(581.5))
    assert lon == pytest.approx(24.43359375)


def test_bounds_is_the_tile_box() -> None:
    south, north, west, east = bounds("z10_0581_0295")
    assert south < north
    assert west < east
    assert north == pytest.approx(lat_of(295))
    assert south == pytest.approx(lat_of(296))
    assert west == pytest.approx(lon_of(581))
    assert east == pytest.approx(lon_of(582))
    _south0, north0, west0, east0 = bounds("z10_0000_0000")
    assert north0 == pytest.approx(85.0511287798)
    assert west0 == pytest.approx(-180.0)
    assert east0 == pytest.approx(-179.6484375)
    south1, _north1, _west1, east1 = bounds("z10_1023_1023")
    assert south1 == pytest.approx(-85.0511287798)
    assert east1 == pytest.approx(180.0)


def test_neighbours_meet_at_sides_and_corners() -> None:
    parts = neighbours("z10_0581_0295")
    assert parts["n"] == "z10_0581_0294"
    assert parts["s"] == "z10_0581_0296"
    assert parts["e"] == "z10_0582_0295"
    assert parts["w"] == "z10_0580_0295"
    assert parts["ne"] == "z10_0582_0294"
    assert parts["sw"] == "z10_0580_0296"


def test_neighbours_wrap_east_west_and_stop_at_the_poles() -> None:
    parts = neighbours("z10_0000_0000")
    assert parts["n"] is None
    assert parts["nw"] is None
    assert parts["ne"] is None
    assert parts["w"] == "z10_1023_0000"
    assert parts["e"] == "z10_0001_0000"
    assert parts["s"] == "z10_0000_0001"
    assert parts["se"] == "z10_0001_0001"
    bottom = neighbours("z10_1023_1023")
    assert bottom["s"] is None
    assert bottom["e"] == "z10_0000_1023"


def test_group_is_eight_by_eight() -> None:
    assert group("z10_0581_0295") == (72, 36)
    assert group("z10_0000_0000") == (0, 0)
    assert group_path("z10_0581_0295") == "tiles/v2/z10/072_036.json"
