"""The planner: code_only tiles and honest items. No network."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

from builder.habitats import HabitatGrid
from builder.plan import nearest_home, plan_name, plan_tile
from builder.schemas import validate


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
    (world / "animals" / "dormouse.json").write_text(
        json.dumps({"playable": True, "tags": ["small_mammal"], "diet": ["berries"]})
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
                    {
                        "id": "dormouse",
                        "gbif_key": 2,
                        "habitats": ["boreal_forest"],
                    },
                    {
                        "id": "lion",
                        "gbif_key": 5219404,
                        "habitats": ["boreal_forest"],
                        "start_from": "fox",
                        "can_make": True,
                    },
                    {
                        "id": "zebra",
                        "gbif_key": 444,
                        "habitats": ["boreal_forest"],
                        "start_from": "mountain_hare",
                        "can_make": True,
                    },
                    {
                        "id": "okapi",
                        "gbif_key": 445,
                        "habitats": ["boreal_forest"],
                        "start_from": "fox",
                        "can_make": True,
                    },
                    {
                        "id": "shrew",
                        "gbif_key": 446,
                        "habitats": ["boreal_forest"],
                        "can_make": False,
                    },
                    {
                        "id": "octopus",
                        "gbif_key": 447,
                        "habitats": ["boreal_forest"],
                        "can_make": True,
                    },
                ],
            }
        )
    )
    return world


def _habitat_grid(tmp_path: Path, cells: dict[str, list[Any]]) -> HabitatGrid:
    path = tmp_path / "z10.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump({"grid": "z10", "cells": cells}, handle)
    return HabitatGrid.load(path)


def _boreal(cell: list[Any] | None = None) -> dict[str, list[Any]]:
    base = cell or [717, 1.0, None, 0.0, 6, 1.0, 0.0, 80.0]
    return {f"{c},{r}": list(base) for r in (293, 294, 295) for c in (580, 581, 582)}


def test_plan_ignores_region_grids(tmp_path: Path) -> None:
    """#202.3: a region grid no longer blocks planning; ordinary tile."""
    world = _world(tmp_path)
    (world / "regions").mkdir()
    (world / "regions" / "fi_test.json").write_text(
        json.dumps({"id": "fi_test", "grid": {"grid": "z10", "tiles": [[581, 294]]}})
    )
    habitat_grid = _habitat_grid(tmp_path, _boreal())
    plan = plan_tile(
        "z10_0581_0294",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={"fox": 5.0, "vole": 20.0},
        unfiled_presence={},
    )
    validate("plan", plan)
    assert plan["code_only"] is True
    assert plan.get("blocked") is None


def test_plan_is_code_only_when_tile_makes(tmp_path: Path) -> None:
    world = _world(tmp_path)
    habitat_grid = _habitat_grid(tmp_path, _boreal())
    plan = plan_tile(
        "z10_0581_0294",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={"fox": 5.0, "vole": 20.0},
        unfiled_presence={},
    )
    validate("plan", plan)
    assert plan["code_only"] is True
    assert plan["items"] == []
    assert plan["improve"] == []
    assert plan["estimate_usd"] == 0
    assert plan["tile"] == "z10_0581_0294"
    assert plan["world"] == "abc123"
    assert plan["name"]["en"] == "Boreal forest north of Nuuksio"
    assert plan["name"]["fi"] == "Havumetsä Nuuksion pohjoispuolella"


def test_plan_lists_home_from_nearest(tmp_path: Path) -> None:
    world = _world(tmp_path)
    cells = _boreal([999, 1.0, None, 0.0, 11, 1.0, 0.0, 200.0])
    cells["582,294"] = [717, 1.0, None, 0.0, 6, 1.0, 0.0, 80.0]
    habitat_grid = _habitat_grid(tmp_path, cells)
    plan = plan_tile(
        "z10_0581_0294",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={},
        unfiled_presence={},
    )
    assert plan["code_only"] is False
    assert plan["items"] == [
        {
            "id": "home_999",
            "kind": "home",
            "what": "999",
            "from": "boreal_forest",
            "estimate_usd": 0.2,
        }
    ]
    assert plan["estimate_usd"] == 0.2
    assert plan["coming"] == []


def test_nearest_home_falls_back_without_neighbours(tmp_path: Path) -> None:
    world = _world(tmp_path)
    habitat_grid = _habitat_grid(
        tmp_path, _boreal([999, 1.0, None, 0.0, 11, 1.0, 0.0, 200.0])
    )
    from builder.tiles import available_homes

    assert (
        nearest_home(
            "z10_0581_0294",
            habitat_grid,
            available_homes(world),
            "tundra",  # type: ignore[arg-type]
        )
        == "boreal_forest"
    )


def test_nearest_home_prefers_the_same_habitat(tmp_path: Path) -> None:
    """#139.2: a sea border near the forest adapts from sea, not the forest."""
    world = _world(tmp_path)
    (world / "homes" / "sea.json").write_text(
        json.dumps({"habitat": "sea", "ecoregions": [651]})
    )
    habitat_grid = _habitat_grid(tmp_path, _boreal())
    from builder.tiles import available_homes

    assert (
        nearest_home(
            "z10_0581_0294",
            habitat_grid,
            available_homes(world),
            "sea",  # type: ignore[arg-type]
        )
        == "sea"
    )


