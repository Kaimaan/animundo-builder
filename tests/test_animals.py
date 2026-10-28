"""Animal stage: scripted answers, checker loop, and round caps. No network."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from builder.pipeline import BudgetTracker, JobContext, Pipeline, StageFailedError
from builder.stages.animals import AnimalsStage

DRAFT_STUB = """\
import glob
import json
import os
import sys

draft = sys.argv[1]
foods = set()
for path in glob.glob(os.path.join(draft, "regions", "*.json")):
    region = json.load(open(path))
    foods.update(f.get("kind") for f in region.get("food", []))
schema_problems = []
# The world's own unbuilt models fail these verdicts on main; the stage
# must pass anyway. Every green test below proves the ignore rule.
animals_problems = ["ERROR: bee: model file not found: res://assets/animals/bee.glb"]
for path in sorted(glob.glob(os.path.join(draft, "animals", "*.json"))):
    animal_id = os.path.basename(path)[:-5]
    data = json.load(open(path))
    if "name" not in data:
        schema_problems += [
            f"FAIL animals/{animal_id}.json",
            "- 'name' is a required property",
        ]
    for kind in data.get("diet", []):
        if kind not in foods:
            animals_problems.append(
                f'ERROR: {animal_id}: diet kind "{kind}" not in region food'
            )
