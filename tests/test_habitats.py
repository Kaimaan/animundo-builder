"""The habitat grid: table, thresholds, and baking. No network."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from builder.habitats import (
    BIOME_TO_HABITAT,
    HabitatGrid,
    bake,
    coast_mask,
    habitat_for,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "habitats"


def test_biome_table_covers_all_fourteen() -> None:
    assert sorted(BIOME_TO_HABITAT) == list(range(1, 15))
    assert BIOME_TO_HABITAT[1] == "rainforest"
    assert BIOME_TO_HABITAT[6] == "boreal_forest"
    assert BIOME_TO_HABITAT[7] == "savanna"
    assert BIOME_TO_HABITAT[11] == "tundra"
    assert BIOME_TO_HABITAT[13] == "desert"
    assert BIOME_TO_HABITAT[14] == "mangrove"


def test_habitat_thresholds() -> None:
    assert habitat_for(717, 6, 1.0, 0.0) == "boreal_forest"
    assert habitat_for(717, 6, 1.0, 0.16) == "boreal_lake"
    assert habitat_for(717, 6, 1.0, 0.14) == "boreal_forest"
    assert habitat_for(123, 4, 1.0, 0.5) == "lake"
    assert habitat_for(123, 4, 1.0, 0.49) == "forest"
    assert habitat_for(None, None, 0.0, 0.0) == "open_ocean"
    assert habitat_for(None, None, 0.09, 0.0) == "open_ocean"
    assert habitat_for(5, 4, 0.1, 0.0) == "sea"
    assert habitat_for(5, 4, 0.49, 0.0) == "sea"
    assert habitat_for(0, 11, 1.0, 0.0) == "ice"
    assert habitat_for(5, 99, 1.0, 0.0) == "sea"


def test_bake_writes_cells_from_fixtures(tmp_path: Path) -> None:
    counts = bake(
        FIXTURES / "resolve.geojson",
        FIXTURES / "land.geojson",
        FIXTURES / "lakes.geojson",
        None,
        tmp_path,
        bbox=(580, 294, 581, 295),
        hydrolakes_path=FIXTURES / "hydrolakes.geojson",
    )
    assert counts == {"z10": 4}
    grid = HabitatGrid.load(tmp_path / "z10.json.gz")
    seen_biomes = set()
    seen_water = False
    for col in (580, 581):
        for row in (294, 295):
            eco1, _s1, _e2, _s2, biome, land, water, elev = grid.cell_data(col, row)
            assert land == 1.0
            assert eco1 in (1, 2)
            assert biome in (6, 11)
            assert elev == 0.0
            seen_biomes.add(biome)
            seen_water = seen_water or water > 0
            assert grid.habitat(col, row) in (
                "boreal_forest",
                "boreal_lake",
                "tundra",
            )
    assert seen_biomes == {6, 11}
    assert seen_water


def test_bake_keeps_two_ecoregions_with_shares(tmp_path: Path) -> None:
    bake(
        FIXTURES / "resolve.geojson",
        FIXTURES / "land.geojson",
        FIXTURES / "lakes.geojson",
        None,
        tmp_path,
        bbox=(581, 294, 581, 294),
        hydrolakes_path=FIXTURES / "hydrolakes.geojson",
    )
    grid = HabitatGrid.load(tmp_path / "z10.json.gz")
    eco1, share1, eco2, share2 = grid.ecoregions_at("z10_0581_0294")
    assert eco1 == 2
    assert share1 == pytest.approx(0.6, abs=0.01)
    assert eco2 == 1
    assert share2 == pytest.approx(0.4, abs=0.01)


def test_coast_mask_runs_rows_north_to_south() -> None:
    """#142: bake order (columns) becomes coast rows along n to s."""
    west = [ix < 2 for ix in range(5) for _iy in range(5)]
    assert coast_mask(west) == "11000" * 5
    north = [iy < 2 for _ix in range(5) for iy in range(5)]
    assert coast_mask(north) == "11111" * 2 + "00000" * 3
    assert coast_mask([True] * 25) == "1" * 25
    assert coast_mask([False] * 25) == "0" * 25