def test_nearest_home_prefers_land_behind_the_coast(tmp_path: Path) -> None:
    """#141.4: a forest border adapts from the farther forest, not the sea."""
    world = _world(tmp_path)
    (world / "homes" / "sea.json").write_text(
        json.dumps({"habitat": "sea", "ecoregions": [651]})
    )
    forest = [679, 1.0, None, 0.0, 4, 1.0, 0.0, 50.0]
    sea = [651, 1.0, None, 0.0, 4, 0.3, 0.0, 0.0]
    boreal = [717, 1.0, None, 0.0, 6, 1.0, 0.0, 80.0]
    bare = [999, 1.0, None, 0.0, 11, 1.0, 0.0, 200.0]
    cells = {f"{c},{r}": list(bare) for r in range(292, 297) for c in range(579, 584)}
    cells["581,294"] = list(forest)
    cells["582,294"] = list(sea)
    cells["579,294"] = list(boreal)
    habitat_grid = _habitat_grid(tmp_path, cells)
    from builder.tiles import available_homes

    assert (
        nearest_home(
            "z10_0581_0294",
            habitat_grid,
            available_homes(world),
            "forest",  # type: ignore[arg-type]
        )
        == "boreal_forest"
    )


def test_nearest_home_prefers_water_for_the_sea(tmp_path: Path) -> None:
    """#141.4: a sea border adapts from the farther sea, not the land."""
    world = _world(tmp_path)
    (world / "homes" / "sea.json").write_text(
        json.dumps({"habitat": "sea", "ecoregions": [651]})
    )
    sea = [651, 1.0, None, 0.0, 4, 0.3, 0.0, 0.0]
    boreal = [717, 1.0, None, 0.0, 6, 1.0, 0.0, 80.0]
    bare = [999, 1.0, None, 0.0, 11, 1.0, 0.0, 200.0]
    cells = {f"{c},{r}": list(bare) for r in (293, 294, 295) for c in (580, 581, 582)}
    cells["581,294"] = [None, 1.0, None, 0.0, None, 0.0, 1.0, -50.0]
    cells["580,294"] = list(boreal)
    cells["582,294"] = list(sea)
    habitat_grid = _habitat_grid(tmp_path, cells)
    from builder.tiles import available_homes

    assert (
        nearest_home(
            "z10_0581_0294",
            habitat_grid,
            available_homes(world),
            "open_ocean",  # type: ignore[arg-type]
        )
        == "sea"
    )


def test_tallinn_adapts_from_the_land_home() -> None:
    """#141.4: z10_0582_0300 adapts from boreal_forest, not sea."""
    import pytest

    from builder.tiles import available_homes

    world = Path(__file__).resolve().parent.parent.parent / "animundo-world"
    if not (world / "animal-list.json").is_file():
        pytest.skip("needs an animundo-world checkout")
    grid_dir = Path(__file__).resolve().parent.parent / "data" / "habitat-grid"
    from builder.habitats import load_grid

    habitat_grid = load_grid(grid_dir)
    homes = available_homes(world)
    assert (
        nearest_home("z10_0582_0300", habitat_grid, homes, "forest") == "boreal_forest"
    )


def test_plan_blocks_no_land_without_a_style(tmp_path: Path) -> None:
    """#148.3: a biome the index never styled blocks the plan as no_land."""
    world = _world(tmp_path)
    (world / "lands").mkdir()
    (world / "lands" / "index.json").write_text(
        json.dumps({"styles": [{"land": "boreal", "biome": 6}]})
    )
    cells = _boreal()
    cells["581,294"] = [999, 1.0, None, 0.0, 13, 1.0, 0.0, 80.0]
    habitat_grid = _habitat_grid(tmp_path, cells)
    plan = plan_tile(
        "z10_0581_0294",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={},
        unfiled_presence={},
    )
    assert plan["blocked"] == "no_land"
    assert plan["items"] == []


def test_tropical_sea_blocks_instead_of_the_generic_sea(tmp_path: Path) -> None:
    """#169.1: z10_0926_0560 never plans with home: sea."""
    import gzip

    from builder.tiles import available_biomes, home_for_cell

    world = _world(tmp_path)
    (world / "homes" / "sea.json").write_text(
        json.dumps({"habitat": "sea", "ecoregions": [651]})
    )
    biomes = world / "biomes"
    biomes.mkdir()
    (biomes / "sea.json").write_text(
        json.dumps(
            {"habitat": "sea", "ecoregions": [651], "land": "temperate_broadleaf"}
        )
    )
    (biomes / "sea_717.json").write_text(
        json.dumps({"habitat": "sea", "ecoregions": [717], "land": "boreal"})
    )
    lands = world / "lands"
    lands.mkdir()
    (lands / "index.json").write_text(
        json.dumps(
            {
                "styles": [
                    {"land": "boreal", "biome": 6},
                    {"land": "tropical_moist", "biome": 1},
                ]
            }
        )
    )
    tropical = [150, 1.0, None, 0.0, 1, 0.28, 0.0, 165.6]
    cells = {
        f"{c},{r}": list(tropical) for r in (559, 560, 561) for c in (926, 927, 928)
    }
    path = tmp_path / "z10.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump({"grid": "z10", "cells": cells}, handle)
    habitat_grid = HabitatGrid.load(path)
    plan = plan_tile(
        "z10_0926_0560",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={},
        unfiled_presence={},
    )
    assert plan["blocked"] == "no_home"
    assert plan["items"] == []
    filed = available_biomes(world)
    assert home_for_cell(150, "sea", filed, 1) is None
    assert home_for_cell(717, "sea", filed, 6) == "sea_717"


