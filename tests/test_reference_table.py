"""Reference-table reading and row matching. No network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from builder.reference import impossible_match, pick_row, read_table

README = """\
# Models

## Reference models: where to start

Don't start a new animal from nothing.

| New animal | Start from | Change |
|---|---|---|
| Deer: red deer, roe deer | `moose` | Lighter body and legs |
| Birds of prey, owls | `kestrel` | Size, wing length |
| Bear, lynx | `fox`, scaled up | Heavy body |
| Songbirds | `great_tit`, `chaffinch` | Size, beak |
| **Not possible yet** | | |
| Orcas and great whales (over about 3 m) | none | The water is shallow |
| Dragonflies | none | Two pairs of wings |
| Bats | none | Wings from the arms |

## The spec

Positions are in metres.
"""


def _world(tmp_path: Path) -> Path:
    world = tmp_path / "world"
    (world / "models").mkdir(parents=True)
    (world / "models" / "README.md").write_text(README)
    return world


def test_reads_rows_and_starts(tmp_path: Path) -> None:
    rows = read_table(_world(tmp_path))
    assert [(r["animal"], r["start"]) for r in rows] == [
        ("Deer: red deer, roe deer", "moose"),
        ("Birds of prey, owls", "kestrel"),
        ("Bear, lynx", "fox"),
        ("Songbirds", "great_tit"),
        ("Orcas and great whales (over about 3 m)", ""),
        ("Dragonflies", ""),
        ("Bats", ""),
    ]
    assert rows[0]["change"] == "Lighter body and legs"


def test_missing_table_is_loud(tmp_path: Path) -> None:
    world = tmp_path / "world"
    (world / "models").mkdir(parents=True)
    (world / "models" / "README.md").write_text("# Models\n\nNo table.\n")
    with pytest.raises(ValueError, match="no reference-models table"):
        read_table(world)


def test_impossible_matches_bat_whale_dragonfly(tmp_path: Path) -> None:
    rows = read_table(_world(tmp_path))
    bat = {
        "species": "serotine_bat",
        "latin": "Eptesicus serotinus",
        "description": "A bat hunting over meadows.",
    }
    assert impossible_match(rows, bat)["animal"] == "Bats"
    whale = {
        "species": "blue_whale",
        "latin": "Balaenoptera musculus",
        "description": "A great whale.",
    }
    assert impossible_match(rows, whale)["animal"].startswith("Orcas")
    dragonfly = {
        "species": "migrant_hawker",
        "latin": "Aeshna mixta",
        "description": "A dragonfly over the lake.",
    }
    assert impossible_match(rows, dragonfly)["animal"] == "Dragonflies"


def test_impossible_misses_named_species(tmp_path: Path) -> None:
    rows = read_table(_world(tmp_path))
    deer = {
        "species": "red_deer",
        "latin": "Cervus elaphus",
        "description": "A very large deer.",
    }
    assert impossible_match(rows, deer) is None
    eagle = {
        "species": "golden_eagle",
        "latin": "Aquila chrysaetos",
        "description": "A very large eagle.",
    }
    assert impossible_match(rows, eagle) is None
    skua = {
        "species": "great_skua",
        "latin": "Stercorarius skua",
        "description": "A great seabird.",
    }
    assert impossible_match(rows, skua) is None, "'great' is not a whale"


def test_pick_row_matches_by_body_plan(tmp_path: Path) -> None:
    rows = read_table(_world(tmp_path))
    seen: list[str] = []

    def ask(text: str) -> str:
        seen.append(text)
        return json.dumps({"row": 2, "kind": "bird"})

    ref = pick_row(rows, {"species": "golden_eagle"}, ask)
    assert ref == {
        "row": "Birds of prey, owls",
        "start": "kestrel",
        "change": "Size, wing length",
        "kind": "bird",
    }
    assert "Birds of prey, owls" in seen[0]


def test_pick_row_retries_a_bad_row(tmp_path: Path) -> None:
    rows = read_table(_world(tmp_path))
    answers = [
        json.dumps({"row": 99, "kind": "bird"}),
        json.dumps({"row": 1, "kind": "mammal"}),
    ]
    ref = pick_row(rows, {"species": "red_deer"}, lambda text: answers.pop(0))
    assert ref["start"] == "moose"


def test_pick_row_gives_up_loudly(tmp_path: Path) -> None:
    rows = read_table(_world(tmp_path))

    def ask(text: str) -> str:
        return "not json"

    with pytest.raises(ValueError, match="no row for this species"):
        pick_row(rows, {"species": "red_deer"}, ask)
