"""Enrich place checks: sorting the model's failures from the place's. No key."""

from __future__ import annotations

import json
from pathlib import Path

from builder.enrich import (
    place_tiles,
    region_additions_only,
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


def _biome_place(world: Path) -> None:
    regions = world / "regions"
    regions.mkdir(parents=True, exist_ok=True)
    (regions / "xx_test_place.json").write_text(
        json.dumps(
            {
                "id": "xx_test_place",
                "name": {"en": "Test Place"},
                "biome": "xx_test_biome",
                "grid": {"grid": "ease2_n25", "tiles": [[413, 478]]},
                "species": {
                    "playable": ["fox", "perch"],
                    "prey": ["vole"],
                    "predators": ["fox"],
                },
                "terrain": {"water_level": 8, "height": 32},
            }
        ),
        encoding="utf-8",
    )
    biomes = world / "biomes"
    biomes.mkdir(parents=True, exist_ok=True)
    (biomes / "xx_test_biome.json").write_text(
        json.dumps(
            {
                "id": "xx_test_biome",
                "fish": [{"kind": "perch", "count": 6}],
                "plants": {"cover": [{"id": "bilberry", "density": 0.2}]},
            }
        ),
        encoding="utf-8",
    )


def test_region_place_seats_a_biome_animal_in_species(tmp_path: Path) -> None:
    """#178.1: on a biome the animal joins species, never animals."""
    import fake_world

    world = fake_world.make_world(tmp_path)
    _biome_place(world)
    (world / "animals" / "marten.json").write_text(
        json.dumps({"playable": True, "tags": ["predator"], "diet": ["berries"]})
    )
    before = json.loads((world / "regions" / "xx_test_place.json").read_text())
    place = region_place("xx_test_place", world, "marten", "animal")
    assert "animals" not in place
    assert "fish" not in place
    assert place["species"]["playable"] == ["fox", "perch", "marten"]
    assert place["species"]["predators"] == ["fox"]
    changed = [key for key in place if place[key] != before.get(key)]
    assert changed == ["species"]


def test_region_place_starts_a_biome_plant_from_its_cover(
    tmp_path: Path,
) -> None:
    """#178.2: without its own list the plant joins the biome's cover."""
    import fake_world

    world = fake_world.make_world(tmp_path)
    _biome_place(world)
    greens = world / "models" / "plants"
    greens.mkdir(parents=True, exist_ok=True)
    (greens / "cloudberry.json").write_text(
        json.dumps({"grows": {"on": ["grassland"], "density": 0.3}})
    )
    place = region_place("xx_test_place", world, "cloudberry", "plant")
    assert [p["id"] for p in place["plants"]["cover"]] == [
        "bilberry",
        "cloudberry",
    ]
    biome = json.loads((world / "biomes" / "xx_test_biome.json").read_text())
    assert [p["id"] for p in biome["plants"]["cover"]] == ["bilberry"]


def test_region_place_keeps_a_biome_place_own_cover(tmp_path: Path) -> None:
    """#178.2: with its own list the plant joins that, not the biome's."""
    import fake_world

    world = fake_world.make_world(tmp_path)
    _biome_place(world)
    region_path = world / "regions" / "xx_test_place.json"
    region = json.loads(region_path.read_text())
    region["plants"] = {"cover": [{"id": "lingonberry", "density": 0.1}]}
    region_path.write_text(json.dumps(region), encoding="utf-8")
    greens = world / "models" / "plants"
    greens.mkdir(parents=True, exist_ok=True)
    (greens / "cloudberry.json").write_text(
        json.dumps({"grows": {"on": ["grassland"], "density": 0.3}})
    )
    place = region_place("xx_test_place", world, "cloudberry", "plant")
    assert [p["id"] for p in place["plants"]["cover"]] == [
        "lingonberry",
        "cloudberry",
    ]


def test_region_place_seats_a_biome_fish_from_its_fish(tmp_path: Path) -> None:
    """#178.3: a fish joins species and the biome's fish with its count."""
    import fake_world

    world = fake_world.make_world(tmp_path)
    _biome_place(world)
    (world / "animals" / "minnow.json").write_text(
        json.dumps(
            {
                "playable": True,
                "tags": ["fish", "small_fish"],
                "diet": ["water_bugs"],
            }
        )
    )
    (world / "animals" / "hucho.json").write_text(
        json.dumps(
            {"playable": True, "tags": ["fish", "big_fish"], "diet": ["small_fish"]}
        )
    )
    small = region_place("xx_test_place", world, "minnow", "animal")
    assert small["species"]["playable"] == ["fox", "perch", "minnow"]
    assert small["fish"] == [
        {"kind": "perch", "count": 6},
        {"kind": "minnow", "count": 5},
    ]
    big = region_place("xx_test_place", world, "hucho", "animal")
    assert big["fish"][-1] == {"kind": "hucho", "count": 2}
    biome = json.loads((world / "biomes" / "xx_test_biome.json").read_text())
    assert biome["fish"] == [{"kind": "perch", "count": 6}]


def test_region_place_seats_a_fish_from_its_list_example(
    tmp_path: Path,
) -> None:
    """#178.3: before its file exists the list's example says fish."""
    import fake_world

    world = fake_world.make_world(tmp_path)
    _biome_place(world)
    (world / "animals" / "perch.json").write_text(
        json.dumps({"playable": True, "tags": ["fish"], "diet": ["water_bugs"]})
    )
    (world / "animals" / "seal.json").write_text(
        json.dumps({"playable": True, "tags": ["mammal"], "diet": ["fish"]})
    )
    (world / "animal-list.json").write_text(
        json.dumps(
            {
                "animals": [
                    {"id": "new_fish", "file_from": "perch"},
                    {"id": "new_whale", "file_from": "seal"},
                ]
            }
        )
    )
    fish = region_place("xx_test_place", world, "new_fish", "animal")
    assert [e["kind"] for e in fish["fish"]] == ["perch", "new_fish"]
    whale = region_place("xx_test_place", world, "new_whale", "animal")
    assert "fish" not in whale
    assert "new_whale" in whale["species"]["playable"]


def test_region_additions_only_weighs_the_biome_base(tmp_path: Path) -> None:
    """#178: species and biome-started fish additions pass; the rest fails."""
    import fake_world

    world = fake_world.make_world(tmp_path)
    _biome_place(world)
    (world / "animals" / "minnow.json").write_text(
        json.dumps({"playable": True, "tags": ["fish"], "diet": ["water_bugs"]})
    )
    before = json.loads((world / "regions" / "xx_test_place.json").read_text())
    assert (
        region_additions_only(
            before,
            region_place("xx_test_place", world, "minnow", "animal"),
            "minnow",
            world,
        )
        == []
    )
    (world / "animals" / "marten.json").write_text(
        json.dumps({"playable": True, "tags": ["predator"], "diet": ["berries"]})
    )
    assert (
        region_additions_only(
            before,
            region_place("xx_test_place", world, "marten", "animal"),
            "marten",
            world,
        )
        == []
    )
    tampered = region_place("xx_test_place", world, "minnow", "animal")
    tampered["fish"] = [{"kind": "minnow", "count": 5}]
    assert region_additions_only(before, tampered, "minnow", world) == [
        "fish changed beyond minnow"
    ]
    renamed = region_place("xx_test_place", world, "marten", "animal")
    renamed["species"]["playable"] = ["marten"]
    assert region_additions_only(before, renamed, "marten", world) == [
        "species.playable changed beyond marten"
    ]


def test_region_place_leaves_the_reef_otherwise_alone() -> None:
    """#173.1: the reef before and after differs only in its lists.

    The reef is on a biome too now (world 68d9571), so the new
    way: species and the biome's fish, nothing else (#178).
    """
    import pytest

    world = ROOT.parent / "animundo-world"
    if not (world / "regions" / "au_great_barrier_reef.json").is_file():
        pytest.skip("needs the world checkout next to the builder")
    before = json.loads((world / "regions" / "au_great_barrier_reef.json").read_text())
    biome = json.loads((world / "biomes" / "coral_reef.json").read_text())
    place = region_place("au_great_barrier_reef", world, "hammerhead", "animal")
    changed = [key for key in place if place[key] != before.get(key)]
    assert changed == ["species", "fish"]
    assert place["species"]["playable"][-1] == "hammerhead"
    assert place["fish"][:-1] == biome["fish"]
    assert place["fish"][-1]["kind"] == "hammerhead"


_RESOLVE = """
import json, sys
sys.path.insert(0, sys.argv[1])
import layers
region = json.load(open(sys.argv[2], encoding="utf-8"))
print(json.dumps(layers.resolve(region)))
"""


def _nuuksio_world(tmp_path: Path) -> Path | None:
    """A scratch world with Nuuksio, its biome and the real tools."""
    import shutil

    real = ROOT.parent / "animundo-world"
    if not (real / "regions" / "fi_nuuksio.json").is_file():
        return None
    world = tmp_path / "world"
    for name in ("animals", "food", "lands", "models/plants", "models/trees"):
        shutil.copytree(real / name, world / name)
    (world / "regions").mkdir(parents=True)
    shutil.copy(real / "regions" / "fi_nuuksio.json", world / "regions")
    (world / "biomes").mkdir(parents=True)
    shutil.copy(real / "biomes" / "boreal_forest.json", world / "biomes")
    (world / "tools").mkdir(parents=True)
    for tool in ("layers.py", "food.py"):
        shutil.copy(real / "tools" / tool, world / "tools")
    (world / "animals" / "xx_newt.json").write_text(
        json.dumps({"playable": True, "tags": ["amphibian"], "diet": ["insects"]}),
        encoding="utf-8",
    )
    (world / "animals" / "xx_minnow.json").write_text(
        json.dumps(
            {
                "playable": True,
                "tags": ["fish", "small_fish"],
                "diet": ["water_bugs"],
            }
        ),
        encoding="utf-8",
    )
    (world / "models" / "plants" / "xx_cloudberry.json").write_text(
        json.dumps({"grows": {"on": ["grassland"], "density": 0.3}}),
        encoding="utf-8",
    )
    return world


def _resolved(world: Path, place: dict) -> dict:
    import subprocess
    import sys
    import tempfile

    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as handle:
        json.dump(place, handle)
        path = handle.name
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _RESOLVE, str(world / "tools"), path],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        Path(path).unlink(missing_ok=True)
    if proc.returncode != 0:
        raise AssertionError(f"layers.resolve refused: {proc.stderr.strip()[:400]}")
    return json.loads(proc.stdout)


