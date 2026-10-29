"""Plants stage: draft, GBIF check, build, review, fix. No network."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from builder.pipeline import BudgetTracker, JobContext, Pipeline, StageFailedError
from builder.stages.plants import PlantsStage, _Stopped

SPEC = {
    "id": "wood_sorrel",
    "name": "Wood sorrel",
    "author": "Animundo Builder",
    "latin": "Oxalis acetosella",
    "kind": "ground",
    "colors": {"leaf": "#3f7a34"},
    "parts": [{"name": "leaves"}],
    "year": {"leaves": "deciduous", "flowers": "May.", "fruit": "none"},
    "sources": ["https://example.org/wood-sorrel"],
}
TAXON = {"gbif_key": 123, "rank": "SPECIES", "name": "Oxalis acetosella"}
REVIEW_CLEAN = {"species": "wood_sorrel", "round": 1, "issues": []}

DRAFT_STUB = """\
import json
print(json.dumps({
    "ok": True,
    "results": [{"check": "models", "status": "pass", "problems": []}],
}))
"""


def _png() -> bytes:
    canvas = Image.new("RGB", (64, 64), (63, 122, 52))
    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()


def _world(tmp_path: Path) -> Path:
    world = tmp_path / "world"
    (world / "models" / "plants").mkdir(parents=True)
    (world / "models" / "plants" / "bilberry.json").write_text(
        json.dumps({"id": "bilberry", "name": "Bilberry", "kind": "bush"})
    )
    (world / "models" / "plants" / "wood_anemone.json").write_text(
        json.dumps({"id": "wood_anemone", "name": "Wood anemone", "kind": "ground"})
    )
    (world / "models" / "previews" / "plants").mkdir(parents=True)
    (world / "models" / "previews" / "plants" / "bilberry.png").write_bytes(b"png")
    (world / "models" / "previews" / "plants" / "wood_anemone.png").write_bytes(b"png")
    (world / "models" / "README.md").write_text(
        "# Models\n\n## Plants\n\nParts take shapes.\n"
    )
    (world / "CONTRIBUTING.md").write_text(
        "# World\n\n## Adding plants\n\n### The rules\n\nReal species only.\n\n"
        "> Add a **[PLANT]** to the game.\n"
    )
    (world / "tools").mkdir(parents=True)
    (world / "tools" / "check_draft.py").write_text(DRAFT_STUB)
    (world / "schemas").mkdir(parents=True)
    (world / "schemas" / "model.schema.json").write_text(
        json.dumps(
            {
                "if": {"required": ["kind"]},
                "then": {"$ref": "#/$defs/plant_model"},
                "$defs": {
                    "plant_model": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            key: {}
                            for key in (
                                "id",
                                "name",
                                "author",
                                "latin",
                                "kind",
                                "colors",
                                "parts",
                                "year",
                                "sources",
                                "taxon",
                            )
                        },
                    }
                },
            }
        )
    )
    (world / "poisonous-plants.json").write_text(
        json.dumps({"plants": [{"gbif_key": 999, "name": "Deadly one"}]})
    )
    return world


def _inputs(tmp_path: Path, plants: list[dict] | None = None) -> None:
    (tmp_path / "survey.json").write_text(
        json.dumps(
            {
                "area_id": "xx_test",
                "facts": [
                    {"topic": "vegetation", "text": "Sorrel carpets the forest."}
                ],
                "species": [],
            }
        )
    )
    regions = tmp_path / "regions"
    regions.mkdir(parents=True)
    (regions / "xx_test.json").write_text(
        json.dumps(
            {
                "id": "xx_test",
                "name": "Test forest",
                "country": "FI",
                "ground_cover": {
                    "plants": plants
                    if plants is not None
                    else [
                        {
                            "model": "wood_sorrel",
                            "names": ["wood sorrel"],
                            "density": 0.2,
                        }
                    ]
                },
            }
        )
    )


def _ctx(tmp_path: Path, world: Path) -> JobContext:
    return JobContext(
        job_id="job-1",
        job_dir=tmp_path,
        client=None,
        tracker=BudgetTracker(budget_usd=8.0),
        world_path=world,
    )


class ScriptChat:
    """Plays back canned answers, recording prompts."""

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, messages: list[dict], **kwargs: Any) -> SimpleNamespace:
        content = messages[-1]["content"]
        text = content if isinstance(content, str) else content[0]["text"]
        self.prompts.append(text)
        message = SimpleNamespace(content=self.answers.pop(0), tool_calls=None)
        return SimpleNamespace(
            model="fake-test-model", choices=[SimpleNamespace(message=message)]
        )


def _match_new(world: Path, latin: str) -> dict[str, Any]:
    assert latin == "Oxalis acetosella"
    return {"taxon": TAXON, "files": []}


def _build_ok(sandbox: Path, plant_id: str) -> dict[str, Any]:
    previews = sandbox / "models" / "previews" / "plants"
    previews.mkdir(parents=True, exist_ok=True)
    (previews / f"{plant_id}.png").write_bytes(_png())
    assets = sandbox / "assets" / "plants"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / f"{plant_id}.glb").write_bytes(b"glb-bytes")
    return {"ok": True, "summary": "wood_sorrel: 40 triangles", "log": "BUILT"}


def test_skips_when_no_region_names_plants(tmp_path: Path) -> None:
    """Items that name no plants skip survey, sandbox and calls."""
    world = _world(tmp_path)
    result = PlantsStage().run(_ctx(tmp_path, world))
    assert result == {
        "plant_files": [],
        "rounds": {},
        "reused": {},
        "expert_review": [],
    }
    assert not (tmp_path / "sandbox").exists()

    _inputs(tmp_path, plants=[])
    result = PlantsStage().run(_ctx(tmp_path, world))
    assert result["plant_files"] == []


def test_builds_one_new_plant(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    chat = ScriptChat([json.dumps(SPEC), json.dumps(REVIEW_CLEAN)])
    result = PlantsStage(match=_match_new, chat=chat, build=_build_ok).run(
        _ctx(tmp_path, world)
    )
    assert result["plant_files"] == ["models/plants/wood_sorrel.json"]
    assert result["rounds"] == {"wood_sorrel": 1}
    assert result["reused"] == {}
    assert result["expert_review"] == []
    saved = tmp_path / "models" / "plants" / "wood_sorrel.json"
    final = json.loads(saved.read_text())
    assert final["taxon"] == TAXON
    assert (tmp_path / "models" / "plants" / "wood_sorrel.glb").is_file()
    assert "Oxalis" not in chat.prompts[0], "latin comes from the draft, not in"
    assert "bilberry" in chat.prompts[0]
    assert "wood_anemone" in chat.prompts[0]
    assert "Wood anemone" in chat.prompts[1], "ground plants bar the anemone"
    bar = tmp_path / "references" / "wood_sorrel" / "closest-wood_anemone.png"
    assert bar.is_file()
    assert "Real species only" in chat.prompts[0]
    assert "40 triangles" in chat.prompts[1], "review sees the build stats"


def test_known_species_reuses_its_spec(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    (world / "models" / "plants" / "sorrel.json").write_text(json.dumps(SPEC))

    def match(world: Path, latin: str) -> dict[str, Any]:
        return {"taxon": TAXON, "files": ["models/plants/sorrel.json"]}

    chat = ScriptChat([json.dumps(SPEC)])
    result = PlantsStage(match=match, chat=chat, build=_build_ok).run(
        _ctx(tmp_path, world)
    )
    assert result["plant_files"] == []
    assert result["reused"] == {"wood_sorrel": "sorrel"}
    region = json.loads((tmp_path / "regions" / "xx_test.json").read_text())
    models = [p["model"] for p in region["ground_cover"]["plants"]]
    assert models == ["sorrel"]
    assert not (tmp_path / "models").exists(), "no build for a reuse"


def test_fourth_new_plant_stops(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(
        tmp_path,
        [
            {"model": f"plant_{n}", "names": [f"plant {n}"], "density": 0.1}
            for n in range(4)
        ],
    )
    pipeline = Pipeline([PlantsStage(match=_match_new)], tmp_path / "state.json")
    with pytest.raises(StageFailedError, match="stage plants failed"):
        pipeline.run(_ctx(tmp_path, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "at most 3" in state["failed"]["error"]


def test_checker_error_fixed_next_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    monkeypatch.setenv("SEEN", str(tmp_path / "seen"))
    (world / "tools" / "check_draft.py").write_text(
        "import json, os\n"
        "seen = os.path.exists(os.environ.get('SEEN', '/none'))\n"
        "open(os.environ.get('SEEN', '/none'), 'w').write('x')\n"
        "problems = [] if seen else ['FAIL models/plants/wood_sorrel.json', "
        "'- \\'year.leaves\\' must be evergreen or deciduous']\n"
        "print(json.dumps({'ok': not problems, 'results': ["
        "{'check': 'models', 'status': 'fail' if problems else 'pass', "
        "'problems': problems}]}))"
    )
    chat = ScriptChat(
        [
            json.dumps(SPEC),
            json.dumps(REVIEW_CLEAN),
            json.dumps(SPEC),
            json.dumps({**REVIEW_CLEAN, "round": 2}),
        ]
    )
    result = PlantsStage(match=_match_new, chat=chat, build=_build_ok).run(
        _ctx(tmp_path, world)
    )
    assert result["rounds"] == {"wood_sorrel": 2}
    assert "year.leaves" in chat.prompts[2]


def test_review_blocking_fixed_next_round(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    blocking = {
        "species": "wood_sorrel",
        "round": 1,
        "issues": [
            {
                "part": "leaves",
                "problem": "Missing.",
                "severity": "blocking",
                "fix": "Add them.",
            }
        ],
    }
    chat = ScriptChat(
        [
            json.dumps(SPEC),
            json.dumps(blocking),
            json.dumps(SPEC),
            json.dumps({**REVIEW_CLEAN, "round": 2}),
        ]
    )
    result = PlantsStage(match=_match_new, chat=chat, build=_build_ok).run(
        _ctx(tmp_path, world)
    )
    assert result["rounds"] == {"wood_sorrel": 2}
    assert "Missing." in chat.prompts[2]
    assert "Simple, not realistic" in chat.prompts[1]
    assert "never `blocking`" in chat.prompts[1]


def test_rounds_cap(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    blocking = {
        "species": "wood_sorrel",
        "round": 1,
        "issues": [
            {
                "part": "leaves",
                "problem": "Missing.",
                "severity": "blocking",
                "fix": "Add them.",
            }
        ],
    }
    answers = []
    for round_no in range(1, 7):
        answers.append(json.dumps(SPEC))
        answers.append(json.dumps({**blocking, "round": round_no}))
    chat = ScriptChat(answers)
    pipeline = Pipeline(
        [PlantsStage(match=_match_new, chat=chat, build=_build_ok)],
        tmp_path / "state.json",
    )
    with pytest.raises(StageFailedError, match="stage plants failed"):
        pipeline.run(_ctx(tmp_path, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "still failing after 6 rounds" in state["failed"]["error"]


def test_silent_build_stops_at_once(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    chat = ScriptChat([json.dumps(SPEC)])

    def silent(sandbox: Path, plant_id: str) -> dict[str, Any]:
        return {"ok": False, "summary": "no build", "log": ""}

    pipeline = Pipeline(
        [PlantsStage(match=_match_new, chat=chat, build=silent)],
        tmp_path / "state.json",
    )
    with pytest.raises(StageFailedError, match="stage plants failed"):
        pipeline.run(_ctx(tmp_path, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "with no output" in state["failed"]["error"]
    assert len(chat.prompts) == 1, "no fix rounds on an environment failure"


def test_food_plant_flagged_for_expert(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(
        tmp_path,
        [
            {
                "model": "wood_sorrel",
                "names": ["wood sorrel"],
                "density": 0.2,
                "food": {"kind": "berries", "part": "leaf"},
            }
        ],
    )
    chat = ScriptChat([json.dumps(SPEC), json.dumps(REVIEW_CLEAN)])
    result = PlantsStage(match=_match_new, chat=chat, build=_build_ok).run(
        _ctx(tmp_path, world)
    )
    assert result["expert_review"] == ["wood_sorrel"]


def test_schema_break_fixed_next_round(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    warned = dict(SPEC, warning="Oxalic acid: don't eat much.")
    chat = ScriptChat(
        [
            json.dumps(warned),
            json.dumps(SPEC),
            json.dumps({**REVIEW_CLEAN, "round": 2}),
        ]
    )
    result = PlantsStage(match=_match_new, chat=chat, build=_build_ok).run(
        _ctx(tmp_path, world)
    )
    assert result["rounds"] == {"wood_sorrel": 2}
    assert "plant schema" in chat.prompts[1]


def test_empty_draft_retries_once(tmp_path: Path) -> None:
    """#140.4: an empty draft retries once, like a cut one."""
    world = _world(tmp_path)
    _inputs(tmp_path)
    chat = ScriptChat(["", json.dumps(SPEC), json.dumps(REVIEW_CLEAN)])
    result = PlantsStage(match=_match_new, chat=chat, build=_build_ok).run(
        _ctx(tmp_path, world)
    )
    assert result["plant_files"] == ["models/plants/wood_sorrel.json"]
    assert result["rounds"] == {"wood_sorrel": 1}
    assert len(chat.prompts) == 3


