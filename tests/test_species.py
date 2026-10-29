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
    fetch_species,
)
from builder.tiles import make_tile

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "species"
SPOTS = ("sahel", "taymyr-tundra", "amazon-interior", "everglades", "kakadu")


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
    """#131.6: the five lab answers decode and read as their own cells."""
    ids = _real_ids()
    if ids is None:
        pytest.skip("needs an animundo-world checkout for the animal list")
    grid_dir = Path(__file__).resolve().parent.parent / "data" / "habitat-grid"
    grids = {
        name: HabitatGrid.load(grid_dir / f"{name}.json.gz", name)
        for name in ("ease2_n25", "ease2_s25")
    }
    for spot in SPOTS:
        fix = json.loads((FIXTURES / f"{spot}.json").read_text())
        answer = check_answer({"place": fix["place"], "present": fix["present"]}, ids)
        assert answer["present"] == fix["present"]
        grid, col, row = parse_tile(fix["tile"])
        habitat = grids[grid].habitat(col, row)
        lat, lon = center(fix["tile"])
        assert check_place(answer["place"], habitat, lat, lon) is None, spot


def test_place_mismatch_retries_once_then_blocks(tmp_path: Path) -> None:
    """#131.2: a twice-misread cell blocks with `species_place`."""
    world = _world(tmp_path)
    grids = _grids(tmp_path)
    client = _StubClient(
        [
            {"place": "Open ocean far from land", "present": ["fox"]},
            {"place": "Open ocean far from land", "present": ["fox"]},
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
            {"place": "Open ocean far from land", "present": ["fox"]},
            {
                "place": "Boreal forest in southern Finland",
                "present": ["fox", "vole"],
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
                "present": ["fox", "dragon"],
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
        [{"place": "Boreal forest in southern Finland", "present": ["fox", "vole"]}]
    )
    source = Source(client=client, cache_dir=tmp_path / "cache")
    lat, lon = tile_center("n_413_478")
    first = fetch_species(
        "n_413_478", animals, "boreal_forest", lat, lon, source, "fp1"
    )
    assert client.calls == 1
    second = fetch_species(
        "n_413_478", animals, "boreal_forest", lat, lon, source, "fp1"
    )
    assert client.calls == 1
    assert second == first
    fetch_species("n_413_478", animals, "boreal_forest", lat, lon, source, "fp2")
    assert client.calls == 2


def test_corrupt_species_cache_calls_again(tmp_path: Path) -> None:
    """#131.4: a broken cache file never poisons a tile."""
    from builder.grid import center as tile_center

    animals = [{"id": "fox", "name": "Red fox", "latin": "Vulpes vulpes"}]
    client = _StubClient(
        [{"place": "Boreal forest in southern Finland", "present": ["fox"]}]
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "n_413_478.json").write_text("not json")
    source = Source(client=client, cache_dir=cache)
    lat, lon = tile_center("n_413_478")
    answer = fetch_species(
        "n_413_478", animals, "boreal_forest", lat, lon, source, "fp1"
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
        fetch_species("n_413_478", animals, "boreal_forest", lat, lon, source, "fp1")


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
            "present": ["mountain_hare", "fox", "vole"],
        },
    )
    assert blocked is None
    assert made is not None
    assert made["species"]["playable"] == ["mountain_hare", "fox"]
    assert made["species"]["prey"] == ["mountain_hare", "vole"]
    assert made["sources"]["species"].startswith("muse-spark-1.3:")
