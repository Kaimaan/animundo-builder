"""Enrich place checks: sorting the model's failures from the place's. No key."""

from __future__ import annotations

import json
from pathlib import Path

from builder.enrich import (
    place_tiles,
    region_place,
    sort_problems,
    sort_region_problems,
    write_places,
)
from builder.schemas import ROOT


def _places(job_dir: Path) -> None:
    regions = job_dir / "regions"
    regions.mkdir(parents=True, exist_ok=True)
    (regions / "tl_n_413_478.json").write_text(
        json.dumps({"id": "tl_n_413_478", "animals": {}}), encoding="utf-8"
    )
    (regions / "tl_n_413_479.json").write_text(
        json.dumps({"id": "tl_n_413_479", "animals": {}}), encoding="utf-8"
    )


def test_place_tiles_maps_ids_and_stems(tmp_path: Path) -> None:
    """#161.2: food-web lines name region ids; both spellings land."""
    _places(tmp_path)
    mapping = place_tiles(tmp_path)
    assert mapping["tl_n_413_478"] == "n_413_478"
    assert mapping["tl_n_413_479"] == "n_413_479"


def test_sort_problems_splits_model_tile_and_place(tmp_path: Path) -> None:
    """#161.2: the new file's failures are the model's; food is the tile's."""
    _places(tmp_path)
    mapping = place_tiles(tmp_path)
    model, tiled, general = sort_problems(
        [
            'ERROR: lynx: needs text "intro"',
            'ERROR: squirrel in tl_n_413_479 eats "mushrooms", '
            "but the region has no such food",
            'ERROR: lynx in tl_n_413_479 hunts "small_mammal", '
            "but no animal there has that tag",
            'ERROR: region tl_n_413_479: playable animal "bee" has no file',
            "ERROR: fox: model file not found: res://assets/animals/fox.glb",
        ],
        "lynx",
        mapping,
    )
    assert model == ['ERROR: lynx: needs text "intro"']
    assert sorted(tiled) == ["n_413_479"]
    assert len(tiled["n_413_479"]) == 3
    assert general == ["ERROR: fox: model file not found: res://assets/animals/fox.glb"]


def test_sort_problems_unknown_place_is_general(tmp_path: Path) -> None:
    """#161.2: a place line for no known tile stays loud, never the model's."""
    _places(tmp_path)
    model, tiled, general = sort_problems(
        ['ERROR: squirrel in tl_n_999_999 eats "mushrooms"'],
        "lynx",
        place_tiles(tmp_path),
    )
    assert model == []
    assert tiled == {}
    assert general == ['ERROR: squirrel in tl_n_999_999 eats "mushrooms"']


def test_sort_region_problems_groups_by_place_file(tmp_path: Path) -> None:
    """#161.2: a failing place file lands its details on its tile."""
    _places(tmp_path)
    tiled, general = sort_region_problems(
        [
            "FAIL regions/tl_n_413_479.json",
            '- "center.lat" must be a number between -90 and 90',
            "FAIL regions/fi_nuuksio.json",
            '- "welcome" must be a non-empty text',
        ],
        place_tiles(tmp_path),
    )
    assert tiled == {"n_413_479": ['"center.lat" must be a number between -90 and 90']}
    assert general == ['"welcome" must be a non-empty text']


def test_write_places_lists_every_tile(tmp_path: Path) -> None:
    """#161.2: checks/places.json passes quiet tiles, fails loud ones."""
    _places(tmp_path)
    mapping = place_tiles(tmp_path)
    write_places(
        tmp_path,
        mapping,
        {"n_413_479": ["ERROR: squirrel in tl_n_413_479 eats mushrooms"]},
        ["ERROR: fox: something broke"],
        region_note="models not built: run tools/build_model.sh first",
    )
    data = json.loads((tmp_path / "checks" / "places.json").read_text())
    assert data["ok"] is False
    by_check = {entry["check"]: entry for entry in data["results"]}
    assert by_check["place n_413_478"]["status"] == "pass"
    failed = by_check["place n_413_479"]
    assert failed["status"] == "fail"
    assert failed["problems"] == ["ERROR: squirrel in tl_n_413_479 eats mushrooms"]
    assert by_check["place"]["status"] == "fail"
    assert by_check["region"]["status"] == "skip"