def test_helsinki_sea_plans_code_only_with_its_own_plants() -> None:
    """#148.3: 717 by the sea plans code-only on sea_717, no home item.

    The tile sits just east of Nuuksio's place cells (#182).
    """
    import pytest

    from builder.tiles import make_tile

    world = Path(__file__).resolve().parent.parent.parent / "animundo-world"
    if not (world / "homes" / "sea_717.json").is_file():
        pytest.skip("needs world d009df2 with the reviewed sea home")
    grid_dir = Path(__file__).resolve().parent.parent / "data" / "habitat-grid"
    from builder.habitats import load_grid

    habitat_grid = load_grid(grid_dir)
    answer = {
        "place": "Helsinki shore on the Gulf of Finland",
        "parts": [
            {"kind": "sea", "species": ["herring_gull"]},
            {
                "kind": "shore",
                "species": [
                    "fox",
                    "mountain_hare",
                    "squirrel",
                    "vole",
                    "chaffinch",
                    "frog",
                ],
            },
        ],
        "plants": [
            {"id": "white_clover", "count": 10},
            {"id": "porcini", "count": 4},
            {"id": "crowberry", "count": 10},
        ],
        "trees": [
            {"id": "scots_pine", "count": 20},
            {"id": "birch", "count": 10},
        ],
        "name_en": "Helsinki Shore",
        "name_fi": "Helsingin ranta",
    }
    plan = plan_tile(
        "z10_0583_0296",
        None,
        habitat_grid,
        world,
        "abc123",
        presence=None,
        unfiled_presence={},
        species=answer,
    )
    assert plan["code_only"] is True
    assert plan["items"] == []
    made, blocked = make_tile(
        "z10_0583_0296",
        habitat_grid,
        world,
        species=answer,
    )
    assert blocked is None
    assert made is not None
    assert made["biome"] == "sea_717"
    assert (made.get("plants") or {}).get("cover")


def test_tallinn_blocks_without_a_matching_bundle() -> None:
    """#148.3: styled but unmatched, z10_0582_0300 blocks instead of a home item."""
    import pytest

    world = Path(__file__).resolve().parent.parent.parent / "animundo-world"
    if not (world / "animal-list.json").is_file():
        pytest.skip("needs an animundo-world checkout")
    grid_dir = Path(__file__).resolve().parent.parent / "data" / "habitat-grid"
    from builder.habitats import load_grid

    habitat_grid = load_grid(grid_dir)
    plan = plan_tile(
        "z10_0582_0300",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={},
        unfiled_presence={},
    )
    assert plan["blocked"] == "no_home"
    assert plan["items"] == []


def test_plan_picks_most_recorded_unfiled_animal(tmp_path: Path) -> None:
    world = _world(tmp_path)
    habitat_grid = _habitat_grid(tmp_path, _boreal())
    plan = plan_tile(
        "z10_0581_0294",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={},
        unfiled_presence={"lion": 5.0, "zebra": 9.0, "okapi": 2.0},
    )
    assert plan["code_only"] is False
    assert plan["items"] == [
        {
            "id": "animal_zebra",
            "kind": "animal",
            "what": "zebra",
            "from": "mountain_hare",
            "estimate_usd": 0.42,
        }
    ]
    assert [i["id"] for i in plan["improve"]] == ["improve_lion", "improve_okapi"]
    assert plan["coming"] == ["zebra", "lion", "okapi"]
    assert plan["estimate_usd"] == 1.26


def test_plan_prefers_the_explorers_animal(tmp_path: Path) -> None:
    world = _world(tmp_path)
    habitat_grid = _habitat_grid(tmp_path, _boreal())
    plan = plan_tile(
        "z10_0581_0294",
        {"species_played": {"okapi": 3, "fox": 1}},
        habitat_grid,
        world,
        "abc123",
        presence={},
        unfiled_presence={"lion": 50.0, "okapi": 2.0},
    )
    assert [i["id"] for i in plan["items"]] == ["animal_okapi"]
    assert [i["id"] for i in plan["improve"]] == ["improve_lion"]


def test_plan_skips_animals_it_cannot_make(tmp_path: Path) -> None:
    world = _world(tmp_path)
    habitat_grid = _habitat_grid(tmp_path, _boreal())
    plan = plan_tile(
        "z10_0581_0294",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={},
        unfiled_presence={"shrew": 99.0},
    )
    assert plan["items"] == []
    assert plan["improve"] == []


def test_plan_lists_missing_food_for_filed_animals(tmp_path: Path) -> None:
    world = _world(tmp_path)
    (world / "homes" / "boreal_forest.json").write_text(
        json.dumps(
            {
                "habitat": "boreal_forest",
                "ecoregions": [717],
                "food": [{"kind": "berries"}],
            }
        )
    )
    habitat_grid = _habitat_grid(tmp_path, _boreal())
    plan = plan_tile(
        "z10_0581_0294",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={"mountain_hare": 9.0},
        unfiled_presence={},
    )
    assert plan["code_only"] is False
    assert plan["items"] == [
        {
            "id": "food_grass",
            "kind": "food",
            "what": "grass",
            "from": "boreal_forest",
            "estimate_usd": 0.0,
        }
    ]


