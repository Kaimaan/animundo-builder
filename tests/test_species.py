"""Tile species from Muse Spark: checked answers, cached per tile. No key."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from builder.grid import center, parse_tile
from builder.habitats import HabitatGrid
from builder.species import (
    Source,
    check_answer,
    check_place,
    check_survey,
    fetch_species,
    list_animals,
)
from builder.tiles import available_homes, home_for_cell, make_tile, tile_facts

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "species"
SPOTS = ("sahel", "taymyr-tundra", "amazon-interior", "everglades", "kakadu")
_GREENS = [{"id": "bilberry", "name": "Bilberry", "latin": "Vaccinium myrtillus"}]
_TREES: list[dict[str, str]] = []


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
    (world / "animal-list.json").write_text(
        json.dumps(
            {
                "homes": ["boreal_forest"],
                "not_yet": {},
                "animals": [
                    {
                        "id": "fox",
                        "name": "Red fox",
                        "latin": "Vulpes vulpes",
                        "gbif_key": 5219243,
                        "habitats": ["boreal_forest"],
                    },
                    {
                        "id": "mountain_hare",
                        "name": "Mountain hare",
                        "latin": "Lepus timidus",
                        "gbif_key": 2436756,
                        "habitats": ["boreal_forest"],
                    },
                    {
                        "id": "vole",
                        "name": "Vole",
                        "latin": "Microtus agrestis",
                        "gbif_key": 1,
                        "habitats": ["boreal_forest"],
                    },
                ],
            }
        )
    )
    return world


def _grids(tmp_path: Path) -> dict[str, HabitatGrid]:
    import gzip

    cell = [717, 1.0, None, 0.0, 6, 1.0, 0.0, 80.0]
    cells = {f"{c},{r}": list(cell) for r in (477, 478, 479) for c in (412, 413, 414)}
    path = tmp_path / "ease2_n25.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump({"grid": "ease2_n25", "cells": cells}, handle)
    return {"ease2_n25": HabitatGrid.load(path, "ease2_n25")}  # type: ignore[dict-item]


def _response(answer: dict[str, Any]) -> Any:
    """A stub chat answer with the shape `ask` reads."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(answer)))]
    )


class _StubClient:
    """A scripted model: answers in order, counting its calls."""

    def __init__(self, answers: list[dict[str, Any]]) -> None:
        self.answers = answers
        self.calls = 0

    def chat(self, **kwargs: Any) -> Any:
        self.calls += 1
        return _response(self.answers[min(self.calls - 1, len(self.answers) - 1)])


def _facts() -> dict[str, Any]:
    """Boreal facts for the stub calls: full land, no lake, no sea."""
    from builder.species import survey_facts

    return survey_facts(
        habitat="boreal_forest",
        eco1=717,
        share1=1.0,
        eco2=None,
        share2=0.0,
        land=1.0,
        water=0.0,
        elevation_m=80.0,
        level=9,
        edge_lo=8,
        edge_hi=11,
        home="boreal_forest",
        adapt=False,
    )


def _real_ids() -> list[str] | None:
    path = (
        Path(__file__).resolve().parent.parent.parent
        / "animundo-world"
        / "animal-list.json"
    )
    if not path.is_file():
        return None
    return [a["id"] for a in json.loads(path.read_text())["animals"]]