def _reef(world: Path) -> None:
    regions = world / "regions"
    regions.mkdir(parents=True, exist_ok=True)
    (regions / "xx_test_reef.json").write_text(
        json.dumps(
            {
                "id": "xx_test_reef",
                "name": {"en": "Test Reef"},
                "grid": {"grid": "ease2_n25", "tiles": [[413, 478]]},
                "animals": {
                    "playable": ["clownfish", "blue_tang"],
                    "prey": [],
                    "predators": ["reef_shark"],
                },
                "plants": {
                    "water": [{"id": "branching_staghorn_coral", "density": 0.08}]
                },
                "food": [{"kind": "water_plants", "names": ["seagrass"]}],
            }
        ),
        encoding="utf-8",
    )


def test_region_place_seats_the_new_animal(tmp_path: Path) -> None:
    """#168.1: the region keeps its cast; the new animal joins playable."""
    import fake_world

    world = fake_world.make_world(tmp_path)
    fake_world.write_converter_tools(world)
    _reef(world)
    before = json.loads((world / "regions" / "xx_test_reef.json").read_text())
    place = region_place("xx_test_reef", world, "hammerhead", "animal")
    assert place["id"] == "xx_test_reef"
    assert place["animals"]["playable"] == ["clownfish", "blue_tang", "hammerhead"]
    assert place["animals"]["predators"] == ["reef_shark"]
    changed = [key for key in place if place[key] != before.get(key)]
    assert changed == ["animals"]


def test_region_place_seats_a_plant_where_it_grows(tmp_path: Path) -> None:
    """#168.1: a new plant joins the region's grown side, not a tile's."""
    import fake_world

    world = fake_world.make_world(tmp_path)
    fake_world.write_converter_tools(world)
    _reef(world)
    place = region_place("xx_test_reef", world, "paddle_grass", "plant")
    grown = place["plants"]["water"]
    assert [p["id"] for p in grown] == [
        "branching_staghorn_coral",
        "paddle_grass",
    ]


def test_region_place_leaves_the_reef_otherwise_alone() -> None:
    """#173.1: the reef before and after differs only in its lists."""
    import pytest

    world = ROOT.parent / "animundo-world"
    if not (world / "regions" / "au_great_barrier_reef.json").is_file():
        pytest.skip("needs the world checkout next to the builder")
    before = json.loads((world / "regions" / "au_great_barrier_reef.json").read_text())
    place = region_place("au_great_barrier_reef", world, "hammerhead", "animal")
    changed = [key for key in place if place[key] != before.get(key)]
    assert changed == ["animals"]
    assert place["animals"]["playable"][-1] == "hammerhead"


def test_place_tiles_maps_a_region_to_itself(tmp_path: Path) -> None:
    """#168.1: a hand-made region's verdict line names the region."""
    regions = tmp_path / "regions"
    regions.mkdir(parents=True, exist_ok=True)
    (regions / "xx_test_reef.json").write_text(
        json.dumps({"id": "xx_test_reef"}), encoding="utf-8"
    )
    checks = tmp_path / "checks"
    checks.mkdir(parents=True, exist_ok=True)
    (checks / "enrich_places.json").write_text(
        json.dumps({"places": ["xx_test_reef"]}), encoding="utf-8"
    )
    mapping = place_tiles(tmp_path)
    assert mapping["xx_test_reef"] == "xx_test_reef"
    write_places(tmp_path, mapping, {}, [], None)
    data = json.loads((tmp_path / "checks" / "places.json").read_text())
    assert data["results"] == [
        {"check": "place xx_test_reef", "status": "pass", "problems": []}
    ]


def test_split_not_built_names_only_unbuilt_models() -> None:
    """#173.3: 'build it first' lines are unknown, not failures."""
    from builder.enrich import split_not_built

    real, models = split_not_built(
        [
            "FAIL au_great_barrier_reef",
            '- "ground_cover.plants[0]": build it first with '
            "tools/build_model.sh models/plants/branching_staghorn_coral.json",
            '- "ground_cover.plants[1]": build it first with '
            "tools/build_model.sh models/plants/giant_sea_fan.json",
            "- au_great_barrier_reef: no food for clownfish",
        ]
    )
    assert real == [
        "FAIL au_great_barrier_reef",
        "- au_great_barrier_reef: no food for clownfish",
    ]
    assert models == ["branching_staghorn_coral", "giant_sea_fan"]
    assert split_not_built(["build it first with tools/build_model.sh "]) == (
        [],
        [],
    )


def test_place_tiles_ignores_an_unmanifested_scaffold(tmp_path: Path) -> None:
    """#168.1: a build scaffold is never read as an enrich place."""
    regions = tmp_path / "regions"
    regions.mkdir(parents=True, exist_ok=True)
    (regions / "zz_work.json").write_text(
        json.dumps({"id": "zz_work"}), encoding="utf-8"
    )
    assert place_tiles(tmp_path) == {}