def test_plan_lists_food_and_animal_together(tmp_path: Path) -> None:
    """A hungry tile with recorded newcomers needs both (spec 04, minimum)."""
    world = _world(tmp_path)
    (world / "homes" / "boreal_forest.json").write_text(
        json.dumps(
            {
                "habitat": "boreal_forest",
                "ecoregions": [717],
                "food": [{"kind": "berries"}],
            }
        )
    )
    habitat_grid = _habitat_grid(tmp_path, _boreal())
    plan = plan_tile(
        "z10_0581_0294",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={"mountain_hare": 9.0},
        unfiled_presence={"lion": 5.0},
    )
    assert [i["id"] for i in plan["items"]] == ["food_grass", "animal_lion"]


def test_plan_skips_animal_when_one_serves(tmp_path: Path) -> None:
    """A fed playable on the tile means no new animal, only the food.

    The world's food check disagrees with the picker here (the stub
    fails it), so the tile blocks with the fox serving: the plan
    lists the missing food, never a redundant animal.
    """
    world = _world(tmp_path)
    (world / "homes" / "boreal_forest.json").write_text(
        json.dumps(
            {
                "habitat": "boreal_forest",
                "ecoregions": [717],
                "food": [{"kind": "berries"}],
            }
        )
    )
    tools = world / "tools"
    tools.mkdir()
    (tools / "check_tile.py").write_text(
        "import json, sys\n"
        "json.load(open(sys.argv[1]))\n"
        "print(json.dumps({'results': ["
        "{'check': 'food', 'status': 'fail', 'problems': ['hungry']}"
        "]}))\n"
    )
    (tools / "tile_region.py").write_text('"""A converter new enough.\n"""\n')
    habitat_grid = _habitat_grid(tmp_path, _boreal())
    plan = plan_tile(
        "z10_0581_0294",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={"fox": 5.0, "dormouse": 4.0, "mountain_hare": 9.0},
        unfiled_presence={"lion": 50.0},
    )
    assert [i["id"] for i in plan["items"]] == ["food_grass"]
    assert [i["id"] for i in plan["improve"]] == ["improve_lion"]


def test_plan_lists_one_thing_to_find(tmp_path: Path) -> None:
    """The home's first grown plant without a file is the find (T74)."""
    world = _world(tmp_path)
    plants = world / "models" / "plants"
    plants.mkdir(parents=True)
    (plants / "bilberry.json").write_text(json.dumps({"id": "bilberry"}))
    (world / "homes" / "boreal_forest.json").write_text(
        json.dumps(
            {
                "habitat": "boreal_forest",
                "ecoregions": [717],
                "food": [
                    {"kind": "grass"},
                    {"kind": "berries", "names": ["bilberry", "Arctic Willow"]},
                ],
                "ground_cover": {"plants": [{"model": "arctic_willow"}]},
            }
        )
    )
    habitat_grid = _habitat_grid(tmp_path, _boreal())
    plan = plan_tile(
        "z10_0581_0294",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={},
        unfiled_presence={},
    )
    assert plan["code_only"] is False
    assert plan["items"] == [
        {
            "id": "plant_arctic_willow",
            "kind": "plant",
            "what": "arctic_willow",
            "from": "bilberry",
            "estimate_usd": 0.13,
        }
    ]


def _find_world(tmp_path: Path) -> Path:
    """Bilberry filed, the home growing cloudberry after it."""
    world = _world(tmp_path)
    plants = world / "models" / "plants"
    plants.mkdir(parents=True)
    (plants / "bilberry.json").write_text(json.dumps({"id": "bilberry"}))
    (world / "homes" / "boreal_forest.json").write_text(
        json.dumps(
            {
                "habitat": "boreal_forest",
                "ecoregions": [717],
                "food": [
                    {"kind": "berries", "names": ["bilberry", "cloudberry"]},
                ],
                "ground_cover": {"plants": [{"model": "cloudberry"}]},
            }
        )
    )
    return world


def _survey(*plant_ids: str) -> dict[str, Any]:
    return {
        "place": "Boreal forest in southern Finland",
        "parts": [{"kind": "forest", "species": ["fox", "vole"]}],
        "plants": [{"id": pid, "count": 5} for pid in plant_ids],
        "trees": [],
        "name_en": "Nuuksio forest",
        "name_fi": "Nuuksion metsä",
    }


def test_plan_plant_prefers_the_survey_pick(tmp_path: Path) -> None:
    """#147.3: the survey's unfiled pick becomes the plant item."""
    world = _find_world(tmp_path)
    habitat_grid = _habitat_grid(tmp_path, _boreal())
    plan = plan_tile(
        "z10_0581_0294",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={},
        unfiled_presence={},
        species=_survey("bladderwrack"),
    )
    assert plan["items"] == [
        {
            "id": "plant_bladderwrack",
            "kind": "plant",
            "what": "bladderwrack",
            "from": "bilberry",
            "estimate_usd": 0.13,
        }
    ]


def test_plan_plant_falls_back_when_the_survey_pick_is_filed(
    tmp_path: Path,
) -> None:
    """#147.3: a filed survey pick leaves the home's missing one."""
    world = _find_world(tmp_path)
    habitat_grid = _habitat_grid(tmp_path, _boreal())
    plan = plan_tile(
        "z10_0581_0294",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={},
        unfiled_presence={},
        species=_survey("bilberry"),
    )
    assert plan["items"] == [
        {
            "id": "plant_cloudberry",
            "kind": "plant",
            "what": "cloudberry",
            "from": "bilberry",
            "estimate_usd": 0.13,
        }
    ]