report = {
    "ok": False,
    "draft_files": [],
    "results": [
        {"check": "locale", "status": "pass", "problems": []},
        {
            "check": "schema",
            "status": "fail" if schema_problems else "pass",
            "problems": schema_problems,
        },
        {"check": "taxa", "status": "pass", "problems": []},
        {"check": "region", "status": "pass", "problems": []},
        {"check": "animals", "status": "fail", "problems": animals_problems},
        {
            "check": "models",
            "status": "fail",
            "problems": [
                "ERROR: bee: res://assets/animals/bee.glb is missing. "
                "Run tools/build_model.sh models/bee.json"
            ],
        },
    ],
}
print(json.dumps(report))
"""


def _world(tmp_path: Path) -> Path:
    world = tmp_path / "world"
    (world / "tools").mkdir(parents=True)
    (world / "tools" / "check_draft.py").write_text(DRAFT_STUB)
    (world / "CONTRIBUTING.md").write_text(
        "# World\n\n## Adding animals\n\nName it well.\n\n## Next\n\nMore.\n"
    )
    animals = world / "animals"
    animals.mkdir(parents=True)
    (animals / "fox.json").write_text(
        json.dumps({"latin": "Vulpes vulpes", "tags": ["predator"]})
    )
    (animals / "mountain_hare.json").write_text(
        json.dumps({"latin": "Lepus timidus", "tags": ["prey"]})
    )
    (animals / "archetypes.json").write_text(json.dumps({"land_mammal": {}}))
    (world / "schemas").mkdir(parents=True)
    (world / "schemas" / "animal.schema.json").write_text(
        '{"type": "object", "required": ["name"]}'
    )
    (world / "regions").mkdir(parents=True)
    return world


def _inputs(tmp_path: Path) -> None:
    (tmp_path / "survey.json").write_text(
        json.dumps(
            {
                "area_id": "xx_test",
                "facts": [],
                "species": [
                    {
                        "species": "zz_deer",
                        "latin": "Cervus fictus",
                        "role": "playable",
                        "evidence": "Seen.",
                        "sources": ["https://example.org/x"],
                    },
                    {
                        "species": "zz_owl",
                        "latin": "Bubo fictus",
                        "role": "predator",
                        "evidence": "Heard.",
                        "sources": ["https://example.org/y"],
                    },
                ],
            }
        )
    )
    (tmp_path / "species-plan.json").write_text(
        json.dumps(
            {
                "species": [
                    {
                        "species": "zz_deer",
                        "decision": "new",
                        "reason": "New.",
                        "taxon": {
                            "gbif_key": 1,
                            "rank": "SPECIES",
                            "name": "Cervus fictus",
                        },
                    },
                    {
                        "species": "zz_owl",
                        "decision": "new",
                        "reason": "New.",
                        "taxon": {
                            "gbif_key": 2,
                            "rank": "SPECIES",
                            "name": "Bubo fictus",
                        },
                    },
                ],
                "food_web_ok": True,
                "missing_food": [],
            }
        )
    )
    regions = tmp_path / "regions"
    regions.mkdir(parents=True)
    (regions / "xx_test.json").write_text(
        json.dumps(
            {
                "id": "xx_test",
                "animals": {
                    "playable": ["zz_deer"],
                    "predators": ["zz_owl"],
                    "prey": [],
                },
                "food": [{"kind": "grass"}, {"kind": "berries"}],
            }
        )
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

    def __init__(self, scripts: dict[str, list[dict]]) -> None:
        self.scripts = {k: list(v) for k, v in scripts.items()}
        self.prompts: list[str] = []

    def chat(self, messages: list[dict], **kwargs: Any) -> SimpleNamespace:
        content = messages[0]["content"]
        last = messages[-1]["content"]
        self.prompts.append(last if isinstance(last, str) else last[0]["text"])
        species_id = re.search(r'"species": "([^"]+)"', content).group(1)  # type: ignore[union-attr]
        answer = self.scripts[species_id].pop(0)
        message = SimpleNamespace(content=json.dumps(answer), tool_calls=None)
        return SimpleNamespace(
            model="fake-test-model",
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=10, prompt_tokens_details=None
            ),
        )


def _answer(species_id: str, **overrides: Any) -> dict:
    answer: dict[str, Any] = {
        "id": species_id,
        "name": "Good",
        "latin": "X y",
        "diet": ["grass"],
        "text": {"title": "T"},
    }
    answer.update(overrides)
    return answer


def test_writes_valid_animals(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient(
        {"zz_deer": [_answer("zz_deer")], "zz_owl": [_answer("zz_owl")]}
    )
    result = AnimalsStage().run(_ctx(tmp_path, client, world))
    assert result["animal_files"] == ["animals/zz_deer.json", "animals/zz_owl.json"]
    assert result["rounds"] == {"zz_deer": 1, "zz_owl": 1}
    assert (
        json.loads((tmp_path / "animals" / "zz_deer.json").read_text())["name"]
        == "Good"
    )


def test_checker_error_fixed_per_species(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    bad = _answer("zz_owl", diet=["meat"])
    client = ScriptClient(
        {"zz_deer": [_answer("zz_deer")], "zz_owl": [bad, _answer("zz_owl")]}
    )
    result = AnimalsStage().run(_ctx(tmp_path, client, world))
    assert result["rounds"] == {"zz_deer": 1, "zz_owl": 2}
    assert "not in region food" in client.prompts[-1]


def test_missing_world_models_are_ignored(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient(
        {"zz_deer": [_answer("zz_deer")], "zz_owl": [_answer("zz_owl")]}
    )
    result = AnimalsStage().run(_ctx(tmp_path, client, world))
    assert result["rounds"] == {"zz_deer": 1, "zz_owl": 1}
    saved = json.loads((tmp_path / "checks" / "animals.json").read_text())
    by_check = {entry["check"]: entry["status"] for entry in saved["results"]}
    assert by_check["animals"] == "fail", "bee is missing, and ignored"
    assert by_check["models"] == "fail"


def test_new_species_missing_model_is_ignored(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    stub = world / "tools" / "check_draft.py"
    stub.write_text(
        stub.read_text().replace(
            'animals_problems = ["ERROR: bee: model file not found:',
            'animals_problems = ["ERROR: zz_deer: model file not found: '
            'res://assets/animals/zz_deer.glb", "ERROR: bee: model file not found:',
        )
    )
    client = ScriptClient(
        {"zz_deer": [_answer("zz_deer")], "zz_owl": [_answer("zz_owl")]}
    )
    result = AnimalsStage().run(_ctx(tmp_path, client, world))
    assert result["rounds"] == {"zz_deer": 1, "zz_owl": 1}


def test_unowned_problem_stops(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    (world / "tools" / "check_draft.py").write_text(
        "import json;print(json.dumps({'ok': False, 'results': ["
        "{'check': 'animals', 'status': 'fail', "
        "'problems': ['ERROR: taxonomy database is on fire']}]}))"
    )
    client = ScriptClient(
        {"zz_deer": [_answer("zz_deer")], "zz_owl": [_answer("zz_owl")]}
    )
    pipeline = Pipeline([AnimalsStage()], tmp_path / "state.json")
    with pytest.raises(StageFailedError, match="stage animals failed"):
        pipeline.run(_ctx(tmp_path, client, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "outside the new files" in state["failed"]["error"]


def test_schema_break_fixed_per_species(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    nameless = _answer("zz_owl")
    del nameless["name"]
    client = ScriptClient(
        {"zz_deer": [_answer("zz_deer")], "zz_owl": [nameless, _answer("zz_owl")]}
    )
    result = AnimalsStage().run(_ctx(tmp_path, client, world))
    assert result["rounds"] == {"zz_deer": 1, "zz_owl": 2}
    assert "animal schema" in client.prompts[-1]


def test_rounds_cap_per_species(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    bad = [_answer("zz_owl", diet=["meat"]) for _ in range(8)]
    client = ScriptClient({"zz_deer": [_answer("zz_deer")], "zz_owl": bad})
    pipeline = Pipeline([AnimalsStage()], tmp_path / "state.json")
    with pytest.raises(StageFailedError, match="stage animals failed"):
        pipeline.run(_ctx(tmp_path, client, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "zz_owl spent its 6 rounds" in state["failed"]["error"]


def test_crash_keeps_paid_work(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    bad = [_answer("zz_owl", diet=["meat"]) for _ in range(8)]
    client = ScriptClient({"zz_deer": [_answer("zz_deer")], "zz_owl": bad})
    pipeline = Pipeline([AnimalsStage()], tmp_path / "state.json")
    with pytest.raises(StageFailedError, match="stage animals failed"):
        pipeline.run(_ctx(tmp_path, client, world))
    assert (
        json.loads((tmp_path / "animals" / "zz_deer.json").read_text())["name"]
        == "Good"
    )
    progress = json.loads((tmp_path / "animals" / "progress.json").read_text())
    assert progress["passed"] == ["zz_deer"]
    assert progress["rounds"]["zz_deer"] == 1


def test_resume_carries_on_with_the_rest(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    animals_dir = tmp_path / "animals"
    animals_dir.mkdir()
    (animals_dir / "zz_deer.json").write_text(
        json.dumps(_answer("zz_deer")), encoding="utf-8"
    )
    (animals_dir / "zz_owl.json").write_text(
        json.dumps(_answer("zz_owl", diet=["meat"])), encoding="utf-8"
    )
    (animals_dir / "progress.json").write_text(
        json.dumps({"rounds": {"zz_deer": 1, "zz_owl": 1}, "passed": ["zz_deer"]}),
        encoding="utf-8",
    )
    client = ScriptClient({"zz_owl": [_answer("zz_owl")]})
    result = AnimalsStage().run(_ctx(tmp_path, client, world))
    assert result["rounds"] == {"zz_deer": 1, "zz_owl": 2}
    assert len(client.prompts) == 1
    progress = json.loads((animals_dir / "progress.json").read_text())
    assert sorted(progress["passed"]) == ["zz_deer", "zz_owl"]


def test_taxon_reaches_the_prompt(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient(
        {"zz_deer": [_answer("zz_deer")], "zz_owl": [_answer("zz_owl")]}
    )
    AnimalsStage().run(_ctx(tmp_path, client, world))
    by_species = {
        re.search(r'"species": "([^"]+)"', p).group(1): p for p in client.prompts
    }  # type: ignore[union-attr]
    assert '"gbif_key": 1' in by_species["zz_deer"]
    assert "Cervus fictus" in by_species["zz_deer"]
    assert '"gbif_key": 2' in by_species["zz_owl"]


def test_cast_tags_reach_the_prompt(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    (world / "animals" / "fox.json").write_text(
        json.dumps({"latin": "Vulpes vulpes", "tags": ["predator"]})
    )
    client = ScriptClient(
        {"zz_deer": [_answer("zz_deer")], "zz_owl": [_answer("zz_owl")]}
    )
    AnimalsStage().run(_ctx(tmp_path, client, world))
    assert "predator" in client.prompts[0]
    assert "never animal ids" in client.prompts[0]


def test_role_examples_in_prompt(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient(
        {"zz_deer": [_answer("zz_deer")], "zz_owl": [_answer("zz_owl")]}
    )
    AnimalsStage().run(_ctx(tmp_path, client, world))
    by_species = {
        re.search(r'"species": "([^"]+)"', p).group(1): p for p in client.prompts
    }  # type: ignore[union-attr]
    assert "mountain_hare" in by_species["zz_deer"]
    assert "Vulpes vulpes" in by_species["zz_owl"]


PLANT_LINE = (
    '"ground_cover.plants[0].model" must be a plant in models/plants/ '
    "(known: ['bilberry'])"
)


def _region_with_plant(tmp_path: Path) -> None:
    path = tmp_path / "regions" / "xx_test.json"
    region = json.loads(path.read_text())
    region["ground_cover"] = {"plants": [{"model": "zz_cloudberry"}]}
    path.write_text(json.dumps(region))


def _region_verdict(problems: list) -> str:
    return (
        "import json;print(json.dumps({'ok': False, 'results': ["
        "{'check': 'region', 'status': 'fail', 'problems': "
        + json.dumps(problems).replace("'", "\\'")
        + "}]}))"
    )


def test_new_plant_region_failure_is_excused(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    _region_with_plant(tmp_path)
    (world / "tools" / "check_draft.py").write_text(_region_verdict([PLANT_LINE]))
    client = ScriptClient(
        {"zz_deer": [_answer("zz_deer")], "zz_owl": [_answer("zz_owl")]}
    )
    result = AnimalsStage().run(_ctx(tmp_path, client, world))
    assert result["rounds"] == {"zz_deer": 1, "zz_owl": 1}


def test_known_plant_region_failure_still_stops(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    _region_with_plant(tmp_path)
    plants = world / "models" / "plants"
    plants.mkdir(parents=True)
    (plants / "zz_cloudberry.json").write_text("{}")
    (world / "tools" / "check_draft.py").write_text(_region_verdict([PLANT_LINE]))
    client = ScriptClient(
        {"zz_deer": [_answer("zz_deer")], "zz_owl": [_answer("zz_owl")]}
    )
    pipeline = Pipeline([AnimalsStage()], tmp_path / "state.json")
    with pytest.raises(StageFailedError, match="stage animals failed"):
        pipeline.run(_ctx(tmp_path, client, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "the region broke after stage 4" in state["failed"]["error"]


def test_other_region_failure_still_stops(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    (world / "tools" / "check_draft.py").write_text(
        _region_verdict(["ERROR: the terrain seed is missing"])
    )
    client = ScriptClient(
        {"zz_deer": [_answer("zz_deer")], "zz_owl": [_answer("zz_owl")]}
    )
    pipeline = Pipeline([AnimalsStage()], tmp_path / "state.json")
    with pytest.raises(StageFailedError, match="stage animals failed"):
        pipeline.run(_ctx(tmp_path, client, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "the region broke after stage 4" in state["failed"]["error"]
