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
    near_made_or_noted,
    open_sea_entries,
    pick_species,
    tile_edges,
    tile_facts,
    tile_food_kinds,
    tile_gaps_for,
    tile_height,
    tile_seed,
    world_eats,
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


def _habitat_grid(tmp_path: Path, cells: dict[str, list[Any]]) -> HabitatGrid:
    path = tmp_path / "z10.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump({"grid": "z10", "cells": cells}, handle)
    return HabitatGrid.load(path)


def _home(world: Path, home_id: str = "boreal_forest") -> dict[str, Any]:
    return json.loads((world / "homes" / f"{home_id}.json").read_text())


_RULES = {
    "large_animals": ["large_mammal"],
    "kinds": {
        "berries": {"from": "plants", "weight": 30},
        "mushrooms": {"from": "plants"},
        "browse": {"from": "plants", "weight": 12},
        "seeds": {"from": "plants", "weight": 12},
        "water_plants": {"from": "plants", "or": "lake_life", "weight": 8},
        "grass": {"from": "grass", "amount": 10},
        "bark": {"from": "bark", "amount": 15},
        "insects": {"from": "small_life", "when": "land", "weight": 15},
        "water_bugs": {"from": "small_life", "when": "fresh_water", "weight": 15},
        "bottom_bugs": {"from": "small_life", "when": "water", "weight": 8},
        "carrion": {
            "from": "large_animals",
            "or": "small_life",
            "when": "land",
            "weight": 4,
        },
    },
}


def _layered(world: Path) -> dict[str, Any]:
    """A layered home: plants and trees with grows, plus a decoy food list."""
    (world / "food").mkdir(exist_ok=True)
    (world / "food" / "rules.json").write_text(json.dumps(_RULES))
    (world / "models" / "plants").mkdir(parents=True, exist_ok=True)
    (world / "models" / "plants" / "bilberry.json").write_text(
        json.dumps({"id": "bilberry", "grows": {"food": {"kind": "berries"}}})
    )
    (world / "models" / "plants" / "porcini.json").write_text(
        json.dumps({"id": "porcini", "grows": {"food": {"kind": "mushrooms"}}})
    )
    (world / "models" / "trees").mkdir(parents=True, exist_ok=True)
    (world / "models" / "trees" / "oak.json").write_text(
        json.dumps(
            {
                "id": "oak",
                "grows": {
                    "food": {"kind": "seeds"},
                    "more_food": {"browse": ["oak buds"]},
                    "bark": True,
                },
            }
        )
    )
    return {
        "habitat": "boreal_forest",
        "ecoregions": [717],
        "food": [{"kind": "honey", "names": ["honey"]}],
        "ground_cover": {
            "grass": 0.5,
            "plants": [{"model": "bilberry"}, {"model": "porcini"}],
        },
        "trees": {"density": 0.03, "mix": [{"plant": "oak", "share": 1.0}]},
        "lakes": [{"name": "Bog pond"}],
        "lake_life": {"pondweed": 0.5},
    }


def test_tile_food_kinds_works_out_food_from_rules(tmp_path: Path) -> None:
    """#147.4: food comes from grows, land and water, never the list."""
    world = _world(tmp_path)
    home = _layered(world)
    assert tile_food_kinds(world, home) == {
        "berries",
        "seeds",
        "browse",
        "bark",
        "grass",
        "water_plants",
        "insects",
        "water_bugs",
        "bottom_bugs",
        "carrion",
        "mushrooms",
    }


def test_tile_food_kinds_counts_grown_mushrooms(tmp_path: Path) -> None:
    """#152: mushrooms count from grows, as the world's derive does."""
    world = _world(tmp_path)
    home = _layered(world)
    assert "mushrooms" in tile_food_kinds(world, home)
    home["ground_cover"]["plants"].append({"model": "morel", "food": "mushrooms"})
    assert "mushrooms" in tile_food_kinds(world, home)


def test_tile_food_kinds_falls_back_without_rules(tmp_path: Path) -> None:
    """#147.4: a world without rules reads the written list, as before."""
    world = _world(tmp_path)
    home = _home(world)
    from builder.tiles import home_food_kinds

    assert (
        tile_food_kinds(world, home)
        == home_food_kinds(home)
        == {
            "grass",
            "berries",
        }
    )


def test_pick_species_keeps_every_filed_survey_species(tmp_path: Path) -> None:
    """#241.1: food never removes an animal; only diet-less stays out."""
    world = _world(tmp_path)
    home = _layered(world)
    playable, prey, predators = pick_species(
        {"fox": 5.0, "mountain_hare": 9.0, "vole": 20.0}, world, home
    )
    assert playable == ["mountain_hare", "fox"]
    assert predators == ["fox"]
    assert prey == ["vole", "mountain_hare"]
    bare = dict(home, ground_cover={"grass": 0.0, "plants": []})
    assert pick_species({"fox": 5.0}, world, bare) == (["fox"], [], ["fox"])
    (world / "animals" / "blob.json").write_text(json.dumps({"playable": True}))
    assert pick_species({"blob": 5.0}, world, home) == ([], [], [])


def test_pick_species_orders_most_present_first(tmp_path: Path) -> None:
    """#241.1: kept animals rank by presence, not food."""
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


def test_pick_species_keeps_hunter_without_prey(tmp_path: Path) -> None:
    """#241.1: a hunter with no prey here stays, with gaps, not out."""
    world = _world(tmp_path)
    assert pick_species({"fox": 5.0}, world, _home(world)) == (["fox"], [], ["fox"])


def test_pick_species_keeps_animal_without_food(tmp_path: Path) -> None:
    """#241.1: an animal whose food is missing here stays."""
    world = _world(tmp_path)
    assert pick_species({"mountain_hare": 9.0}, world, {"food": []}) == (
        ["mountain_hare"],
        [],
        [],
    )


def test_pick_species_home_fish_join_the_pool_unfed(tmp_path: Path) -> None:
    """#241.1: home fish join the prey pool whatever they find."""
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
    assert pick_species({"pike": 9.0, "frog": 5.0}, world, home) == (
        ["frog"],
        ["frog"],
        ["pike"],
    )
    hungry = {"food": [], "fish": [{"kind": "pike", "count": 2}]}
    assert pick_species({"pike": 9.0, "frog": 5.0}, world, hungry) == (
        ["frog"],
        ["frog"],
        ["pike"],
    )
    assert pick_species({"pike": 9.0}, world, home) == ([], [], [])


def test_tile_seed_is_stable() -> None:
    assert tile_seed("z10_0581_0294") == tile_seed("z10_0581_0294")
    assert 0 <= tile_seed("z10_0581_0294") < 2**31
    assert tile_seed("z10_0581_0294") != tile_seed("z10_0580_0295")


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


def test_home_for_cell_refuses_the_generic_sea_in_the_tropics(
    tmp_path: Path,
) -> None:
    """#169.1: a tropical sea cell never builds from the cold sea home."""
    homes = available_homes(_world(tmp_path))
    assert home_for_cell(150, "sea", homes, 1) is None
    assert home_for_cell(150, "open_ocean", homes, 7) is None
    assert home_for_cell(999, "sea", homes, 6) == "sea"
    assert home_for_cell(999, "sea", homes, 4) == "sea"


def test_coast_habitat_flips_to_the_sea_side() -> None:
    """#198.2: an unbuildable land coast builds as sea; else unchanged."""
    from builder.tiles import coast_habitat

    homes = {
        "rainforest": {"habitat": "rainforest", "ecoregions": [483]},
        "coral_reef": {"habitat": "sea", "ecoregions": [150]},
        "sea": {"habitat": "sea", "ecoregions": [717]},
    }
    assert coast_habitat(150, "rainforest", 0.6, homes, 1) == "sea"
    assert coast_habitat(150, "rainforest", 1.0, homes, 1) == "rainforest"
    assert coast_habitat(150, "sea", 0.6, homes, 1) == "sea"
    assert coast_habitat(999, "rainforest", 0.6, homes, 1) == "rainforest"
    assert coast_habitat(679, "forest", 0.76, homes, 4) == "forest"
    lush = {**homes, "coast_forest": {"habitat": "rainforest", "ecoregions": [150]}}
    assert coast_habitat(150, "rainforest", 0.6, lush, 1) == "rainforest"


def test_default_bundle_keeps_a_listed_ecoregion(tmp_path: Path) -> None:
    """#198.2: a bundle must list a listed ecoregion; new land as before."""
    from builder.tiles import available_biomes, default_bundle

    world = _world(tmp_path)
    biomes = world / "biomes"
    biomes.mkdir()
    (biomes / "rainforest.json").write_text(
        json.dumps(
            {
                "habitat": "rainforest",
                "ecoregions": [483],
                "land": "tropical_moist",
            }
        )
    )
    (biomes / "coral_reef.json").write_text(
        json.dumps({"habitat": "sea", "ecoregions": [150], "land": "reef"})
    )
    filed = available_biomes(world)
    grid = _habitat_grid(tmp_path, _boreal_3x3(581, 294))
    assert (
        default_bundle(
            "z10_0581_0294", grid, filed, "rainforest", "tropical_moist", 150
        )
        is None
    )
    assert (
        default_bundle(
            "z10_0581_0294", grid, filed, "rainforest", "tropical_moist", 999
        )
        == "rainforest"
    )
    assert (
        default_bundle("z10_0581_0294", grid, filed, "rainforest", "tropical_moist")
        == "rainforest"
    )


def test_make_tile_builds_a_marine_coast_from_its_sea_home(
    tmp_path: Path,
) -> None:
    """#198.2: z10_0927_0563's shape builds as sea from coral_reef."""
    world = _world(tmp_path)
    (world / "homes" / "rainforest.json").write_text(
        json.dumps(
            {
                "habitat": "rainforest",
                "ecoregions": [483],
                "food": [{"kind": "grass"}, {"kind": "berries"}],
            }
        )
    )
    (world / "homes" / "coral_reef.json").write_text(
        json.dumps(
            {
                "habitat": "sea",
                "ecoregions": [150],
                "food": [{"kind": "grass"}, {"kind": "berries"}],
            }
        )
    )
    listed = json.loads((world / "animal-list.json").read_text())
    for entry in listed["animals"]:
        entry["habitats"].append("rainforest")
    (world / "animal-list.json").write_text(json.dumps(listed))
    cell = [150, 1.0, None, 0.0, 1, 0.6, 0.0, 53.0]
    cells = {f"{c},{r}": list(cell) for r in (293, 294, 295) for c in (580, 581, 582)}
    grid = _habitat_grid(tmp_path, cells)
    made, blocked = make_tile(
        "z10_0581_0294",
        grid,
        world,
        builder_root=tmp_path,
        species={
            "place": "Tropical rainforest coast with coral reef",
            "parts": [{"kind": "forest", "species": ["fox", "mountain_hare"]}],
            "plants": [],
            "trees": [],
            "outside_animals": [],
            "outside_plants": [],
            "name_en": "Rainforest coast",
            "name_fi": "Sademetsän rannikko",
        },
    )
    assert blocked is None
    assert made is not None
    assert made["home"] == "coral_reef"
    assert made["habitat"] == "sea"


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


