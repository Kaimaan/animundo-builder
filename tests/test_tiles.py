"""The tile maker: tiles, blocked cells, and agreeing edges. No network."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import pytest

from builder.habitats import HabitatGrid
from builder.schemas import validate
from builder.tiles import (
    available_homes,
    cell_level,
    home_for_cell,
    make_tile,
    pick_species,
    tile_edges,
    tile_facts,
    tile_height,
    tile_seed,
    write_group,
)


def _world(tmp_path: Path) -> Path:
    world = tmp_path / "world"
    (world / "homes").mkdir(parents=True)
    (world / "animals").mkdir(parents=True)
    (world / "homes" / "boreal_forest.json").write_text(
        json.dumps(
            {
                "habitat": "boreal_forest",
                "ecoregions": [717],
                "food": [{"kind": "grass"}, {"kind": "berries"}],
            }
        )
    )
    (world / "homes" / "sea.json").write_text(
        json.dumps(
            {
                "habitat": "sea",
                "ecoregions": [651],
                "food": [{"kind": "water_bugs"}],
            }
        )
    )
    (world / "animals" / "fox.json").write_text(
        json.dumps(
            {
                "playable": True,
                "tags": ["predator"],
                "diet": ["berries"],
                "hunts": ["small_mammal"],
            }
        )
    )
    (world / "animals" / "mountain_hare.json").write_text(
        json.dumps({"playable": True, "tags": ["small_mammal"], "diet": ["grass"]})
    )
    (world / "animals" / "vole.json").write_text(
        json.dumps(
            {"playable": False, "tags": ["small_mammal", "vole"], "diet": ["grass"]}
        )
    )
    (world / "animal-list.json").write_text(
        json.dumps(
            {
                "homes": ["boreal_forest"],
                "not_yet": {},
                "animals": [
                    {
                        "id": "fox",
                        "gbif_key": 5219243,
                        "habitats": ["boreal_forest"],
                    },
                    {
                        "id": "mountain_hare",
                        "gbif_key": 2436756,
                        "habitats": ["boreal_forest"],
                    },
                    {"id": "vole", "gbif_key": 1, "habitats": ["boreal_forest"]},
                ],
            }
        )
    )
    return world


def _grids(tmp_path: Path, cells: dict[str, list[Any]]) -> dict[str, HabitatGrid]:
    path = tmp_path / "ease2_n25.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump({"grid": "ease2_n25", "cells": cells}, handle)
    return {"ease2_n25": HabitatGrid.load(path, "ease2_n25")}  # type: ignore[dict-item]


def _home(world: Path, home_id: str = "boreal_forest") -> dict[str, Any]:
    return json.loads((world / "homes" / f"{home_id}.json").read_text())


def test_pick_species_orders_food_first(tmp_path: Path) -> None:
    world = _world(tmp_path)
    playable, prey, predators = pick_species(
        {"fox": 5.0, "mountain_hare": 9.0, "vole": 20.0},
        world,
        _home(world),
    )
    assert playable == ["mountain_hare", "fox"]
    assert predators == ["fox"]
    assert prey == ["vole", "mountain_hare"]


def test_pick_species_lists_every_playable_file(tmp_path: Path) -> None:
    world = _world(tmp_path)
    (world / "animals" / "shrew.json").write_text(
        json.dumps({"playable": True, "tags": ["small_mammal"], "diet": ["grass"]})
    )
    (world / "animals" / "mouse.json").write_text(
        json.dumps({"playable": True, "tags": ["small_mammal"], "diet": ["grass"]})
    )
    (world / "animals" / "lepus.json").write_text(
        json.dumps({"playable": True, "tags": ["small_mammal"], "diet": ["grass"]})
    )
    (world / "animals" / "stoat.json").write_text(
        json.dumps({"playable": True, "tags": ["small_mammal"], "diet": ["grass"]})
    )
    playable, _prey, _predators = pick_species(
        {
            "fox": 5.0,
            "mountain_hare": 9.0,
            "vole": 20.0,
            "shrew": 4.0,
            "mouse": 3.0,
            "lepus": 2.0,
            "stoat": 1.0,
        },
        world,
        _home(world),
    )
    assert playable == [
        "mountain_hare",
        "fox",
        "shrew",
        "mouse",
        "lepus",
        "stoat",
    ]


def test_pick_species_drops_hunter_without_prey(tmp_path: Path) -> None:
    world = _world(tmp_path)
    assert pick_species({"fox": 5.0}, world, _home(world)) == ([], [], [])


def test_pick_species_drops_animal_without_food(tmp_path: Path) -> None:
    world = _world(tmp_path)
    assert pick_species({"mountain_hare": 9.0}, world, {"food": []}) == ([], [], [])


def test_pick_species_feeds_home_fish(tmp_path: Path) -> None:
    world = _world(tmp_path)
    (world / "animals" / "pike.json").write_text(
        json.dumps(
            {
                "playable": False,
                "tags": ["fish"],
                "diet": ["water_bugs"],
                "hunts": ["frog"],
            }
        )
    )
    (world / "animals" / "frog.json").write_text(
        json.dumps({"playable": True, "tags": ["frog"], "diet": ["insects"]})
    )
    home = {
        "food": [{"kind": "water_bugs"}, {"kind": "insects"}],
        "fish": [{"kind": "pike", "count": 2}],
    }
    playable, _prey, predators = pick_species({"pike": 9.0, "frog": 5.0}, world, home)
    assert playable == ["frog"]
    assert predators == ["pike"]
    assert pick_species({"pike": 9.0}, world, home) == ([], [], [])


def test_tile_seed_is_stable() -> None:
    assert tile_seed("n_413_478") == tile_seed("n_413_478")
    assert 0 <= tile_seed("n_413_478") < 2**31
    assert tile_seed("n_413_478") != tile_seed("n_413_479")


def _boreal_3x3(col: int, row: int) -> dict[str, list]:
    cell = [717, 1.0, None, 0.0, 6, 1.0, 0.0, 80.0]
    return {
        f"{c},{r}": list(cell)
        for r in (row - 1, row, row + 1)
        for c in (col - 1, col, col + 1)
    }


def test_home_for_cell_matches_ecoregion_and_habitat(tmp_path: Path) -> None:
    homes = available_homes(_world(tmp_path))
    assert home_for_cell(717, "boreal_forest", homes) == "boreal_forest"
    assert home_for_cell(717, "tundra", homes) is None
    assert home_for_cell(999, "boreal_forest", homes) is None
    assert home_for_cell(651, "sea", homes) == "sea"
    assert home_for_cell(651, "open_ocean", homes) == "sea"
    assert home_for_cell(None, "sea", homes) is None


def test_cell_level() -> None:
    assert cell_level("sea", 0) == 5
    assert cell_level("open_ocean", -100) == 5
    assert cell_level("boreal_forest", 0) == 9
    assert cell_level("boreal_forest", 80) == 12
    assert cell_level("mountain", 4000) == 56
    assert cell_level("mountain", 0) == 9
    assert cell_level("desert", -100) == 9


def test_cell_level_is_monotonic_and_stays_under_64() -> None:
    levels = [cell_level("mountain", elev) for elev in range(0, 8900, 50)]
    assert levels == sorted(levels)
    assert cell_level("mountain", 8849) < 64


def test_tile_height() -> None:
    flat = {side: [12] * 65 for side in ("n", "e", "s", "w")}
    assert tile_height(12, flat) == 32
    assert tile_height(29, flat) == 64
    tall = {side: [12] * 65 for side in ("n", "e", "s", "w")}
    tall["n"][0] = 29
    assert tile_height(12, tall) == 64


def test_make_tile_validates(tmp_path: Path) -> None:
    world = _world(tmp_path)
    grids = _grids(tmp_path, _boreal_3x3(413, 478))
    made, blocked = make_tile(
        "n_413_478",
        grids,  # type: ignore[arg-type]
        world,
        presence={"fox": 5.0, "mountain_hare": 9.0, "vole": 20.0},
        builder_root=tmp_path,
    )
    assert blocked is None
    assert made is not None
    validate("tile", made)
    assert made["habitat"] == "boreal_forest"
    assert made["ecoregion"] == 717
    assert made["home"] == "boreal_forest"
    assert made["level"] == cell_level("boreal_forest", 80.0)
    assert made["height"] == 32
    assert "blend" not in made
    assert made["species"]["playable"]
    assert made["seed"] == tile_seed("n_413_478")
    for side in ("n", "e", "s", "w"):
        assert len(made["edges"][side]) == 65


def _real_world() -> Path | None:
    world = Path(__file__).resolve().parent.parent.parent / "animundo-world"
    grid = Path(__file__).resolve().parent.parent / "data" / "habitat-grid"
    if not (world / "animals" / "fox.json").is_file():
        return None
    if not (grid / "ease2_n25.json.gz").is_file():
        return None
    return world


def test_nuuksio_tile_keeps_hunters_playable(tmp_path: Path) -> None:
    """Spec 04 roles on the Nuuksio tile: every kept playable file plays."""
    world = _real_world()
    if world is None:
        pytest.skip("needs the committed grid and a real world checkout")
    grid = Path(__file__).resolve().parent.parent / "data" / "habitat-grid"
    grids = {"ease2_n25": HabitatGrid.load(grid / "ease2_n25.json.gz", "ease2_n25")}
    made, blocked = make_tile(
        "n_413_478",
        grids,  # type: ignore[dict-item]
        world,
        presence={
            "great_tit": 12.0,
            "chaffinch": 11.0,
            "kestrel": 10.0,
            "flying_squirrel": 9.0,
            "squirrel": 8.0,
            "fox": 7.0,
            "frog": 6.0,
            "bee": 5.0,
            "moose": 4.0,
            "mountain_hare": 3.0,
            "vole": 2.0,
            "lizard": 1.0,
        },
        builder_root=tmp_path,
    )
    assert blocked is None
    assert made is not None
    playable = made["species"]["playable"]
    assert "fox" in playable
    assert "moose" in playable
    assert "mountain_hare" in playable
    assert playable == [
        "great_tit",
        "chaffinch",
        "kestrel",
        "flying_squirrel",
        "squirrel",
        "fox",
        "frog",
        "bee",
        "moose",
        "mountain_hare",
    ]
    assert "mountain_hare" in made["species"]["prey"]


def test_make_tile_blocks_without_home(tmp_path: Path) -> None:
    world = _world(tmp_path)
    grids = _grids(tmp_path, {"400,440": [999, 1.0, None, 0.0, 11, 1.0, 0.0, 200.0]})
    made, blocked = make_tile(
        "n_400_440",
        grids,
        world,
        presence={"fox": 5.0},  # type: ignore[arg-type]
        builder_root=tmp_path,
    )
    assert made is None
    assert blocked == {"reason": "no_home", "habitat": "tundra"}


def test_make_tile_blocks_without_animals(tmp_path: Path) -> None:
    world = _world(tmp_path)
    grids = _grids(tmp_path, _boreal_3x3(413, 478))
    made, blocked = make_tile(
        "n_413_478",
        grids,
        world,
        presence={},  # type: ignore[arg-type]
        builder_root=tmp_path,
    )
    assert made is None
    assert blocked == {"reason": "no_animal", "habitat": "boreal_forest"}


def test_make_tile_blocks_excluded_cells(tmp_path: Path) -> None:
    """#133.7: a cell in exclusions.json blocks with `excluded`."""
    import json

    world = _world(tmp_path)
    grids = _grids(tmp_path, _boreal_3x3(413, 478))
    (tmp_path / "exclusions.json").write_text(json.dumps({"tiles": ["n_413_478"]}))
    made, blocked = make_tile(
        "n_413_478",
        grids,
        world,
        presence={"fox": 5.0, "mountain_hare": 9.0, "vole": 20.0},
        builder_root=tmp_path,  # type: ignore[arg-type]
    )
    assert made is None
    assert blocked == {"reason": "excluded", "habitat": "boreal_forest"}


