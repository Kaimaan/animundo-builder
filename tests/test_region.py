"""Region stage: mechanical fields, validator loop, planned species."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from builder.pipeline import BudgetTracker, JobContext, Pipeline, StageFailedError
from builder.stages.region import (
    RegionStage,
    seed_for_area,
    sensitive_place_errors,
)

DRAFT_STUB = """\
import glob
import json
import os
import sys

files = glob.glob(os.path.join(sys.argv[1], "regions", "*.json"))
region = json.load(open(files[0])) if files else {}
problems = []
if region.get("name") == "Bad":
    problems += ["FAIL regions/gb_test.json", '- "name" must be good']
for a in region.get("animals", {}).get("playable", []):
    if a != "fox":
        problems += [
            "FAIL regions/gb_test.json",
            f'- "animals.playable" has unknown animal "{a}" (known: ["fox"])',
        ]
status = "fail" if problems else "pass"
report = {
    "ok": not problems,
    "draft_files": ["regions/gb_test.json"],
    "results": [
        {"check": "locale", "status": "pass", "problems": []},
        {"check": "schema", "status": "pass", "problems": []},
        {"check": "taxa", "status": "pass", "problems": []},
        {"check": "region", "status": status, "problems": problems},
        {"check": "animals", "status": "skip", "problems": [], "reason": "stub"},
        {"check": "models", "status": "skip", "problems": [], "reason": "stub"},
    ],
}
print(json.dumps(report))
"""


def _world(tmp_path: Path) -> Path:
    world = tmp_path / "world"
    (world / "tools").mkdir(parents=True)
    (world / "tools" / "check_draft.py").write_text(DRAFT_STUB)
    (world / "CONTRIBUTING.md").write_text(
        "# World\n\n## The region file\n\nName it well.\n\n## Next\n\nMore.\n"
    )
    regions = world / "regions"
    regions.mkdir(parents=True)
    (regions / "aa_one.json").write_text('{"id": "aa_one"}')
    (regions / "bb_two.json").write_text('{"id": "bb_two"}')
    (world / "schemas").mkdir(parents=True)
    (world / "schemas" / "region.schema.json").write_text(
        '{"type": "object", "required": ["name"]}'
    )
    (world / "animals").mkdir(parents=True)
    (world / "animals" / "fox.json").write_text("{}")
    (world / "animals" / "archetypes.json").write_text("{}")
    (world / "models" / "plants").mkdir(parents=True)
    (world / "models" / "plants" / "moss.json").write_text("{}")
    (world / "models" / "plants" / "fern.json").write_text("{}")
    (world / "assets" / "plants").mkdir(parents=True)
    (world / "assets" / "plants" / "moss.glb").write_text("fake-glb")
    return world


def _inputs(tmp_path: Path) -> None:
    (tmp_path / "area.json").write_text(
        json.dumps(
            {
                "area_id": "gb_test",
                "name": "Test",
                "country": "GB",
                "centroid": {"lat": 57.0, "lon": -3.0},
            }
        )
    )
    (tmp_path / "survey.json").write_text(
        json.dumps(
            {
                "area_id": "gb_test",
                "facts": [
                    {
                        "topic": "forest",
                        "text": "Pine.",
                        "sources": ["https://example.org/a"],
                    }
                ],
                "species": [
                    {
                        "species": "fox",
                        "role": "predator",
                        "evidence": "Seen.",
                        "sources": ["https://example.org/b"],
                    },
                    {
                        "species": "zz_new",
                        "role": "playable",
                        "evidence": "Seen.",
                        "sources": ["https://example.org/c"],
                    },
                ],
            }
        )
    )
    (tmp_path / "capabilities.json").write_text(
        json.dumps({"area_id": "gb_test", "possible": True, "needs_code": []})
    )


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

    def __init__(self, scripts: list[dict]) -> None:
        self.scripts = list(scripts)
        self.calls: list[dict] = []
        self.prompts: list[str] = []

    def chat(self, messages: list[dict], **kwargs: Any) -> SimpleNamespace:
        content = messages[-1]["content"]
        self.prompts.append(content if isinstance(content, str) else content[0]["text"])
        self.calls.append(kwargs)
        content = json.dumps(self.scripts.pop(0))
        message = SimpleNamespace(content=content, tool_calls=None)
        return SimpleNamespace(
            model="fake-test-model",
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=10, prompt_tokens_details=None
            ),
        )


def _answer(**overrides: Any) -> dict:
    region: dict[str, Any] = {
        "id": "wrong",
        "name": "Good",
        "country": "Nowhere",
        "description": "A test place.",
        "author": "wrong",
        "status": "draft",
        "center": {"lat": 0.0, "lon": 0.0},
        "terrain": {"seed": 1},
        "sources": ["https://a.example", "https://b.example", "https://c.example"],
        "animals": {"playable": ["fox", "zz_new"], "predators": [], "prey": []},
    }
    region.update(overrides)
    return region


def test_writes_validated_region(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient([_answer()])
    result = RegionStage(author="Tester").run(_ctx(tmp_path, client, world))
    assert result["planned_species"] == ["zz_new"]
    written = json.loads((tmp_path / "regions" / "gb_test.json").read_text())
    assert written["id"] == "gb_test"
    assert written["author"] == "Tester"
    assert written["status"] == "reviewed"
    assert written["center"] == {"lat": 57.0, "lon": -3.0}
    assert written["terrain"]["seed"] == seed_for_area("gb_test")
    assert "response_format" not in client.calls[0]
    saved = json.loads((tmp_path / "checks" / "region.json").read_text())
    assert [entry["check"] for entry in saved["results"]] == [
        "locale",
        "schema",
        "taxa",
        "region",
        "animals",
        "models",
    ]


def test_validator_error_sent_back(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient([_answer(name="Bad"), _answer()])
    result = RegionStage().run(_ctx(tmp_path, client, world))
    assert result["rounds"] == 2
    written = json.loads((tmp_path / "regions" / "gb_test.json").read_text())
    assert written["name"] == "Good"


def test_schema_break_sent_back(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    nameless = _answer()
    del nameless["name"]
    client = ScriptClient([nameless, _answer()])
    RegionStage().run(_ctx(tmp_path, client, world))
    assert len(client.calls) == 2
    assert "region schema" in client.prompts[1]


def test_unknown_non_surveyed_animal_is_an_error(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    bad = _answer()
    bad["animals"]["playable"] = ["fox", "zz_stranger"]
    client = ScriptClient([bad, _answer()])
    RegionStage().run(_ctx(tmp_path, client, world))
    assert len(client.calls) == 2


def test_missing_surveyed_species_sent_back(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    thin = _answer()
    thin["animals"]["playable"] = ["fox"]
    client = ScriptClient([thin, _answer()])
    RegionStage().run(_ctx(tmp_path, client, world))
    assert len(client.calls) == 2
    assert "zz_new" in client.prompts[1]
    assert "does not exist yet" in client.prompts[1]
    assert "fox" in client.prompts[0]
    assert "moss" in client.prompts[0]
    assert "fern" not in client.prompts[0], "unbuilt plants are not offered"


def test_rounds_exhausted(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient([_answer(name="Bad")] * 3)
    pipeline = Pipeline([RegionStage(max_rounds=2)], tmp_path / "state.json")
    with pytest.raises(StageFailedError):
        pipeline.run(_ctx(tmp_path, client, world))


def test_skipped_region_check_stops(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    (world / "tools" / "check_draft.py").write_text(
        "import json;print(json.dumps({'ok': True, 'results': ["
        "{'check': 'region', 'status': 'skip', "
        "'problems': [], 'reason': 'no models yet'}]}))"
    )
    client = ScriptClient([_answer()])
    pipeline = Pipeline([RegionStage()], tmp_path / "state.json")
    with pytest.raises(StageFailedError, match="stage region failed"):
        pipeline.run(_ctx(tmp_path, client, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "skipped" in state["failed"]["error"]


def test_seed_is_stable_per_area() -> None:
    assert seed_for_area("gb_test") == seed_for_area("gb_test")
    assert seed_for_area("gb_test") != seed_for_area("fi_test")
    assert 0 <= seed_for_area("gb_test") < 2**32


def test_named_nest_site_sent_back(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    survey = json.loads((tmp_path / "survey.json").read_text())
    survey["species"].append(
        {
            "species": "golden_eagle",
            "role": "predator",
            "evidence": "Seen.",
            "sources": ["https://example.org/d"],
            "sensitive": True,
        }
    )
    (tmp_path / "survey.json").write_text(json.dumps(survey))
    nested = _answer(
        animals={
            "playable": ["fox", "zz_new"],
            "predators": ["golden_eagle"],
            "prey": [],
        },
        places=[{"name": "Eagle nest on Grey Crag"}],
    )
    clean = _answer(
        animals={
            "playable": ["fox", "zz_new"],
            "predators": ["golden_eagle"],
            "prey": [],
        },
        places=[],
    )
    client = ScriptClient([nested, clean])
    RegionStage().run(_ctx(tmp_path, client, world))
    assert len(client.calls) == 2
    assert "golden_eagle" in client.prompts[1]


def test_sensitive_place_errors() -> None:
    surveyed = [
        {"species": "golden_eagle", "sensitive": True},
        {"species": "fox", "sensitive": False},
    ]
    assert sensitive_place_errors(surveyed, [{"name": "Grey Fell"}]) == []
    assert sensitive_place_errors(surveyed, None) == []
    errors = sensitive_place_errors(surveyed, [{"name": "Eagle Crag"}])
    assert len(errors) == 1
    assert "golden_eagle" in errors[0]
    errors = sensitive_place_errors(surveyed, [{"name": "Fox den"}])
    assert errors == []
    errors = sensitive_place_errors(
        surveyed, [{"name": "Wolverine den"}], ["wolverine"]
    )
    assert len(errors) == 1
    assert "wolverine" in errors[0]


def test_unknown_plant_lines_excused(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    (world / "tools" / "check_draft.py").write_text(
        "import json;print(json.dumps({'ok': False, 'results': ["
        "{'check': 'locale', 'status': 'pass', 'problems': []},"
        "{'check': 'schema', 'status': 'pass', 'problems': []},"
        "{'check': 'taxa', 'status': 'pass', 'problems': []},"
        "{'check': 'region', 'status': 'fail', 'problems': ["
        "'FAIL regions/gb_test.json',"
        '\'- "ground_cover.plants[0].model" must be a plant '
        'in models/plants/ (known: ["moss"])\']},'
        "{'check': 'animals', 'status': 'skip', 'problems': []},"
        "{'check': 'models', 'status': 'skip', 'problems': []}]}))"
    )
    draft = _answer(ground_cover={"plants": [{"model": "sorrel", "density": 0.2}]})
    client = ScriptClient([draft])
    result = RegionStage().run(_ctx(tmp_path, client, world))
    assert result["rounds"] == 1


def test_fourth_new_plant_sent_back(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    draft = _answer(
        ground_cover={
            "plants": [{"model": f"weed_{n}", "density": 0.1} for n in range(4)]
        }
    )
    client = ScriptClient([draft, _answer()])
    RegionStage().run(_ctx(tmp_path, client, world))
    assert len(client.calls) == 2
    assert "at most 3" in client.prompts[1]


def test_unbuilt_checkout_plant_sent_back(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    draft = _answer(ground_cover={"plants": [{"model": "fern", "density": 0.2}]})
    client = ScriptClient([draft, _answer()])
    RegionStage().run(_ctx(tmp_path, client, world))
    assert "not built" in client.prompts[1]
