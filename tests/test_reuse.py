"""Reuse stage: a built place gets the picked animal (#82.3)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from builder.stages import ReuseStage

LYNX_TAXON = {"gbif_key": 5219404, "name": "Lynx lynx", "rank": "species"}

REGION = {
    "id": "tt_park",
    "name": "Test Park",
    "center": {"lat": 61.55, "lon": 26.05},
    "welcome": {"text": "Old words stay."},
    "animals": {"playable": ["fox"], "prey": ["hare"], "predators": ["fox"]},
    "food": [{"kind": "hares", "amount": 10, "names": ["Hares"]}],
}

FOX = {"id": "fox", "diet": ["hares"], "tags": ["mammal"], "playable": True}

HARE = {"id": "hare", "tags": ["rodent"]}

LYNX = {
    "id": "lynx",
    "diet": ["berries"],
    "tags": ["mammal"],
    "hunts": [],
    "playable": True,
    "taxon": dict(LYNX_TAXON),
}


def _world(tmp_path: Path, with_lynx: bool = True) -> Path:
    world = tmp_path / "world"
    (world / "regions").mkdir(parents=True)
    (world / "regions" / "tt_park.json").write_text(json.dumps(REGION))
    animals = world / "animals"
    animals.mkdir()
    (animals / "fox.json").write_text(json.dumps(FOX))
    (animals / "hare.json").write_text(json.dumps(HARE))
    if with_lynx:
        (animals / "lynx.json").write_text(json.dumps(LYNX))
    return world


def _brief(**over: Any) -> dict[str, Any]:
    brief = {
        "species_key": 5219404,
        "id": "lynx",
        "latin": "Lynx lynx",
        "in_library": "lynx",
        "home": "boreal_forest",
        "reuse_place_id": "tt_park",
    }
    brief.update(over)
    return brief


def _research() -> dict[str, Any]:
    return {
        "main_food": {"name": "Bilberries", "kind": "berries"},
        "other_foods": [],
        "surroundings": [],
    }


def _job(tmp_path: Path, brief: dict[str, Any], minset: dict[str, Any]) -> Path:
    job = tmp_path / "job"
    job.mkdir()
    (job / "home.json").write_text(json.dumps(brief))
    (job / "research.json").write_text(json.dumps(_research()))
    (job / "minset.json").write_text(json.dumps(minset))
    return job


def _ctx(job: Path, world: Path) -> SimpleNamespace:
    return SimpleNamespace(job_dir=job, world_path=world)


def _minset(**over: Any) -> dict[str, Any]:
    minset = {"make_animal": False, "make_home": False, "prey": None}
    minset.update(over)
    return minset


def test_reuse_seats_feeds_and_plans(tmp_path: Path) -> None:
    world = _world(tmp_path)
    job = _job(tmp_path, _brief(), _minset())
    result = ReuseStage().run(_ctx(job, world))
    assert result["region_file"] == "regions/tt_park.json"
    assert result["place_id"] == "tt_park"
    assert result["food_added"] == ["berries"]
    region = json.loads((job / "regions" / "tt_park.json").read_text())
    assert region["id"] == "tt_park"
    assert region["animals"]["playable"] == ["fox", "lynx"]
    assert region["animals"]["prey"] == ["hare"]
    assert region["welcome"] == {"text": "Old words stay."}
    kinds = {food["kind"]: food["names"] for food in region["food"]}
    assert kinds == {"hares": ["Hares"], "berries": ["Bilberries"]}
    plan = json.loads((job / "species-plan.json").read_text())
    assert plan["species"] == [
        {
            "species": "lynx",
            "decision": "reuse",
            "reason": "the picked animal, reused from the library",
            "wild": True,
            "taxon": LYNX_TAXON,
        }
    ]


def test_reuse_plans_new_when_the_maker_runs(tmp_path: Path) -> None:
    world = _world(tmp_path, with_lynx=False)
    job = _job(tmp_path, _brief(in_library=None), _minset(make_animal=True))
    ReuseStage().run(_ctx(job, world))
    region = json.loads((job / "regions" / "tt_park.json").read_text())
    assert "lynx" in region["animals"]["playable"]
    plan = json.loads((job / "species-plan.json").read_text())
    assert [entry["species"] for entry in plan["species"]] == ["lynx"]
    assert plan["species"][0]["decision"] == "new"


def test_reuse_needs_a_place_draft(tmp_path: Path) -> None:
    world = _world(tmp_path)
    job = _job(tmp_path, _brief(reuse_place_id=None), _minset())
    with pytest.raises(ValueError, match="place: draft"):
        ReuseStage().run(_ctx(job, world))