def test_lab_recordings_decode_and_match_their_cells() -> None:
    """#141.1: the five lab surveys decode and read as their own cells."""
    ids = _real_ids()
    if ids is None:
        pytest.skip("needs an animundo-world checkout for the animal list")
    world = Path(__file__).resolve().parent.parent.parent / "animundo-world"
    from builder.species import list_library

    animals = list_animals(world)
    greens = [p["id"] for p in list_library(world, "plants")]
    trees = [t["id"] for t in list_library(world, "trees")]
    homes = available_homes(world)
    grid_dir = Path(__file__).resolve().parent.parent / "data" / "habitat-grid"
    grids = {
        name: HabitatGrid.load(grid_dir / f"{name}.json.gz", name)
        for name in ("ease2_n25", "ease2_s25")
    }
    for spot in SPOTS:
        fix = json.loads((FIXTURES / f"{spot}.json").read_text())
        answer = check_answer(
            {
                "place": fix["place"],
                "parts": fix["parts"],
                "plants": [],
                "trees": [],
                "name_en": fix["name_en"],
                "name_fi": fix["name_fi"],
            },
            ids,
            greens,
            trees,
        )
        assert answer["present"] == [s for p in fix["parts"] for s in p["species"]], (
            spot
        )
        assert answer["plants"] == []
        assert answer["name_en"] == fix["name_en"]
        assert answer["name_fi"] == fix["name_fi"]
        grid, col, row = parse_tile(fix["tile"])
        habitat = grids[grid].habitat(col, row)
        lat, lon = center(fix["tile"])
        assert check_place(answer["place"], habitat, lat, lon) is None, spot
        eco1 = grids[grid].cell_data(col, row)[0]
        home = home_for_cell(eco1, habitat, homes)
        if home is None:
            from builder.plan import nearest_home

            home = nearest_home(fix["tile"], grids, homes, habitat)  # type: ignore[arg-type]
            adapt = True
        else:
            adapt = False
        facts = tile_facts(fix["tile"], grids, world, home, adapt)  # type: ignore[arg-type]
        assert check_survey(answer, habitat, lat, lon, facts, animals) is None, spot


def test_place_mismatch_retries_once_then_blocks(tmp_path: Path) -> None:
    """#131.2: a twice-misread cell blocks with `species_place`."""
    world = _world(tmp_path)
    grids = _grids(tmp_path)
    client = _StubClient(
        [
            {
                "place": "Open ocean far from land",
                "parts": [{"kind": "sea", "species": ["fox"]}],
                "plants": [],
                "trees": [],
                "name_en": "Open ocean",
                "name_fi": "Avomeri",
            },
            {
                "place": "Open ocean far from land",
                "parts": [{"kind": "sea", "species": ["fox"]}],
                "plants": [],
                "trees": [],
                "name_en": "Open ocean",
                "name_fi": "Avomeri",
            },
        ]
    )
    source = Source(client=client, cache_dir=tmp_path / "cache")
    made, blocked = make_tile(
        "n_413_478",
        grids,  # type: ignore[arg-type]
        world,
        builder_root=tmp_path,
        species_source=source,
    )
    assert made is None
    assert blocked == {"reason": "species_place", "habitat": "boreal_forest"}
    assert client.calls == 2


def test_place_mismatch_passes_on_retry(tmp_path: Path) -> None:
    """#131.2: a retry that reads right makes the tile."""
    world = _world(tmp_path)
    grids = _grids(tmp_path)
    client = _StubClient(
        [
            {
                "place": "Open ocean far from land",
                "parts": [{"kind": "sea", "species": ["fox"]}],
                "plants": [],
                "trees": [],
                "name_en": "Open ocean",
                "name_fi": "Avomeri",
            },
            {
                "place": "Boreal forest in southern Finland",
                "parts": [{"kind": "forest", "species": ["fox", "vole"]}],
                "plants": [],
                "trees": [],
                "name_en": "Nuuksio forest",
                "name_fi": "Nuuksion metsä",
            },
        ]
    )
    source = Source(client=client, cache_dir=tmp_path / "cache")
    made, blocked = make_tile(
        "n_413_478",
        grids,  # type: ignore[arg-type]
        world,
        builder_root=tmp_path,
        species_source=source,
    )
    assert blocked is None
    assert made is not None
    assert client.calls == 2
    assert made["species"]["playable"] == ["fox"]


def test_unknown_species_id_is_refused(tmp_path: Path) -> None:
    """#131.6: an id off the list fails the schema, live or recorded."""
    world = _world(tmp_path)
    grids = _grids(tmp_path)
    with pytest.raises(ValueError, match="refused"):
        make_tile(
            "n_413_478",
            grids,  # type: ignore[arg-type]
            world,
            builder_root=tmp_path,
            species={
                "place": "Boreal forest in southern Finland",
                "parts": [{"kind": "forest", "species": ["fox", "dragon"]}],
                "plants": [],
                "trees": [],
                "name_en": "Nuuksio forest",
                "name_fi": "Nuuksion metsä",
            },
        )


