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
    place_cells,
    tile_edges,
    tile_facts,
    tile_food_kinds,
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


def test_pick_species_uses_worked_out_food(tmp_path: Path) -> None:
    """#147.4: a layered home feeds from grows, not its list."""
    world = _world(tmp_path)
    home = _layered(world)
    playable, prey, predators = pick_species(
        {"fox": 5.0, "mountain_hare": 9.0, "vole": 20.0}, world, home
    )
    assert playable == ["mountain_hare", "fox"]
    assert predators == ["fox"]
    assert prey == ["vole", "mountain_hare"]
    bare = dict(home, ground_cover={"grass": 0.0, "plants": []})
    assert pick_species({"fox": 5.0}, world, bare) == ([], [], [])


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
    grids = _grids(tmp_path, _boreal_3x3(413, 478))
    answer = {
        "place": "Boreal forest in southern Finland",
        "parts": [
            {"kind": "forest", "species": ["fox", "mountain_hare", "vole", "lion"]}
        ],
        "plants": [],
        "trees": [],
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
        "n_413_478",
        grids,  # type: ignore[arg-type]
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
        "n_413_478",
        grids,  # type: ignore[arg-type]
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
    grids = _grids(tmp_path, _boreal_3x3(413, 478))
    made, blocked = make_tile(
        "n_413_478",
        grids,  # type: ignore[arg-type]
        world,
        builder_root=tmp_path,
        species={
            "place": "Boreal forest in southern Finland",
            "parts": [{"kind": "forest", "species": ["lion"]}],
            "plants": [],
            "trees": [],
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
    path = write_group(tmp_path / "groups", "n_413_478", None, blocked)
    data = json.loads(path.read_text())
    assert data["blocked"]["n_413_478"]["wanted"][0]["what"] == "lion"


def _region(world: Path, region_id: str, cells: list[list[int]]) -> None:
    folder = world / "regions"
    folder.mkdir(exist_ok=True)
    (folder / f"{region_id}.json").write_text(
        json.dumps({"id": region_id, "grid": {"grid": "ease2_n25", "tiles": cells}})
    )


def test_place_cells_read_region_grids(tmp_path: Path) -> None:
    """#147.1: place cells map to their region and part."""
    world = _world(tmp_path)
    assert place_cells(world) == {}
    _region(world, "fi_test", [[413, 478], [414, 478], [413, 479], [414, 479]])
    _region(world, "fi_pool", [[418, 468]])
    assert place_cells(world) == {
        "n_413_478": ("fi_test", [0, 0]),
        "n_414_478": ("fi_test", [1, 0]),
        "n_413_479": ("fi_test", [0, 1]),
        "n_414_479": ("fi_test", [1, 1]),
        "n_418_468": ("fi_pool", [0, 0]),
    }


def test_place_cells_refuse_bad_grids(tmp_path: Path) -> None:
    """#147.1: a malformed or overlapping grid fails loudly."""
    world = _world(tmp_path)
    folder = world / "regions"
    folder.mkdir()
    (folder / "fi_bad.json").write_text(
        json.dumps({"id": "fi_bad", "grid": {"grid": "ease2_n25", "tiles": "x"}})
    )
    with pytest.raises(ValueError, match="malformed grid section"):
        place_cells(world)
    (folder / "fi_bad.json").write_text(
        json.dumps(
            {"id": "fi_bad", "grid": {"grid": "ease2_n25", "tiles": [[413, 478]]}}
        )
    )
    _region(world, "fi_test", [[413, 478]])
    with pytest.raises(ValueError, match="two place regions"):
        place_cells(world)


def _baked_grids() -> dict[str, HabitatGrid]:
    path = (
        Path(__file__).resolve().parent.parent
        / "data"
        / "habitat-grid"
        / "ease2_n25.json.gz"
    )
    if not path.is_file():
        pytest.skip("needs the baked habitat grid")
    return {"ease2_n25": HabitatGrid.load(path, "ease2_n25")}  # type: ignore[dict-item]


def test_make_tile_returns_a_place_entry(tmp_path: Path) -> None:
    """#147.1: n_414_479 is fi_nuuksio part [1, 1], edges agreeing east."""
    world = _world(tmp_path)
    _region(world, "fi_nuuksio", [[413, 478], [414, 478], [413, 479], [414, 479]])
    grids = _baked_grids()
    made, blocked = make_tile(
        "n_414_479",
        grids,  # type: ignore[arg-type]
        world,
        builder_root=tmp_path,
    )
    assert blocked is None
    assert made is not None
    validate("tile", made)
    assert made["kind"] == "place"
    assert made["place"] == "fi_nuuksio"
    assert made["part"] == [1, 1]
    for key in ("habitat", "home", "biome", "seed", "water", "species"):
        assert key not in made
    assert made["requires"] == {"homes": [], "animals": []}
    east = tile_edges(
        "n_415_479",
        grids,  # type: ignore[arg-type]
    )["w"]
    assert made["edges"]["e"] == east


def test_place_entry_replaces_a_published_code_tile(tmp_path: Path) -> None:
    """#147.1: the place entry wins over a stale code tile."""
    world = _world(tmp_path)
    _region(world, "fi_test", [[413, 478]])
    grids = _grids(tmp_path, _boreal_3x3(413, 478))
    out = tmp_path / "out"
    write_group(
        out,
        "n_413_478",
        {"id": "n_413_478", "kind": "code", "version": 1},
        None,
    )
    made, blocked = make_tile(
        "n_413_478",
        grids,  # type: ignore[arg-type]
        world,
        builder_root=tmp_path,
    )
    assert blocked is None and made is not None
    path = write_group(out, "n_413_478", made, None)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["tiles"]["n_413_478"]["kind"] == "place"
    assert data["tiles"]["n_413_478"]["place"] == "fi_test"


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
    assert made["biome"] == "boreal_forest"
    assert made["requires"]["biomes"] == made["requires"]["homes"]
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
    """Spec 04 roles by Nuuksio: every kept playable file plays.

    n_413_478 itself is a place cell now (#147.1); the roles
    are tried on the forest cell west of it.
    """
    world = _real_world()
    if world is None:
        pytest.skip("needs the committed grid and a real world checkout")
    grid = Path(__file__).resolve().parent.parent / "data" / "habitat-grid"
    grids = {"ease2_n25": HabitatGrid.load(grid / "ease2_n25.json.gz", "ease2_n25")}
    made, blocked = make_tile(
        "n_412_478",
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
    assert data["tiles"]["n_413_478"]["version"] == 1
    made["species"]["playable"].append("moose")
    write_group(out, "n_413_478", made, None)
    again = json.loads(first.read_text())
    assert again["version"] == 2
    assert again["tiles"]["n_413_478"]["version"] == 2
    second = write_group(
        out, "n_414_478", None, {"reason": "no_home", "habitat": "tundra"}
    )
    assert second == first
    data = json.loads(second.read_text())
    assert data["version"] == 3
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
            "plants": [],
            "trees": [],
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
    assert "plants" not in facts


def _live_grids() -> dict[str, HabitatGrid]:
    from builder.schemas import ROOT

    grid_dir = ROOT / "data" / "habitat-grid"
    return {
        name: HabitatGrid.load(grid_dir / f"{name}.json.gz", name)
        for name in ("ease2_n25", "ease2_s25")
    }


def test_helsinki_mask_has_land_north_and_sea_south() -> None:
    """#142: n_415_479's mask from the re-baked grid, about 12 ones."""
    from builder.grid import parse_tile

    grids = _live_grids()
    grid, col, row = parse_tile("n_415_479")
    mask = grids[grid].coast(col, row)
    if mask is None:
        pytest.skip("needs the re-baked grid with coast masks")
    rows = [mask[r * 5 : (r + 1) * 5] for r in range(5)]
    assert rows[0].count("1") >= 4, rows
    assert rows[4] == "00000", rows
    assert 10 <= mask.count("1") <= 14, rows


def test_neighbour_masks_agree_along_the_shared_side() -> None:
    """#142: touching mask sides turn within a sample of each other."""
    from builder.grid import parse_tile

    grids = _live_grids()
    grid, col, row = parse_tile("n_415_479")
    mine = grids[grid].coast(col, row)
    west = grids[grid].coast(col - 1, row)
    north = grids[grid].coast(col, row - 1)
    if mine is None or west is None or north is None:
        pytest.skip("needs the re-baked grid with coast masks")

    def turn(bits: list[str]) -> int:
        return bits.index("0")

    mine_west = [mine[r * 5] for r in range(5)]
    west_east = [west[r * 5 + 4] for r in range(5)]
    assert abs(turn(mine_west) - turn(west_east)) <= 1
    mine_north = list(mine[:5])
    north_south = list(north[20:25])
    matching = sum(1 for a, b in zip(mine_north, north_south) if a == b)
    assert matching >= 4


def test_helsinki_tile_carries_its_coast() -> None:
    """#142: the remade n_415_479 writes `coast` from the mask."""
    from builder.schemas import ROOT

    world = ROOT.parent / "animundo-world"
    if not (world / "homes" / "sea_717.json").is_file():
        pytest.skip("needs world d009df2 with the reviewed sea home")
    if not (world / "animals" / "herring_gull.json").is_file():
        pytest.skip("needs world d009df2 with the reviewed gull")
    grids = _live_grids()
    from builder.grid import parse_tile

    grid, col, row = parse_tile("n_415_479")
    mask = grids[grid].coast(col, row)
    if mask is None:
        pytest.skip("needs the re-baked grid with coast masks")
    made, blocked = make_tile(
        "n_415_479",
        grids,  # type: ignore[arg-type]
        world,
        presence={"herring_gull": 30.0},
    )
    assert blocked is None
    assert made is not None
    assert made["coast"] == [mask[r * 5 : (r + 1) * 5] for r in range(5)]
    assert made["coast"][0].count("1") >= 4
    assert made["coast"][4] == "00000"


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


def test_make_tile_writes_coast_for_coastal_cells(tmp_path: Path) -> None:
    """#142: a cell part land part sea carries its 5 mask rows."""
    world = _world(tmp_path)
    (world / "animals" / "herring.json").write_text(
        json.dumps({"playable": True, "tags": ["fish"], "diet": ["water_bugs"]})
    )
    cell = [651, 1.0, None, 0.0, 4, 0.48, 0.0, 0.0, "1" * 12 + "0" * 13]
    cells = {f"{c},{r}": list(cell) for r in (499, 500, 501) for c in (99, 100, 101)}
    grids = _grids(tmp_path, cells)
    made, blocked = make_tile(
        "n_100_500",
        grids,
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
    grids = _grids(tmp_path, _sea_3x3(100, 500))
    made, blocked = make_tile(
        "n_100_500",
        grids,  # type: ignore[arg-type]
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
    cells = {f"{c},{r}": list(cell) for r in (477, 478, 479) for c in (412, 413, 414)}
    grids = _grids(tmp_path, cells)
    made, blocked = make_tile(
        "n_413_478",
        grids,  # type: ignore[arg-type]
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
    assert made["blend"] == {"home": "taiga", "biome": "taiga", "share": 0.15}
    assert made["requires"]["homes"] == ["boreal_forest", "taiga"]
    assert made["requires"]["biomes"] == ["boreal_forest", "taiga"]


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


def test_remade_nuuksio_tile_keeps_squirrel_and_bee(tmp_path: Path) -> None:
    """#152: a code re-make of n_414_480 keeps the squirrel and the bee."""
    from builder.schemas import ROOT

    world = ROOT.parent / "animundo-world"
    if not (world / "food" / "rules.json").is_file():
        pytest.skip("needs a layered world with food rules")
    if not (world / "animals" / "bee.json").is_file():
        pytest.skip("needs world main with the bee filed")
    grids = _live_grids()
    answer = json.loads(
        (ROOT / "tests" / "fixtures" / "tiles" / "n_414_480-survey.json").read_text()
    )
    made, blocked = make_tile(
        "n_414_480",
        grids,  # type: ignore[arg-type]
        world,
        builder_root=tmp_path,
        species=answer,
    )
    assert blocked is None
    assert made is not None
    assert "squirrel" in made["species"]["playable"]
    assert "bee" in made["species"]["playable"]