def test_empty_draft_twice_stops(tmp_path: Path) -> None:
    """#140.4: two empty drafts stop, as one bad draft did."""
    world = _world(tmp_path)
    _inputs(tmp_path)
    chat = ScriptChat(["", ""])
    with pytest.raises(_Stopped, match="was not JSON"):
        PlantsStage(match=_match_new, chat=chat, build=_build_ok).run(
            _ctx(tmp_path, world)
        )
    assert len(chat.prompts) == 2


def test_poison_notes_flagged_for_expert(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    noted = dict(SPEC, year={**SPEC["year"], "notes": "Poisonous in large amounts."})
    chat = ScriptChat([json.dumps(noted), json.dumps(REVIEW_CLEAN)])
    result = PlantsStage(match=_match_new, chat=chat, build=_build_ok).run(
        _ctx(tmp_path, world)
    )
    assert result["expert_review"] == ["wood_sorrel"]


def test_listed_plant_flagged_for_expert(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)

    def match(world: Path, latin: str) -> dict[str, Any]:
        return {
            "taxon": {"gbif_key": 999, "rank": "SPECIES", "name": latin},
            "files": [],
        }

    chat = ScriptChat([json.dumps(SPEC), json.dumps(REVIEW_CLEAN)])
    result = PlantsStage(match=match, chat=chat, build=_build_ok).run(
        _ctx(tmp_path, world)
    )
    assert result["expert_review"] == ["wood_sorrel"]


def test_missing_preview_line_skipped(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    (world / "tools" / "check_draft.py").write_text(
        "import json;print(json.dumps({'ok': False, 'results': ["
        "{'check': 'models', 'status': 'fail', 'problems': ["
        "'ERROR: wood_sorrel: the preview sheet is missing: run "
        "tools/build_model.sh models/plants/wood_sorrel.json']}]}))"
    )
    chat = ScriptChat([json.dumps(SPEC), json.dumps(REVIEW_CLEAN)])
    result = PlantsStage(match=_match_new, chat=chat, build=_build_ok).run(
        _ctx(tmp_path, world)
    )
    assert result["rounds"] == {"wood_sorrel": 1}


def test_unowned_problem_stops(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    (world / "tools" / "check_draft.py").write_text(
        "import json;print(json.dumps({'ok': False, 'results': ["
        "{'check': 'models', 'status': 'fail', "
        "'problems': ['ERROR: bilberry is on fire']}]}))"
    )
    chat = ScriptChat([json.dumps(SPEC)])
    pipeline = Pipeline(
        [PlantsStage(match=_match_new, chat=chat, build=_build_ok)],
        tmp_path / "state.json",
    )
    with pytest.raises(StageFailedError, match="stage plants failed"):
        pipeline.run(_ctx(tmp_path, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "outside the new plant" in state["failed"]["error"]