def test_species_answer_cached_per_tile(tmp_path: Path) -> None:
    """#131.4: a re-run calls again only when the animal list changes."""
    from builder.grid import center as tile_center

    animals = [
        {"id": "fox", "name": "Red fox", "latin": "Vulpes vulpes"},
        {"id": "vole", "name": "Vole", "latin": "Microtus agrestis"},
    ]
    client = _StubClient(
        [
            {
                "place": "Boreal forest in southern Finland",
                "parts": [{"kind": "forest", "species": ["fox", "vole"]}],
                "plants": [],
                "trees": [],
                "name_en": "Nuuksio forest",
                "name_fi": "Nuuksion metsä",
            }
        ]
    )
    source = Source(client=client, cache_dir=tmp_path / "cache")
    lat, lon = tile_center("n_413_478")
    first = fetch_species(
        "n_413_478",
        animals,
        _GREENS,
        _TREES,
        "boreal_forest",
        lat,
        lon,
        source,
        "fp1",
        "pp1",
        _facts(),
    )
    assert client.calls == 1
    assert first["name_en"] == "Nuuksio forest"
    second = fetch_species(
        "n_413_478",
        animals,
        _GREENS,
        _TREES,
        "boreal_forest",
        lat,
        lon,
        source,
        "fp1",
        "pp1",
        _facts(),
    )
    assert client.calls == 1
    assert second == first
    assert second["name_fi"] == "Nuuksion metsä"
    fetch_species(
        "n_413_478",
        animals,
        _GREENS,
        _TREES,
        "boreal_forest",
        lat,
        lon,
        source,
        "fp2",
        "pp1",
        _facts(),
    )
    assert client.calls == 2


def test_list_fingerprint_ignores_library_status() -> None:
    """#143.2: shipping an animal keeps the survey cache key."""
    from builder.species import list_fingerprint

    base = [
        {"id": "fox", "name": "Red fox", "latin": "Vulpes vulpes"},
        {"id": "lynx", "name": "Eurasian lynx", "latin": "Lynx lynx"},
    ]
    shipped = [dict(base[0]), {**base[1], "in_library": "lynx"}]
    assert list_fingerprint(shipped) == list_fingerprint(base)


def test_corrupt_species_cache_calls_again(tmp_path: Path) -> None:
    """#131.4: a broken cache file never poisons a tile."""
    from builder.grid import center as tile_center

    animals = [{"id": "fox", "name": "Red fox", "latin": "Vulpes vulpes"}]
    client = _StubClient(
        [
            {
                "place": "Boreal forest in southern Finland",
                "parts": [{"kind": "forest", "species": ["fox"]}],
                "plants": [],
                "trees": [],
                "name_en": "Nuuksio forest",
                "name_fi": "Nuuksion metsä",
            }
        ]
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "n_413_478.json").write_text("not json")
    source = Source(client=client, cache_dir=cache)
    lat, lon = tile_center("n_413_478")
    answer = fetch_species(
        "n_413_478",
        animals,
        _GREENS,
        _TREES,
        "boreal_forest",
        lat,
        lon,
        source,
        "fp1",
        "pp1",
        _facts(),
    )
    assert client.calls == 1
    assert answer["present"] == ["fox"]


def test_species_without_key_or_cache_fails_clearly(tmp_path: Path) -> None:
    """The keyless path makes cached tiles and says so on a miss."""
    from builder.grid import center as tile_center

    animals = [{"id": "fox", "name": "Red fox", "latin": "Vulpes vulpes"}]
    source = Source(client=None, cache_dir=tmp_path / "cache")
    lat, lon = tile_center("n_413_478")
    with pytest.raises(ValueError, match="MODEL_API_KEY is not set"):
        fetch_species(
            "n_413_478",
            animals,
            _GREENS,
            _TREES,
            "boreal_forest",
            lat,
            lon,
            source,
            "fp1",
            "pp1",
            _facts(),
        )


