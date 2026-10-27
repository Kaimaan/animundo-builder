"""Safety stage: review, rewrite once, or stop. No network."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from builder.pipeline import BudgetTracker, JobContext, Pipeline, StageFailedError
from builder.stages.safety import SafetyStage


def _world(tmp_path: Path) -> Path:
    world = tmp_path / "world"
    animals = world / "animals"
    animals.mkdir(parents=True)
    (animals / "moose.json").write_text(
        json.dumps(
            {
                "text": {
                    "title": "Moose",
                    "intro_safe": "Safe: explore!",
                    "hungry": "Hungry.",
                }
            }
        )
    )
    return world


def _inputs(tmp_path: Path) -> None:
    (tmp_path / "survey.json").write_text(
        json.dumps(
            {
                "area_id": "xx_test",
                "facts": [],
                "species": [
                    {
                        "species": "red_deer",
                        "sources": ["https://example.org/deer"],
                        "sensitive": True,
                    }
                ],
            }
        )
    )
    animals = tmp_path / "animals"
    animals.mkdir(parents=True)
    (animals / "red_deer.json").write_text(
        json.dumps({"text": {"title": "Red deer", "hungry": "You're hungry."}})
    )
    (tmp_path / "animals" / "progress.json").write_text("{}")
    regions = tmp_path / "regions"
    regions.mkdir(parents=True)
    (regions / "xx_test.json").write_text(
        json.dumps(
            {
                "id": "xx_test",
                "name": "Test meadow",
                "description": "A meadow.",
                "sources": ["https://example.org/meadow"],
                "places": [{"name": "Old forest"}],
                "food": [{"names": ["grass"]}],
                "ground_cover": {
                    "flower_names": ["daisy"],
                    "plants": [{"model": "sorrel", "names": ["sorrel"]}],
                },
            }
        )
    )
    fi = tmp_path / "locale" / "fi"
    fi.mkdir(parents=True)
    (fi / "animals.json").write_text(
        json.dumps(
            {
                "animal.red_deer.title": "Saksanhirvi",
                "animal.red_deer.hungry": "Sinulla on nälkä.",
            }
        )
    )
    (fi / "regions.json").write_text(json.dumps({"region.xx_test.name": "Testiniitty"}))


def _ctx(tmp_path: Path, client: Any, world: Path) -> JobContext:
    return JobContext(
        job_id="job-1",
        job_dir=tmp_path,
        client=client,
        tracker=BudgetTracker(budget_usd=8.0),
        world_path=world,
    )


class ScriptChat:
    """Plays back canned answers, recording prompts."""

    model = "fake-test-model"

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def chat(self, messages: list[dict], **kwargs: Any) -> SimpleNamespace:
        content = messages[0]["content"]
        self.prompts.append(content if isinstance(content, str) else content[0]["text"])
        message = SimpleNamespace(content=self.answers.pop(0), tool_calls=None)
        return SimpleNamespace(
            model="fake-test-model",
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=10, prompt_tokens_details=None
            ),
        )


def _pass_all(keys: list[str]) -> str:
    return json.dumps(
        {
            "verdicts": [
                {"key": key, "verdict": "pass", "reasons": [], "rule": ""}
                for key in keys
            ]
        }
    )


KEYS = [
    "animal.red_deer.hungry",
    "animal.red_deer.title",
    "region.xx_test.description",
    "region.xx_test.flower.0",
    "region.xx_test.food.0.0",
    "region.xx_test.name",
    "region.xx_test.place.0",
    "region.xx_test.plantname.0.0",
]


def test_all_pass(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    chat = ScriptChat([_pass_all(KEYS)])
    result = SafetyStage().run(_ctx(tmp_path, chat, world))
    assert result == {"reviewed": 8, "rewritten": [], "failed": []}
    saved = json.loads((tmp_path / "checks" / "safety.json").read_text())
    assert saved["reviewed"] == 8
    assert saved["failed"] == []


def test_prompt_names_sensitive_and_tone(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    chat = ScriptChat([_pass_all(KEYS)])
    SafetyStage().run(_ctx(tmp_path, chat, world))
    assert "red_deer" in chat.prompts[0], "sensitive species flagged"
    assert "never name or place a real breeding" in chat.prompts[0]
    assert "Moose" in chat.prompts[0], "tone examples included"
    assert "https://example.org/deer" in chat.prompts[0], "sources included"


def test_rewrite_fixed_and_written_back(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    first = {
        "verdicts": [
            {
                "key": key,
                "verdict": "rewrite" if key == "animal.red_deer.hungry" else "pass",
                "reasons": ["too stiff"] if key == "animal.red_deer.hungry" else [],
                "rule": "tone" if key == "animal.red_deer.hungry" else "",
            }
            for key in KEYS
        ]
    }
    chat = ScriptChat(
        [
            json.dumps(first),
            json.dumps(
                {
                    "animal.red_deer.hungry": {
                        "en": "You're hungry. Graze!",
                        "fi": "Sinulla on nälkä. Laidunna!",
                    }
                }
            ),
            _pass_all(["animal.red_deer.hungry"]),
        ]
    )
    result = SafetyStage().run(_ctx(tmp_path, chat, world))
    assert result["rewritten"] == ["animal.red_deer.hungry"]
    animal = json.loads((tmp_path / "animals" / "red_deer.json").read_text())
    assert animal["text"]["hungry"] == "You're hungry. Graze!"
    fi = json.loads((tmp_path / "locale" / "fi" / "animals.json").read_text())
    assert fi["animal.red_deer.hungry"] == "Sinulla on nälkä. Laidunna!"


def test_rewrite_still_bad_stops(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    flagged = {
        "verdicts": [
            {
                "key": key,
                "verdict": "rewrite" if key == "region.xx_test.name" else "pass",
                "reasons": ["too long"] if key == "region.xx_test.name" else [],
                "rule": "length" if key == "region.xx_test.name" else "",
            }
            for key in KEYS
        ]
    }
    still = {
        "verdicts": [
            {
                "key": "region.xx_test.name",
                "verdict": "fail",
                "reasons": ["still too long"],
                "rule": "length",
            }
        ]
    }
    chat = ScriptChat(
        [
            json.dumps(flagged),
            json.dumps({"region.xx_test.name": {"en": "Meadow", "fi": "Niitty"}}),
            json.dumps(still),
        ]
    )
    pipeline = Pipeline([SafetyStage()], tmp_path / "state.json")
    with pytest.raises(StageFailedError, match="stage safety failed"):
        pipeline.run(_ctx(tmp_path, chat, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "region.xx_test.name" in state["failed"]["error"]
    assert "a person looks" in state["failed"]["error"]
    region = json.loads((tmp_path / "regions" / "xx_test.json").read_text())
    assert region["name"] == "Meadow", "the rewrite stays for the person"


def test_fail_stops_at_once(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    failed = {
        "verdicts": [
            {
                "key": key,
                "verdict": "fail" if key == "animal.red_deer.title" else "pass",
                "reasons": ["a brand"] if key == "animal.red_deer.title" else [],
                "rule": "brands" if key == "animal.red_deer.title" else "",
            }
            for key in KEYS
        ]
    }
    chat = ScriptChat([json.dumps(failed)])
    pipeline = Pipeline([SafetyStage()], tmp_path / "state.json")
    with pytest.raises(StageFailedError, match="stage safety failed"):
        pipeline.run(_ctx(tmp_path, chat, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "a brand" in state["failed"]["error"]
    assert len(chat.prompts) == 1, "no rewrite round for a fail"


def test_shape_retry_then_stop(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    chat = ScriptChat(['{"verdicts": []}', '{"verdicts": []}'])
    pipeline = Pipeline([SafetyStage()], tmp_path / "state.json")
    with pytest.raises(StageFailedError, match="stage safety failed"):
        pipeline.run(_ctx(tmp_path, chat, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "never wrote valid verdicts" in state["failed"]["error"]


def test_home_without_survey_reviews_region_name(tmp_path: Path) -> None:
    world = _world(tmp_path)
    regions = tmp_path / "regions"
    regions.mkdir(parents=True)
    (regions / "home_601_249_fox.json").write_text(
        json.dumps(
            {
                "id": "home_601_249_fox",
                "name": "Fox copse",
                "sources": ["https://example.org/template"],
            }
        )
    )
    chat = ScriptChat([_pass_all(["region.home_601_249_fox.name"])])
    result = SafetyStage().run(_ctx(tmp_path, chat, world))
    assert result == {"reviewed": 1, "rewritten": [], "failed": []}
    assert "(no sensitive species" in chat.prompts[0]


def test_no_texts_passes_empty(tmp_path: Path) -> None:
    world = _world(tmp_path)
    (tmp_path / "survey.json").write_text(
        json.dumps({"area_id": "xx_test", "facts": [], "species": []})
    )
    (tmp_path / "animals").mkdir(parents=True)
    regions = tmp_path / "regions"
    regions.mkdir(parents=True)
    (regions / "xx_test.json").write_text(json.dumps({"id": "xx_test"}))
    chat = ScriptChat([json.dumps({"verdicts": []})])
    result = SafetyStage().run(_ctx(tmp_path, chat, world))
    assert result == {"reviewed": 0, "rewritten": [], "failed": []}