def test_bake_keeps_the_mask_with_the_land_share(tmp_path: Path) -> None:
    """#142, #202.2: 25 samples; ones are dry land, lake points are zeroes."""
    bake(
        FIXTURES / "resolve.geojson",
        FIXTURES / "land.geojson",
        FIXTURES / "lakes.geojson",
        None,
        tmp_path,
        bbox=(580, 294, 581, 295),
        hydrolakes_path=FIXTURES / "hydrolakes.geojson",
    )
    grid = HabitatGrid.load(tmp_path / "z10.json.gz")
    for col in (580, 581):
        for row in (294, 295):
            mask = grid.coast(col, row)
            assert mask is not None
            assert len(mask) == 25
            assert set(mask) <= {"0", "1"}
            land = grid.cell_data(col, row)[5]
            assert mask.count("1") / 25 <= land
    assert set(grid.coast(580, 295) or "") == {"0", "1"}
    assert grid.coast(581, 294) == "1" * 25


def test_habitat_grid_reads_tiles(tmp_path: Path) -> None:
    path = tmp_path / "z10.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(
            {
                "grid": "z10",
                "cells": {"581,295": [717, 1.0, None, 0.0, 6, 1.0, 0.0, 80.0]},
            },
            handle,
        )
    grid = HabitatGrid.load(path)
    assert grid.habitat_at("z10_0581_0295") == "boreal_forest"
    assert grid.water_at("z10_0581_0295") == 0.0
    assert grid.elevation_at("z10_0581_0295") == 80.0
    assert grid.ecoregions_at("z10_0581_0295") == (717, 1.0, None, 0.0)


def test_habitat_grid_refuses_an_old_grid(tmp_path: Path) -> None:
    """#182.2: a v1 file fails loudly, never mis-maps its cells."""
    path = tmp_path / "ease2_n25.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump({"grid": "ease2_n25", "cells": {}}, handle)
    with pytest.raises(ValueError, match="not z10"):
        HabitatGrid.load(path)


def test_habitat_grid_reads_reef_shares(tmp_path: Path) -> None:
    """#226.1: the reef file beside the grid reads as shares."""
    with gzip.open(tmp_path / "z10.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(
            {
                "grid": "z10",
                "cells": {
                    "581,295": [None, 0.0, None, 0.0, None, 0.0, 0.0, 0.0],
                    "582,295": [None, 0.0, None, 0.0, None, 0.0, 0.0, 0.0],
                },
            },
            handle,
        )
    with gzip.open(tmp_path / "reef.json.gz", "wt", encoding="utf-8") as handle:
        json.dump({"grid": "z10", "cells": {"581,295": 0.25}}, handle)
    grid = HabitatGrid.load(tmp_path / "z10.json.gz")
    assert grid.reef_at("z10_0581_0295") == 0.25
    assert grid.reef_at("z10_0582_0295") == 0.0
    assert grid.habitat_at("z10_0581_0295") == "open_ocean"


def test_committed_grid_ring_reef_shares() -> None:
    """#226.5: the ring's reef shares come from the map, not the web."""
    grid = _committed_grid()
    if grid is None:
        return
    assert grid.reef_at("z10_0927_0560") == pytest.approx(0.211)
    assert grid.reef_at("z10_0926_0559") == pytest.approx(0.229)
    assert grid.reef_at("z10_0928_0561") == pytest.approx(0.164)
    assert grid.reef_at("z10_0926_0560") == pytest.approx(0.002)
    assert grid.habitat_at("z10_0927_0560") == "open_ocean"


def test_committed_grid_reef_coverage() -> None:
    """#230.2: the map's extent says looked-at, not the shares."""
    grid = _committed_grid()
    if grid is None:
        return
    assert grid.reef_covered(927, 560)
    assert grid.reef_covered(926, 560)
    assert grid.reef_covered(928, 559)
    assert not grid.reef_covered(581, 294)
    assert not grid.reef_covered(100, 100)


def test_committed_grid_deep_coral_sea_has_no_reef() -> None:
    """#226.5: a deep Coral Sea cell reads no reef."""
    grid = _committed_grid()
    if grid is None:
        return
    assert grid.reef_at("z10_0952_0570") == 0.0
    assert grid.habitat_at("z10_0952_0570") == "open_ocean"


