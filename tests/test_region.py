"""Region stage: mechanical fields, validator loop, planned species."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from builder.pipeline import BudgetTracker, JobContext, Pipeline, StageFailedError
from builder.stages.region import RegionStage, seed_for_area

VALIDATOR_STUB = """\
import json
import sys

d = json.load(open(sys.argv[1]))
errors = []
if d.get("name") == "Bad":
    errors.append('"name" must be good')
for a in d.get("animals", {}).get("playable", []):
    if a != "fox":
        errors.append(f'"animals.playable" has unknown animal "{a}" (known: ["fox"])')
if errors:
    print("FAIL x")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
print("ok   x")
"""


def _world(tmp_path: Path) -> Path:
    world = tmp_path / "world"
    (world / "tools").mkdir(parents=True)
    (world / "tools" / "validate_region.py").write_text(VALIDATOR_STUB)
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

    def __init__(self, drafts: list[dict]) -> None:
        self.drafts = list(drafts)
        self.calls: list[dict] = []
        self.prompts: list[str] = []

    def chat(self, messages: list[dict], **kwargs: Any) -> SimpleNamespace:
        self.prompts.append(messages[0]["content"])
        self.calls.append(kwargs)
        content = json.dumps(self.drafts.pop(0))
        message = SimpleNamespace(content=content, tool_calls=None)
        return SimpleNamespace(
            model="fake-test-model",
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=10, prompt_tokens_details=None
            ),
        )


def _draft(**overrides: Any) -> dict:
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
    client = ScriptClient([_draft()])
    result = RegionStage(author="Tester").run(_ctx(tmp_path, client, world))
    assert result["planned_species"] == ["zz_new"]
    written = json.loads((tmp_path / "regions" / "gb_test.json").read_text())
    assert written["id"] == "gb_test"
    assert written["author"] == "Tester"
    assert written["status"] == "reviewed"
    assert written["center"] == {"lat": 57.0, "lon": -3.0}
    assert written["terrain"]["seed"] == seed_for_area("gb_test")
    assert "response_format" not in client.calls[0]


def test_validator_error_sent_back(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient([_draft(name="Bad"), _draft()])
    result = RegionStage().run(_ctx(tmp_path, client, world))
    assert result["rounds"] == 2
    written = json.loads((tmp_path / "regions" / "gb_test.json").read_text())
    assert written["name"] == "Good"


def test_schema_break_sent_back(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    nameless = _draft()
    del nameless["name"]
    client = ScriptClient([nameless, _draft()])
    RegionStage().run(_ctx(tmp_path, client, world))
    assert len(client.calls) == 2
    assert "region schema" in client.prompts[1]


def test_unknown_non_surveyed_animal_is_an_error(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    bad = _draft()
    bad["animals"]["playable"] = ["fox", "zz_stranger"]
    client = ScriptClient([bad, _draft()])
    RegionStage().run(_ctx(tmp_path, client, world))
    assert len(client.calls) == 2


def test_missing_surveyed_species_sent_back(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    thin = _draft()
    thin["animals"]["playable"] = ["fox"]
    client = ScriptClient([thin, _draft()])
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
    client = ScriptClient([_draft(name="Bad")] * 3)
    pipeline = Pipeline([RegionStage(max_rounds=2)], tmp_path / "state.json")
    with pytest.raises(StageFailedError):
        pipeline.run(_ctx(tmp_path, client, world))


def test_seed_is_stable_per_area() -> None:
    assert seed_for_area("gb_test") == seed_for_area("gb_test")
    assert seed_for_area("gb_test") != seed_for_area("fi_test")
    assert 0 <= seed_for_area("gb_test") < 2**32