def test_remake_after_ship_adds_the_animal_with_no_new_call(
    tmp_path: Path,
) -> None:
    """#143.2: the cached survey survives `in_library`; the fed animal joins."""
    import json
    from types import SimpleNamespace

    from builder.species import Source as SpeciesSource

    world = _world(tmp_path)
    listed = json.loads((world / "animal-list.json").read_text())
    names = {
        "fox": ("Red fox", "Vulpes vulpes"),
        "mountain_hare": ("Mountain hare", "Lepus timidus"),
        "vole": ("Vole", "Microtus agrestis"),
    }
    for entry in listed["animals"]:
        entry["name"], entry["latin"] = names[entry["id"]]
    listed["animals"].append(
        {
            "id": "lion",
            "name": "Lion",
            "latin": "Panthera leo",
            "gbif_key": 5219404,
            "habitats": ["boreal_forest"],
        }
    )
    (world / "animal-list.json").write_text(json.dumps(listed))
    habitat_grid = _habitat_grid(tmp_path, _boreal_3x3(581, 294))
    answer = {
        "place": "Boreal forest in southern Finland",
        "parts": [
            {"kind": "forest", "species": ["fox", "mountain_hare", "vole", "lion"]}
        ],
        "plants": [],
        "trees": [],
        "outside_animals": [],
        "outside_plants": [],
        "name_en": "Fox and lion woods",
        "name_fi": "Kettujen ja leijonien metsä",
    }

    class _Stub:
        calls = 0

        def chat(self, **kwargs: object) -> object:
            _Stub.calls += 1
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content=json.dumps(answer)))
                ]
            )

    cache = tmp_path / "cache"
    made, blocked = make_tile(
        "z10_0581_0294",
        habitat_grid,
        world,
        builder_root=tmp_path,
        species_source=SpeciesSource(client=_Stub(), cache_dir=cache),
    )
    assert blocked is None
    assert made is not None
    assert "lion" not in made["species"]["playable"]
    assert made["wanted"] == [{"kind": "animal", "what": "lion", "part": "forest"}]
    assert _Stub.calls == 1
    (world / "animals" / "lion.json").write_text(
        json.dumps({"playable": True, "tags": ["big_cat"], "diet": ["grass"]})
    )
    listed["animals"][-1]["in_library"] = "lion"
    (world / "animal-list.json").write_text(json.dumps(listed))
    again, blocked = make_tile(
        "z10_0581_0294",
        habitat_grid,
        world,
        builder_root=tmp_path,
        species_source=SpeciesSource(client=None, cache_dir=cache),
    )
    assert blocked is None
    assert again is not None
    assert "lion" in again["species"]["playable"]
    assert "lion" in again["requires"]["animals"]
    assert "wanted" not in again
    assert _Stub.calls == 1


def test_remake_moves_the_13th_into_wanted_without_a_call(tmp_path: Path) -> None:
    """#196.2: a cached answer with 15 missing; one gains a file."""
    from types import SimpleNamespace

    from builder.species import Source as SpeciesSource

    world = _world(tmp_path)
    listed = json.loads((world / "animal-list.json").read_text())
    names = {
        "fox": ("Red fox", "Vulpes vulpes"),
        "mountain_hare": ("Mountain hare", "Lepus timidus"),
        "vole": ("Vole", "Microtus agrestis"),
    }
    for entry in listed["animals"]:
        entry["name"], entry["latin"] = names[entry["id"]]
    beasts = [f"beast_{n}" for n in range(15)]
    for n, beast in enumerate(beasts):
        listed["animals"].append(
            {
                "id": beast,
                "name": f"Beast {n}",
                "latin": f"Beastus numerus{n}",
                "gbif_key": 1000 + n,
                "habitats": ["boreal_forest"],
            }
        )
    (world / "animal-list.json").write_text(json.dumps(listed))
    habitat_grid = _habitat_grid(tmp_path, _boreal_3x3(581, 294))
    answer = {
        "place": "Boreal forest in southern Finland",
        "parts": [{"kind": "forest", "species": ["fox", "mountain_hare", *beasts]}],
        "plants": [],
        "trees": [],
        "outside_animals": [],
        "outside_plants": [],
        "name_en": "Beast woods",
        "name_fi": "Petojen metsä",
    }

    class _Stub:
        calls = 0

        def chat(self, **kwargs: object) -> object:
            _Stub.calls += 1
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content=json.dumps(answer)))
                ]
            )

    cache = tmp_path / "cache"
    made, blocked = make_tile(
        "z10_0581_0294",
        habitat_grid,
        world,
        builder_root=tmp_path,
        species_source=SpeciesSource(client=_Stub(), cache_dir=cache),
    )
    assert blocked is None
    assert made is not None
    assert [w["what"] for w in made["wanted"]] == beasts[:12]
    assert _Stub.calls == 1
    (world / "animals" / "beast_0.json").write_text(
        json.dumps({"playable": True, "tags": ["beast"], "diet": ["grass"]})
    )
    again, blocked = make_tile(
        "z10_0581_0294",
        habitat_grid,
        world,
        builder_root=tmp_path,
        species_source=SpeciesSource(client=None, cache_dir=cache),
    )
    assert blocked is None
    assert again is not None
    assert [w["what"] for w in again["wanted"]] == beasts[1:13]
    assert _Stub.calls == 1


def test_replayed_unknown_plant_wants_a_gbif_plant(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#196.3: a replayed nomination resolves once and sticks in wanted."""
    from types import SimpleNamespace

    import builder.species as species_mod
    from builder.species import Source as SpeciesSource

    world = _world(tmp_path)
    listed = json.loads((world / "animal-list.json").read_text())
    names = {
        "fox": ("Red fox", "Vulpes vulpes"),
        "mountain_hare": ("Mountain hare", "Lepus timidus"),
        "vole": ("Vole", "Microtus agrestis"),
    }
    for entry in listed["animals"]:
        entry["name"], entry["latin"] = names[entry["id"]]
    (world / "animal-list.json").write_text(json.dumps(listed))
    habitat_grid = _habitat_grid(tmp_path, _boreal_3x3(581, 294))
    answer = {
        "place": "Boreal forest in southern Finland",
        "parts": [{"kind": "forest", "species": ["fox", "mountain_hare"]}],
        "plants": [],
        "trees": [],
        "outside_animals": [],
        "outside_plants": [],
        "name_en": "Fox and hare woods",
        "name_fi": "Kettujen ja jänisten metsä",
    }

    class _Stub:
        calls = 0

        def chat(self, **kwargs: object) -> object:
            _Stub.calls += 1
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content=json.dumps(answer)))
                ]
            )

    cache = tmp_path / "cache"
    made, blocked = make_tile(
        "z10_0581_0294",
        habitat_grid,
        world,
        builder_root=tmp_path,
        species_source=SpeciesSource(client=_Stub(), cache_dir=cache),
    )
    assert blocked is None
    assert made is not None
    assert "wanted" not in made
    cached = json.loads((cache / "z10_0581_0294.json").read_text())
    cached["outside_plants"] = ["Rubus chamaemorus"]
    (cache / "z10_0581_0294.json").write_text(json.dumps(cached))

    matched: list[str] = []

    def fake_match(name: str) -> int | None:
        matched.append(name)
        return 2886150

    monkeypatch.setattr(species_mod, "match_key", fake_match)
    monkeypatch.setattr(species_mod, "has_records_near", lambda key, tile, span=2: True)
    again, blocked = make_tile(
        "z10_0581_0294",
        habitat_grid,
        world,
        builder_root=tmp_path,
        species_source=SpeciesSource(client=None, cache_dir=cache),
    )
    assert blocked is None
    assert again is not None
    assert again["wanted"] == [
        {"kind": "plant", "what": "gbif:2886150", "part": "wild"}
    ]
    assert _Stub.calls == 1
    assert matched == ["Rubus chamaemorus"]
    written = json.loads((cache / "z10_0581_0294.json").read_text())
    assert written["outside_keys"] == {"Rubus chamaemorus": 2886150}
    third, blocked = make_tile(
        "z10_0581_0294",
        habitat_grid,
        world,
        builder_root=tmp_path,
        species_source=SpeciesSource(client=None, cache_dir=cache),
    )
    assert blocked is None
    assert third is not None
    assert third["wanted"] == again["wanted"]
    assert matched == ["Rubus chamaemorus"]


def test_replayed_made_up_name_wants_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    """#196.3: a nomination with no GBIF match drops out quietly."""
    from types import SimpleNamespace

    import builder.species as species_mod
    from builder.species import Source as SpeciesSource

    world = _world(tmp_path)
    listed = json.loads((world / "animal-list.json").read_text())
    names = {
        "fox": ("Red fox", "Vulpes vulpes"),
        "mountain_hare": ("Mountain hare", "Lepus timidus"),
        "vole": ("Vole", "Microtus agrestis"),
    }
    for entry in listed["animals"]:
        entry["name"], entry["latin"] = names[entry["id"]]
    (world / "animal-list.json").write_text(json.dumps(listed))
    habitat_grid = _habitat_grid(tmp_path, _boreal_3x3(581, 294))
    answer = {
        "place": "Boreal forest in southern Finland",
        "parts": [{"kind": "forest", "species": ["fox", "mountain_hare"]}],
        "plants": [],
        "trees": [],
        "name_en": "Fox and hare woods",
        "name_fi": "Kettujen ja jänisten metsä",
        "outside_animals": ["Fictus fictionalis"],
        "outside_plants": [],
    }

    class _Stub:
        calls = 0

        def chat(self, **kwargs: object) -> object:
            _Stub.calls += 1
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content=json.dumps(answer)))
                ]
            )

    monkeypatch.setattr(species_mod, "match_key", lambda name: None)
    made, blocked = make_tile(
        "z10_0581_0294",
        habitat_grid,
        world,
        builder_root=tmp_path,
        species_source=SpeciesSource(client=_Stub(), cache_dir=tmp_path / "cache"),
    )
    assert blocked is None
    assert made is not None
    assert "wanted" not in made


def test_wanted_lists_drops_in_survey_order_capped_at_twelve(
    tmp_path: Path,
) -> None:
    """#143.1: unfiled habitat animals in survey order, at most 12."""
    from builder.tiles import wanted_drops

    world = _world(tmp_path)
    answer = {
        "parts": [
            {"kind": "forest", "species": ["fox", *[f"beast_{n}" for n in range(13)]]},
            {"kind": "lake", "species": ["vole"]},
        ],
        "present": ["fox", *[f"beast_{n}" for n in range(13)], "vole"],
        "plants": [],
        "trees": [],
    }
    candidates = {"fox", "vole", *[f"beast_{n}" for n in range(13)]}
    drops = wanted_drops(answer, candidates, world)
    assert len(drops) == 12
    assert [d["what"] for d in drops] == [f"beast_{n}" for n in range(12)]
    assert all(d["kind"] == "animal" and d["part"] == "forest" for d in drops)
    drops = wanted_drops(answer, {"fox", "vole"}, world)
    assert drops == []