def test_edges_agree_at_corners(tmp_path: Path) -> None:
    grids = _grids(tmp_path, _boreal_3x3(413, 478))
    edges = tile_edges("n_413_478", grids)  # type: ignore[arg-type]
    assert edges["n"][0] == edges["w"][64]
    assert edges["n"][64] == edges["e"][64]
    assert edges["s"][0] == edges["w"][0]
    assert edges["s"][64] == edges["e"][0]


def test_edges_agree_with_neighbours(tmp_path: Path) -> None:
    cells = _boreal_3x3(413, 478) | _boreal_3x3(414, 478) | _boreal_3x3(413, 477)
    grids = _grids(tmp_path, cells)
    mine = tile_edges("n_413_478", grids)  # type: ignore[arg-type]
    east = tile_edges("n_414_478", grids)  # type: ignore[arg-type]
    north = tile_edges("n_413_477", grids)  # type: ignore[arg-type]
    assert mine["e"] == east["w"]
    assert mine["n"] == north["s"]


def test_write_group_merges(tmp_path: Path) -> None:
    world = _world(tmp_path)
    grids = _grids(tmp_path, _boreal_3x3(413, 478))
    out = tmp_path / "out"
    made, _ = make_tile(
        "n_413_478",
        grids,
        world,  # type: ignore[arg-type]
        presence={"fox": 5.0, "vole": 20.0},
        builder_root=tmp_path,
    )
    assert made is not None
    first = write_group(out, "n_413_478", made, None)
    assert first == out / "ease2_n25" / "51_59.json"
    data = json.loads(first.read_text())
    assert data["version"] == 1
    assert "n_413_478" in data["tiles"]
    second = write_group(
        out, "n_414_478", None, {"reason": "no_home", "habitat": "tundra"}
    )
    assert second == first
    data = json.loads(second.read_text())
    assert data["version"] == 2
    assert "n_413_478" in data["tiles"]
    assert data["blocked"]["n_414_478"]["reason"] == "no_home"
    validate("tile-group", data)


