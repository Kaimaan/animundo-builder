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
from builder.prompts import PROMPTS_DIR, load_prompt
from builder.stages.models import (
    ModelsStage,
    _review_prompt_name,
    _unfence,
    check_in_sandbox,
)

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
    (world / "models" / "README.md").write_text(
        "# Models\n\nOverlap.\n\n"
        "## Reference models: where to start\n\n"
        "| New animal | Start from | Change |\n"
        "|---|---|---|\n"
        "| Birds of prey, owls | `kestrel` | Size, beak |\n"
        "| Bats | none | Wings from the arms |\n"
    )
    (world / "models" / "kestrel.json").write_text(json.dumps(SPEC))
    (world / "models" / "previews").mkdir(parents=True)
    (world / "models" / "previews" / "kestrel.png").write_bytes(b"png-bytes")
    (world / "schemas").mkdir()
    (world / "schemas" / "model.schema.json").write_text(
        json.dumps({"type": "object", "required": ["id", "parts"]})
    )
    (world / "tools").mkdir()
    (world / "tools" / "build_model.sh").write_text("#!/bin/sh\n")
    return world


def _inputs(tmp_path: Path) -> None:
    (tmp_path / "species-plan.json").write_text(
        json.dumps(
            {
                "species": [
                    {
                        "species": "golden_eagle",
                        "decision": "new",
                        "reference": {
                            "row": "Birds of prey, owls",
                            "start": "kestrel",
                            "change": "Size, beak.",
                            "kind": "bird",
                        },
                    }
                ]
            }
        )
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
            json.dumps({**REVIEW_CLEAN, "round": 2}),
        ]
    )
    result = ModelsStage(chat=chat, build=_build_ok, check=lambda c, s, i, n: []).run(
        _ctx(tmp_path, world)
    )
    assert result == {"model_files": ["models/golden_eagle.json"]}
    assert len(chat.prompts) == 5, "spec, review, fix, review, verify"
    assert "A very large eagle" in chat.prompts[0]
    assert "kestrel" in chat.prompts[0]
    assert "900 triangles" in chat.prompts[1], "review sees the build stats"
    assert "Floats." in chat.prompts[2], "fix sees the issue"
    assert "previous build" in chat.prompts[2], "fix looks at its work"
    assert "Floats." not in chat.prompts[3], "review judges blind"
    assert "Floats." in chat.prompts[4], "verify names the old issue"
    assert "Kestrel" in chat.prompts[3], "review names the bar"
    assert chat.images == [1, 3, 2, 3, 3], "bar joins the review images"
    bar = tmp_path / "references" / "golden_eagle" / "closest-kestrel.png"
    assert bar.is_file()
    assert (tmp_path / "rounds" / "golden_eagle" / "1" / "preview.png").is_file()
    assert (tmp_path / "rounds" / "golden_eagle" / "2" / "build.log").is_file()
    assert (tmp_path / "reviews" / "golden_eagle-1.json").is_file()
    assert (tmp_path / "reviews" / "golden_eagle-2.json").is_file()
    assert (tmp_path / "reviews" / "golden_eagle-2-unverified.json").is_file()
    assert (tmp_path / "models" / "golden_eagle.json").is_file()
    assert (tmp_path / "models" / "golden_eagle.glb").read_bytes() == b"glb-bytes"
    assert (tmp_path / "models" / "golden_eagle.png").is_file()
    assert (tmp_path / "sandbox" / "tools" / "build_model.sh").is_file()


