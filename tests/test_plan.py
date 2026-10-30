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


def _grids(tmp_path: Path, cells: dict[str, list[Any]]) -> dict[str, HabitatGrid]:
    path = tmp_path / "ease2_n25.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump({"grid": "ease2_n25", "cells": cells}, handle)
    return {"ease2_n25": HabitatGrid.load(path, "ease2_n25")}  # type: ignore[dict-item]


def _boreal(cell: list[Any] | None = None) -> dict[str, list[Any]]:
    base = cell or [717, 1.0, None, 0.0, 6, 1.0, 0.0, 80.0]
    return {f"{c},{r}": list(base) for r in (477, 478, 479) for c in (412, 413, 414)}


def test_plan_skips_place_cells(tmp_path: Path) -> None:
    """#147.1: a place cell plans nothing, and says whose it is."""
    world = _world(tmp_path)
    (world / "regions").mkdir()
    (world / "regions" / "fi_test.json").write_text(
        json.dumps(
            {"id": "fi_test", "grid": {"grid": "ease2_n25", "tiles": [[413, 478]]}}
        )
    )
    grids = _grids(tmp_path, _boreal())
    plan = plan_tile(
        "n_413_478",
        None,
        grids,  # type: ignore[arg-type]
        world,
        "abc123",
        presence={},
        unfiled_presence={},
    )
    validate("plan", plan)
    assert plan["items"] == []
    assert plan["improve"] == []
    assert plan["coming"] == []
    assert plan["blocked"] == "place:fi_test"