def test_survey_prompt_carries_our_facts() -> None:
    """#141.1: the call states our facts before asking."""
    from builder.species import survey_facts, user_message

    facts = survey_facts(
        habitat="sea",
        eco1=717,
        share1=0.8,
        eco2=812,
        share2=0.2,
        land=0.48,
        water=0.0,
        elevation_m=-20.0,
        level=5,
        edge_lo=4,
        edge_hi=9,
        home="sea_717",
        adapt=False,
    )
    text = user_message(
        60.15,
        24.91,
        [{"id": "herring_gull", "name": "Herring gull", "latin": "Larus argentatus"}],
        [{"id": "bladderwrack", "name": "Bladderwrack", "latin": "Fucus vesiculosus"}],
        [],
        facts,
    )
    assert "habitat sea, ecoregion 717 over 0.8 of it" in text
    assert "ecoregion 812 over 0.2" in text
    assert "Land on 12 of 25 sample points" in text
    assert "sea on 13" in text
    assert "level 5, edges 4 to 9 blocks" in text
    assert "builds from the home sea_717" in text
    assert "- herring_gull: Herring gull (Larus argentatus)" in text
    assert "- bladderwrack: Bladderwrack (Fucus vesiculosus)" in text
    assert "with how many of each grow" in text


def test_survey_refuses_sea_part_without_sea() -> None:
    """#141.1: a sea part needs sea in the square."""
    from builder.species import check_survey

    animals = [{"id": "fox", "name": "Red fox", "latin": "Vulpes vulpes"}]
    answer = {
        "place": "Boreal forest in southern Finland",
        "parts": [{"kind": "sea", "species": ["fox"]}],
        "plants": [],
        "trees": [],
        "present": ["fox"],
        "name_en": "Nuuksio forest",
        "name_fi": "Nuuksion metsä",
    }
    assert (
        check_survey(answer, "boreal_forest", 60.55, 24.30, _facts(), animals)
        == "a sea part with no sea in the square"
    )
    wet = {**_facts(), "sea": 0.5, "land": 0.5}
    at_sea = {**answer, "place": "Open sea west of Helsinki"}
    assert check_survey(at_sea, "sea", 60.03, 24.34, wet, animals) is None


def test_survey_refuses_lake_part_without_lake() -> None:
    """#141.1: a lake part needs lake water in the square."""
    from builder.species import check_survey

    animals = [{"id": "fox", "name": "Red fox", "latin": "Vulpes vulpes"}]
    answer = {
        "place": "Boreal forest and lakes in southern Finland",
        "parts": [{"kind": "lake", "species": ["fox"]}],
        "plants": [],
        "trees": [],
        "present": ["fox"],
        "name_en": "Nuuksio forest",
        "name_fi": "Nuuksion metsä",
    }
    assert (
        check_survey(answer, "boreal_forest", 60.55, 24.30, _facts(), animals)
        == "a lake part with no lake in the square"
    )


def test_survey_refuses_name_naming_an_absent_animal() -> None:
    """#141.1: no "Seal Shores" when the tile has no seal."""
    from builder.species import check_survey

    animals = [
        {"id": "grey_seal", "name": "Grey seal", "latin": "Halichoerus grypus"},
        {"id": "fox", "name": "Red fox", "latin": "Vulpes vulpes"},
    ]
    answer = {
        "place": "Boreal forest in southern Finland",
        "parts": [{"kind": "forest", "species": ["fox"]}],
        "plants": [],
        "trees": [],
        "present": ["fox"],
        "name_en": "Seal and Swan Shores",
        "name_fi": "Hylje- ja joutsenrannat",
    }
    assert (
        check_survey(answer, "boreal_forest", 60.55, 24.30, _facts(), animals)
        == "names seal, which is not on the tile"
    )
    answer["present"] = ["grey_seal", "fox"]
    answer["parts"] = [{"kind": "forest", "species": ["grey_seal", "fox"]}]
    assert (
        check_survey(answer, "boreal_forest", 60.55, 24.30, _facts(), animals) is None
    )