def test_plan_plant_without_survey_picks_is_the_home_missing(
    tmp_path: Path,
) -> None:
    """#147.3: no survey picks, the home's missing plant as before."""
    world = _find_world(tmp_path)
    habitat_grid = _habitat_grid(tmp_path, _boreal())
    plan = plan_tile(
        "z10_0581_0294",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={},
        unfiled_presence={},
        species=_survey(),
    )
    assert [i["id"] for i in plan["items"]] == ["plant_cloudberry"]


def test_plan_writes_no_items_for_excluded_cells(tmp_path: Path) -> None:
    """#133.7: an excluded cell is never planned and never built."""
    world = _world(tmp_path)
    (tmp_path / "exclusions.json").write_text(json.dumps({"tiles": ["z10_0581_0294"]}))
    habitat_grid = _habitat_grid(tmp_path, _boreal())
    plan = plan_tile(
        "z10_0581_0294",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={"fox": 5.0, "vole": 20.0},
        unfiled_presence={"lion": 50.0},
        builder_root=tmp_path,
    )
    validate("plan", plan)
    assert plan["code_only"] is False
    assert plan["items"] == []
    assert plan["improve"] == []
    assert plan["coming"] == []


def test_plan_leaves_out_animals_without_start_from(tmp_path: Path) -> None:
    """#133.8: no reference on the list means no item, not a home `from`."""
    world = _world(tmp_path)
    habitat_grid = _habitat_grid(tmp_path, _boreal())
    plan = plan_tile(
        "z10_0581_0294",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={},
        unfiled_presence={"octopus": 99.0},
    )
    assert plan["items"] == []
    assert plan["improve"] == []
    assert plan["coming"] == []


def test_plan_prefers_a_buildable_animal_over_an_unbuildable_one(
    tmp_path: Path,
) -> None:
    """#133.8: the most recorded animal with a reference wins."""
    world = _world(tmp_path)
    habitat_grid = _habitat_grid(tmp_path, _boreal())
    plan = plan_tile(
        "z10_0581_0294",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={},
        unfiled_presence={"octopus": 99.0, "lion": 5.0},
    )
    assert [i["id"] for i in plan["items"]] == ["animal_lion"]
    assert plan["items"][0]["from"] == "fox"
    assert plan["coming"] == ["lion"]


def test_plan_adapts_a_find_from_the_same_food_kind(tmp_path: Path) -> None:
    """#133.8: a find's reference is a filed plant of its food kind."""
    world = _world(tmp_path)
    plants = world / "models" / "plants"
    plants.mkdir(parents=True)
    (plants / "amanita.json").write_text(json.dumps({"id": "amanita"}))
    (plants / "bilberry.json").write_text(json.dumps({"id": "bilberry"}))
    (world / "homes" / "boreal_forest.json").write_text(
        json.dumps(
            {
                "habitat": "boreal_forest",
                "ecoregions": [717],
                "food": [
                    {"kind": "berries", "names": ["bilberry", "cloudberry"]},
                ],
                "ground_cover": {
                    "plants": [{"model": "cloudberry", "food": {"kind": "berries"}}]
                },
            }
        )
    )
    habitat_grid = _habitat_grid(tmp_path, _boreal())
    plan = plan_tile(
        "z10_0581_0294",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={},
        unfiled_presence={},
    )
    assert plan["items"] == [
        {
            "id": "plant_cloudberry",
            "kind": "plant",
            "what": "cloudberry",
            "from": "bilberry",
            "estimate_usd": 0.13,
        }
    ]


def test_plan_names_direction_from_nearest_place() -> None:
    assert plan_name("sea", 52.077, -5.007) == {
        "en": "Sea southwest of Nuuksio",
        "fi": "Meri Nuuksion lounaispuolella",
    }
    assert plan_name("tundra", 68.40, 23.60) == {
        "en": "Tundra north of Saimaa",
        "fi": "Tundra Saimaan pohjoispuolella",
    }


def _reef_place(world: Path) -> None:
    places = world / "places"
    places.mkdir(exist_ok=True)
    (places / "au_great_barrier_reef.json").write_text(
        json.dumps(
            {
                "id": "au_great_barrier_reef",
                "name": "Great Barrier Reef – Green Island",
                "center": {"lat": -16.759, "lon": 145.973},
            }
        )
    )


def test_plan_names_from_the_nearest_place(tmp_path: Path) -> None:
    """#169.2: a reef neighbour is named from the reef, both languages."""
    from builder.grid import center

    world = _world(tmp_path)
    _reef_place(world)
    lat, lon = center("z10_0927_0559")
    named = plan_name("open_ocean", lat, lon, world)
    assert named["en"] == "Open ocean north of Great Barrier Reef – Green Island"
    assert named["fi"] == (
        "Avomeri Great Barrier Reef – Green Islandin pohjoispuolella"
    )


def _named_world(tmp_path: Path) -> Path:
    """A world with the three filed places and their genitives."""
    world = tmp_path / "world"
    places = world / "places"
    places.mkdir(parents=True)
    for pid, en, fi, gen, lat, lon in (
        (
            "fi_nuuksio",
            "Nuuksio National Park",
            "Nuuksion kansallispuisto",
            "Nuuksion kansallispuiston",
            60.2398,
            24.6094,
        ),
        (
            "fi_saimaa",
            "Lake Saimaa – Linnansaari",
            "Saimaa – Linnansaari",
            "Saimaan – Linnansaaren",
            62.186,
            28.6523,
        ),
        (
            "au_great_barrier_reef",
            "Great Barrier Reef – Green Island",
            "Iso valliriutta – Vihreä saari",
            "Ison Valliriutan – Green Islandin",
            -16.8045,
            146.0742,
        ),
    ):
        (places / f"{pid}.json").write_text(
            json.dumps(
                {
                    "id": pid,
                    "name": {"en": en, "fi": fi, "fi_gen": gen},
                    "center": {"lat": lat, "lon": lon},
                }
            )
        )
    return world