def _sea_3x3(col: int, row: int) -> dict[str, list]:
    cell = [651, 1.0, None, 0.0, 4, 0.3, 0.0, 0.0]
    return {
        f"{c},{r}": list(cell)
        for r in (row - 1, row, row + 1)
        for c in (col - 1, col, col + 1)
    }


def test_home_for_cell_prefers_the_sea_home_listing_the_cell() -> None:
    """#140.1: a sea home naming the ecoregion beats the generic sea."""
    homes = {
        "sea": {"habitat": "sea", "ecoregions": [651]},
        "sea_717": {"habitat": "sea", "ecoregions": [717]},
    }
    assert home_for_cell(717, "sea", homes) == "sea_717"
    assert home_for_cell(651, "sea", homes) == "sea"
    assert home_for_cell(999, "sea", homes) == "sea"
    assert home_for_cell(None, "sea", homes) is None
    assert home_for_cell(717, "sea", {}) is None


def test_make_tile_builds_the_reviewed_sea_border() -> None:
    """#140.1: with world d009df2, n_415_479 is ready from sea_717."""
    from builder.schemas import ROOT

    world = ROOT.parent / "animundo-world"
    if not (world / "homes" / "sea_717.json").is_file():
        pytest.skip("needs world d009df2 with the reviewed sea home")
    if not (world / "animals" / "herring_gull.json").is_file():
        pytest.skip("needs world d009df2 with the reviewed gull")
    grid_dir = ROOT / "data" / "habitat-grid"
    grids = {
        name: HabitatGrid.load(grid_dir / f"{name}.json.gz", name)
        for name in ("ease2_n25", "ease2_s25")
    }
    made, blocked = make_tile(
        "n_415_479",
        grids,  # type: ignore[arg-type]
        world,
        species={
            "place": "Gulf of Finland off Helsinki",
            "parts": [{"kind": "sea", "species": ["herring_gull"]}],
            "find": None,
            "name_en": "Helsinki coast",
            "name_fi": "Helsingin ranta",
        },
    )
    assert blocked is None
    assert made is not None
    assert made["home"] == "sea_717"
    assert "herring_gull" in made["species"]["playable"]