def test_survey_name_may_paint_but_not_name_a_missing_kind() -> None:
    """#141.1: "Blue Sea" paints; "Trout Bay" without trout is refused."""
    from builder.species import check_name

    animals = [
        {"id": "sea_trout", "name": "Sea trout", "latin": "Salmo trutta"},
        {"id": "red_fox", "name": "Red fox", "latin": "Vulpes vulpes"},
    ]
    assert check_name("Blue Sea Square", [], animals) is None
    assert check_name("Red Sunset Forest", [], animals) is None
    assert (
        check_name("Trout Bay", [], animals) == "names trout, which is not on the tile"
    )
    assert check_name("Trout Bay", ["sea_trout"], animals) is None


def test_empty_parts_are_refused() -> None:
    """#141.1: a survey with no parts is no survey."""
    import pytest

    ids = ["fox"]
    with pytest.raises(ValueError, match="no parts"):
        check_answer(
            {
                "place": "Boreal forest in southern Finland",
                "parts": [],
                "plants": [],
                "trees": [],
                "name_en": "Nuuksio forest",
                "name_fi": "Nuuksion metsä",
            },
            ids,
            ["bilberry"],
            [],
        )


def test_survey_part_mismatch_blocks_after_retry(tmp_path: Path) -> None:
    """#141.1: a twice-waterless sea part blocks with `species_place`."""
    world = _world(tmp_path)
    grids = _grids(tmp_path)
    sea = {
        "place": "Boreal forest by the sea in southern Finland",
        "parts": [{"kind": "sea", "species": ["fox"]}],
        "plants": [],
        "trees": [],
        "name_en": "Nuuksio forest",
        "name_fi": "Nuuksion metsä",
    }
    client = _StubClient([sea, dict(sea)])
    source = Source(client=client, cache_dir=tmp_path / "cache")
    made, blocked = make_tile(
        "n_413_478",
        grids,  # type: ignore[arg-type]
        world,
        builder_root=tmp_path,
        species_source=source,
    )
    assert made is None
    assert blocked == {"reason": "species_place", "habitat": "boreal_forest"}
    assert client.calls == 2


def test_retry_carries_the_refusal_back(tmp_path: Path) -> None:
    """#141.1: the retry is told what the first answer got wrong."""
    from builder.grid import center as tile_center

    animals = [{"id": "fox", "name": "Red fox", "latin": "Vulpes vulpes"}]
    prompts: list[str] = []

    class _NotingClient(_StubClient):
        def chat(self, **kwargs: Any) -> Any:
            prompts.append(kwargs["messages"][1]["content"])
            return super().chat(**kwargs)

    client = _NotingClient(
        [
            {
                "place": "Open ocean far from land",
                "parts": [{"kind": "sea", "species": ["fox"]}],
                "plants": [],
                "trees": [],
                "name_en": "Open ocean",
                "name_fi": "Avomeri",
            },
            {
                "place": "Boreal forest in southern Finland",
                "parts": [{"kind": "forest", "species": ["fox"]}],
                "plants": [],
                "trees": [],
                "name_en": "Nuuksio forest",
                "name_fi": "Nuuksion metsä",
            },
        ]
    )
    source = Source(client=client, cache_dir=tmp_path / "cache")
    lat, lon = tile_center("n_413_478")
    fetch_species(
        "n_413_478",
        animals,
        _GREENS,
        _TREES,
        "boreal_forest",
        lat,
        lon,
        source,
        "fp1",
        "pp1",
        _facts(),
    )
    assert client.calls == 2
    assert "was refused" not in prompts[0]
    assert "Your last answer was refused: a land cell answered as sea." in prompts[1]


