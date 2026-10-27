"""Words stage: translations land as new locale keys only. No network."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from builder.pipeline import BudgetTracker, JobContext, Pipeline, StageFailedError
from builder.stages.words import WordsStage

DRAFT_STUB = """\
import json
print(json.dumps({
    "ok": True,
    "results": [{"check": "locale", "status": "pass", "problems": []}],
}))
"""


def _world(tmp_path: Path) -> Path:
    world = tmp_path / "world"
    (world / "locale" / "fi").mkdir(parents=True)
    (world / "locale" / "languages.json").write_text(
        json.dumps({"en": "English", "fi": "Suomi"})
    )
    (world / "locale" / "fi" / "animals.json").write_text(
        json.dumps(
            {
                "animal.moose.title": "Hirvi",
                "animal.moose.intro_safe": "Turvallinen tila: tutki!",
                "animal.moose.hungry": "Sinulla on nälkä.",
            }
        )
    )
    animals = world / "animals"
    animals.mkdir(parents=True)
    (animals / "moose.json").write_text(
        json.dumps(
            {
                "text": {
                    "title": "Moose",
                    "intro_safe": "Safe mode: explore!",
                    "hungry": "You're hungry.",
                }
            }
        )
    )
    (world / "tools").mkdir(parents=True)
    (world / "tools" / "check_draft.py").write_text(DRAFT_STUB)
    return world


def _inputs(tmp_path: Path) -> None:
    (tmp_path / "species-plan.json").write_text(
        json.dumps({"species": [{"species": "red_deer", "decision": "new"}]})
    )
    regions = tmp_path / "regions"
    regions.mkdir(parents=True)
    (regions / "xx_test.json").write_text(
        json.dumps({"id": "xx_test", "name": "Test meadow"})
    )
    animals = tmp_path / "animals"
    animals.mkdir(parents=True)
    (animals / "red_deer.json").write_text(
        json.dumps({"text": {"title": "Red deer", "hungry": "You're hungry."}})
    )


def _ctx(tmp_path: Path, client: Any, world: Path) -> JobContext:
    return JobContext(
        job_id="job-1",
        job_dir=tmp_path,
        client=client,
        tracker=BudgetTracker(budget_usd=8.0),
        world_path=world,
    )


class ScriptChat:
    """Plays back canned translations, recording prompts and options."""

    model = "fake-test-model"

    def __init__(self, answers: list[dict[str, str]]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.options: list[dict[str, Any]] = []

    def chat(self, messages: list[dict], **kwargs: Any) -> SimpleNamespace:
        content = messages[0]["content"]
        self.prompts.append(content if isinstance(content, str) else content[0]["text"])
        self.options.append(kwargs)
        message = SimpleNamespace(
            content=json.dumps(self.answers.pop(0)), tool_calls=None
        )
        return SimpleNamespace(
            model="fake-test-model",
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=10, prompt_tokens_details=None
            ),
        )


def test_writes_new_keys_only(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    chat = ScriptChat(
        [
            {"title": "Hirvi!", "hungry": "Sinulla on nälkä!"},
            {"name": "Testiniitty"},
        ]
    )
    result = WordsStage().run(_ctx(tmp_path, chat, world))
    assert result == {
        "locale_files": ["locale/fi/animals.json", "locale/fi/regions.json"],
        "languages": ["fi"],
    }
    animals = json.loads((tmp_path / "locale" / "fi" / "animals.json").read_text())
    assert animals == {
        "animal.red_deer.title": "Hirvi!",
        "animal.red_deer.hungry": "Sinulla on nälkä!",
    }
    regions = json.loads((tmp_path / "locale" / "fi" / "regions.json").read_text())
    assert regions == {"region.xx_test.name": "Testiniitty"}
    saved = json.loads((tmp_path / "checks" / "words.json").read_text())
    assert saved["results"][0] == {"check": "locale", "status": "pass", "problems": []}


def test_prompt_carries_tone_and_placeholders(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    chat = ScriptChat(
        [{"title": "Hirvi!", "hungry": "Sinulla on nälkä!"}, {"name": "X"}]
    )
    WordsStage().run(_ctx(tmp_path, chat, world))
    assert "Hirvi" in chat.prompts[0], "the game's own Finnish sets the tone"
    assert "{placeholder}" in chat.prompts[0]
    assert chat.options[0]["reasoning_effort"] == "low"


def test_changed_keys_retry_then_pass(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    chat = ScriptChat(
        [
            {"title": "Hirvi!"},
            {"title": "Hirvi!", "hungry": "Sinulla on nälkä!"},
            {"name": "Testiniitty"},
        ]
    )
    WordsStage().run(_ctx(tmp_path, chat, world))
    assert len(chat.prompts) == 3
    assert "missing ['hungry']" in chat.prompts[1]


def test_never_clean_stops(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    chat = ScriptChat([{"title": "Hirvi!"}, {"title": "Hirvi!"}])
    pipeline = Pipeline([WordsStage()], tmp_path / "state.json")
    with pytest.raises(StageFailedError, match="stage words failed"):
        pipeline.run(_ctx(tmp_path, chat, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "never came back clean" in state["failed"]["error"]


def test_locale_failure_stops(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    (world / "tools" / "check_draft.py").write_text(
        "import json;print(json.dumps({'ok': False, 'results': ["
        "{'check': 'locale', 'status': 'fail', "
        "'problems': ['CHANGED locale/fi/animals.json']}]"
        "}))"
    )
    chat = ScriptChat(
        [{"title": "Hirvi!", "hungry": "Sinulla on nälkä!"}, {"name": "X"}]
    )
    pipeline = Pipeline([WordsStage()], tmp_path / "state.json")
    with pytest.raises(StageFailedError, match="stage words failed"):
        pipeline.run(_ctx(tmp_path, chat, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "broke the locale check" in state["failed"]["error"]


def test_missing_text_stops(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    (tmp_path / "animals" / "red_deer.json").write_text(json.dumps({"text": {}}))
    chat = ScriptChat([])
    pipeline = Pipeline([WordsStage()], tmp_path / "state.json")
    with pytest.raises(StageFailedError, match="stage words failed"):
        pipeline.run(_ctx(tmp_path, chat, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "left no text" in state["failed"]["error"]


def test_english_only_writes_nothing(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    (world / "locale" / "languages.json").write_text(json.dumps({"en": "English"}))
    chat = ScriptChat([])
    result = WordsStage().run(_ctx(tmp_path, chat, world))
    assert result == {"locale_files": [], "languages": []}
    assert not (tmp_path / "locale").exists()
