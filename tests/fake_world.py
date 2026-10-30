"""Shared fakes: a tiny world and habitat grids. No network."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

_TILE_REGION_STUB = '''
"""Stub of the world's tile converter for enrich tests (#161.1)."""


def place(tile):
    species = tile.get("species", {})
    return {
        "center": tile.get("center", {}),
        "description": "A converted tile.",
        "sources": ["tile_region stub"],
        "animals": {
            k: list(species.get(k) or []) for k in ("playable", "prey", "predators")
        },
        "food": [{"kind": "grass"}, {"kind": "berries"}],
        "trees": {"density": 0.0, "mix": []},
        "ground_cover": {"plants": []},
    }


def tile_in(doc, want=""):
    return doc


def read_biome(bid, extra=None):
    return {"author": "Test"}
'''

_FOOD_STUB = '''
"""Stub of the world's food derive for enrich tests (#161.1)."""


def worked_out(place):
    return list(place.get("food", []))
'''

_LAYERS_STUB = '''
"""Stub of the world's layers for enrich tests (#161.1)."""


def spec(folder, pid):
    return {}


def use_plants(region, plants):
    grown = []
    for side in ("cover", "water"):
        for entry in plants.get(side) or []:
            grown.append({"model": entry.get("id")})
    region["ground_cover"] = {"plants": grown}
'''


def write_converter_tools(world: Path) -> None:
    """Stub tile converter, food and layers tools in a fake world."""
    tools = world / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    (tools / "tile_region.py").write_text(_TILE_REGION_STUB)
    (tools / "food.py").write_text(_FOOD_STUB)
    (tools / "layers.py").write_text(_LAYERS_STUB)


def make_world(tmp_path: Path) -> Path:
    """A world with one home, three filed animals and two unfiled."""
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
    (world / "homes" / "tundra.json").write_text(
        json.dumps(
            {
                "id": "tundra",
                "habitat": "tundra",
                "ecoregions": [780],
                "description": "Treeless northern land.",
                "palette": {"ground_lush": "#5f9445"},
                "trees": {"density": 0.0, "mix": []},
                "lake_life": {"reeds": 0.0, "lilies": 0.0, "pondweed": 0.0},
                "food": [{"kind": "berries"}, {"kind": "grass"}],
            }
        )
    )
    plants = world / "models" / "plants"
    plants.mkdir(parents=True)
    (plants / "bilberry.json").write_text(json.dumps({"id": "bilberry"}))
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
                    {
                        "id": "lion",
                        "gbif_key": 5219404,
                        "habitats": ["boreal_forest"],
                        "start_from": "fox",
                        "can_make": True,
                    },
                ],
            }
        )
    )
    return world


def write_grids(grid_dir: Path, cells: dict[str, list[Any]]) -> Path:
    """Both baked grid files with these northern cells (south empty)."""
    grid_dir.mkdir(parents=True, exist_ok=True)
    for name, content in (
        ("ease2_n25", cells),
        ("ease2_s25", {}),
    ):
        with gzip.open(grid_dir / f"{name}.json.gz", "wt", encoding="utf-8") as handle:
            json.dump({"grid": name, "cells": content}, handle)
    return grid_dir


def boreal_3x3(col: int = 413, row: int = 478) -> dict[str, list[Any]]:
    """A 3 by 3 boreal taiga block for neighbour reads."""
    cell = [717, 1.0, None, 0.0, 6, 1.0, 0.0, 80.0]
    return {
        f"{c},{r}": list(cell)
        for r in (row - 1, row, row + 1)
        for c in (col - 1, col, col + 1)
    }
