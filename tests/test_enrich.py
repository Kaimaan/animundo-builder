"""Enrich place checks: sorting the model's failures from the place's. No key."""

from __future__ import annotations

import json
from pathlib import Path

from builder.enrich import (
    place_tiles,
    run_flatten,
    sort_problems,
    sort_region_problems,
    write_places,
)


def _places(job_dir: Path) -> None:
    regions = job_dir / "regions"
    regions.mkdir(parents=True, exist_ok=True)
    (regions / "tl_z10_0581_0294.json").write_text(
        json.dumps({"id": "tl_z10_0581_0294", "animals": {}}), encoding="utf-8"
    )
    (regions / "tl_z10_0580_0295.json").write_text(
        json.dumps({"id": "tl_z10_0580_0295", "animals": {}}), encoding="utf-8"
    )


def test_place_tiles_maps_ids_and_stems(tmp_path: Path) -> None:
    """#161.2: food-web lines name region ids; both spellings land."""
    _places(tmp_path)
    mapping = place_tiles(tmp_path)
    assert mapping["tl_z10_0581_0294"] == "z10_0581_0294"
    assert mapping["tl_z10_0580_0295"] == "z10_0580_0295"


def test_sort_problems_splits_model_tile_and_place(tmp_path: Path) -> None:
    """#161.2: the new file's failures are the model's; food is the tile's."""
    _places(tmp_path)
    mapping = place_tiles(tmp_path)
    model, tiled, general = sort_problems(
        [
            'ERROR: lynx: needs text "intro"',
            'ERROR: squirrel in tl_z10_0580_0295 eats "mushrooms", '
            "but the region has no such food",
            'ERROR: lynx in tl_z10_0580_0295 hunts "small_mammal", '
            "but no animal there has that tag",
            'ERROR: region tl_z10_0580_0295: playable animal "bee" has no file',
            "ERROR: fox: model file not found: res://assets/animals/fox.glb",
        ],
        "lynx",
        mapping,
    )
    assert model == ['ERROR: lynx: needs text "intro"']
    assert sorted(tiled) == ["z10_0580_0295"]
    assert len(tiled["z10_0580_0295"]) == 3
    assert general == ["ERROR: fox: model file not found: res://assets/animals/fox.glb"]


def test_sort_problems_unknown_place_is_general(tmp_path: Path) -> None:
    """#161.2: a place line for no known tile stays loud, never the model's."""
    _places(tmp_path)
    model, tiled, general = sort_problems(
        ['ERROR: squirrel in tl_z10_1023_1023 eats "mushrooms"'],
        "lynx",
        place_tiles(tmp_path),
    )
    assert model == []
    assert tiled == {}
    assert general == ['ERROR: squirrel in tl_z10_1023_1023 eats "mushrooms"']


def test_sort_region_problems_groups_by_place_file(tmp_path: Path) -> None:
    """#161.2: a failing place file lands its details on its tile."""
    _places(tmp_path)
    tiled, general = sort_region_problems(
        [
            "FAIL regions/tl_z10_0580_0295.json",
            '- "center.lat" must be a number between -90 and 90',
            "FAIL regions/fi_nuuksio.json",
            '- "welcome" must be a non-empty text',
        ],
        place_tiles(tmp_path),
    )
    assert tiled == {
        "z10_0580_0295": ['"center.lat" must be a number between -90 and 90']
    }
    assert general == ['"welcome" must be a non-empty text']


def test_write_places_lists_every_tile(tmp_path: Path) -> None:
    """#161.2: checks/places.json passes quiet tiles, fails loud ones."""
    _places(tmp_path)
    mapping = place_tiles(tmp_path)
    write_places(
        tmp_path,
        mapping,
        {"z10_0580_0295": ["ERROR: squirrel in tl_z10_0580_0295 eats mushrooms"]},
        ["ERROR: fox: something broke"],
        region_note="models not built: run tools/build_model.sh first",
    )
    data = json.loads((tmp_path / "checks" / "places.json").read_text())
    assert data["ok"] is False
    by_check = {entry["check"]: entry for entry in data["results"]}
    assert by_check["place z10_0581_0294"]["status"] == "pass"
    failed = by_check["place z10_0580_0295"]
    assert failed["status"] == "fail"
    assert failed["problems"] == ["ERROR: squirrel in tl_z10_0580_0295 eats mushrooms"]
    assert by_check["place"]["status"] == "fail"
    assert by_check["region"]["status"] == "skip"


def test_reef_biome_and_flattened_home_differ_by_one_entry(
    tmp_path: Path,
) -> None:
    """#184.3: flatten carries the one new biome entry into its homes/ copy."""
    import shutil

    import pytest

    checkout = Path(__file__).resolve().parent.parent.parent / "animundo-world"
    if not (checkout / "tools" / "biomes.py").is_file():
        pytest.skip("needs a world checkout beside the builder")
    world = tmp_path / "world"
    (world / "tools").mkdir(parents=True)
    for tool in ("biomes.py", "layers.py", "food.py"):
        shutil.copy(checkout / "tools" / tool, world / "tools" / tool)
    shutil.copytree(checkout / "food", world / "food")
    (world / "animals").mkdir(parents=True)
    (world / "models" / "trees").mkdir(parents=True)
    (world / "lands").mkdir(parents=True)
    (world / "lands" / "xx_test.json").write_text(json.dumps({"id": "xx_test"}))
    greens = world / "models" / "plants"
    greens.mkdir(parents=True)
    for stem, on in (("stag", ["reef"]), ("table_coral", ["reef"])):
        (greens / f"{stem}.json").write_text(
            json.dumps({"id": stem, "grows": {"on": on, "density": 0.2}})
        )
    biomes = world / "biomes"
    biomes.mkdir(parents=True)
    before = {
        "id": "xx_test_biome",
        "land": "xx_test",
        "terrain": {"size": 512},
        "plants": {"water": [{"id": "stag", "density": 0.3}]},
    }
    (biomes / "xx_test_biome.json").write_text(json.dumps(before))
    (world / "homes").mkdir(parents=True)
    run_flatten(world)
    home_before = json.loads((world / "homes" / "xx_test_biome.json").read_text())
    after = json.loads(json.dumps(before))
    after["plants"]["water"].append({"id": "table_coral", "density": 0.2})
    (biomes / "xx_test_biome.json").write_text(json.dumps(after))
    run_flatten(world)
    home_after = json.loads((world / "homes" / "xx_test_biome.json").read_text())
    assert [p["model"] for p in home_after["ground_cover"]["plants"]] == [
        "stag",
        "table_coral",
    ]
    assert home_after["ground_cover"]["plants"][1]["density"] == 0.2
    assert (
        home_after["ground_cover"]["plants"][0]
        == home_before["ground_cover"]["plants"][0]
    )
    assert {key: home_after[key] for key in home_after if key != "ground_cover"} == {
        key: home_before[key] for key in home_before if key != "ground_cover"
    }


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