def test_plan_name_cases_filed_finnish_names(tmp_path: Path) -> None:
    """#209.3: the compass uses each place's filed genitive."""
    world = _named_world(tmp_path)
    assert plan_name("boreal_forest", 60.2398, 24.7094, world) == {
        "en": "Boreal forest east of Nuuksio National Park",
        "fi": "Havumetsä Nuuksion kansallispuiston itäpuolella",
    }
    assert plan_name("boreal_lake", 62.286, 28.6523, world) == {
        "en": "Boreal lake north of Lake Saimaa – Linnansaari",
        "fi": "Erämaajärvi Saimaan – Linnansaaren pohjoispuolella",
    }
    assert plan_name("sea", -16.8045, 145.9742, world) == {
        "en": "Sea west of Great Barrier Reef – Green Island",
        "fi": "Meri Ison Valliriutan – Green Islandin länsipuolella",
    }


def test_plan_name_finnish_carries_no_english_place_name(tmp_path: Path) -> None:
    """#209.4: no English place name inside a Finnish one."""
    world = _named_world(tmp_path)
    fi = plan_name("boreal_forest", 60.2398, 24.7094, world)["fi"]
    assert "National Park" not in fi
    fi = plan_name("boreal_lake", 62.286, 28.6523, world)["fi"]
    assert "Lake Saimaa" not in fi
    fi = plan_name("sea", -16.8045, 145.9742, world)["fi"]
    assert "Great Barrier Reef" not in fi


def test_named_places_skips_a_place_without_finnish(tmp_path: Path) -> None:
    """#209.3: a name dict without fi is left out, not Englished."""
    from builder.plan import named_places

    world = tmp_path / "world"
    places = world / "places"
    places.mkdir(parents=True)
    (places / "a.json").write_text(
        json.dumps(
            {
                "id": "a",
                "name": {"en": "English Only"},
                "center": {"lat": 60.0, "lon": 24.0},
            }
        )
    )
    (places / "b.json").write_text(
        json.dumps(
            {
                "id": "b",
                "name": {"en": "Aho", "fi": "Aho"},
                "center": {"lat": 61.0, "lon": 25.0},
            }
        )
    )
    found = {pid: gen for pid, _en, gen, _lat, _lon in named_places(world)}
    assert "a" not in found
    assert found["b"] == "Ahon"


def test_plan_keeps_the_curated_name_on_a_tie(tmp_path: Path) -> None:
    """#169.2: where a curated name exists, the place doesn't rename it."""
    world = _world(tmp_path)
    places = world / "places"
    places.mkdir(exist_ok=True)
    (places / "fi_nuuksio.json").write_text(
        json.dumps(
            {
                "id": "fi_nuuksio",
                "name": "Nuuksio National Park",
                "center": {"lat": 60.3007, "lon": 24.5263},
            }
        )
    )
    assert plan_name("sea", 52.077, -5.007, world) == {
        "en": "Sea southwest of Nuuksio",
        "fi": "Meri Nuuksion lounaispuolella",
    }


def test_plan_tile_surveys_end_to_end_through_the_probe(tmp_path: Path) -> None:
    """#210: a surveyed tile plans; the probe keeps the outside lists."""
    from types import SimpleNamespace

    from builder.species import Source

    world = _world(tmp_path)
    listed = json.loads((world / "animal-list.json").read_text())
    for entry in listed["animals"]:
        entry.setdefault("name", entry["id"].replace("_", " ").title())
        entry.setdefault("latin", "Sus scrofa")
    (world / "animal-list.json").write_text(json.dumps(listed))
    habitat_grid = _habitat_grid(tmp_path, _boreal())
    answer = {
        "place": "Boreal forest in southern Finland",
        "parts": [{"kind": "forest", "species": ["fox", "mountain_hare"]}],
        "plants": [],
        "trees": [],
        "outside_animals": [],
        "outside_plants": [],
        "name_en": "Fox woods",
        "name_fi": "Kettujen metsä",
    }

    class _FakeSurvey:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, **kwargs: Any) -> Any:
            self.calls += 1
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content=json.dumps(answer)))
                ]
            )

    client = _FakeSurvey()
    plan = plan_tile(
        "z10_0581_0294",
        None,
        habitat_grid,
        world,
        "abc123",
        presence=None,
        unfiled_presence={},
        species_source=Source(client=client, cache_dir=tmp_path / "sp"),
    )
    assert client.calls == 1
    assert "blocked" not in plan
    assert "probe" not in plan


def test_plan_leaves_the_probe_undecided_without_a_key(tmp_path: Path) -> None:
    """#169.3: no cached survey and no key prints needs_survey, free."""
    from builder.species import Source

    world = _world(tmp_path)
    habitat_grid = _habitat_grid(tmp_path, _boreal())
    plan = plan_tile(
        "z10_0581_0294",
        None,
        habitat_grid,
        world,
        "abc123",
        presence=None,
        unfiled_presence={},
        species_source=Source(client=None, cache_dir=tmp_path / "sp"),
    )
    assert plan["probe"] == "needs_survey"
    assert plan["estimate_usd"] == 0.05
    assert plan["code_only"] is False
    assert plan["items"] == []
    assert "blocked" not in plan