def test_tile_facts_come_from_the_grids(tmp_path: Path) -> None:
    """#141.1: the survey's facts are our own grid numbers."""
    world = _world(tmp_path)
    (world / "models" / "plants").mkdir(parents=True)
    (world / "models" / "plants" / "bilberry.json").write_text("{}")
    grids = _grids(tmp_path, _sea_3x3(413, 478))
    facts = tile_facts(
        "n_413_478",
        grids,
        world,
        "sea",
        True,  # type: ignore[arg-type]
    )
    assert facts["habitat"] == "sea"
    assert facts["eco1"] == 651
    assert facts["land"] == 0.3
    assert facts["lake"] == 0.0
    assert facts["sea"] == 0.7
    assert facts["level"] == cell_level("sea", 0.0)
    assert facts["edge_lo"] <= facts["level"] <= facts["edge_hi"]
    assert facts["home"] == "sea"
    assert facts["adapt"] is True
    assert facts["plants"] == ["bilberry"]


def test_make_tile_uses_sea_home_for_sea(tmp_path: Path) -> None:
    world = _world(tmp_path)
    (world / "animals" / "herring.json").write_text(
        json.dumps({"playable": True, "tags": ["fish"], "diet": ["water_bugs"]})
    )
    grids = _grids(tmp_path, _sea_3x3(100, 500))
    made, blocked = make_tile(
        "n_100_500",
        grids,
        world,  # type: ignore[arg-type]
        presence={"herring": 30.0},
        builder_root=tmp_path,
    )
    assert blocked is None
    assert made is not None
    assert made["habitat"] == "sea"
    assert made["ecoregion"] == 651
    assert made["home"] == "sea"
    assert made["level"] == 5