def test_picked_plants_land_on_the_tile(tmp_path: Path) -> None:
    """#147.3: the survey's picks become the tile's cover and water plants."""
    import json

    from builder.tiles import tile_plants

    world = _world(tmp_path)
    greens = world / "models" / "plants"
    greens.mkdir(parents=True)
    (greens / "bilberry.json").write_text(
        json.dumps({"grows": {"on": ["grassland"], "density": 0.5}})
    )
    (greens / "bladderwrack.json").write_text(
        json.dumps({"grows": {"on": ["seabed"], "density": 0.4}})
    )
    answer = {
        "plants": [
            {"id": "bilberry", "count": 6},
            {"id": "bladderwrack", "count": 3},
        ],
        "trees": [{"id": "pine", "count": 5, "share": 1.0}],
    }
    plants, requires, tree_ids = tile_plants(world, answer)
    assert plants is not None
    assert [c["id"] for c in plants["cover"]] == ["bilberry"]
    assert [c["id"] for c in plants["water"]] == ["bladderwrack"]
    assert plants["trees"] == [{"id": "pine", "share": 1.0}]
    assert requires == ["bilberry", "bladderwrack"]
    assert tree_ids == ["pine"]


def test_picked_reef_plants_land_in_water(tmp_path: Path) -> None:
    """#184.1: a survey pick whose spec grows reef lands in water."""
    import json

    from builder.tiles import tile_plants

    world = _world(tmp_path)
    greens = world / "models" / "plants"
    greens.mkdir(parents=True)
    (greens / "table_coral.json").write_text(
        json.dumps({"grows": {"on": ["reef"], "density": 0.2}})
    )
    answer = {"plants": [{"id": "table_coral", "count": 4}], "trees": []}
    plants, requires, _tree_ids = tile_plants(world, answer)
    assert plants is not None
    assert [c["id"] for c in plants["water"]] == ["table_coral"]
    assert "cover" not in plants
    assert requires == ["table_coral"]


def test_ground_keep_treats_reef_as_water() -> None:
    """#184.1: a reef home plant keeps only where the tile lists no water."""
    from builder.tiles import _tile_ground_plants

    ground = {
        "plants": [
            {"model": "table_coral", "on": ["reef"]},
            {"model": "dugong_grass", "on": ["seabed"]},
            {"model": "beach_naupaka", "on": ["sand"]},
        ]
    }
    kept = _tile_ground_plants(ground, {"water": []})
    assert [p["model"] for p in kept] == ["beach_naupaka"]
    kept = _tile_ground_plants(ground, {"cover": []})
    assert [p["model"] for p in kept] == ["table_coral", "dugong_grass"]


def test_wanted_skips_filed_and_unfed_filed_animals(tmp_path: Path) -> None:
    """#143.1: rebuilding a hungry animal helps nothing."""
    import json

    from builder.tiles import wanted_drops

    world = _world(tmp_path)
    (world / "animals" / "bear.json").write_text(
        json.dumps({"playable": True, "tags": ["predator"], "diet": ["honey"]})
    )
    answer = {
        "parts": [{"kind": "forest", "species": ["fox", "bear"]}],
        "present": ["fox", "bear"],
        "plants": [],
        "trees": [],
    }
    assert wanted_drops(answer, {"fox", "bear"}, world) == []


def test_blocked_no_animal_carries_the_opener(tmp_path: Path) -> None:
    """#143.1: a no_animal tile still wants the animal that would open it."""
    import json

    world = _world(tmp_path)
    listed = json.loads((world / "animal-list.json").read_text())
    listed["animals"].append(
        {
            "id": "lion",
            "name": "Lion",
            "latin": "Panthera leo",
            "gbif_key": 5219404,
            "habitats": ["boreal_forest"],
        }
    )
    (world / "animal-list.json").write_text(json.dumps(listed))
    habitat_grid = _habitat_grid(tmp_path, _boreal_3x3(581, 294))
    made, blocked = make_tile(
        "z10_0581_0294",
        habitat_grid,
        world,
        builder_root=tmp_path,
        species={
            "place": "Boreal forest in southern Finland",
            "parts": [{"kind": "forest", "species": ["lion"]}],
            "plants": [],
            "trees": [],
            "outside_animals": [],
            "outside_plants": [],
            "name_en": "Lion woods",
            "name_fi": "Leijonametsä",
        },
    )
    assert made is None
    assert blocked == {
        "reason": "no_animal",
        "habitat": "boreal_forest",
        "wanted": [{"kind": "animal", "what": "lion", "part": "forest"}],
    }
    path = write_group(tmp_path / "groups", "z10_0581_0294", None, blocked)
    data = json.loads(path.read_text())
    assert data["blocked"]["z10_0581_0294"]["wanted"][0]["what"] == "lion"


def _region(world: Path, region_id: str, cells: list[list[int]]) -> None:
    folder = world / "regions"
    folder.mkdir(exist_ok=True)
    (folder / f"{region_id}.json").write_text(
        json.dumps({"id": region_id, "grid": {"grid": "z10", "tiles": cells}})
    )


def test_make_tile_ignores_region_grids(tmp_path: Path) -> None:
    """#202.3: a region grid no longer makes a place stub; ordinary code."""
    world = _world(tmp_path)
    _region(world, "fi_test", [[581, 294], [582, 294], [581, 295], [582, 295]])
    habitat_grid = _habitat_grid(tmp_path, _boreal_3x3(581, 294))
    made, blocked = make_tile(
        "z10_0581_0294",
        habitat_grid,
        world,
        presence={"fox": 5.0, "mountain_hare": 9.0, "vole": 20.0},
        builder_root=tmp_path,
    )
    assert blocked is None
    assert made is not None
    validate("tile", made)
    assert made["kind"] == "code"
    assert made["home"] == "boreal_forest"
    assert "place" not in made


def _baked_grid() -> HabitatGrid:
    path = (
        Path(__file__).resolve().parent.parent / "data" / "habitat-grid" / "z10.json.gz"
    )
    if not path.is_file():
        pytest.skip("needs the baked habitat grid")
    return HabitatGrid.load(path)


def test_make_tile_returns_a_code_entry_in_a_region_cell(tmp_path: Path) -> None:
    """#202.3: z10_0581_0295 is ordinary code, edges agreeing east."""
    world = _world(tmp_path)
    _region(world, "fi_nuuksio", [[580, 294], [581, 294], [580, 295], [581, 295]])
    habitat_grid = _baked_grid()
    made, blocked = make_tile(
        "z10_0581_0295",
        habitat_grid,
        world,
        presence={"fox": 5.0, "mountain_hare": 9.0, "vole": 20.0},
        builder_root=tmp_path,
    )
    assert blocked is None
    assert made is not None
    validate("tile", made)
    assert made["kind"] == "code"
    assert made["home"] == "boreal_forest"
    east = tile_edges(
        "z10_0582_0295",
        habitat_grid,
    )["w"]
    assert made["edges"]["e"] == east