def test_committed_grid_covers_the_lab_spots() -> None:
    path = (
        Path(__file__).resolve().parent.parent / "data" / "habitat-grid" / "z10.json.gz"
    )
    if not path.is_file():
        return
    grid = HabitatGrid.load(path)
    assert grid.habitat_at("z10_0581_0295") in ("boreal_forest", "boreal_lake")


def _committed_grid() -> HabitatGrid | None:
    path = (
        Path(__file__).resolve().parent.parent / "data" / "habitat-grid" / "z10.json.gz"
    )
    if not path.is_file():
        return None
    return HabitatGrid.load(path)


def test_committed_grid_paijanne_shore_has_lake_zeros() -> None:
    """#202.5: a cell across Päijänne's shore mixes land and lake 0s."""
    grid = _committed_grid()
    if grid is None:
        return
    mask = grid.coast(585, 285)
    assert mask is not None
    assert "0" in mask and "1" in mask


def test_committed_grid_wide_lake_middle_is_all_zeros() -> None:
    """#202.5: a cell inside a wide lake gets all 0s (Ladoga)."""
    grid = _committed_grid()
    if grid is None:
        return
    assert grid.coast(600, 288) == "0" * 25


def test_bake_water_counts_lakes_only_hydrolakes_sees(tmp_path: Path) -> None:
    """#202.1: a cell with only small lakes gets water above zero."""
    bake(
        FIXTURES / "resolve.geojson",
        FIXTURES / "land.geojson",
        FIXTURES / "lakes.geojson",
        None,
        tmp_path,
        bbox=(580, 294, 581, 295),
        hydrolakes_path=FIXTURES / "hydrolakes.geojson",
    )
    grid = HabitatGrid.load(tmp_path / "z10.json.gz")
    assert grid.coast(581, 295) == "1" * 25
    assert grid.cell_data(581, 295)[6] > 0
    assert grid.cell_data(581, 294)[6] == 0


def test_habitat_grid_reads_landcover(tmp_path: Path) -> None:
    """#238: the land-cover file beside the grid reads as shares."""
    with gzip.open(tmp_path / "z10.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(
            {
                "grid": "z10",
                "cells": {
                    "581,295": [717, 1.0, None, 0.0, 6, 1.0, 0.0, 80.0],
                    "582,295": [717, 1.0, None, 0.0, 6, 1.0, 0.0, 80.0],
                },
            },
            handle,
        )
    with gzip.open(tmp_path / "landcover.json.gz", "wt", encoding="utf-8") as handle:
        json.dump({"cells": {"581,295": [0.31, 0.0, "b" + "l" * 24]}}, handle)
    grid = HabitatGrid.load(tmp_path / "z10.json.gz")
    assert grid.landcover_share(581, 295) == (0.31, 0.0)
    assert grid.wild_mask(581, 295) == "b" + "l" * 24
    assert grid.landcover_share(582, 295) is None
    assert grid.wild_mask(582, 295) is None


def test_committed_landcover_covers_every_land_cell() -> None:
    """#238: the baked file values every land cell with a good mask."""
    grid = _committed_grid()
    if grid is None:
        return
    path = (
        Path(__file__).resolve().parent.parent
        / "data"
        / "habitat-grid"
        / "landcover.json.gz"
    )
    if not path.is_file():
        return
    checked = 0
    for key in grid.cells():
        col, row = (int(part) for part in key.split(","))
        if grid.cell_data(col, row)[5] <= 0:
            continue
        shares = grid.landcover_share(col, row)
        assert shares is not None, key
        assert 0.0 <= shares[0] <= 1.0, key
        assert 0.0 <= shares[1] <= 1.0, key
        mask = grid.wild_mask(col, row)
        assert mask is not None, key
        assert len(mask) == 25, key
        assert set(mask) <= set("bclw"), key
        checked += 1
    assert checked > 400000


def test_committed_manaus_is_built_mamiraua_wild() -> None:
    """#238: Manaus fails wild, Mamirauá passes, on the baked map."""
    from builder.place import tile_wild

    grid = _committed_grid()
    if grid is None:
        return
    path = (
        Path(__file__).resolve().parent.parent
        / "data"
        / "habitat-grid"
        / "landcover.json.gz"
    )
    if not path.is_file():
        return
    assert tile_wild("z10_0341_0520", grid) is False
    assert tile_wild("z10_0325_0518", grid) is True