def test_plan_probe_reraises_a_real_tile_error(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#169.3: only the missing survey is caught; a bug stays loud."""
    import pytest

    import builder.plan as plan_mod

    world = _world(tmp_path)
    habitat_grid = _habitat_grid(tmp_path, _boreal())

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("the tile maker broke")

    monkeypatch.setattr(plan_mod, "make_tile", broken)
    with pytest.raises(ValueError, match="the tile maker broke"):
        plan_tile(
            "z10_0581_0294",
            None,
            habitat_grid,
            world,
            "abc123",
            presence={},
            unfiled_presence={},
        )


def test_open_water_plan_names_its_place_neighbour(tmp_path: Path) -> None:
    """#169.2: z10_0927_0559's plan points from the Great Barrier Reef."""
    world = _world(tmp_path)
    _reef_place(world)
    path = tmp_path / "z10.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(
            {
                "grid": "z10",
                "cells": {"927,559": [None, 0.0, None, 0.0, None, 0.0, 0.0, 0.0]},
            },
            handle,
        )
    habitat_grid = HabitatGrid.load(path)
    plan = plan_tile(
        "z10_0927_0559",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={},
        unfiled_presence={},
    )
    assert plan["blocked"] == "open_water"
    assert "Great Barrier Reef – Green Island" in plan["name"]["en"]
    assert "Green Islandin" in plan["name"]["fi"]


def test_plan_ignores_the_species_names(tmp_path: Path) -> None:
    """#195.4: however fancy the answer's names, the compass names it."""
    from builder.grid import center

    world = _world(tmp_path)
    habitat_grid = _habitat_grid(tmp_path, _boreal())
    plan = plan_tile(
        "z10_0581_0294",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={"fox": 5.0, "vole": 20.0},
        unfiled_presence={},
        species={
            "place": "Boreal forest in southern Finland",
            "parts": [{"kind": "forest", "species": ["fox", "vole"]}],
            "plants": [],
            "trees": [],
            "name_en": "Whispering Pinewoods, Farms and Brook",
            "name_fi": "Kuusimetsien, peltojen ja puron maa",
        },
    )
    assert plan["name"] == plan_name("boreal_forest", *center("z10_0581_0294"), world)
    assert plan["name"]["en"] != "Whispering Pinewoods, Farms and Brook"


def test_plan_falls_back_to_compass_without_names(tmp_path: Path) -> None:
    """#135.5: no names in the answer, the compass names it."""
    world = _world(tmp_path)
    cells = _boreal([999, 1.0, None, 0.0, 11, 1.0, 0.0, 200.0])
    cells["582,294"] = [717, 1.0, None, 0.0, 6, 1.0, 0.0, 80.0]
    habitat_grid = _habitat_grid(tmp_path, cells)
    nameless = {
        "place": "Boreal forest in southern Finland",
        "parts": [{"kind": "forest", "species": ["fox", "vole"]}],
        "plants": [],
        "trees": [],
    }
    plan = plan_tile(
        "z10_0581_0294",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={},
        unfiled_presence={},
        species=nameless,
    )
    plain = plan_tile(
        "z10_0581_0294",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={},
        unfiled_presence={},
    )
    assert plan["name"] == plain["name"]
    assert "Nuuksio" in plan["name"]["en"]


def _open_water() -> dict[str, list[Any]]:
    cell: list[Any] = [None, 1.0, None, 0.0, None, 0.0, 1.0, -50.0]
    return {f"{c},{r}": list(cell) for r in (293, 294, 295) for c in (580, 581, 582)}


