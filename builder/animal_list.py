"""The static animal list for animal-first builds.

animundo-world owns `animal-list.json` (the animals a grown-up can
ask for, with GBIF keys, starting models and habitats); the web app
vendors a copy. Both sides resolve the animal's home the same way:
its first habitat with a built home.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .schemas import ROOT

LIST_NAME = "animal-list.json"


def find_list(world: Path | None) -> Path:
    """The animal list: the world's, else the web app's vendored copy."""
    if world is not None and (world / LIST_NAME).is_file():
        return world / LIST_NAME
    vendored = ROOT / "web" / "build" / LIST_NAME
    if vendored.is_file():
        return vendored
    raise ValueError("no animal-list.json in the world or the web app")


def load_list(path: Path) -> dict[str, Any]:
    """Parse the list; KeyError later beats a half list now."""
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("homes", "not_yet", "animals"):
        if key not in data:
            raise ValueError(f"{path} is not an animal list (no {key})")
    return data


def find_entry(entries: list[dict[str, Any]], species_key: int) -> dict[str, Any]:
    """The list entry with this GBIF key, or a ValueError."""
    for entry in entries:
        if entry.get("gbif_key") == species_key:
            return entry
    raise ValueError(f"species key {species_key} is not on the animal list")


def find_by_id(entries: list[dict[str, Any]], animal_id: str) -> dict[str, Any]:
    """The list entry with this id, or a ValueError."""
    for entry in entries:
        if entry.get("id") == animal_id:
            return entry
    raise ValueError(f"{animal_id!r} is not on the animal list")


def home_for(entry: dict[str, Any], homes: list[str]) -> str:
    """The animal's first habitat with a built home, or a ValueError."""
    for habitat in entry.get("habitats", []):
        if habitat in homes:
            return habitat
    raise ValueError(f"no built home for {entry.get('latin', 'this animal')} yet")
