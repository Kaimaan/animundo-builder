"""Capability check: northern places pass, mangroves stop the job."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from builder.pipeline import BudgetTracker, JobContext, Pipeline, StageFailedError
from builder.schemas import validate
from builder.stages import CapabilityStage


def _ctx(tmp_path: Path, world: Path | None) -> JobContext:
    return JobContext(
        job_id="job-1",
        job_dir=tmp_path,
        client=None,
        tracker=BudgetTracker(budget_usd=8.0),
        world_path=world,
    )


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _world(tmp_path: Path) -> Path:
    world = tmp_path / "world"
    _write(
        world / "locale" / "en" / "food.json",
        {"food.kind.clover": "Clover", "food.kind.bark": "Bark", "flower.x": "X"},
    )
    _write(world / "animals" / "archetypes.json", {"grazer": {}, "hunter": {}})
    return world


def _survey(tmp_path: Path, facts: list[dict], species: list[dict]) -> None:
    _write(
        tmp_path / "survey.json",
        {"area_id": "xx_test", "facts": facts, "species": species},
    )


def _fact(topic: str, text: str) -> dict:
    return {"topic": topic, "text": text, "sources": ["https://example.org/x"]}


def test_northern_place_passes(tmp_path: Path) -> None:
    _survey(
        tmp_path,
        facts=[
            _fact("forest", "Old pine forest with birch on the slopes."),
            _fact("water", "Cold clear lochs."),
        ],
        species=[],
    )
    result = CapabilityStage().run(_ctx(tmp_path, _world(tmp_path)))
    assert result == {"capabilities_file": "capabilities.json", "possible": True}
    written = json.loads((tmp_path / "capabilities.json").read_text())
    validate("capabilities", written)
    assert written["possible"] is True
    assert written["needs_code"] == []
    assert written["supported"]["tree_shapes"] == [
        "spruce",
        "pine",
        "birch",
        "round",
        "rowan",
    ]
    assert written["supported"]["food_kinds"] == ["bark", "clover"]
    assert written["supported"]["behaviours"] == ["grazer", "hunter"]


def test_mangroves_stop_with_needs_code(tmp_path: Path) -> None:
    _survey(
        tmp_path,
        facts=[
            _fact("forest", "Dense mangrove forest lines the tidal shore."),
            _fact("water", "Brackish channels."),
        ],
        species=[],
    )
    pipeline = Pipeline([CapabilityStage()], tmp_path / "state.json")
    with pytest.raises(StageFailedError, match="stage capability failed"):
        pipeline.run(_ctx(tmp_path, _world(tmp_path)))
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["failed"]["stage"] == "capability"
    assert "cannot show this place honestly" in state["failed"]["error"]
    written = json.loads((tmp_path / "capabilities.json").read_text())
    assert written["possible"] is False
    assert written["needs_code"] == [
        {
            "item": "mangrove trees",
            "reason": "the survey names mangrove trees and no game shape shows them",
        }
    ]


def test_negation_does_not_stop(tmp_path: Path) -> None:
    _survey(
        tmp_path,
        facts=[_fact("forest", "Pine and birch. No palms grow this far north.")],
        species=[],
    )
    result = CapabilityStage().run(_ctx(tmp_path, _world(tmp_path)))
    assert result["possible"] is True


def test_non_tree_mention_flags_without_stopping(tmp_path: Path) -> None:
    _survey(
        tmp_path,
        facts=[_fact("forest", "Pine and birch.")],
        species=[
            {
                "species": "crab",
                "role": "prey",
                "evidence": "Crabs hide among mangrove roots in stories.",
                "sources": ["https://example.org/crab"],
            }
        ],
    )
    result = CapabilityStage().run(_ctx(tmp_path, _world(tmp_path)))
    assert result["possible"] is True
    written = json.loads((tmp_path / "capabilities.json").read_text())
    assert [item["item"] for item in written["needs_code"]] == ["mangrove trees"]


def test_archetype_list_form(tmp_path: Path) -> None:
    world = tmp_path / "world"
    _write(
        world / "locale" / "en" / "food.json",
        {"food.kind.clover": "Clover"},
    )
    _write(world / "animals" / "archetypes.json", [{"id": "swimmer"}])
    _survey(tmp_path, facts=[_fact("forest", "Pine.")], species=[])
    result = CapabilityStage().run(_ctx(tmp_path, world))
    assert result["possible"] is True
    written = json.loads((tmp_path / "capabilities.json").read_text())
    assert written["supported"]["behaviours"] == ["swimmer"]


def test_needs_world(tmp_path: Path) -> None:
    _survey(tmp_path, facts=[_fact("forest", "Pine.")], species=[])
    with pytest.raises(ValueError, match="--world"):
        CapabilityStage().run(_ctx(tmp_path, None))
