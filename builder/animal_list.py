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
    """The first habitat with a built home, else the first habitat.

    A habitat with no home yet is made by the new-home stage (#42);
    `needs_home` says which case this is.
    """
    habitats = entry.get("habitats", [])
    for habitat in habitats:
        if habitat in homes:
            return habitat
    if not habitats:
        raise ValueError(f"no habitat for {entry.get('latin', 'this animal')}")
    return habitats[0]


def needs_home(entry: dict[str, Any], homes: list[str]) -> bool:
    """Whether the animal's home must be made first."""
    return home_for(entry, homes) not in homes


def match_by_name(entries: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    """The list entry with this English name, if any."""
    want = name.strip().lower()
    for entry in entries:
        if str(entry.get("name", "")).strip().lower() == want:
            return entry
    return None


def short_name(name: str) -> str:
    """The plain animal word: the last word, capitalized."""
    word = name.split()[-1] if name.split() else name
    return word[:1].upper() + word[1:]


def habitat_word(home: str) -> str:
    """The plain habitat word: the last segment (`boreal_forest` → `forest`)."""
    return home.split("_")[-1] if home else home
