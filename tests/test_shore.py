"""Tile shore grids from real 1:10m shores (#204). No network."""

from __future__ import annotations

import base64
import struct
from pathlib import Path

GRID = Path(__file__).resolve().parent.parent / "data" / "habitat-grid"


def _need_grid() -> None:
    import pytest

    if not (GRID / "shore.json.gz").is_file():
        pytest.skip("needs the baked shore geometry")


def _decode(shore: dict) -> tuple[tuple[int, ...], bytes]:
    d = struct.unpack("<1089h", base64.b64decode(shore["d"]))
    return d, base64.b64decode(shore["lake"])


def test_neighbours_share_their_side_number_for_number() -> None:
    """#204.4: across a coast, east column equals west column."""
    _need_grid()
    from builder.shore import shore_for_tile

    west = shore_for_tile("z10_0580_0297", GRID)
    east = shore_for_tile("z10_0581_0297", GRID)
    assert west is not None and east is not None
    west_d, west_lake = _decode(west)
    east_d, east_lake = _decode(east)
    assert [west_d[j * 33 + 32] for j in range(33)] == [
        east_d[j * 33] for j in range(33)
    ]
    assert [west_lake[j * 33 + 32] for j in range(33)] == [
        east_lake[j * 33] for j in range(33)
    ]
    # The shared side really crosses water and land.
    side = [west_d[j * 33 + 32] for j in range(33)]
    assert min(side) < 0 < max(side)


def test_a_point_in_paijanne_is_negative_and_names_it() -> None:
    """#204.4: lake water reads negative with the lake's number."""
    _need_grid()
    from builder.shore import shore_for_tile

    shore = shore_for_tile("z10_0584_0286", GRID)
    assert shore is not None
    assert {"en": "Päijänne", "fi": "Päijänne", "wikidata": "Q213769"} in shore["lakes"]
    which = shore["lakes"].index(
        {"en": "Päijänne", "fi": "Päijänne", "wikidata": "Q213769"}
    )
    d, lake = _decode(shore)
    wet = [k for k in range(1089) if d[k] < 0 and lake[k] == which + 1]
    assert wet, "no Päijänne water point in its own tile"


def test_a_point_600m_inland_reads_plus_512() -> None:
    """#204.4: past reach, points take ±512 by side."""
    _need_grid()
    from builder.grid import lat_of, lon_of
    from builder.shore import load, scale, shore_for_tile

    data = load(str(GRID))
    assert data is not None
    shore = shore_for_tile("z10_0581_0295", GRID)
    assert shore is not None
    d, _lake = _decode(shore)
    # Point 594 sits 596 game metres inland: past reach, clamped.
    lat, lon = lat_of(295 + 18 / 32), lon_of(581 + 0 / 32)
    metres, _ = data.nearest(lon, lat)
    assert 512 < metres * scale(lat) < 700
    assert not data.side(lon, lat)
    assert d[594] == 512


def test_a_tile_10km_from_any_shore_carries_nothing() -> None:
    """#204.4: far from every shore, no `shore` key."""
    _need_grid()
    from builder.shore import shore_for_tile

    assert shore_for_tile("z10_0582_0298", GRID) is None


def test_shore_matches_the_game_shape() -> None:
    """Spec 02: span, reach, int16 d, one byte a point, named lakes."""
    _need_grid()
    from builder.shore import shore_for_tile

    shore = shore_for_tile("z10_0597_0278", GRID)
    assert shore is not None
    assert shore["span"] == 32
    assert shore["reach"] == 512
    d, lake = _decode(shore)
    assert len(d) == 1089 == len(lake)
    assert all(-512 <= v <= 512 for v in d)
    assert shore["lakes"] == [
        {"en": "Pielinen", "fi": "Pielinen", "wikidata": "Q845891"}
    ]
    assert set(lake) <= {0, 1, 255}
    assert 1 in set(lake)


def _line_grid(tmp_path: Path) -> Path:
    """A grid dir with one landmass just west of the date line."""
    import gzip
    import json

    grid_dir = tmp_path / "grids"
    grid_dir.mkdir()
    ring = [[-180.1, -1.0], [-179.9, -1.0], [-179.9, 1.0], [-180.1, 1.0]]
    ring.append(ring[0])
    raw = {"land": [[ring]], "lakes": {}}
    with gzip.open(grid_dir / "shore.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(raw, handle)
    (grid_dir / "lakes.json").write_text(json.dumps({"lakes": {}}))
    return grid_dir


def test_shore_and_side_wrap_at_the_date_line(tmp_path: Path) -> None:
    """#272.8: a tile east of the line sees the coast west of it."""
    from builder.grid import tile_at
    from builder.shore import ShoreData, shore_for_tile

    grid_dir = _line_grid(tmp_path)
    tile = tile_at(0.0, 179.9)
    assert tile.startswith("z10_1023_")
    shore = shore_for_tile(tile, grid_dir)
    assert shore is not None, "the reject box missed the cross-line coast"
    d, _lake = _decode(shore)
    assert min(d) < 0 < max(d), "no water-to-land crossing in the tile"
    data = ShoreData(grid_dir)
    assert data.side(179.7, 0.0) is True
    assert data.side(180.0, 0.0) is False