def test_plan_blocked_when_probe_fails_world_check(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#198.1: a world-check failure ends the request as blocked."""
    import builder.plan as plan_mod

    world = _world(tmp_path)
    habitat_grid = _habitat_grid(tmp_path, _boreal())

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(
            "tile z10_0581_0294 failed the world's check: "
            "animals: no home lists ecoregion 999"
        )

    monkeypatch.setattr(plan_mod, "make_tile", boom)
    plan = plan_tile(
        "z10_0581_0294",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={},
        unfiled_presence={},
        species=_survey(),
    )
    assert plan["blocked"] == "world_check: animals: no home lists ecoregion 999"
    assert plan["items"] == []
    assert plan["coming"] == []


def test_plan_reraises_a_transient_probe_failure(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#198.1: anything but the world check still raises to retry."""
    import pytest

    import builder.plan as plan_mod

    world = _world(tmp_path)
    habitat_grid = _habitat_grid(tmp_path, _boreal())

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("GBIF stayed down")

    monkeypatch.setattr(plan_mod, "make_tile", boom)
    with pytest.raises(RuntimeError, match="GBIF stayed down"):
        plan_tile(
            "z10_0581_0294",
            None,
            habitat_grid,
            world,
            "abc123",
            presence={},
            unfiled_presence={},
            species=_survey(),
        )


def test_plan_marks_open_water_blocked(tmp_path: Path) -> None:
    """#141.2: open water says nobody can go there yet, with no coming."""
    world = _world(tmp_path)
    habitat_grid = _habitat_grid(tmp_path, _open_water())
    plan = plan_tile(
        "z10_0581_0294",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={},
        unfiled_presence={},
    )
    assert plan["blocked"] == "open_water"
    assert plan["items"] == []
    assert plan["improve"] == []
    assert plan["coming"] == []
    assert plan["code_only"] is False


def test_plan_makes_no_species_call_for_open_water(tmp_path: Path) -> None:
    """#141.3: a tile nobody will open gets the compass, free."""
    from builder.species import Source

    world = _world(tmp_path)
    habitat_grid = _habitat_grid(tmp_path, _open_water())
    calls: list[str] = []

    class SpyClient:
        def chat(self, **kwargs: Any) -> Any:
            calls.append("chat")
            raise RuntimeError("no species call for open water")

    plan = plan_tile(
        "z10_0581_0294",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={},
        unfiled_presence={},
        species_source=Source(client=SpyClient(), cache_dir=tmp_path / "sp"),
    )
    assert calls == []
    assert plan["blocked"] == "open_water"
    from builder.grid import center

    assert plan["name"] == plan_name("open_ocean", *center("z10_0581_0294"))


def _open_sea_world(tmp_path: Path) -> Path:
    world = _world(tmp_path)
    (world / "biomes").mkdir(exist_ok=True)
    (world / "biomes" / "open_sea.json").write_text(
        json.dumps({"habitat": "sea", "food": [{"kind": "fish"}]})
    )
    (world / "biomes" / "boreal_forest.json").write_text(
        json.dumps(
            {
                "habitat": "boreal_forest",
                "ecoregions": [717],
                "food": [{"kind": "grass"}, {"kind": "berries"}],
            }
        )
    )
    (world / "animals" / "herring_gull.json").write_text(
        json.dumps(
            {
                "playable": True,
                "open_sea": True,
                "tags": ["seabird"],
                "diet": ["fish"],
            }
        )
    )
    listed = json.loads((world / "animal-list.json").read_text())
    listed["animals"].append(
        {"id": "herring_gull", "gbif_key": 2481126, "habitats": ["open_ocean"]}
    )
    (world / "animal-list.json").write_text(json.dumps(listed))
    (world / "seas").mkdir(exist_ok=True)
    (world / "seas" / "names.json").write_text(
        json.dumps(
            {
                "source": "evs:test",
                "seas": {
                    "gulf_of_finland": {
                        "en": "Gulf of Finland",
                        "fi": "Suomenlahti",
                        "ne_ids": [1],
                        "wikidata": "Q16978",
                    }
                },
            }
        )
    )
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    with gzip.open(data / "seas.json.gz", "wt", encoding="utf-8") as handle:
        json.dump({"cells": {"z10_0581_0294": "gulf_of_finland"}}, handle)
    (world / "lands").mkdir(exist_ok=True)
    (world / "lands" / "index.json").write_text(
        json.dumps({"styles": [{"land": "boreal", "biome": 6}]})
    )
    return world


def _open_sea_cells() -> dict[str, list[Any]]:
    cells = _open_water()
    cells["580,293"] = [717, 1.0, None, 0.0, 6, 1.0, 0.0, 80.0]
    return cells


def test_plan_lists_open_sea_as_code_only_with_no_survey(
    tmp_path: Path,
) -> None:
    """#177.5: a named sea tile is code_only, and asks the model nothing."""
    from builder.species import Source

    world = _open_sea_world(tmp_path)
    habitat_grid = _habitat_grid(tmp_path, _open_sea_cells())
    calls: list[str] = []

    class SpyClient:
        def chat(self, **kwargs: Any) -> Any:
            calls.append("chat")
            raise RuntimeError("no species call for open sea")

    plan = plan_tile(
        "z10_0581_0294",
        None,
        habitat_grid,
        world,
        "abc123",
        presence={"herring_gull": 5.0},
        unfiled_presence={},
        builder_root=tmp_path,
        species_source=Source(client=SpyClient(), cache_dir=tmp_path / "sp"),
    )
    validate("plan", plan)
    assert calls == []
    assert plan["code_only"] is True
    assert plan["items"] == []
    assert "blocked" not in plan


def test_finish_blocked_carries_no_improve() -> None:
    """#141.2: a blocked plan keeps no improve species."""
    from builder.plan import _finish

    plan = _finish(
        "z10_0581_0294",
        False,
        [],
        [{"id": "arctic_tern", "start_from": "tern"}],
        {"arctic_tern": 5.0},
        None,
        "open_ocean",
        59.5,
        24.0,
        "abc123",
        None,
        "open_water",
    )
    assert plan["blocked"] == "open_water"
    assert plan["improve"] == []
    assert plan["coming"] == []


def test_plan_never_plans_a_food_name_as_a_plant(tmp_path: Path) -> None:
    """#200.3: a Coral Sea open-sea tile plans no blue_mussel item."""
    import pytest

    from builder.schemas import ROOT

    world = ROOT.parent / "animundo-world"
    if not (world / "homes" / "open_sea.json").is_file():
        pytest.skip("needs the world checkout next to the builder")
    col, row = 928, 560
    cell: list[Any] = [None, 1.0, None, 0.0, None, 0.0, 1.0, -50.0]
    cells = {
        f"{c},{r}": list(cell)
        for r in (row - 1, row, row + 1)
        for c in (col - 1, col, col + 1)
    }
    plan = plan_tile(
        "z10_0928_0560",
        None,
        _habitat_grid(tmp_path, cells),
        world,
        "abc123",
        presence={},
        unfiled_presence={},
        species=_survey(),
    )
    assert "blue_mussel" not in [i["what"] for i in plan["items"]]
