"""The model loop: spec, build, review, fix. No network, no Blender."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from builder.pipeline import BudgetTracker, JobContext, Pipeline, StageFailedError
from builder.stages.models import ModelsStage, _unfence

SPEC = {"id": "golden_eagle", "parts": [{"name": "body"}]}
REVIEW_CLEAN = {"species": "golden_eagle", "round": 1, "issues": []}


def _png() -> bytes:
    canvas = Image.new("RGB", (64, 64), (122, 82, 48))
    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()


def _world(tmp_path: Path) -> Path:
    world = tmp_path / "world"
    world.mkdir(parents=True)
    (world / "ART.md").write_text("# Art\n\nReal first.\n")
    (world / "models").mkdir(parents=True)
    (world / "models" / "README.md").write_text("# Models\n\nOverlap.\n")
    (world / "models" / "kestrel.json").write_text(json.dumps(SPEC))
    (world / "schemas").mkdir()
    (world / "schemas" / "model.schema.json").write_text(
        json.dumps({"type": "object", "required": ["id", "parts"]})
    )
    (world / "tools").mkdir()
    (world / "tools" / "build_model.sh").write_text("#!/bin/sh\n")
    return world


def _inputs(tmp_path: Path) -> None:
    (tmp_path / "species-plan.json").write_text(
        json.dumps({"species": [{"species": "golden_eagle", "decision": "new"}]})
    )
    (tmp_path / "survey.json").write_text(
        json.dumps(
            {
                "area_id": "xx_test",
                "facts": [],
                "species": [
                    {
                        "species": "golden_eagle",
                        "latin": "Aquila chrysaetos",
                        "description": "A very large eagle.",
                    }
                ],
            }
        )
    )
    (tmp_path / "measurements").mkdir(exist_ok=True)
    (tmp_path / "measurements" / "golden_eagle.json").write_text(
        json.dumps({"species": "golden_eagle"})
    )
    refs = tmp_path / "references" / "golden_eagle"
    refs.mkdir(parents=True)
    (refs / "sheet-1.png").write_bytes(_png())
    animals = tmp_path / "animals"
    animals.mkdir(exist_ok=True)
    (animals / "golden_eagle.json").write_text(
        json.dumps({"text": {"title": "Golden eagle"}})
    )


def _ctx(tmp_path: Path, world: Path) -> JobContext:
    return JobContext(
        job_id="job-1",
        job_dir=tmp_path,
        client=None,
        tracker=BudgetTracker(budget_usd=8.0),
        world_path=world,
    )


def _answer(content: str) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=10),
    )


class ScriptChat:
    """Plays back canned answers, recording prompts and image counts."""

    def __init__(self, answers: list[str]) -> None:
        self.answers = [_answer(text) for text in answers]
        self.prompts: list[str] = []
        self.images: list[int] = []

    def __call__(self, messages: list[dict], **kwargs: Any) -> SimpleNamespace:
        content = messages[0]["content"]
        self.prompts.append(content[0]["text"])
        self.images.append(sum(1 for part in content if part["type"] == "image_url"))
        return self.answers.pop(0)


def _build_ok(sandbox: Path, species_id: str) -> dict[str, Any]:
    previews = sandbox / "models" / "previews"
    previews.mkdir(parents=True, exist_ok=True)
    (previews / f"{species_id}.png").write_bytes(_png())
    assets = sandbox / "assets" / "animals"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / f"{species_id}.glb").write_bytes(b"glb-bytes")
    return {"ok": True, "summary": "golden_eagle: 900 triangles", "log": "BUILT"}


def test_fix_round_then_pass(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    review_blocking = {
        "species": "golden_eagle",
        "round": 1,
        "issues": [
            {
                "part": "beak",
                "problem": "Floats.",
                "severity": "blocking",
                "fix": "Sink it in.",
            }
        ],
    }
    chat = ScriptChat(
        [
            json.dumps(SPEC),
            json.dumps(review_blocking),
            json.dumps(SPEC),
            json.dumps({**REVIEW_CLEAN, "round": 2}),
        ]
    )
    result = ModelsStage(chat=chat, build=_build_ok, check=lambda s, i: []).run(
        _ctx(tmp_path, world)
    )
    assert result == {"model_files": ["models/golden_eagle.json"]}
    assert len(chat.prompts) == 4, "spec, review, fix, review"
    assert "A very large eagle" in chat.prompts[0]
    assert "kestrel" in chat.prompts[0]
    assert "900 triangles" in chat.prompts[1], "review sees the build stats"
    assert "Floats." in chat.prompts[2], "fix sees the issue"
    assert chat.images == [1, 2, 1, 2], "sheet+preview where each needs them"
    assert (tmp_path / "rounds" / "golden_eagle" / "1" / "preview.png").is_file()
    assert (tmp_path / "rounds" / "golden_eagle" / "2" / "build.log").is_file()
    assert (tmp_path / "reviews" / "golden_eagle-1.json").is_file()
    assert (tmp_path / "reviews" / "golden_eagle-2.json").is_file()
    assert (tmp_path / "models" / "golden_eagle.json").is_file()
    assert (tmp_path / "models" / "golden_eagle.glb").read_bytes() == b"glb-bytes"
    assert (tmp_path / "models" / "golden_eagle.png").is_file()
    assert (tmp_path / "sandbox" / "tools" / "build_model.sh").is_file()


def test_build_failure_skips_review_and_fixes_from_log(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    calls = []

    def build(sandbox: Path, species_id: str) -> dict[str, Any]:
        calls.append(species_id)
        if len(calls) == 1:
            return {"ok": False, "summary": "the build failed", "log": "ERROR boom"}
        return _build_ok(sandbox, species_id)

    chat = ScriptChat(
        [
            json.dumps(SPEC),
            json.dumps(SPEC),
            json.dumps({**REVIEW_CLEAN, "round": 2}),
        ]
    )
    ModelsStage(chat=chat, build=build, check=lambda s, i: []).run(
        _ctx(tmp_path, world)
    )
    assert len(chat.prompts) == 3, "spec, fix, review: no review of thin air"
    assert "ERROR boom" in chat.prompts[1]
    assert not (tmp_path / "reviews" / "golden_eagle-1.json").exists()
    assert (tmp_path / "rounds" / "golden_eagle" / "1" / "build.log").read_text() == (
        "ERROR boom"
    )


def test_broken_spec_retried_inside_the_round(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    builds = []
    chat = ScriptChat(
        [
            "not json",
            json.dumps({"id": "x"}),
            json.dumps(SPEC),
            json.dumps(REVIEW_CLEAN),
        ]
    )
    ModelsStage(
        chat=chat,
        build=lambda s, i: builds.append(i) or _build_ok(s, i),
        check=lambda s, i: [],
    ).run(_ctx(tmp_path, world))
    assert len(builds) == 1, "only valid specs reach Blender"
    assert "not JSON" in chat.prompts[1]
    assert "schema" in chat.prompts[2]


def test_none_mode_attaches_no_sheet(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    chat = ScriptChat([json.dumps(SPEC), json.dumps(REVIEW_CLEAN)])
    ModelsStage(
        reference="none", chat=chat, build=_build_ok, check=lambda s, i: []
    ).run(_ctx(tmp_path, world))
    assert chat.images == [0, 1], "spec from text, review of the preview alone"


def test_check_errors_reach_the_fix(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    chat = ScriptChat(
        [
            json.dumps(SPEC),
            json.dumps(REVIEW_CLEAN),
            json.dumps(SPEC),
            json.dumps({**REVIEW_CLEAN, "round": 2}),
        ]
    )
    checks = []

    def check(sandbox: Path, species_id: str) -> list[str]:
        checks.append(species_id)
        return ["no skeleton"] if len(checks) == 1 else []

    ModelsStage(chat=chat, build=_build_ok, check=check).run(_ctx(tmp_path, world))
    assert "no skeleton" in chat.prompts[2]


def test_unknown_species_stops(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    with pytest.raises(StageFailedError, match="stage models failed"):
        Pipeline(
            [ModelsStage(closest={}, chat=ScriptChat([]))], tmp_path / "state.json"
        ).run(_ctx(tmp_path, world))


def test_missing_sheet_stops(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    for path in (tmp_path / "references" / "golden_eagle").glob("sheet-*.png"):
        path.unlink()
    with pytest.raises(StageFailedError, match="stage models failed"):
        Pipeline([ModelsStage(chat=ScriptChat([]))], tmp_path / "state.json").run(
            _ctx(tmp_path, world)
        )


def test_rounds_run_out(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    chat = ScriptChat(
        [
            json.dumps(SPEC),
            json.dumps(
                {
                    "species": "golden_eagle",
                    "round": 1,
                    "issues": [
                        {
                            "part": "wing",
                            "problem": "Bent.",
                            "severity": "blocking",
                            "fix": "Straighten.",
                        }
                    ],
                }
            ),
            json.dumps(SPEC),
            json.dumps(
                {
                    "species": "golden_eagle",
                    "round": 2,
                    "issues": [
                        {
                            "part": "wing",
                            "problem": "Still bent.",
                            "severity": "blocking",
                            "fix": "Straighten.",
                        }
                    ],
                }
            ),
        ]
    )
    with pytest.raises(StageFailedError, match="stage models failed"):
        Pipeline(
            [
                ModelsStage(
                    max_rounds=2, chat=chat, build=_build_ok, check=lambda s, i: []
                )
            ],
            tmp_path / "state.json",
        ).run(_ctx(tmp_path, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "2 rounds" in state["failed"]["error"]


def test_unfence() -> None:
    assert _unfence('{"a": 1}') == '{"a": 1}'
    assert _unfence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _unfence('```\n{"a": 1}\n```') == '{"a": 1}'