def test_make_tile_validates(tmp_path: Path) -> None:
    world = _world(tmp_path)
    habitat_grid = _habitat_grid(tmp_path, _boreal_3x3(581, 294))
    made, blocked = make_tile(
        "z10_0581_0294",
        habitat_grid,
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
    assert made["biome"] == "boreal_forest"
    assert made["requires"]["biomes"] == made["requires"]["homes"]
    assert made["level"] == cell_level("boreal_forest", 80.0)
    assert made["height"] == 32
    assert "blend" not in made
    assert made["species"]["playable"]
    assert made["seed"] == tile_seed("z10_0581_0294")
    for side in ("n", "e", "s", "w"):
        assert len(made["edges"][side]) == 65


def _real_world() -> Path | None:
    world = Path(__file__).resolve().parent.parent.parent / "animundo-world"
    grid = Path(__file__).resolve().parent.parent / "data" / "habitat-grid"
    if not (world / "animals" / "fox.json").is_file():
        return None
    if not (grid / "z10.json.gz").is_file():
        return None
    return world


def test_nuuksio_tile_keeps_hunters_playable(tmp_path: Path) -> None:
    """Spec 04 roles by Nuuksio: every kept playable file plays.

    z10_0581_0294 itself is a place cell now (#147.1); the roles
    are tried on the forest cell west of it.
    """
    world = _real_world()
    if world is None:
        pytest.skip("needs the committed grid and a real world checkout")
    grid = Path(__file__).resolve().parent.parent / "data" / "habitat-grid"
    habitat_grid = HabitatGrid.load(grid / "z10.json.gz")
    made, blocked = make_tile(
        "z10_0579_0293",
        habitat_grid,
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
    habitat_grid = _habitat_grid(
        tmp_path, {"587,231": [999, 1.0, None, 0.0, 11, 1.0, 0.0, 200.0]}
    )
    made, blocked = make_tile(
        "z10_0587_0231",
        habitat_grid,
        world,
        presence={"fox": 5.0},  # type: ignore[arg-type]
        builder_root=tmp_path,
    )
    assert made is None
    assert blocked == {"reason": "no_home", "habitat": "tundra"}


def test_make_tile_blocks_without_animals(tmp_path: Path) -> None:
    world = _world(tmp_path)
    habitat_grid = _habitat_grid(tmp_path, _boreal_3x3(581, 294))
    made, blocked = make_tile(
        "z10_0581_0294",
        habitat_grid,
        world,
        presence={},  # type: ignore[arg-type]
        builder_root=tmp_path,
    )
    assert made is None
    assert blocked == {"reason": "no_animal", "habitat": "boreal_forest"}


def test_survey_needs_call_without_a_cached_survey(tmp_path: Path) -> None:
    """#243.1: no cached survey means the remake prices a call."""
    from builder.tiles import survey_needs_call

    world = _world(tmp_path)
    habitat_grid = _habitat_grid(tmp_path, _boreal_3x3(581, 294))
    cache = tmp_path / "species"
    cache.mkdir()
    assert (
        survey_needs_call("z10_0581_0294", habitat_grid, world, cache, tmp_path) is True
    )


def test_survey_needs_no_call_for_excluded_cells(tmp_path: Path) -> None:
    """#243.1: a tile that stops before species work prices nothing."""
    import json

    from builder.tiles import survey_needs_call

    world = _world(tmp_path)
    habitat_grid = _habitat_grid(tmp_path, _boreal_3x3(581, 294))
    (tmp_path / "exclusions.json").write_text(json.dumps({"tiles": ["z10_0581_0294"]}))
    cache = tmp_path / "species"
    cache.mkdir()
    assert (
        survey_needs_call("z10_0581_0294", habitat_grid, world, cache, tmp_path)
        is False
    )


def test_make_tile_blocks_excluded_cells(tmp_path: Path) -> None:
    """#133.7: a cell in exclusions.json blocks with `excluded`."""
    import json

    world = _world(tmp_path)
    habitat_grid = _habitat_grid(tmp_path, _boreal_3x3(581, 294))
    (tmp_path / "exclusions.json").write_text(json.dumps({"tiles": ["z10_0581_0294"]}))
    made, blocked = make_tile(
        "z10_0581_0294",
        habitat_grid,
        world,
        presence={"fox": 5.0, "mountain_hare": 9.0, "vole": 20.0},
        builder_root=tmp_path,  # type: ignore[arg-type]
    )
    assert made is None
    assert blocked == {"reason": "excluded", "habitat": "boreal_forest"}


def test_edges_agree_at_corners(tmp_path: Path) -> None:
    habitat_grid = _habitat_grid(tmp_path, _boreal_3x3(581, 294))
    edges = tile_edges("z10_0581_0294", habitat_grid)
    assert edges["n"][0] == edges["w"][64]
    assert edges["n"][64] == edges["e"][64]
    assert edges["s"][0] == edges["w"][0]
    assert edges["s"][64] == edges["e"][0]


def test_edges_agree_with_neighbours(tmp_path: Path) -> None:
    cells = _boreal_3x3(581, 294) | _boreal_3x3(582, 294) | _boreal_3x3(581, 293)
    habitat_grid = _habitat_grid(tmp_path, cells)
    mine = tile_edges("z10_0581_0294", habitat_grid)
    east = tile_edges("z10_0582_0294", habitat_grid)
    north = tile_edges("z10_0581_0293", habitat_grid)
    assert mine["e"] == east["w"]
    assert mine["n"] == north["s"]


def test_write_group_merges(tmp_path: Path) -> None:
    world = _world(tmp_path)
    habitat_grid = _habitat_grid(tmp_path, _boreal_3x3(581, 294))
    out = tmp_path / "out"
    made, _ = make_tile(
        "z10_0581_0294",
        habitat_grid,
        world,  # type: ignore[arg-type]
        presence={"fox": 5.0, "vole": 20.0},
        builder_root=tmp_path,
    )
    assert made is not None
    first = write_group(out, "z10_0581_0294", made, None)
    assert first == out / "z10" / "072_036.json"
    data = json.loads(first.read_text())
    assert data["version"] == 1
    assert "z10_0581_0294" in data["tiles"]
    assert data["tiles"]["z10_0581_0294"]["version"] == 1
    made["species"]["playable"].append("moose")
    write_group(out, "z10_0581_0294", made, None)
    again = json.loads(first.read_text())
    assert again["version"] == 2
    assert again["tiles"]["z10_0581_0294"]["version"] == 2
    second = write_group(
        out, "z10_0582_0294", None, {"reason": "no_home", "habitat": "tundra"}
    )
    assert second == first
    data = json.loads(second.read_text())
    assert data["version"] == 3
    assert "z10_0581_0294" in data["tiles"]
    assert data["blocked"]["z10_0582_0294"]["reason"] == "no_home"
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


def test_home_for_cell_builds_reef_from_its_home() -> None:
    """#226.1: the reef blend resolves through the coral_reef home."""
    homes = {
        "sea": {"habitat": "sea", "ecoregions": [651]},
        "coral_reef": {"habitat": "sea", "ecoregions": [150]},
    }
    assert home_for_cell(None, "coral_reef", homes) == "coral_reef"
    assert home_for_cell(150, "coral_reef", homes) == "coral_reef"
    retired = {"coral_reef": {"habitat": "sea", "retired": True}}
    assert home_for_cell(None, "coral_reef", retired) is None


def _recorded_reef_species() -> dict[str, Any]:
    return {
        "place": "Coral Sea reef waters with coral reef",
        "parts": [{"kind": "sea", "species": ["clownfish"]}],
        "plants": [],
        "trees": [],
        "outside_animals": [],
        "outside_plants": [],
        "name_en": "Reef Waters",
        "name_fi": "Riuttavedet",
    }


def test_reef_cell_without_a_place_plans_coral_reef() -> None:
    """#226.1: a mostly-reef cell with no live place builds coral_reef."""
    import sys

    from builder.habitats import load_grid
    from builder.schemas import ROOT

    world = ROOT.parent / "animundo-world"
    grid_dir = ROOT / "data" / "habitat-grid"
    if not (world / "animal-list.json").is_file():
        pytest.skip("needs the world checkout next to the builder")
    if not (grid_dir / "z10.json.gz").is_file():
        pytest.skip("needs the baked habitat grid")
    made, blocked = make_tile(
        "z10_0926_0559",
        load_grid(grid_dir),
        world,
        builder_root=ROOT,
        species=_recorded_reef_species(),
    )
    assert blocked is None, blocked
    assert made is not None
    assert made["home"] == "coral_reef"
    assert made["habitat"] == "sea"
    assert made["biome"] == "coral_reef"
    assert made.get("blend") is None
    assert "clownfish" in made["species"]["playable"]
    assert made["ecoregion"] == 150
    assert made["sources"]["habitat"] == "gbrmpa:torres_strait_features_v1b"
    tools = str(world / "tools")
    sys.path.insert(0, tools)
    try:
        import tile_region
    finally:
        sys.path.remove(tools)
    assert tile_region.place(made)["terrain"]["reef"] > 0


def test_coast_land_builds_from_its_land_home() -> None:
    """#232.1: a coast tile with land builds taiga, keeping its gull."""
    from builder.habitats import load_grid
    from builder.schemas import ROOT

    world = ROOT.parent / "animundo-world"
    grid_dir = ROOT / "data" / "habitat-grid"
    if not (world / "animal-list.json").is_file():
        pytest.skip("needs the world checkout next to the builder")
    if not (grid_dir / "z10.json.gz").is_file():
        pytest.skip("needs the baked habitat grid")
    made, blocked = make_tile(
        "z10_0583_0296",
        load_grid(grid_dir),
        world,
        builder_root=ROOT,
        presence={"fox": 5.0, "vole": 5.0, "frog": 5.0, "herring_gull": 5.0},
        species={
            "place": "Boreal forest coast east of Nuuksio with sea shore",
            "parts": [
                {"kind": "forest", "species": ["fox", "vole", "frog"]},
                {"kind": "sea", "species": ["herring_gull"]},
            ],
            "plants": [
                {"id": "bilberry", "count": 3},
                {"id": "red_clover", "count": 2},
            ],
            "trees": [],
            "outside_animals": [],
            "outside_plants": [],
            "name_en": "Taiga Coast",
            "name_fi": "Taigarannikko",
        },
    )
    assert blocked is None, blocked
    assert made is not None
    assert made["home"] == "boreal_taiga"
    assert made["habitat"] == "boreal_forest"
    assert "coast" in made
    assert "fox" in made["species"]["playable"]
    assert "herring_gull" in made["species"]["playable"]


def test_sea_without_a_land_home_stays_sea(tmp_path: Path) -> None:
    """#232.1: a sea cell whose land habitat has no home stays sea.

    (Land under 0.1 is open ocean, never sea, so the landless
    case is the deep-sea test; here eco 651's forest has no
    home in the checkout.)
    """
    world = _world(tmp_path)
    (world / "animals" / "herring.json").write_text(
        json.dumps({"playable": True, "tags": ["fish"], "diet": ["water_bugs"]})
    )
    habitat_grid = _habitat_grid(tmp_path, _sea_3x3(336, 455))
    made, blocked = make_tile(
        "z10_0336_0455",
        habitat_grid,
        world,  # type: ignore[arg-type]
        presence={"herring": 30.0},
        builder_root=tmp_path,
    )
    assert blocked is None
    assert made is not None
    assert made["home"] == "sea"
    assert made["habitat"] == "sea"


def test_trace_reef_open_sea_has_no_blend() -> None:
    """#230: the ring's N tile stays plain open_sea under the 0.1 floor."""
    from builder.habitats import load_grid
    from builder.schemas import ROOT

    world = ROOT.parent / "animundo-world"
    grid_dir = ROOT / "data" / "habitat-grid"
    if not (world / "animal-list.json").is_file():
        pytest.skip("needs the world checkout next to the builder")
    if not (grid_dir / "z10.json.gz").is_file():
        pytest.skip("needs the baked habitat grid")
    made, blocked = make_tile(
        "z10_0927_0559",
        load_grid(grid_dir),
        world,
        presence={"common_tern": 30.0},
        builder_root=ROOT,
    )
    assert blocked is None, blocked
    assert made is not None
    assert made["home"] == "open_sea"
    assert made.get("blend") is None


def test_deep_coral_sea_cell_still_plans_open_sea() -> None:
    """#226.1: a deep Coral Sea cell still builds open_sea."""
    from builder.habitats import load_grid
    from builder.schemas import ROOT

    world = ROOT.parent / "animundo-world"
    grid_dir = ROOT / "data" / "habitat-grid"
    if not (world / "animal-list.json").is_file():
        pytest.skip("needs the world checkout next to the builder")
    if not (grid_dir / "z10.json.gz").is_file():
        pytest.skip("needs the baked habitat grid")
    made, blocked = make_tile(
        "z10_0952_0570",
        load_grid(grid_dir),
        world,
        presence={"common_tern": 30.0},
        builder_root=ROOT,
    )
    assert blocked is None, blocked
    assert made is not None
    assert made["home"] == "open_sea"
    assert made["habitat"] == "open_ocean"
    assert made["biome"] == "open_sea"


def test_make_tile_builds_the_reviewed_sea_border() -> None:
    """#232.1: z10_0581_0297 builds taiga with a coast, keeping its gull."""
    from builder.schemas import ROOT

    world = ROOT.parent / "animundo-world"
    if not (world / "animals" / "herring_gull.json").is_file():
        pytest.skip("needs the world checkout next to the builder")
    grid_dir = ROOT / "data" / "habitat-grid"
    if not (grid_dir / "z10.json.gz").is_file():
        pytest.skip("needs the baked habitat grid")
    habitat_grid = HabitatGrid.load(grid_dir / "z10.json.gz")
    made, blocked = make_tile(
        "z10_0581_0297",
        habitat_grid,
        world,
        presence={"fox": 5.0, "vole": 5.0, "frog": 5.0, "herring_gull": 5.0},
        species={
            "place": "Boreal forest coast south of Nuuksio with sea shore",
            "parts": [
                {"kind": "forest", "species": ["fox", "vole", "frog"]},
                {"kind": "sea", "species": ["herring_gull"]},
            ],
            "plants": [
                {"id": "bilberry", "count": 3},
                {"id": "red_clover", "count": 2},
            ],
            "trees": [],
            "outside_animals": [],
            "outside_plants": [],
            "name_en": "Taiga Coast",
            "name_fi": "Taigarannikko",
        },
    )
    assert blocked is None, blocked
    assert made is not None
    assert made["home"] == "boreal_taiga"
    assert made["habitat"] == "boreal_forest"
    assert "coast" in made
    assert "herring_gull" in made["species"]["playable"]


def test_tile_facts_come_from_the_grids(tmp_path: Path) -> None:
    """#141.1: the survey's facts are our own grid numbers."""
    world = _world(tmp_path)
    (world / "models" / "plants").mkdir(parents=True)
    (world / "models" / "plants" / "bilberry.json").write_text("{}")
    habitat_grid = _habitat_grid(tmp_path, _sea_3x3(581, 294))
    facts = tile_facts(
        "z10_0581_0294",
        habitat_grid,
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
    assert "plants" not in facts


def _live_grid() -> HabitatGrid:
    from builder.habitats import load_grid
    from builder.schemas import ROOT

    return load_grid(ROOT / "data" / "habitat-grid")


def test_helsinki_mask_has_land_north_and_sea_south() -> None:
    """#142: z10_0582_0296's mask from the re-baked grid, about 15 ones."""
    from builder.grid import parse_tile

    habitat_grid = _live_grid()
    col, row = parse_tile("z10_0582_0296")
    mask = habitat_grid.coast(col, row)
    if mask is None:
        pytest.skip("needs the re-baked grid with coast masks")
    rows = [mask[r * 5 : (r + 1) * 5] for r in range(5)]
    assert rows[0].count("1") >= 4, rows
    assert rows[4] == "00000", rows
    assert 10 <= mask.count("1") <= 16, rows


def test_neighbour_masks_agree_along_the_shared_side() -> None:
    """#142: touching mask sides turn within a sample of each other."""
    from builder.grid import parse_tile

    habitat_grid = _live_grid()
    col, row = parse_tile("z10_0582_0296")
    mine = habitat_grid.coast(col, row)
    west = habitat_grid.coast(col - 1, row)
    north = habitat_grid.coast(col, row - 1)
    if mine is None or west is None or north is None:
        pytest.skip("needs the re-baked grid with coast masks")

    def matching_side(mine_side: list[str], other_side: list[str]) -> int:
        return sum(1 for a, b in zip(mine_side, other_side) if a == b)

    mine_west = [mine[r * 5] for r in range(5)]
    west_east = [west[r * 5 + 4] for r in range(5)]
    assert matching_side(mine_west, west_east) >= 4
    mine_north = list(mine[:5])
    north_south = list(north[20:25])
    assert matching_side(mine_north, north_south) >= 4


def test_helsinki_tile_carries_its_coast() -> None:
    """#142: the remade z10_0580_0297 writes `coast` from the mask."""
    from builder.schemas import ROOT

    world = ROOT.parent / "animundo-world"
    if not (world / "homes" / "sea_717.json").is_file():
        pytest.skip("needs world d009df2 with the reviewed sea home")
    if not (world / "animals" / "herring_gull.json").is_file():
        pytest.skip("needs world d009df2 with the reviewed gull")
    habitat_grid = _live_grid()
    from builder.grid import parse_tile

    col, row = parse_tile("z10_0580_0297")
    mask = habitat_grid.coast(col, row)
    if mask is None:
        pytest.skip("needs the re-baked grid with coast masks")
    made, blocked = make_tile(
        "z10_0580_0297",
        habitat_grid,
        world,
        presence={"herring_gull": 30.0},
    )
    assert blocked is None
    assert made is not None
    assert made["coast"] == [mask[r * 5 : (r + 1) * 5] for r in range(5)]
    assert made["coast"][0].count("1") >= 4
    assert made["coast"][4] == "00000"


def _fed_world(tmp_path: Path) -> Path:
    world = _world(tmp_path)
    (world / "food").mkdir(exist_ok=True)
    (world / "food" / "rules.json").write_text(json.dumps({"kinds": {}}))
    plants = world / "models" / "plants"
    plants.mkdir(parents=True)
    (plants / "red_clover.json").write_text(
        json.dumps({"id": "red_clover", "grows": {"food": "clover"}})
    )
    (plants / "thyme.json").write_text(
        json.dumps({"id": "thyme", "grows": {"food": "thyme"}})
    )
    (world / "animals" / "hare.json").write_text(
        json.dumps({"playable": True, "tags": ["small_mammal"], "diet": ["clover"]})
    )
    (world / "animals" / "stoat.json").write_text(
        json.dumps(
            {"playable": True, "tags": ["predator"], "diet": [], "hunts": ["vole"]}
        )
    )
    return world


def test_web_failures_drops_what_eats_nothing() -> None:
    """#241.1: the 0061 wording still faults its animal for selection."""
    import builder.tiles as tiles_mod

    assert tiles_mod._web_failures(
        ["blob in tl_z10_0581_0294 eats nothing: give it a diet or hunts"]
    ) == {"blob"}


def test_world_eats_without_a_food_rule_keeps_all(tmp_path: Path) -> None:
    """#241.3: a checkout without the rule reports nothing; all stay."""
    world = _fed_world(tmp_path)
    assert (
        world_eats(
            world,
            [{"diet": ["clover"], "hunts": [], "food": ["grass"], "prey_tags": []}],
        )
        is None
    )
    assert tile_gaps_for(world, ["clover"], [], _home(world), None, []) == {
        "diet": [],
        "hunts": [],
    }


def test_world_eats_runs_the_worlds_own_cases() -> None:
    """#241.3: food/eats-cases.json through the checkout's rule."""
    from builder.schemas import ROOT

    world = ROOT.parent / "animundo-world"
    cases_path = world / "food" / "eats-cases.json"
    if not cases_path.is_file() or not (world / "tools" / "food.py").is_file():
        pytest.skip("needs a 0061 world checkout next to the builder")
    cases = json.loads(cases_path.read_text(encoding="utf-8"))["cases"]
    verdicts = world_eats(world, cases)
    assert verdicts is not None
    assert len(verdicts) == len(cases)
    for case, verdict in zip(cases, verdicts):
        assert verdict["eats"] == case["eats"], case["name"]
        assert verdict["gaps"] == case["gaps"], case["name"]


def test_tile_gaps_for_reports_what_the_tile_lacks() -> None:
    """#241.2: gaps name the missing kinds, judged by the world's rule."""
    from builder.schemas import ROOT

    world = ROOT.parent / "animundo-world"
    if not (world / "tools" / "food.py").is_file():
        pytest.skip("needs a 0061 world checkout next to the builder")
    hare = json.loads((world / "animals" / "mountain_hare.json").read_text())
    taiga = json.loads((world / "homes" / "boreal_taiga.json").read_text())
    assert tile_gaps_for(
        world, hare["diet"], hare.get("hunts") or [], taiga, None, []
    ) == {"diet": [], "hunts": []}
    assert tile_gaps_for(world, ["honey"], ["vole"], taiga, None, []) == {
        "diet": ["honey"],
        "hunts": ["vole"],
    }


def test_make_tile_blends_a_part_reef_cell(tmp_path: Path) -> None:
    """#226.1: a part-reef sea tile carries the reef as its blend."""
    from builder.habitats import HabitatGrid

    world = _world(tmp_path)
    (world / "animals" / "herring.json").write_text(
        json.dumps({"playable": True, "tags": ["fish"], "diet": ["water_bugs"]})
    )
    (world / "homes" / "coral_reef.json").write_text(
        json.dumps(
            {
                "habitat": "coral_reef",
                "ecoregions": [150],
                "food": [{"kind": "water_bugs"}],
            }
        )
    )
    habitat_grid = HabitatGrid(_sea_3x3(336, 455), reef={"336,455": 0.1})
    made, blocked = make_tile(
        "z10_0336_0455",
        habitat_grid,
        world,  # type: ignore[arg-type]
        presence={"herring": 30.0},
        builder_root=tmp_path,
    )
    assert blocked is None
    assert made is not None
    assert made["habitat"] == "sea"
    assert made["blend"] == {"home": "coral_reef", "share": 0.1}


def test_make_tile_skips_a_trace_reef_blend(tmp_path: Path) -> None:
    """#230: a 0.07 reef share rides no tile, like no eco blend would."""
    from builder.habitats import HabitatGrid

    world = _world(tmp_path)
    (world / "animals" / "herring.json").write_text(
        json.dumps({"playable": True, "tags": ["fish"], "diet": ["water_bugs"]})
    )
    (world / "homes" / "coral_reef.json").write_text(
        json.dumps(
            {
                "habitat": "sea",
                "ecoregions": [150],
                "food": [{"kind": "water_bugs"}],
            }
        )
    )
    habitat_grid = HabitatGrid(_sea_3x3(336, 455), reef={"336,455": 0.07})
    made, blocked = make_tile(
        "z10_0336_0455",
        habitat_grid,
        world,  # type: ignore[arg-type]
        presence={"herring": 30.0},
        builder_root=tmp_path,
    )
    assert blocked is None
    assert made is not None
    assert made.get("blend") is None


def test_make_tile_uses_sea_home_for_sea(tmp_path: Path) -> None:
    world = _world(tmp_path)
    (world / "animals" / "herring.json").write_text(
        json.dumps({"playable": True, "tags": ["fish"], "diet": ["water_bugs"]})
    )
    habitat_grid = _habitat_grid(tmp_path, _sea_3x3(336, 455))
    made, blocked = make_tile(
        "z10_0336_0455",
        habitat_grid,
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


def test_make_tile_writes_coast_for_coastal_cells(tmp_path: Path) -> None:
    """#142: a cell part land part sea carries its 5 mask rows."""
    world = _world(tmp_path)
    (world / "animals" / "herring.json").write_text(
        json.dumps({"playable": True, "tags": ["fish"], "diet": ["water_bugs"]})
    )
    cell = [651, 1.0, None, 0.0, 4, 0.48, 0.0, 0.0, "1" * 12 + "0" * 13]
    cells = {f"{c},{r}": list(cell) for r in (454, 455, 456) for c in (335, 336, 337)}
    habitat_grid = _habitat_grid(tmp_path, cells)
    made, blocked = make_tile(
        "z10_0336_0455",
        habitat_grid,
        world,  # type: ignore[arg-type]
        presence={"herring": 30.0},
        builder_root=tmp_path,
    )
    assert blocked is None
    assert made is not None
    assert made["coast"] == ["11111", "11111", "11000", "00000", "00000"]


def test_make_tile_skips_coast_without_a_mask(tmp_path: Path) -> None:
    """#142: cells from before the coast carry no mask and no `coast`."""
    world = _world(tmp_path)
    (world / "animals" / "herring.json").write_text(
        json.dumps({"playable": True, "tags": ["fish"], "diet": ["water_bugs"]})
    )
    habitat_grid = _habitat_grid(tmp_path, _sea_3x3(336, 455))
    made, blocked = make_tile(
        "z10_0336_0455",
        habitat_grid,
        world,
        presence={"herring": 30.0},
        builder_root=tmp_path,
    )
    assert blocked is None
    assert made is not None
    assert "coast" not in made


def test_make_tile_skips_coast_for_whole_land(tmp_path: Path) -> None:
    """#142: an all-land cell has no `coast`, mask or not."""
    world = _world(tmp_path)
    cell = [717, 1.0, None, 0.0, 6, 1.0, 0.0, 80.0, "1" * 25]
    cells = {f"{c},{r}": list(cell) for r in (293, 294, 295) for c in (580, 581, 582)}
    habitat_grid = _habitat_grid(tmp_path, cells)
    made, blocked = make_tile(
        "z10_0581_0294",
        habitat_grid,
        world,
        presence={"fox": 5.0, "mountain_hare": 5.0, "vole": 20.0},
        builder_root=tmp_path,
    )
    assert blocked is None
    assert made is not None
    assert "coast" not in made


def test_make_tile_blends_second_home(tmp_path: Path) -> None:
    world = _world(tmp_path)
    (world / "homes" / "taiga.json").write_text(
        json.dumps({"habitat": "boreal_forest", "ecoregions": [718]})
    )
    cell = [717, 0.85, 718, 0.15, 6, 1.0, 0.0, 80.0]
    cells = {f"{c},{r}": list(cell) for r in (293, 294, 295) for c in (580, 581, 582)}
    habitat_grid = _habitat_grid(tmp_path, cells)
    made, blocked = make_tile(
        "z10_0581_0294",
        habitat_grid,
        world,
        presence={"fox": 5.0, "vole": 20.0},
        builder_root=tmp_path,
    )
    assert blocked is None
    assert made is not None
    assert made["blend"] == {"home": "taiga", "share": 0.15}
    assert made["requires"]["homes"] == ["boreal_forest", "taiga"]
    assert made["requires"]["biomes"] == ["boreal_forest", "taiga"]


def test_make_tile_blocks_ocean_without_ecoregion(tmp_path: Path) -> None:
    world = _world(tmp_path)
    habitat_grid = _habitat_grid(
        tmp_path, {"336,455": [None, 0.0, None, 0.0, None, 0.0, 0.0, 0.0]}
    )
    made, blocked = make_tile(
        "z10_0336_0455",
        habitat_grid,
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
    (tools / "tile_region.py").write_text('"""A converter new enough.\n"""\n')


def test_make_tile_refuses_a_check_that_predates_the_converter(
    tmp_path: Path,
) -> None:
    """#161.3: a tile check without the converter is refused, loudly."""
    world = _world(tmp_path)
    (world / "tools").mkdir(exist_ok=True)
    (world / "tools" / "check_tile.py").write_text(
        'import json\nprint(json.dumps({"ok": True, "results": []}))\n'
    )
    habitat_grid = _habitat_grid(tmp_path, _boreal_3x3(581, 294))
    with pytest.raises(RuntimeError, match="predates the converter"):
        make_tile(
            "z10_0581_0294",
            habitat_grid,
            world,
            presence={"fox": 5.0, "vole": 20.0},
            builder_root=tmp_path,
        )


def test_make_tile_raises_when_world_check_fails_food(tmp_path: Path) -> None:
    """#241.1: a food failure is a loud maker bug, never no_animal."""
    world = _world(tmp_path)
    _checker(
        world,
        {
            "ok": False,
            "results": [
                {
                    "check": "food",
                    "status": "fail",
                    "problems": ["blob eats nothing: give it a diet or hunts"],
                }
            ],
        },
        1,
    )
    habitat_grid = _habitat_grid(tmp_path, _boreal_3x3(581, 294))
    with pytest.raises(RuntimeError, match="failed the world's check"):
        make_tile(
            "z10_0581_0294",
            habitat_grid,
            world,
            presence={"fox": 5.0, "vole": 20.0},
            builder_root=tmp_path,
        )


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
    habitat_grid = _habitat_grid(tmp_path, _boreal_3x3(581, 294))
    with pytest.raises(RuntimeError, match="world's check"):
        make_tile(
            "z10_0581_0294",
            habitat_grid,
            world,
            presence={"fox": 5.0, "vole": 20.0},
            builder_root=tmp_path,
        )


def _budget_checker(world: Path) -> None:
    """A tile check that enforces the ground budget, like the world's."""
    tools = world / "tools"
    tools.mkdir(exist_ok=True)
    (tools / "tile_region.py").write_text('"""A converter new enough.\n"""\n')
    (tools / "check_tile.py").write_text(
        "import json, sys\n"
        "tile = json.load(open(sys.argv[1]))\n"
        "total = sum(\n"
        "    p.get('density', 0)\n"
        "    for p in (tile.get('plants') or {}).get('cover', []) or []\n"
        ")\n"
        "if total > 1:\n"
        "    print(json.dumps({'ok': False, 'results': [{'check': 'region',"
        " 'status': 'fail', 'problems': ['\"ground_cover\" grass + flowers +"
        " stones + plant densities must add up to 1 or less']}]}))\n"
        "else:\n"
        "    print(json.dumps({'ok': True, 'results': []}))\n"
    )


def _berry_world(tmp_path: Path) -> Path:
    world = _world(tmp_path)
    greens = world / "models" / "plants"
    greens.mkdir(parents=True)
    for stem, latin in (
        ("bilberry", "Vaccinium myrtillus"),
        ("crowberry", "Empetrum nigrum"),
    ):
        (greens / f"{stem}.json").write_text(
            json.dumps(
                {
                    "id": stem,
                    "name": stem.replace("_", " ").title(),
                    "latin": latin,
                    "grows": {"on": ["grassland"], "density": 0.8},
                }
            )
        )
    return world


def _berry_species() -> dict[str, Any]:
    return {
        "place": "Boreal forest in southern Finland",
        "parts": [{"kind": "forest", "species": ["fox", "mountain_hare", "vole"]}],
        "plants": [{"id": "bilberry", "count": 10}, {"id": "crowberry", "count": 10}],
        "trees": [],
        "outside_animals": [],
        "outside_plants": [],
        "name_en": "Berry woods",
        "name_fi": "Marjametsä",
    }


def test_make_tile_thins_densities_to_fit_the_world_budget(
    tmp_path: Path,
) -> None:
    """#186: a tile over the ground budget retries halved, then passes."""
    world = _berry_world(tmp_path)
    _budget_checker(world)
    habitat_grid = _habitat_grid(tmp_path, _boreal_3x3(581, 294))
    made, blocked = make_tile(
        "z10_0581_0294",
        habitat_grid,
        world,
        builder_root=tmp_path,
        species=_berry_species(),
    )
    assert blocked is None
    assert made is not None
    assert [p["density"] for p in made["plants"]["cover"]] == [0.4, 0.4]


def test_make_tile_defaults_missing_outside_lists_on_a_recording(
    tmp_path: Path,
) -> None:
    """#210: a pre-strict recording without outside lists still makes."""
    world = _berry_world(tmp_path)
    habitat_grid = _habitat_grid(tmp_path, _boreal_3x3(581, 294))
    recorded = {
        k: v for k, v in _berry_species().items() if not k.startswith("outside_")
    }
    made, blocked = make_tile(
        "z10_0581_0294",
        habitat_grid,
        world,
        builder_root=tmp_path,
        species=recorded,
    )
    assert blocked is None
    assert made is not None
    assert made["species"]["playable"]


def test_make_tile_raises_when_densities_cannot_fit(tmp_path: Path) -> None:
    """#186: three thinnings that still fail stop loudly, like before."""
    world = _berry_world(tmp_path)
    _checker(
        world,
        {
            "ok": False,
            "results": [
                {
                    "check": "region",
                    "status": "fail",
                    "problems": [
                        '"ground_cover" grass + flowers + stones + plant '
                        "densities must add up to 1 or less"
                    ],
                }
            ],
        },
        1,
    )
    habitat_grid = _habitat_grid(tmp_path, _boreal_3x3(581, 294))
    with pytest.raises(RuntimeError, match="must add up to 1 or less"):
        make_tile(
            "z10_0581_0294",
            habitat_grid,
            world,
            builder_root=tmp_path,
            species=_berry_species(),
        )


def test_published_batch_tile_carries_its_compass_name(tmp_path: Path) -> None:
    """#195.1: a made land tile publishes with name.en and name.fi."""
    import json

    from fake_store import FakeStore

    from builder.build import publish_group

    world = _berry_world(tmp_path)
    _budget_checker(world)
    habitat_grid = _habitat_grid(tmp_path, _boreal_3x3(581, 294))
    made, blocked = make_tile(
        "z10_0581_0294",
        habitat_grid,
        world,
        builder_root=tmp_path,
        species=_berry_species(),
    )
    assert blocked is None
    assert made is not None
    assert set(made["name"]) == {"en", "fi"}
    assert "Nuuksio" in made["name"]["en"]
    store = FakeStore()
    path = publish_group("z10_0581_0294", made, store, "abc123")
    group = json.loads(store.read_blob(path)[0].decode("utf-8"))
    tile = group["tiles"]["z10_0581_0294"]
    assert tile["name"]["en"] == made["name"]["en"]
    assert tile["name"]["fi"] == made["name"]["fi"]


def test_surveyed_plants_keep_the_hare_on_the_tundra(tmp_path: Path) -> None:
    """#154: surveyed clover and birch feed the hare the biome starves."""
    from builder.schemas import ROOT
    from builder.tiles import check_with_world

    world = ROOT.parent / "animundo-world"
    if not (world / "homes" / "tundra.json").is_file():
        pytest.skip("needs a world with the tundra home")
    if not (world / "tools" / "tile_region.py").is_file():
        pytest.skip("needs a world with the converter")
    habitat_grid = _live_grid()
    made, blocked = make_tile(
        "z10_0591_0220",
        habitat_grid,
        world,
        builder_root=tmp_path,
        species={
            "place": "Fell tundra in Enontekiö, Finland",
            "parts": [{"kind": "forest", "species": ["mountain_hare"]}],
            "plants": [{"id": "white_clover", "count": 10}],
            "trees": [{"id": "birch", "count": 5}],
            "outside_animals": [],
            "outside_plants": [],
            "name_en": "Hare Fell",
            "name_fi": "Jäniksen tunturi",
        },
    )
    assert blocked is None
    assert made is not None
    assert "mountain_hare" in made["species"]["playable"]
    assert check_with_world(made, world) == []


def test_replacing_plants_keeps_animals_the_survey_named(tmp_path: Path) -> None:
    """#241.1: a grass-only cover starves nobody; the survey stays."""
    from builder.schemas import ROOT
    from builder.tiles import check_with_world

    world = ROOT.parent / "animundo-world"
    if not (world / "tools" / "tile_region.py").is_file():
        pytest.skip("needs a world with the converter")
    habitat_grid = _live_grid()
    answer = json.loads(
        (
            ROOT / "tests" / "fixtures" / "tiles" / "z10_0579_0297-survey.json"
        ).read_text()
    )
    answer["plants"] = [{"id": "wavy_hair_grass", "count": 20}]
    answer["trees"] = []
    made, blocked = make_tile(
        "z10_0579_0297",
        habitat_grid,
        world,
        builder_root=tmp_path,
        species=answer,
    )
    assert blocked is None
    assert made is not None
    species = made["species"]
    kept = set(species["playable"]) | set(species["prey"]) | set(species["predators"])
    assert "squirrel" in kept
    assert "vole" in kept
    assert "mountain_hare" in kept
    # The kestrel stays with its vole, the fox on what is left.
    assert "kestrel" in kept
    assert "fox" in kept
    assert species["playable"]
    assert check_with_world(made, world) == []


def test_helsinki_shore_keeps_forest_animals_and_gull(tmp_path: Path) -> None:
    """#153: z10_0580_0297 keeps fox, hare, squirrel and gull with shore plants."""
    from builder.schemas import ROOT
    from builder.tiles import check_with_world

    world = ROOT.parent / "animundo-world"
    if not (world / "homes" / "sea_717.json").is_file():
        pytest.skip("needs world d009df2 with the reviewed sea home")
    if not (world / "tools" / "tile_region.py").is_file():
        pytest.skip("needs a world with the converter")
    habitat_grid = _live_grid()
    made, blocked = make_tile(
        "z10_0580_0297",
        habitat_grid,
        world,
        builder_root=tmp_path,
        species={
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
            "outside_animals": [],
            "outside_plants": [],
            "name_en": "Helsinki Shore",
            "name_fi": "Helsingin ranta",
        },
    )
    assert blocked is None
    assert made is not None
    species = made["species"]
    kept = set(species["playable"]) | set(species["prey"]) | set(species["predators"])
    for name in ("fox", "mountain_hare", "squirrel", "herring_gull"):
        assert name in kept
    assert check_with_world(made, world) == []


def test_candidate_habitats_cover_both_sides_of_a_coast() -> None:
    """#153: a 717 coast cell offers forest animals; pure ocean offers none."""
    from builder.tiles import _candidate_habitats

    coast = _candidate_habitats("sea", 717, 6, 0.48, 0.0)
    assert {"sea", "open_ocean", "boreal_forest"} <= coast
    assert "boreal_lake" not in coast
    wet = _candidate_habitats("sea", 717, 6, 0.3, 0.2)
    assert {"boreal_forest", "boreal_lake"} <= wet
    inland = _candidate_habitats("boreal_forest", 717, 6, 0.9, 0.0)
    assert "sea" in inland
    assert "boreal_forest" in inland
    ocean = _candidate_habitats("open_ocean", None, None, 0.0, 0.0)
    assert ocean == {"open_ocean", "sea"}


def test_helsinki_shore_remakes_from_layered_plants(tmp_path: Path) -> None:
    """#147.5: the surveyed shore plants give a ready tile, gull playable."""
    from builder.schemas import ROOT
    from builder.tiles import check_with_world

    world = ROOT.parent / "animundo-world"
    if not (world / "homes" / "sea_717.json").is_file():
        pytest.skip("needs world d009df2 with the reviewed sea home")
    if not (world / "tools" / "tile_region.py").is_file():
        pytest.skip("needs a world with the converter")
    habitat_grid = _live_grid()
    made, blocked = make_tile(
        "z10_0580_0297",
        habitat_grid,
        world,
        builder_root=tmp_path,
        species={
            "place": "Helsinki shore on the Gulf of Finland",
            "parts": [{"kind": "sea", "species": ["herring_gull"]}],
            "plants": [
                {"id": "juniper", "count": 8},
                {"id": "crowberry", "count": 10},
                {"id": "heather", "count": 15},
                {"id": "reindeer_lichen", "count": 6},
                {"id": "wavy_hair_grass", "count": 20},
                {"id": "fireweed", "count": 10},
            ],
            "trees": [
                {"id": "scots_pine", "count": 30},
                {"id": "birch", "count": 20},
                {"id": "rowan", "count": 5},
            ],
            "outside_animals": [],
            "outside_plants": [],
            "name_en": "Helsinki Shore",
            "name_fi": "Helsingin ranta",
        },
    )
    assert blocked is None
    assert made is not None
    assert "herring_gull" in made["species"]["playable"]
    plants = made.get("plants") or {}
    cover = {entry["id"] for entry in plants.get("cover", [])}
    assert cover == {
        "juniper",
        "crowberry",
        "heather",
        "reindeer_lichen",
        "wavy_hair_grass",
        "fireweed",
    }
    assert "lingonberry" not in cover
    assert "bilberry" not in cover
    assert {entry["id"] for entry in plants.get("trees", [])} == {
        "scots_pine",
        "birch",
        "rowan",
    }
    assert check_with_world(made, world) == []


def _lands(world: Path) -> None:
    (world / "lands").mkdir(exist_ok=True)
    (world / "lands" / "index.json").write_text(
        json.dumps({"styles": [{"land": "boreal", "biome": 6}]})
    )


def _biomes(world: Path) -> None:
    (world / "biomes").mkdir(exist_ok=True)
    (world / "biomes" / "boreal_forest.json").write_text(
        json.dumps(
            {
                "habitat": "boreal_forest",
                "ecoregions": [717],
                "land": "boreal",
            }
        )
    )


def test_land_style_comes_from_the_index(tmp_path: Path) -> None:
    """#148.2: the RESOLVE biome number picks the land style by code."""
    from builder.tiles import land_style_for

    world = _world(tmp_path)
    assert land_style_for(world, 6) is None
    _lands(world)
    assert land_style_for(world, 6) == "boreal"
    assert land_style_for(world, 13) is None
    assert land_style_for(world, None) is None


def test_default_bundle_takes_the_nearest_matching_biome(tmp_path: Path) -> None:
    """#148.3: an unlisted ecoregion builds from the matching neighbour."""
    from builder.tiles import available_biomes, default_bundle

    world = _world(tmp_path)
    _biomes(world)
    cells = _boreal_3x3(581, 294)
    cells["581,294"] = [999, 1.0, None, 0.0, 6, 1.0, 0.0, 80.0]
    habitat_grid = _habitat_grid(tmp_path, cells)
    biomes = available_biomes(world)
    assert (
        default_bundle("z10_0581_0294", habitat_grid, biomes, "boreal_forest", "boreal")
        == "boreal_forest"
    )
    assert (
        default_bundle("z10_0581_0294", habitat_grid, biomes, "boreal_forest", "tundra")
        is None
    )


def test_make_tile_blocks_no_land_without_a_style(tmp_path: Path) -> None:
    """#148.3: a styled index without this biome blocks the tile as no_land."""
    world = _world(tmp_path)
    _lands(world)
    cells = _boreal_3x3(581, 294)
    cells["581,294"] = [999, 1.0, None, 0.0, 13, 1.0, 0.0, 80.0]
    habitat_grid = _habitat_grid(tmp_path, cells)
    made, blocked = make_tile(
        "z10_0581_0294",
        habitat_grid,
        world,
        presence={"fox": 5.0},
        builder_root=tmp_path,
    )
    assert made is None
    assert blocked is not None
    assert blocked["reason"] == "no_land"


def test_make_tile_builds_from_the_default_bundle(tmp_path: Path) -> None:
    """#148.3: an unlisted ecoregion with a style builds, no new home."""
    world = _world(tmp_path)
    _lands(world)
    _biomes(world)
    cells = _boreal_3x3(581, 294)
    cells["581,294"] = [999, 1.0, None, 0.0, 6, 1.0, 0.0, 80.0]
    habitat_grid = _habitat_grid(tmp_path, cells)
    made, blocked = make_tile(
        "z10_0581_0294",
        habitat_grid,
        world,
        presence={"fox": 5.0, "mountain_hare": 9.0, "vole": 20.0},
        builder_root=tmp_path,
    )
    assert blocked is None
    assert made is not None
    assert made["home"] == "boreal_forest"
    assert made["biome"] == "boreal_forest"
    assert "fox" in made["species"]["playable"]


def test_remade_nuuksio_tile_keeps_squirrel_and_bee(tmp_path: Path) -> None:
    """#152: a code re-make of z10_0579_0297 keeps the squirrel and the bee."""
    from builder.schemas import ROOT

    world = ROOT.parent / "animundo-world"
    if not (world / "food" / "rules.json").is_file():
        pytest.skip("needs a layered world with food rules")
    if not (world / "animals" / "bee.json").is_file():
        pytest.skip("needs world main with the bee filed")
    habitat_grid = _live_grid()
    answer = json.loads(
        (
            ROOT / "tests" / "fixtures" / "tiles" / "z10_0579_0297-survey.json"
        ).read_text()
    )
    made, blocked = make_tile(
        "z10_0579_0297",
        habitat_grid,
        world,
        builder_root=tmp_path,
        species=answer,
    )
    assert blocked is None
    assert made is not None
    assert "squirrel" in made["species"]["playable"]
    assert "bee" in made["species"]["playable"]


def test_make_at_appends_local_history_without_a_store(tmp_path: Path) -> None:
    """#165.2: the command line keeps jsonl rows, one per event."""
    from builder.grid import center
    from builder.tiles import make_at

    world = _world(tmp_path)
    cells = {"581,294": [999, 1.0, None, 0.0, 13, 1.0, 0.0, 80.0]}
    habitat_grid = _habitat_grid(tmp_path, cells)
    lat, lon = center("z10_0581_0294")
    tile, _path, status = make_at(lat, lon, habitat_grid, world, tmp_path / "out")
    assert tile == "z10_0581_0294"
    assert status == "no_home"
    rows = (tmp_path / "out" / "tile-history.jsonl").read_text().splitlines()
    assert len(rows) == 1
    entry = json.loads(rows[0])
    assert entry["event"] == "blocked"
    assert entry["kind"] == "cli"
    assert entry["job"] is None
    assert entry["reason"] == "no_home"
    assert entry["version"] == 1
    make_at(lat, lon, habitat_grid, world, tmp_path / "out")
    rows = (tmp_path / "out" / "tile-history.jsonl").read_text().splitlines()
    assert len(rows) == 2
    assert json.loads(rows[1])["version"] == 2


def test_make_at_records_to_the_store_when_given(tmp_path: Path) -> None:
    """#165.2: with credentials the row goes to tile_history."""
    from fake_store import FakeStore

    from builder.grid import center
    from builder.tiles import make_at

    world = _world(tmp_path)
    cells = {"581,294": [999, 1.0, None, 0.0, 13, 1.0, 0.0, 80.0]}
    habitat_grid = _habitat_grid(tmp_path, cells)
    lat, lon = center("z10_0581_0294")
    store = FakeStore()
    make_at(lat, lon, habitat_grid, world, tmp_path / "out", history_store=store)
    (entry,) = store.tile_history
    assert entry["tile"] == "z10_0581_0294"
    assert entry["event"] == "blocked"
    assert not (tmp_path / "out" / "tile-history.jsonl").exists()


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
    (world / "lands").mkdir(exist_ok=True)
    (world / "lands" / "index.json").write_text(
        json.dumps({"styles": [{"land": "boreal", "biome": 6}]})
    )
    return world


def _open_sea_cells(col: int, row: int) -> dict[str, list]:
    cells = _boreal_3x3(col, row)
    cells[f"{col},{row}"] = [None, 0.0, None, 0.0, None, 0.0, 1.0, -5.0]
    return cells


def _seas_file(tmp_path: Path, mapping: dict[str, str]) -> Path:
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    path = data / "seas.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump({"cells": mapping}, handle)
    return path


def test_open_sea_tile_carries_sea_name_and_land(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#177.1-4: the gulf tile names its sea and its boreal shore."""
    from builder.species import list_animals

    seen: dict[str, Any] = {}

    def fake_fetch(tile: str, candidates: list[dict[str, Any]]) -> dict[str, float]:
        seen["tile"] = tile
        seen["candidates"] = sorted(c["id"] for c in candidates)
        return {"herring_gull": 5.0}

    monkeypatch.setattr("builder.tiles.fetch_smoothed", fake_fetch)
    world = _open_sea_world(tmp_path)
    _seas_file(tmp_path, {"z10_0581_0294": "gulf_of_finland"})
    habitat_grid = _habitat_grid(tmp_path, _open_sea_cells(581, 294))
    made, blocked = make_tile(
        "z10_0581_0294",
        habitat_grid,
        world,
        builder_root=tmp_path,
    )
    assert blocked is None
    assert made is not None
    assert made["habitat"] == "open_ocean"
    assert made["home"] == "open_sea"
    assert made["biome"] == "open_sea"
    assert "ecoregion" not in made
    assert "coast" not in made
    assert "blend" not in made
    assert made["sea"] == "gulf_of_finland"
    assert made["name"] == {"en": "Gulf of Finland", "fi": "Suomenlahti"}
    assert made["land"] == "boreal"
    assert made["land_share"] == 0.0
    assert made["sources"]["habitat"] == "spec04:open_ocean"
    assert made["sources"]["name"] == "evs:test"
    assert made["sources"]["land"] == "lands/index.json:nearest_coast:717"
    assert made["sources"]["species"] == "gbif:density"
    assert made["sources"]["water"] == "hydrolakes:v1"
    assert made["species"]["playable"] == ["herring_gull"]
    assert set(made["requires"]["homes"]) == {"open_sea"}
    assert made["level"] < 8
    validate("tile", made)
    assert seen["tile"] == "z10_0581_0294"
    assert seen["candidates"] == ["herring_gull"]
    entries = list_animals(world)
    assert [e["id"] for e in open_sea_entries(world, entries)] == ["herring_gull"]


def test_open_sea_tile_without_sea_stays_open_water(tmp_path: Path) -> None:
    """#177.2: unnamed water is still blocked, before any species work."""
    world = _open_sea_world(tmp_path)
    _seas_file(tmp_path, {})
    habitat_grid = _habitat_grid(tmp_path, _open_sea_cells(581, 294))
    made, blocked = make_tile(
        "z10_0581_0294",
        habitat_grid,
        world,
        presence={"herring_gull": 5.0},
        builder_root=tmp_path,
    )
    assert made is None
    assert blocked is not None
    assert blocked["reason"] == "open_water"


def test_open_sea_tile_names_its_sea_from_the_image_layout(
    tmp_path: Path,
) -> None:
    """#185: the worker's data file names a reef-side open-sea tile.

    `builder_root` holds only what the image mounts under
    `/src/builder/data`: the baked seas file, copied from this
    checkout.
    """
    import shutil

    root = Path(__file__).resolve().parent.parent
    builder_root = tmp_path / "src" / "builder"
    (builder_root / "data").mkdir(parents=True)
    shutil.copy(root / "data" / "seas.json.gz", builder_root / "data" / "seas.json.gz")
    assert [p.name for p in (builder_root / "data").iterdir()] == ["seas.json.gz"]
    world = _open_sea_world(tmp_path)
    table = json.loads((world / "seas" / "names.json").read_text())
    table["seas"]["great_barrier_reef"] = {
        "en": "Great Barrier Reef",
        "fi": "Iso Valliriutta",
        "ne_ids": [1],
        "wikidata": "Q7343",
    }
    (world / "seas" / "names.json").write_text(json.dumps(table))
    habitat_grid = _habitat_grid(tmp_path, _open_sea_cells(928, 560))
    made, blocked = make_tile(
        "z10_0928_0560",
        habitat_grid,
        world,
        presence={"herring_gull": 5.0},
        builder_root=builder_root,
    )
    assert blocked is None
    assert made is not None
    assert made["sea"] == "great_barrier_reef"
    assert made["name"] == {"en": "Great Barrier Reef", "fi": "Iso Valliriutta"}


def test_open_sea_tile_without_coast_style_has_no_land(tmp_path: Path) -> None:
    """#177.1: a coast biome with no land style blocks the tile as no_land."""
    world = _open_sea_world(tmp_path)
    _seas_file(tmp_path, {"z10_0581_0294": "gulf_of_finland"})
    cells = _open_sea_cells(581, 294)
    for key, cell in cells.items():
        if cell[0] == 717:
            cell[0] = 999
            cell[4] = 99
    habitat_grid = _habitat_grid(tmp_path, cells)
    made, blocked = make_tile(
        "z10_0581_0294",
        habitat_grid,
        world,
        presence={"herring_gull": 5.0},
        builder_root=tmp_path,
    )
    assert made is None
    assert blocked is not None
    assert blocked["reason"] == "no_land"


def test_near_made_or_noted_reaches_three_cells() -> None:
    """#177.6: a made or noted tile within 3 cells counts, else not."""
    assert near_made_or_noted("z10_0581_0294", set()) is False
    assert near_made_or_noted("z10_0581_0294", {"z10_0579_0297"}) is True
    assert near_made_or_noted("z10_0581_0294", set(), {"z10_0583_0292"}) is True
    assert near_made_or_noted("z10_0581_0294", {"z10_0585_0296"}) is False
    assert near_made_or_noted("z10_0581_0294", set(), {"z10_0585_0296"}) is False


def test_retired_biomes_are_never_picked() -> None:
    """#201.1/#203.1: retired lake and forest cells get their heirs."""
    import pytest

    from builder.schemas import ROOT
    from builder.tiles import available_biomes, home_for_cell

    world = ROOT.parent / "animundo-world"
    if not (world / "biomes" / "boreal_lakeland.json").is_file():
        pytest.skip("needs the world checkout next to the builder")
    biomes = available_biomes(world)
    assert biomes["boreal_lake"].get("retired") is True
    assert biomes["boreal_forest"].get("retired") is True
    assert home_for_cell(717, "boreal_lake", biomes, 6) == "boreal_lakeland"
    assert home_for_cell(717, "boreal_forest", biomes, 6) == "boreal_taiga"


def test_live_place_open_sea_tile_without_a_biome_refuses(tmp_path: Path) -> None:
    """#212.2: a live place's sea tile needs its biome for land species."""
    world = _world(tmp_path)
    (world / "homes" / "open_sea.json").write_text(
        json.dumps({"habitat": "sea", "food": [{"kind": "fish"}]})
    )
    places = world / "places"
    places.mkdir(exist_ok=True)
    (places / "xx_test.json").write_text(
        json.dumps(
            {
                "id": "xx_test",
                "name": {"en": "Test", "fi": "Testi"},
                "center": {"lat": 0.0, "lon": 0.0},
                "tiles": [[581, 294]],
                "species": {"playable": ["fox"], "prey": [], "predators": []},
                "live": True,
            }
        )
    )
    cells = {"581,294": [None, 0.0, None, 0.0, None, 0.0, 0.0, 0.0]}
    with pytest.raises(ValueError, match="names no biome"):
        make_tile(
            "z10_0581_0294",
            _habitat_grid(tmp_path, cells),
            world,
            builder_root=tmp_path,
        )


def test_remade_reef_tile_plays_its_corals_and_clownfish() -> None:
    """#212.3: the reef's tile is coral_reef with rock, corals, clownfish."""
    import sys

    from builder.habitats import load_grid
    from builder.schemas import ROOT

    world = ROOT.parent / "animundo-world"
    grid_dir = ROOT / "data" / "habitat-grid"
    if not (world / "animal-list.json").is_file():
        pytest.skip("needs the world checkout next to the builder")
    if not (grid_dir / "z10.json.gz").is_file():
        pytest.skip("needs the baked habitat grid")
    made, blocked = make_tile(
        "z10_0927_0560", load_grid(grid_dir), world, builder_root=ROOT
    )
    assert blocked is None, blocked
    assert made is not None
    assert made["biome"] == "coral_reef"
    assert "clownfish" in made["species"]["playable"]
    grown = [p["id"] for p in (made.get("plants") or {}).get("water", [])]
    assert "branching_staghorn_coral" in grown
    tools = str(world / "tools")
    sys.path.insert(0, tools)
    try:
        import tile_region
    finally:
        sys.path.remove(tools)
    assert tile_region.place(made)["terrain"]["reef"] > 0


def test_live_place_tile_carries_the_places_name() -> None:
    """#252.4: the reef's own tile is named for the place, not the compass."""
    from builder.habitats import load_grid
    from builder.schemas import ROOT

    world = ROOT.parent / "animundo-world"
    grid_dir = ROOT / "data" / "habitat-grid"
    if not (world / "animal-list.json").is_file():
        pytest.skip("needs the world checkout next to the builder")
    if not (grid_dir / "z10.json.gz").is_file():
        pytest.skip("needs the baked habitat grid")
    place = json.loads((world / "places" / "au_great_barrier_reef.json").read_text())
    made, blocked = make_tile(
        "z10_0927_0560", load_grid(grid_dir), world, builder_root=ROOT
    )
    assert blocked is None, blocked
    assert made is not None
    assert made["name"] == {"en": place["name"]["en"], "fi": place["name"]["fi"]}


def test_reef_map_places_coral_reef_when_region_names_no_biome() -> None:
    """#233: C builds coral_reef from the reef map without its region biome."""
    from unittest import mock

    from builder.habitats import load_grid
    from builder.schemas import ROOT

    world = ROOT.parent / "animundo-world"
    grid_dir = ROOT / "data" / "habitat-grid"
    if not (world / "animal-list.json").is_file():
        pytest.skip("needs the world checkout next to the builder")
    if not (grid_dir / "z10.json.gz").is_file():
        pytest.skip("needs the baked habitat grid")
    place = json.loads((world / "places" / "au_great_barrier_reef.json").read_text())
    presence = {
        species: 1.0
        for key in ("playable", "prey", "predators")
        for species in place["species"].get(key, [])
    }
    presence["common_tern"] = 5.0
    with mock.patch("builder.tiles._place_biome", return_value=None):
        made, blocked = make_tile(
            "z10_0927_0560",
            load_grid(grid_dir),
            world,
            presence=presence,
            builder_root=ROOT,
        )
    assert blocked is None, blocked
    assert made is not None
    assert made["home"] == "coral_reef"
    assert made["habitat"] == "sea"


def test_nuuksio_place_cell_stays_boreal_taiga() -> None:
    """#233: a Nuuksio place cell still builds taiga with no reef blend."""
    from builder.habitats import load_grid
    from builder.schemas import ROOT

    world = ROOT.parent / "animundo-world"
    grid_dir = ROOT / "data" / "habitat-grid"
    if not (world / "animal-list.json").is_file():
        pytest.skip("needs the world checkout next to the builder")
    if not (grid_dir / "z10.json.gz").is_file():
        pytest.skip("needs the baked habitat grid")
    made, blocked = make_tile(
        "z10_0581_0295",
        load_grid(grid_dir),
        world,
        builder_root=ROOT,
        presence={"fox": 5.0, "vole": 5.0, "frog": 5.0},
        species={
            "place": "Boreal forest in Nuuksio with spruce and pine",
            "parts": [{"kind": "forest", "species": ["fox", "vole", "frog"]}],
            "plants": [
                {"id": "bilberry", "count": 3},
                {"id": "red_clover", "count": 2},
            ],
            "trees": [],
            "outside_animals": [],
            "outside_plants": [],
            "name_en": "Nuuksio Forest",
            "name_fi": "Nuuksion metsä",
        },
    )
    assert blocked is None, blocked
    assert made is not None
    assert made["home"] == "boreal_taiga"
    assert made["habitat"] == "boreal_forest"
    assert "blend" not in made


def test_place_cells_come_out_as_code_tiles() -> None:
    """#202.5: the six cells under the places make ordinary code tiles."""
    from builder.habitats import load_grid
    from builder.schemas import ROOT

    world = ROOT.parent / "animundo-world"
    grid_dir = ROOT / "data" / "habitat-grid"
    if not (world / "animal-list.json").is_file():
        pytest.skip("needs the world checkout next to the builder")
    if not (grid_dir / "z10.json.gz").is_file():
        pytest.skip("needs the baked habitat grid")
    grid = load_grid(grid_dir)
    cells = {
        "z10_0581_0295": "fi_nuuksio",
        "z10_0582_0295": "fi_nuuksio",
        "z10_0581_0296": "fi_nuuksio",
        "z10_0582_0296": "fi_nuuksio",
        "z10_0593_0284": "fi_saimaa",
        "z10_0927_0560": "au_great_barrier_reef",
    }
    for tile, pid in cells.items():
        place = json.loads((world / "places" / f"{pid}.json").read_text())
        presence = {
            species: 1.0
            for key in ("playable", "prey", "predators")
            for species in place["species"].get(key, [])
        }
        if tile == "z10_0927_0560":
            # Open sea: the place's reef fish are not open-sea
            # candidates; the tern is, as the live tile shows.
            presence["common_tern"] = 5.0
        made, blocked = make_tile(
            tile, grid, world, presence=presence, builder_root=ROOT
        )
        assert blocked is None, (tile, blocked)
        assert made is not None, tile
        assert made["kind"] == "code", tile
        assert made["sources"]["water"] == "hydrolakes:v1", tile
        assert made["name"]["en"] and made["name"]["fi"], tile
