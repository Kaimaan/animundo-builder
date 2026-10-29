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
            "estimate_usd": 0.02,
        }
    ]
    assert plan["estimate_usd"] == 0.02
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
            "estimate_usd": 0.02,
        }
    ]


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
            "estimate_usd": 0.02,
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
            "present": ["fox", "vole"],
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
        "present": ["fox", "vole"],
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