def test_make_tile_blends_second_home(tmp_path: Path) -> None:
    world = _world(tmp_path)
    (world / "homes" / "taiga.json").write_text(
        json.dumps({"habitat": "boreal_forest", "ecoregions": [718]})
    )
    cell = [717, 0.85, 718, 0.15, 6, 1.0, 0.0, 80.0]
    cells = {f"{c},{r}": list(cell) for r in (477, 478, 479) for c in (412, 413, 414)}
    grids = _grids(tmp_path, cells)
    made, blocked = make_tile(
        "n_413_478",
        grids,  # type: ignore[arg-type]
        world,
        presence={"fox": 5.0, "vole": 20.0},
        builder_root=tmp_path,
    )
    assert blocked is None
    assert made is not None
    assert made["blend"] == {"home": "taiga", "share": 0.15}
    assert made["requires"]["homes"] == ["boreal_forest", "taiga"]


def test_make_tile_blocks_ocean_without_ecoregion(tmp_path: Path) -> None:
    world = _world(tmp_path)
    grids = _grids(tmp_path, {"100,500": [None, 0.0, None, 0.0, None, 0.0, 0.0, 0.0]})
    made, blocked = make_tile(
        "n_100_500",
        grids,
        world,  # type: ignore[arg-type]
        presence={"herring": 30.0},
        builder_root=tmp_path,
    )
    assert made is None
    assert blocked == {"reason": "no_home", "habitat": "open_ocean"}


def _checker(world: Path, report: dict[str, Any], code: int) -> None:
    tools = world / "tools"
    tools.mkdir(exist_ok=True)
    (tools / "check_tile.py").write_text(
        f"import json, sys\nprint(json.dumps({report!r}))\nsys.exit({code})\n"
    )


def test_make_tile_blocks_when_world_check_fails_food(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _checker(
        world,
        {
            "ok": False,
            "results": [
                {
                    "check": "food",
                    "status": "fail",
                    "problems": ["fox hunts nothing here"],
                }
            ],
        },
        1,
    )
    grids = _grids(tmp_path, _boreal_3x3(413, 478))
    made, blocked = make_tile(
        "n_413_478",
        grids,  # type: ignore[arg-type]
        world,
        presence={"fox": 5.0, "vole": 20.0},
        builder_root=tmp_path,
    )
    assert made is None
    assert blocked == {"reason": "no_animal", "habitat": "boreal_forest"}


def test_make_tile_raises_when_world_check_fails_land(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _checker(
        world,
        {
            "ok": False,
            "results": [
                {
                    "check": "edges",
                    "status": "fail",
                    "problems": ["edges.n leaves the land"],
                }
            ],
        },
        1,
    )
    grids = _grids(tmp_path, _boreal_3x3(413, 478))
    with pytest.raises(RuntimeError, match="world's check"):
        make_tile(
            "n_413_478",
            grids,  # type: ignore[arg-type]
            world,
            presence={"fox": 5.0, "vole": 20.0},
            builder_root=tmp_path,
        )