def test_nuuksio_enrich_keeps_the_biome_whole(tmp_path: Path) -> None:
    """#178.4: an animal, plant and fish join Nuuksio; the biome stays."""
    import pytest

    world = _nuuksio_world(tmp_path)
    if world is None:
        pytest.skip("needs the world checkout next to the builder")
    pristine = json.loads((world / "regions" / "fi_nuuksio.json").read_text())
    before = _resolved(world, pristine)
    before_fish = list(before["fish"])
    before_plant_ids = {
        p.get("model", p.get("id"))
        for p in before["ground_cover"]["plants"]
        if isinstance(p, dict)
    }
    before_playable = list(before["animals"]["playable"])

    animal = region_place("fi_nuuksio", world, "xx_newt", "animal")
    assert "animals" not in animal
    resolved = _resolved(world, animal)
    assert "xx_newt" in resolved["animals"]["playable"]
    assert set(before_playable) <= set(resolved["animals"]["playable"])
    assert resolved["fish"] == before_fish

    plant = region_place("fi_nuuksio", world, "xx_cloudberry", "plant")
    resolved = _resolved(world, plant)
    plant_ids = {
        p.get("model", p.get("id"))
        for p in resolved["ground_cover"]["plants"]
        if isinstance(p, dict)
    }
    assert "xx_cloudberry" in plant_ids
    assert before_plant_ids <= plant_ids

    fish = region_place("fi_nuuksio", world, "xx_minnow", "animal")
    resolved = _resolved(world, fish)
    assert "xx_minnow" in resolved["animals"]["playable"]
    assert resolved["fish"] == [
        *before_fish,
        {"kind": "xx_minnow", "count": 5},
    ]


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