def test_plan_is_code_only_when_tile_makes(tmp_path: Path) -> None:
    world = _world(tmp_path)
    grids = _grids(tmp_path, _boreal())
    plan = plan_tile(
        "n_413_478",
        None,
        grids,  # type: ignore[arg-type]
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
    assert plan["tile"] == "n_413_478"
    assert plan["world"] == "abc123"
    assert plan["name"]["en"] == "Boreal forest northwest of Nuuksio"
    assert plan["name"]["fi"] == "Havumetsä Nuuksion luoteispuolella"


def test_plan_lists_home_from_nearest(tmp_path: Path) -> None:
    world = _world(tmp_path)
    cells = _boreal([999, 1.0, None, 0.0, 11, 1.0, 0.0, 200.0])
    cells["414,478"] = [717, 1.0, None, 0.0, 6, 1.0, 0.0, 80.0]
    grids = _grids(tmp_path, cells)
    plan = plan_tile(
        "n_413_478",
        None,
        grids,  # type: ignore[arg-type]
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
    grids = _grids(tmp_path, _boreal([999, 1.0, None, 0.0, 11, 1.0, 0.0, 200.0]))
    from builder.tiles import available_homes

    assert (
        nearest_home(
            "n_413_478",
            grids,
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
    grids = _grids(tmp_path, _boreal())
    from builder.tiles import available_homes

    assert (
        nearest_home(
            "n_413_478",
            grids,  # type: ignore[arg-type]
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
    cells = {f"{c},{r}": list(bare) for r in range(476, 481) for c in range(411, 416)}
    cells["413,478"] = list(forest)
    cells["414,478"] = list(sea)
    cells["411,478"] = list(boreal)
    grids = _grids(tmp_path, cells)
    from builder.tiles import available_homes

    assert (
        nearest_home(
            "n_413_478",
            grids,  # type: ignore[arg-type]
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
    cells = {f"{c},{r}": list(bare) for r in (477, 478, 479) for c in (412, 413, 414)}
    cells["413,478"] = [None, 1.0, None, 0.0, None, 0.0, 1.0, -50.0]
    cells["412,478"] = list(boreal)
    cells["414,478"] = list(sea)
    grids = _grids(tmp_path, cells)
    from builder.tiles import available_homes

    assert (
        nearest_home(
            "n_413_478",
            grids,  # type: ignore[arg-type]
            available_homes(world),
            "open_ocean",  # type: ignore[arg-type]
        )
        == "sea"
    )


def test_tallinn_adapts_from_the_land_home() -> None:
    """#141.4: n_416_482 plans home_679 from boreal_forest, not sea."""
    import pytest

    from builder.tiles import available_homes

    world = Path(__file__).resolve().parent.parent.parent / "animundo-world"
    if not (world / "animal-list.json").is_file():
        pytest.skip("needs an animundo-world checkout")
    grid_dir = Path(__file__).resolve().parent.parent / "data" / "habitat-grid"
    grids = {
        name: HabitatGrid.load(grid_dir / f"{name}.json.gz", name)
        for name in ("ease2_n25", "ease2_s25")
    }
    homes = available_homes(world)
    assert (
        nearest_home("n_416_482", grids, homes, "forest")  # type: ignore[arg-type]
        == "boreal_forest"
    )
    plan = plan_tile(
        "n_416_482",
        None,
        grids,  # type: ignore[arg-type]
        world,
        "abc123",
        presence={},
        unfiled_presence={},
    )
    home_item = next(i for i in plan["items"] if i["kind"] == "home")
    assert home_item == {
        "id": "home_679",
        "kind": "home",
        "what": "679",
        "from": "boreal_forest",
        "estimate_usd": 0.2,
    }
    assert all(i["what"] != "kelp" for i in plan["items"] if i["kind"] == "plant")


def test_plan_picks_most_recorded_unfiled_animal(tmp_path: Path) -> None:
    world = _world(tmp_path)
    grids = _grids(tmp_path, _boreal())
    plan = plan_tile(
        "n_413_478",
        None,
        grids,  # type: ignore[arg-type]
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
            "estimate_usd": 0.15,
        }
    ]
    assert [i["id"] for i in plan["improve"]] == ["improve_lion", "improve_okapi"]
    assert plan["coming"] == ["zebra", "lion", "okapi"]
    assert plan["estimate_usd"] == 0.45


def test_plan_prefers_the_explorers_animal(tmp_path: Path) -> None:
    world = _world(tmp_path)
    grids = _grids(tmp_path, _boreal())
    plan = plan_tile(
        "n_413_478",
        {"species_played": {"okapi": 3, "fox": 1}},
        grids,  # type: ignore[arg-type]
        world,
        "abc123",
        presence={},
        unfiled_presence={"lion": 50.0, "okapi": 2.0},
    )
    assert [i["id"] for i in plan["items"]] == ["animal_okapi"]
    assert [i["id"] for i in plan["improve"]] == ["improve_lion"]


def test_plan_skips_animals_it_cannot_make(tmp_path: Path) -> None:
    world = _world(tmp_path)
    grids = _grids(tmp_path, _boreal())
    plan = plan_tile(
        "n_413_478",
        None,
        grids,  # type: ignore[arg-type]
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
    grids = _grids(tmp_path, _boreal())
    plan = plan_tile(
        "n_413_478",
        None,
        grids,  # type: ignore[arg-type]
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
    grids = _grids(tmp_path, _boreal())
    plan = plan_tile(
        "n_413_478",
        None,
        grids,  # type: ignore[arg-type]
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
    grids = _grids(tmp_path, _boreal())
    plan = plan_tile(
        "n_413_478",
        None,
        grids,  # type: ignore[arg-type]
        world,
        "abc123",
        presence={"fox": 5.0, "dormouse": 4.0, "mountain_hare": 9.0},
        unfiled_presence={"lion": 50.0},
    )
    assert [i["id"] for i in plan["items"]] == ["food_grass"]
    assert [i["id"] for i in plan["improve"]] == ["improve_lion"]


def test_plan_lists_one_thing_to_find(tmp_path: Path) -> None:
    """The home's first named plant without a file is the find (T74)."""
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
            }
        )
    )
    grids = _grids(tmp_path, _boreal())
    plan = plan_tile(
        "n_413_478",
        None,
        grids,  # type: ignore[arg-type]
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
            "estimate_usd": 0.15,
        }
    ]


def _find_world(tmp_path: Path) -> Path:
    """Bilberry filed, the home naming cloudberry after it."""
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
    grids = _grids(tmp_path, _boreal())
    plan = plan_tile(
        "n_413_478",
        None,
        grids,  # type: ignore[arg-type]
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
            "estimate_usd": 0.15,
        }
    ]


def test_plan_plant_falls_back_when_the_survey_pick_is_filed(
    tmp_path: Path,
) -> None:
    """#147.3: a filed survey pick leaves the home's missing one."""
    world = _find_world(tmp_path)
    grids = _grids(tmp_path, _boreal())
    plan = plan_tile(
        "n_413_478",
        None,
        grids,  # type: ignore[arg-type]
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
            "estimate_usd": 0.15,
        }
    ]


def test_plan_plant_without_survey_picks_is_the_home_missing(
    tmp_path: Path,
) -> None:
    """#147.3: no survey picks, the home's missing plant as before."""
    world = _find_world(tmp_path)
    grids = _grids(tmp_path, _boreal())
    plan = plan_tile(
        "n_413_478",
        None,
        grids,  # type: ignore[arg-type]
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
    (tmp_path / "exclusions.json").write_text(json.dumps({"tiles": ["n_413_478"]}))
    grids = _grids(tmp_path, _boreal())
    plan = plan_tile(
        "n_413_478",
        None,
        grids,  # type: ignore[arg-type]
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
    grids = _grids(tmp_path, _boreal())
    plan = plan_tile(
        "n_413_478",
        None,
        grids,  # type: ignore[arg-type]
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
    grids = _grids(tmp_path, _boreal())
    plan = plan_tile(
        "n_413_478",
        None,
        grids,  # type: ignore[arg-type]
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
            }
        )
    )
    grids = _grids(tmp_path, _boreal())
    plan = plan_tile(
        "n_413_478",
        None,
        grids,  # type: ignore[arg-type]
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
            "estimate_usd": 0.15,
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


def test_plan_uses_the_species_names(tmp_path: Path) -> None:
    """#135.5: a recorded answer names the plan in both languages."""
    world = _world(tmp_path)
    grids = _grids(tmp_path, _boreal())
    plan = plan_tile(
        "n_413_478",
        None,
        grids,  # type: ignore[arg-type]
        world,
        "abc123",
        presence={"fox": 5.0, "vole": 20.0},
        unfiled_presence={},
        species={
            "place": "Boreal forest in southern Finland",
            "parts": [{"kind": "forest", "species": ["fox", "vole"]}],
            "plants": [],
            "trees": [],
            "name_en": "Nuuksio forest",
            "name_fi": "Nuuksion metsä",
        },
    )
    assert plan["name"] == {"en": "Nuuksio forest", "fi": "Nuuksion metsä"}


def test_plan_falls_back_to_compass_without_names(tmp_path: Path) -> None:
    """#135.5: no names in the answer, the compass names it."""
    world = _world(tmp_path)
    cells = _boreal([999, 1.0, None, 0.0, 11, 1.0, 0.0, 200.0])
    cells["414,478"] = [717, 1.0, None, 0.0, 6, 1.0, 0.0, 80.0]
    grids = _grids(tmp_path, cells)
    nameless = {
        "place": "Boreal forest in southern Finland",
        "parts": [{"kind": "forest", "species": ["fox", "vole"]}],
        "plants": [],
        "trees": [],
    }
    plan = plan_tile(
        "n_413_478",
        None,
        grids,  # type: ignore[arg-type]
        world,
        "abc123",
        presence={},
        unfiled_presence={},
        species=nameless,
    )
    plain = plan_tile(
        "n_413_478",
        None,
        grids,  # type: ignore[arg-type]
        world,
        "abc123",
        presence={},
        unfiled_presence={},
    )
    assert plan["name"] == plain["name"]
    assert "Nuuksio" in plan["name"]["en"]


def _open_water() -> dict[str, list[Any]]:
    cell: list[Any] = [None, 1.0, None, 0.0, None, 0.0, 1.0, -50.0]
    return {f"{c},{r}": list(cell) for r in (477, 478, 479) for c in (412, 413, 414)}


def test_plan_marks_open_water_blocked(tmp_path: Path) -> None:
    """#141.2: open water says nobody can go there yet, with no coming."""
    world = _world(tmp_path)
    grids = _grids(tmp_path, _open_water())
    plan = plan_tile(
        "n_413_478",
        None,
        grids,  # type: ignore[arg-type]
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
    grids = _grids(tmp_path, _open_water())
    calls: list[str] = []

    class SpyClient:
        def chat(self, **kwargs: Any) -> Any:
            calls.append("chat")
            raise RuntimeError("no species call for open water")

    plan = plan_tile(
        "n_413_478",
        None,
        grids,  # type: ignore[arg-type]
        world,
        "abc123",
        presence={},
        unfiled_presence={},
        species_source=Source(client=SpyClient(), cache_dir=tmp_path / "sp"),
    )
    assert calls == []
    assert plan["blocked"] == "open_water"
    from builder.grid import center

    assert plan["name"] == plan_name("open_ocean", *center("n_413_478"))


def test_finish_blocked_carries_no_improve() -> None:
    """#141.2: a blocked plan keeps no improve species."""
    from builder.plan import _finish

    plan = _finish(
        "n_413_478",
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
