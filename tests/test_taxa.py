"""Species identity through GBIF, with name fallback. No network."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from builder.taxa import match_entry, resolve

TAXON_STUB = """\
import json, sys
KEYS = {
    "Hydrochoerus hydrochaeris": 5219244,
    "Lepus timidus": 2436756,
    "Microtus agrestis": 2438616,
}
latin = sys.argv[-1]
if latin in KEYS:
    print(json.dumps({"gbif_key": KEYS[latin], "rank": "SPECIES", "name": latin}))
else:
    print(f"{latin}: no exact match in GBIF (NONE)")
    sys.exit(1)
"""

ENTRIES = [
    {
        "id": "capybara",
        "name": "Capybara",
        "latin": "Hydrochoerus hydrochaeris",
        "gbif_key": 5219244,
    },
    {
        "id": "hare",
        "name": "Mountain hare",
        "latin": "Lepus timidus",
        "gbif_key": 2436756,
    },
    {"id": "bank_vole", "name": "Bank vole", "gbif_key": 2438615},
]


def _world(tmp_path: Path, stub: str | None = TAXON_STUB) -> Path:
    world = tmp_path / "world"
    if stub is not None:
        tools = world / "tools"
        tools.mkdir(parents=True)
        (tools / "taxon.py").write_text(stub)
    return world


def test_resolve_returns_the_taxon(tmp_path: Path) -> None:
    taxon = resolve(_world(tmp_path), "Hydrochoerus hydrochaeris")
    assert taxon == {
        "gbif_key": 5219244,
        "rank": "SPECIES",
        "name": "Hydrochoerus hydrochaeris",
    }


def test_resolve_is_none_without_an_exact_match(tmp_path: Path) -> None:
    assert resolve(_world(tmp_path), "Not a species") is None


def test_resolve_is_none_without_the_tool(tmp_path: Path) -> None:
    assert resolve(_world(tmp_path, None), "Lepus timidus") is None


def test_resolve_is_none_on_garbage(tmp_path: Path) -> None:
    assert resolve(_world(tmp_path, "print('junk')\n"), "Lepus timidus") is None


def test_match_entry_prefers_the_species_key(tmp_path: Path) -> None:
    world = _world(tmp_path)
    food = {"name": "water hog", "kind": "prey", "latin": "Hydrochoerus hydrochaeris"}
    match, key = match_entry(ENTRIES, world, food)
    assert match is not None and match["id"] == "capybara"
    assert key == 5219244


def test_match_entry_never_substitutes_a_same_named_species(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    food = {"name": "Bank vole", "kind": "prey", "latin": "Microtus agrestis"}
    match, key = match_entry(ENTRIES, world, food)
    assert match is None
    assert key == 2438616


def test_match_entry_keeps_a_resolved_key_off_the_list(tmp_path: Path) -> None:
    world = _world(tmp_path)
    food = {"name": "blue hare", "kind": "prey", "latin": "Lepus timidus"}
    match, key = match_entry([ENTRIES[0]], world, food)
    assert match is None
    assert key == 2436756


def test_match_entry_falls_back_to_the_name(tmp_path: Path) -> None:
    world = _world(tmp_path)
    food: dict[str, Any] = {"name": "Capybara", "kind": "prey"}
    match, key = match_entry(ENTRIES, world, food)
    assert match is not None and match["id"] == "capybara"
    assert key == 5219244