def _reef_lists() -> dict:
    return {
        "food": [{"kind": "water_plants", "names": ["seagrass"]}],
        "fish": [{"kind": "clownfish", "count": 5}],
        "animals": {"playable": ["clownfish"], "prey": [], "predators": []},
    }


def test_region_additions_only_passes_a_clean_add() -> None:
    """#174.1: seating the animal is the only change allowed."""
    before = _reef_lists()
    after = json.loads(json.dumps(before))
    after["fish"].append({"kind": "hammerhead", "count": 2})
    after["animals"]["playable"].append("hammerhead")
    assert region_additions_only(before, after, "hammerhead") == []


def test_region_additions_only_names_every_other_change() -> None:
    before = _reef_lists()
    rewritten = json.loads(json.dumps(before))
    rewritten["food"] = []
    assert region_additions_only(before, rewritten, "hammerhead") == ["food changed"]
    removed = json.loads(json.dumps(before))
    del removed["animals"]["playable"][0]
    assert region_additions_only(before, removed, "hammerhead") == [
        "animals.playable changed beyond hammerhead"
    ]
    stranger = json.loads(json.dumps(before))
    stranger["fish"].append({"kind": "shark", "count": 1})
    assert region_additions_only(before, stranger, "hammerhead") == [
        "fish changed beyond hammerhead"
    ]
    extra = json.loads(json.dumps(before))
    extra["insects"] = ["ant"]
    assert region_additions_only(before, extra, "hammerhead") == ["insects was added"]
    gone = json.loads(json.dumps(before))
    del gone["food"]
    assert region_additions_only(before, gone, "hammerhead") == ["food was removed"]
