"""Species plan: Latin matching, region rewrite, food first. No network."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from builder.pipeline import BudgetTracker, JobContext, Pipeline, StageFailedError
from builder.schemas import validate
from builder.stages.species_plan import SpeciesPlanStage


def _world(tmp_path: Path) -> Path:
    world = tmp_path / "world"
    animals = world / "animals"
    animals.mkdir(parents=True)
    (animals / "fox.json").write_text(json.dumps({"latin": "Vulpes vulpes"}))
    (animals / "perch.json").write_text(json.dumps({"latin": "Perca fluviatilis"}))
    (animals / "young_perch.json").write_text(
        json.dumps({"latin": "Perca fluviatilis"})
    )
    (animals / "archetypes.json").write_text("{}")
    return world


def _inputs(tmp_path: Path, survey_species: list[dict]) -> None:
    (tmp_path / "survey.json").write_text(
        json.dumps({"area_id": "xx_test", "facts": [], "species": survey_species})
    )
    regions = tmp_path / "regions"
    regions.mkdir(parents=True)
    (regions / "xx_test.json").write_text(
        json.dumps(
            {
                "id": "xx_test",
                "animals": {
                    "playable": ["red_deer"],
                    "predators": ["red_fox"],
                    "prey": [],
                },
                "fish": [{"kind": "european_perch", "count": 5}],
            }
        )
    )


def _species(species: str, latin: str, role: str = "playable") -> dict:
    return {
        "species": species,
        "latin": latin,
        "role": role,
        "evidence": "Seen.",
        "sources": ["https://example.org/x"],
    }


def _ctx(tmp_path: Path, client: Any, world: Path) -> JobContext:
    return JobContext(
        job_id="job-1",
        job_dir=tmp_path,
        client=client,
        tracker=BudgetTracker(budget_usd=8.0),
        world_path=world,
    )


class ScriptClient:
    model = "fake-test-model"

    def __init__(self, plans: list[dict]) -> None:
        self.plans = list(plans)
        self.calls = 0

    def chat(self, messages: list[dict], **kwargs: Any) -> SimpleNamespace:
        self.calls += 1
        message = SimpleNamespace(
            content=json.dumps(self.plans.pop(0)), tool_calls=None
        )
        return SimpleNamespace(
            model="fake-test-model",
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=10, prompt_tokens_details=None
            ),
        )


def _plan(entries: list[tuple[str, str]], food_web_ok: bool = True) -> dict:
    return {
        "species": [
            {"species": species, "decision": decision, "reason": "Because."}
            for species, decision in entries
        ],
        "food_web_ok": food_web_ok,
        "missing_food": [],
    }


def _surveyed() -> list[dict]:
    return [
        _species("red_fox", "Vulpes vulpes", "predator"),
        _species("european_perch", "Perca fluviatilis", "prey"),
        _species("red_deer", "Cervus elaphus", "playable"),
    ]


def test_match_rewrite_and_plan(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path, _surveyed())
    client = ScriptClient(
        [
            _plan(
                [("red_fox", "reuse"), ("european_perch", "reuse"), ("red_deer", "new")]
            )
        ]
    )
    result = SpeciesPlanStage().run(_ctx(tmp_path, client, world))
    assert result == {"species_plan_file": "species-plan.json", "reuse": 2, "new": 1}
    written = json.loads((tmp_path / "species-plan.json").read_text())
    validate("species-plan", written)
    assert written["food_web_ok"] is True
    region = json.loads((tmp_path / "regions" / "xx_test.json").read_text())
    assert region["animals"]["predators"] == ["fox"]
    assert region["fish"] == [{"kind": "perch", "count": 5}]
    assert region["animals"]["playable"] == ["red_deer"]


def test_young_forms_lose_to_grown_ones(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path, _surveyed())
    client = ScriptClient(
        [
            _plan(
                [("red_fox", "reuse"), ("european_perch", "reuse"), ("red_deer", "new")]
            )
        ]
    )
    SpeciesPlanStage().run(_ctx(tmp_path, client, world))
    region = json.loads((tmp_path / "regions" / "xx_test.json").read_text())
    assert region["fish"][0]["kind"] == "perch"


def test_cap_checked_before_any_model_call(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path, _surveyed())
    client = ScriptClient([_plan([])])
    pipeline = Pipeline([SpeciesPlanStage(max_new_species=0)], tmp_path / "state.json")
    with pytest.raises(StageFailedError, match="stage species_plan failed"):
        pipeline.run(_ctx(tmp_path, client, world))
    assert client.calls == 0
    state = json.loads((tmp_path / "state.json").read_text())
    assert "over the cap" in state["failed"]["error"]


def test_model_must_keep_the_match(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path, _surveyed())
    wrong = _plan(
        [("red_fox", "new"), ("european_perch", "reuse"), ("red_deer", "new")]
    )
    right = _plan(
        [("red_fox", "reuse"), ("european_perch", "reuse"), ("red_deer", "new")]
    )
    client = ScriptClient([wrong, right])
    SpeciesPlanStage().run(_ctx(tmp_path, client, world))
    assert client.calls == 2


def test_food_web_failure_stops_with_plan_written(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path, _surveyed())
    hungry = _plan(
        [("red_fox", "reuse"), ("european_perch", "reuse"), ("red_deer", "new")],
        food_web_ok=False,
    )
    hungry["missing_food"] = ["prey for red_fox"]
    client = ScriptClient([hungry])
    pipeline = Pipeline([SpeciesPlanStage()], tmp_path / "state.json")
    with pytest.raises(StageFailedError, match="stage species_plan failed"):
        pipeline.run(_ctx(tmp_path, client, world))
    written = json.loads((tmp_path / "species-plan.json").read_text())
    assert written["food_web_ok"] is False
    state = json.loads((tmp_path / "state.json").read_text())
    assert "food first fails" in state["failed"]["error"]


def test_id_collision_needs_a_person(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(
        tmp_path,
        [_species("fox", "Vulpes zerda", "playable")],
    )
    client = ScriptClient([_plan([])])
    pipeline = Pipeline([SpeciesPlanStage()], tmp_path / "state.json")
    with pytest.raises(StageFailedError, match="stage species_plan failed"):
        pipeline.run(_ctx(tmp_path, client, world))
    assert client.calls == 0
    state = json.loads((tmp_path / "state.json").read_text())
    assert "must rename one" in state["failed"]["error"]