def test_reverify_reraises_unfixed_issue(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    blocking = {
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
    chat = ScriptChat(
        [
            json.dumps(SPEC),
            json.dumps(blocking),
            json.dumps(SPEC),
            json.dumps({**REVIEW_CLEAN, "round": 2}),
            json.dumps({**blocking, "round": 2}),
            json.dumps(SPEC),
            json.dumps({**REVIEW_CLEAN, "round": 3}),
            json.dumps({**REVIEW_CLEAN, "round": 3}),
        ]
    )
    result = ModelsStage(chat=chat, build=_build_ok, check=lambda c, s, i, n: []).run(
        _ctx(tmp_path, world)
    )
    assert result == {"model_files": ["models/golden_eagle.json"]}
    assert (tmp_path / "rounds" / "golden_eagle" / "3" / "preview.png").is_file()
    final = json.loads((tmp_path / "reviews" / "golden_eagle-2.json").read_text())
    assert final["issues"][0]["part"] == "wing", "the re-check stands"


def test_review_prompts_check_parts_and_frames() -> None:
    for name in ("model_review_bird", "model_review_bird_none"):
        text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
        for anchor in (
            "Silhouette",
            "Wings",
            "Tail",
            "Legs",
            "Colours",
            "Fly",
            "never by height",
            "Walk frames",
            "game's own",
        ):
            assert anchor in text, name
    for name in ("model_review_mammal", "model_review_mammal_none"):
        text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
        for anchor in (
            "Silhouette",
            "Legs and feet",
            "antlers or horns",
            "Coat colours",
            "no wings and no fly clip",
            "game's own",
        ):
            assert anchor in text, name


def test_review_prompt_names() -> None:
    assert _review_prompt_name("bird", True) == "model_review_bird"
    assert _review_prompt_name("bird", False) == "model_review_bird_none"
    assert _review_prompt_name("mammal", True) == "model_review_mammal"
    assert _review_prompt_name("mammal", False) == "model_review_mammal_none"


def test_missing_bar_preview_stops(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    (world / "models" / "previews" / "kestrel.png").unlink()
    chat = ScriptChat([json.dumps(SPEC)])
    with pytest.raises(StageFailedError, match="stage models failed"):
        Pipeline(
            [ModelsStage(chat=chat, build=_build_ok, check=lambda c, s, i, n: [])],
            tmp_path / "state.json",
        ).run(_ctx(tmp_path, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "no bar preview" in state["failed"]["error"]


def _draft_world(tmp_path: Path, verdict: dict) -> Path:
    world = tmp_path / "world"
    (world / "tools").mkdir(parents=True)
    (world / "tools" / "check_draft.py").write_text(
        f"import json, os, sys\n"
        f"files = sorted(os.path.relpath(os.path.join(r, f), sys.argv[1])\n"
        f"                 for r, _, fs in os.walk(sys.argv[1]) for f in fs)\n"
        f"report = {verdict!r}\n"
        f"report['draft_files'] = files\n"
        f"print(json.dumps(report))\n"
    )
    return world


def test_check_uses_species_models_problems(tmp_path: Path) -> None:
    verdict = {
        "ok": False,
        "results": [
            {
                "check": "models",
                "status": "fail",
                "problems": [
                    "FAIL models/golden_eagle.json",
                    "- triangles over budget",
                    "ERROR: bee: res://assets/animals/bee.glb is missing",
                ],
            }
        ],
    }
    world = _draft_world(tmp_path, verdict)
    job = tmp_path / "job"
    job.mkdir()
    sandbox = tmp_path / "sandbox"
    (sandbox / "models").mkdir(parents=True)
    (sandbox / "models" / "golden_eagle.json").write_text("{}")
    (sandbox / "assets" / "animals").mkdir(parents=True)
    (sandbox / "assets" / "animals" / "golden_eagle.glb").write_bytes(b"glb")
    ctx = SimpleNamespace(job_dir=job, world_path=world)
    assert check_in_sandbox(ctx, sandbox, "golden_eagle", 1) == [
        "models/golden_eagle.json: triangles over budget",
        "ERROR: bee: res://assets/animals/bee.glb is missing",
    ]
    saved = json.loads((job / "checks" / "models-golden_eagle-1.json").read_text())
    assert "models/golden_eagle.json" in saved["draft_files"]
    assert "assets/animals/golden_eagle.glb" in saved["draft_files"]


def test_check_ignores_passing_models(tmp_path: Path) -> None:
    verdict = {
        "ok": True,
        "results": [{"check": "models", "status": "pass", "problems": []}],
    }
    world = _draft_world(tmp_path, verdict)
    job = tmp_path / "job"
    job.mkdir()
    sandbox = tmp_path / "sandbox"
    (sandbox / "models").mkdir(parents=True)
    (sandbox / "models" / "golden_eagle.json").write_text("{}")
    ctx = SimpleNamespace(job_dir=job, world_path=world)
    assert check_in_sandbox(ctx, sandbox, "golden_eagle", 2) == []


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
    ModelsStage(chat=chat, build=build, check=lambda c, s, i, n: []).run(
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
        check=lambda c, s, i, n: [],
    ).run(_ctx(tmp_path, world))
    assert len(builds) == 1, "only valid specs reach Blender"
    assert "not JSON" in chat.prompts[1]
    assert "schema" in chat.prompts[2]


def test_none_mode_attaches_no_sheet(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    chat = ScriptChat([json.dumps(SPEC), json.dumps(REVIEW_CLEAN)])
    ModelsStage(
        reference="none", chat=chat, build=_build_ok, check=lambda c, s, i, n: []
    ).run(_ctx(tmp_path, world))
    assert chat.images == [0, 2], "spec from text, review of bar plus preview"


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

    def check(ctx: Any, sandbox: Path, species_id: str, round_no: int) -> list[str]:
        checks.append(species_id)
        return ["no skeleton"] if len(checks) == 1 else []

    ModelsStage(chat=chat, build=_build_ok, check=check).run(_ctx(tmp_path, world))
    assert "no skeleton" in chat.prompts[2]


def test_unknown_species_stops(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    with pytest.raises(StageFailedError, match="stage models failed"):
        Pipeline(
            [ModelsStage(refs={}, chat=ScriptChat([]))], tmp_path / "state.json"
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
                    max_rounds=2,
                    chat=chat,
                    build=_build_ok,
                    check=lambda c, s, i, n: [],
                )
            ],
            tmp_path / "state.json",
        ).run(_ctx(tmp_path, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "2 rounds" in state["failed"]["error"]


def test_empty_review_content_fails_the_stage(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    chat = ScriptChat([json.dumps(SPEC), "", ""])
    with pytest.raises(StageFailedError, match="stage models failed"):
        Pipeline(
            [ModelsStage(chat=chat, build=_build_ok, check=lambda c, s, i, n: [])],
            tmp_path / "state.json",
        ).run(_ctx(tmp_path, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "never wrote a valid review" in state["failed"]["error"]


def test_silent_build_stops_at_once(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    chat = ScriptChat([json.dumps(SPEC)])

    def silent(sandbox: Path, species_id: str) -> dict[str, Any]:
        return {"ok": False, "summary": "no build", "log": ""}

    with pytest.raises(StageFailedError, match="stage models failed"):
        Pipeline(
            [ModelsStage(chat=chat, build=silent, check=lambda c, s, i, n: [])],
            tmp_path / "state.json",
        ).run(_ctx(tmp_path, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "with no output" in state["failed"]["error"]
    assert len(chat.prompts) == 1, "no fix rounds on an environment failure"


def test_plan_without_reference_picks_live(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    (tmp_path / "species-plan.json").write_text(
        json.dumps({"species": [{"species": "golden_eagle", "decision": "new"}]})
    )
    chat = ScriptChat(
        [
            json.dumps({"row": 1, "kind": "bird"}),
            json.dumps(SPEC),
            json.dumps(REVIEW_CLEAN),
            json.dumps(REVIEW_CLEAN),
        ]
    )
    result = ModelsStage(chat=chat, build=_build_ok, check=lambda c, s, i, n: []).run(
        _ctx(tmp_path, world)
    )
    assert result == {"model_files": ["models/golden_eagle.json"]}
    assert "reference-table row" in chat.prompts[0]
    assert "kestrel" in chat.prompts[1], "spec starts from the picked row"


def test_examples_bar(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    (world / "models" / "examples").mkdir(parents=True)
    (world / "models" / "examples" / "grass_snake.json").write_text(json.dumps(SPEC))
    (world / "models" / "examples" / "previews").mkdir()
    (world / "models" / "examples" / "previews" / "grass_snake.png").write_bytes(
        b"png-bytes"
    )
    refs = {
        "golden_eagle": {
            "row": "Snakes",
            "start": "examples/grass_snake",
            "change": "Size.",
            "kind": "mammal",
        }
    }
    chat = ScriptChat(
        [json.dumps(SPEC), json.dumps(REVIEW_CLEAN), json.dumps(REVIEW_CLEAN)]
    )
    ModelsStage(refs=refs, chat=chat, build=_build_ok, check=lambda c, s, i, n: []).run(
        _ctx(tmp_path, world)
    )
    bar = tmp_path / "references" / "golden_eagle" / "closest-grass_snake.png"
    assert bar.is_file()


def test_impossible_kind_stops(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    refs = {
        "golden_eagle": {"row": "Bats", "start": "", "change": "", "kind": "mammal"}
    }
    pipeline = Pipeline(
        [ModelsStage(refs=refs, chat=ScriptChat([]))], tmp_path / "state.json"
    )
    with pytest.raises(StageFailedError, match="stage models failed"):
        pipeline.run(_ctx(tmp_path, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "not possible yet" in state["failed"]["error"]


def test_unfence() -> None:
    assert _unfence('{"a": 1}') == '{"a": 1}'
    assert _unfence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _unfence('```\n{"a": 1}\n```') == '{"a": 1}'


REVIEW_PROMPTS = (
    "model_review_bird",
    "model_review_bird_none",
    "model_review_mammal",
    "model_review_mammal_none",
)


@pytest.mark.parametrize("name", REVIEW_PROMPTS)
def test_review_prompts_loosened_bar(name: str) -> None:
    prompt = load_prompt(
        name,
        {
            "TITLE": "T",
            "SPECIES": "s",
            "ROUND": "1",
            "BUILT": "b",
            "CLOSEST_TITLE": "C",
        },
    )
    assert "Simple, not realistic" in prompt
    assert "never `blocking`" in prompt
    assert "roughly the right shape" in prompt


def test_reverify_drops_detail_complaints() -> None:
    prompt = load_prompt(
        "model_reverify", {"ISSUES": "[]", "SPECIES": "s", "ROUND": "1"}
    )
    assert "stays dropped" in prompt


def test_blender_build_sees_no_secrets(tmp_path: Path, monkeypatch: Any) -> None:
    import subprocess

    from builder.stages import models as models_module

    seen: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="BUILT x: y\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("MODEL_API_KEY", "secret")
    monkeypatch.setenv("PATH", "/bin")
    models_module.build_in_sandbox(tmp_path, "red_deer")
    assert "MODEL_API_KEY" not in seen["env"]
    assert seen["env"]["PATH"] == "/bin"
