"""Minimum stage: the set this home needs, decided in code."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from builder.stages.capability import NotPossibleError
from builder.stages.minimum import MinimumStage

FOX = {
    "id": "fox",
    "name": "Red fox",
    "latin": "Vulpes vulpes",
    "gbif_key": 5219243,
    "habitats": ["boreal_forest"],
    "can_make": True,
    "in_library": "fox",
}
HARE = {
    "id": "hare",
    "name": "Mountain hare",
    "latin": "Lepus timidus",
    "gbif_key": 2436756,
    "habitats": ["boreal_forest"],
    "can_make": True,
    "in_library": "hare",
}
CAPYBARA = {
    "id": "capybara",
    "name": "Capybara",
    "latin": "Hydrochoerus hydrochaeris",
    "gbif_key": 5219244,
    "habitats": ["rainforest"],
    "can_make": True,
}
ANACONDA = {
    "id": "anaconda",
    "name": "Green anaconda",
    "latin": "Eunectes murinus",
    "gbif_key": 5225712,
    "habitats": ["rainforest"],
    "can_make": True,
}


def _files(
    tmp_path: Path,
    entries: list[dict[str, Any]],
    brief: dict[str, Any],
    research: dict[str, Any],
) -> Path:
    world = tmp_path / "world"
    animals = world / "animals"
    animals.mkdir(parents=True)
    (world / "animal-list.json").write_text(
        json.dumps({"homes": ["boreal_forest"], "not_yet": {}, "animals": entries})
    )
    (animals / "fox.json").write_text(
        json.dumps({"tags": ["mammal"], "hunts": ["rodent"]})
    )
    (animals / "hare.json").write_text(json.dumps({"tags": ["rodent"]}))
    (animals / "archetypes.json").write_text("{}")
    (tmp_path / "home.json").write_text(json.dumps(brief))
    (tmp_path / "research.json").write_text(json.dumps(research))
    return world


def _brief(**over: Any) -> dict[str, Any]:
    brief = {
        "species_key": 5219243,
        "latin": "Vulpes vulpes",
        "in_library": "fox",
        "home": "boreal_forest",
        "needs_home": False,
    }
    brief.update(over)
    return brief


def _research(**over: Any) -> dict[str, Any]:
    research = {
        "main_food": {"name": "mountain hare", "kind": "prey", "size_cm": 55},
        "other_foods": [{"name": "bilberries", "kind": "berries"}],
        "surroundings": [],
        "size": {"length_cm": 70},
        "year": {"winter": "active", "shelter": "x", "young": "y", "food": "z"},
        "sources": ["https://example.org/fox"],
    }
    research.update(over)
    return research


def _ctx(tmp_path: Path, world: Path) -> SimpleNamespace:
    return SimpleNamespace(job_dir=tmp_path, world_path=world)


def test_library_set_makes_nothing(tmp_path: Path) -> None:
    world = _files(tmp_path, [FOX, HARE], _brief(), _research())
    result = MinimumStage().run(_ctx(tmp_path, world))
    assert result == {"minset_file": "minset.json", "make": []}
    minset = json.loads((tmp_path / "minset.json").read_text())
    assert minset["prey"] is None
    assert "shares its habitat" in minset["prey_reason"]


def test_new_animal_waits_for_maker(tmp_path: Path) -> None:
    brief = _brief(
        species_key=5225712,
        latin="Eunectes murinus",
        in_library=None,
        home="rainforest",
        needs_home=True,
    )
    research = _research(main_food={"name": "capybara", "kind": "prey", "size_cm": 110})
    world = _files(tmp_path, [ANACONDA, CAPYBARA], brief, research)
    with pytest.raises(NotPossibleError, match="one-shot"):
        MinimumStage().run(_ctx(tmp_path, world))
    minset = json.loads((tmp_path / "minset.json").read_text())
    assert minset["make_animal"] is True
    assert minset["make_home"] is True
    assert minset["prey"]["name"] == "capybara"


def test_new_home_waits_for_maker(tmp_path: Path) -> None:
    brief = _brief(home="rainforest", needs_home=True)
    world = _files(tmp_path, [FOX, HARE], brief, _research())
    with pytest.raises(NotPossibleError, match="home is made next"):
        MinimumStage().run(_ctx(tmp_path, world))


def test_prey_is_smallest_makeable(tmp_path: Path) -> None:
    brief = _brief(
        species_key=5225712,
        latin="Eunectes murinus",
        in_library=None,
        home="rainforest",
        needs_home=True,
    )
    agouti = {"name": "agouti", "kind": "prey", "size_cm": 50}
    research = _research(
        main_food={"name": "capybara", "kind": "prey", "size_cm": 110},
        other_foods=[agouti, {"name": "bilberries", "kind": "berries"}],
    )
    entries = [
        ANACONDA,
        CAPYBARA,
        {**ANACONDA, "id": "agouti", "name": "Agouti", "can_make": False},
    ]
    world = _files(tmp_path, entries, brief, research)
    with pytest.raises(NotPossibleError, match="one-shot"):
        MinimumStage().run(_ctx(tmp_path, world))
    minset = json.loads((tmp_path / "minset.json").read_text())
    assert minset["prey"]["name"] == "capybara"


def test_no_makeable_prey_proceeds(tmp_path: Path) -> None:
    brief = _brief(
        species_key=5225712,
        latin="Eunectes murinus",
        in_library=None,
        home="boreal_forest",
        needs_home=False,
    )
    research = _research(main_food={"name": "dragon", "kind": "prey", "size_cm": 300})
    world = _files(tmp_path, [FOX, ANACONDA], brief, research)
    with pytest.raises(NotPossibleError, match="one-shot"):
        MinimumStage().run(_ctx(tmp_path, world))
    minset = json.loads((tmp_path / "minset.json").read_text())
    assert minset["make_animal"] is True
    assert minset["prey"] is None
    assert "no named prey" in minset["prey_reason"]


def test_kind_food_needs_no_prey(tmp_path: Path) -> None:
    brief = _brief(species_key=5225712, in_library=None, latin="Eunectes murinus")
    research = _research(
        main_food={"name": "bilberries", "kind": "berries"},
        other_foods=[],
    )
    world = _files(tmp_path, [FOX, ANACONDA], brief, research)
    with pytest.raises(NotPossibleError, match="one-shot"):
        MinimumStage().run(_ctx(tmp_path, world))
    minset = json.loads((tmp_path / "minset.json").read_text())
    assert minset["prey"] is None
    assert "does not hunt" in minset["prey_reason"]
