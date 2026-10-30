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
        grids=("ease2_n25",),
        bbox=(413, 478, 414, 479),
    )
    assert counts == {"ease2_n25": 4}
    grid = HabitatGrid.load(tmp_path / "ease2_n25.json.gz", "ease2_n25")
    seen_biomes = set()
    seen_water = False
    for col in (413, 414):
        for row in (478, 479):
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
        grids=("ease2_n25",),
        bbox=(413, 478, 413, 478),
    )
    grid = HabitatGrid.load(tmp_path / "ease2_n25.json.gz", "ease2_n25")
    eco1, share1, eco2, share2 = grid.ecoregions_at("n_413_478")
    assert eco1 == 1
    assert share1 == pytest.approx(0.76, abs=0.01)
    assert eco2 == 2
    assert share2 == pytest.approx(0.24, abs=0.01)


def test_coast_mask_runs_rows_north_to_south() -> None:
    """#142: bake order (columns) becomes coast rows along n to s."""
    west = [ix < 2 for ix in range(5) for _iy in range(5)]
    assert coast_mask(west) == "11000" * 5
    north = [iy < 2 for _ix in range(5) for iy in range(5)]
    assert coast_mask(north) == "11111" * 2 + "00000" * 3
    assert coast_mask([True] * 25) == "1" * 25
    assert coast_mask([False] * 25) == "0" * 25


def test_bake_keeps_the_mask_with_the_land_share(tmp_path: Path) -> None:
    """#142: every baked cell carries 25 samples; ones are the land."""
    bake(
        FIXTURES / "resolve.geojson",
        FIXTURES / "land.geojson",
        FIXTURES / "lakes.geojson",
        None,
        tmp_path,
        grids=("ease2_n25",),
        bbox=(413, 478, 414, 479),
    )
    grid = HabitatGrid.load(tmp_path / "ease2_n25.json.gz", "ease2_n25")
    for col in (413, 414):
        for row in (478, 479):
            mask = grid.coast(col, row)
            assert mask is not None
            assert len(mask) == 25
            assert set(mask) <= {"0", "1"}
            land = grid.cell_data(col, row)[5]
            assert mask.count("1") / 25 == land


def test_habitat_grid_reads_tiles(tmp_path: Path) -> None:
    path = tmp_path / "ease2_n25.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(
            {
                "grid": "ease2_n25",
                "cells": {"413,478": [717, 1.0, None, 0.0, 6, 1.0, 0.0, 80.0]},
            },
            handle,
        )
    grid = HabitatGrid.load(path, "ease2_n25")
    assert grid.habitat_at("n_413_478") == "boreal_forest"
    assert grid.water_at("n_413_478") == 0.0
    assert grid.elevation_at("n_413_478") == 80.0
    assert grid.ecoregions_at("n_413_478") == (717, 1.0, None, 0.0)
    try:
        grid.habitat_at("s_014_270")
    except ValueError as error:
        assert "not on ease2_n25" in str(error)
    else:
        raise AssertionError("wrong grid accepted")


def test_committed_grid_covers_the_lab_spots() -> None:
    path = (
        Path(__file__).resolve().parent.parent
        / "data"
        / "habitat-grid"
        / "ease2_n25.json.gz"
    )
    if not path.is_file():
        return
    grid = HabitatGrid.load(path, "ease2_n25")
    assert grid.habitat_at("n_413_478") in ("boreal_forest", "boreal_lake")