def test_species_recaches_when_facts_change(tmp_path: Path) -> None:
    """#141.1: the cache keys on the tile, the list and the facts."""
    from builder.grid import center as tile_center

    animals = [
        {"id": "fox", "name": "Red fox", "latin": "Vulpes vulpes"},
        {"id": "vole", "name": "Vole", "latin": "Microtus agrestis"},
    ]
    client = _StubClient(
        [
            {
                "place": "Boreal forest in southern Finland",
                "parts": [{"kind": "forest", "species": ["fox", "vole"]}],
                "plants": [],
                "trees": [],
                "name_en": "Nuuksio forest",
                "name_fi": "Nuuksion metsä",
            }
        ]
    )
    source = Source(client=client, cache_dir=tmp_path / "cache")
    lat, lon = tile_center("n_413_478")
    fetch_species(
        "n_413_478",
        animals,
        _GREENS,
        _TREES,
        "boreal_forest",
        lat,
        lon,
        source,
        "fp1",
        "pp1",
        _facts(),
    )
    assert client.calls == 1
    fetch_species(
        "n_413_478",
        animals,
        _GREENS,
        _TREES,
        "boreal_forest",
        lat,
        lon,
        source,
        "fp1",
        "pp1",
        _facts(),
    )
    assert client.calls == 1
    moved = {**_facts(), "level": 12, "edge_hi": 14}
    fetch_species(
        "n_413_478",
        animals,
        _GREENS,
        _TREES,
        "boreal_forest",
        lat,
        lon,
        source,
        "fp1",
        "pp1",
        moved,
    )
    assert client.calls == 2
    # A changed plants fingerprint alone reuses the cache while
    # every cached pick is still listed (#153.4).
    fetch_species(
        "n_413_478",
        animals,
        _GREENS,
        _TREES,
        "boreal_forest",
        lat,
        lon,
        source,
        "fp1",
        "pp2",
        moved,
    )
    assert client.calls == 2


def test_species_recaches_when_a_cached_pick_leaves_the_library(
    tmp_path: Path,
) -> None:
    """#153.4: cached picks survive library growth, not removal."""
    from builder.grid import center as tile_center

    animals = [
        {"id": "fox", "name": "Red fox", "latin": "Vulpes vulpes"},
        {"id": "vole", "name": "Vole", "latin": "Microtus agrestis"},
    ]
    answer = {
        "place": "Boreal forest in southern Finland",
        "parts": [{"kind": "forest", "species": ["fox", "vole"]}],
        "plants": [{"id": "bilberry", "count": 10}],
        "trees": [],
        "name_en": "Nuuksio forest",
        "name_fi": "Nuuksion metsä",
    }
    client = _StubClient([answer, {**answer, "plants": []}])
    source = Source(client=client, cache_dir=tmp_path / "cache")
    lat, lon = tile_center("n_413_478")
    fetch_species(
        "n_413_478",
        animals,
        _GREENS,
        _TREES,
        "boreal_forest",
        lat,
        lon,
        source,
        "fp1",
        "pp1",
        _facts(),
    )
    assert client.calls == 1
    grown = [
        *_GREENS,
        {"id": "cloudberry", "name": "Cloudberry", "latin": "Rubus chamaemorus"},
    ]
    fetch_species(
        "n_413_478",
        animals,
        grown,
        _TREES,
        "boreal_forest",
        lat,
        lon,
        source,
        "fp1",
        "pp2",
        _facts(),
    )
    assert client.calls == 1
    fetch_species(
        "n_413_478",
        animals,
        [],
        _TREES,
        "boreal_forest",
        lat,
        lon,
        source,
        "fp1",
        "pp3",
        _facts(),
    )
    assert client.calls == 2


def test_model_order_ranks_species(tmp_path: Path) -> None:
    """#131.3: with no counts, the model's order ranks, then the id."""
    world = _world(tmp_path)
    grids = _grids(tmp_path)
    made, blocked = make_tile(
        "n_413_478",
        grids,  # type: ignore[arg-type]
        world,
        builder_root=tmp_path,
        species={
            "place": "Boreal forest in southern Finland",
            "parts": [{"kind": "forest", "species": ["mountain_hare", "fox", "vole"]}],
            "plants": [],
            "trees": [],
            "name_en": "Nuuksio forest",
            "name_fi": "Nuuksion metsä",
        },
    )
    assert blocked is None
    assert made is not None
    assert made["species"]["playable"] == ["mountain_hare", "fox"]
    assert made["species"]["prey"] == ["mountain_hare", "vole"]
    assert made["sources"]["species"].startswith("muse-spark-1.3:")
