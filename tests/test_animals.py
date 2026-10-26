"""Animal stage: drafts, checker loop, and round caps. No network."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from builder.pipeline import BudgetTracker, JobContext, Pipeline, StageFailedError
from builder.stages.animals import AnimalsStage

CHECKER_STUB = """\
import argparse
import glob
import json
import os

ap = argparse.ArgumentParser()
ap.add_argument("--root", required=True)
ap.add_argument("--no-built-models", action="store_true")
args = ap.parse_args()
errors = []
foods = set()
for path in glob.glob(os.path.join(args.root, "regions", "*.json")):
    region = json.load(open(path))
    foods.update(f.get("kind") for f in region.get("food", []))
for path in sorted(glob.glob(os.path.join(args.root, "animals", "*.json"))):
    animal_id = os.path.basename(path)[:-5]
    if animal_id in ("archetypes", "fox", "mountain_hare"):
        continue
    data = json.load(open(path))
    if "name" not in data:
        errors.append(f"ERROR: {animal_id}: missing name")
    for kind in data.get("diet", []):
        if kind not in foods:
            errors.append(f'ERROR: {animal_id}: diet kind "{kind}" not in region food')
print("warning: some texts not translated yet (shown in English)")
print("\\n".join(errors))
sys_exit = 1 if errors else 0
raise SystemExit(sys_exit)
"""


def _world(tmp_path: Path) -> Path:
    world = tmp_path / "world"
    (world / "tools").mkdir(parents=True)
    (world / "tools" / "check_animals.py").write_text(CHECKER_STUB)
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

    def __init__(self, drafts: dict[str, list[dict]]) -> None:
        self.drafts = {k: list(v) for k, v in drafts.items()}
        self.prompts: list[str] = []

    def chat(self, messages: list[dict], **kwargs: Any) -> SimpleNamespace:
        content = messages[0]["content"]
        self.prompts.append(content)
        species_id = re.search(r'"species": "([^"]+)"', content).group(1)  # type: ignore[union-attr]
        draft = self.drafts[species_id].pop(0)
        message = SimpleNamespace(content=json.dumps(draft), tool_calls=None)
        return SimpleNamespace(
            model="fake-test-model",
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=10, prompt_tokens_details=None
            ),
        )


def _draft(species_id: str, **overrides: Any) -> dict:
    draft: dict[str, Any] = {
        "id": species_id,
        "name": "Good",
        "latin": "X y",
        "diet": ["grass"],
        "text": {"title": "T"},
    }
    draft.update(overrides)
    return draft


def test_writes_valid_animals(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient(
        {"zz_deer": [_draft("zz_deer")], "zz_owl": [_draft("zz_owl")]}
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
    bad = _draft("zz_owl", diet=["meat"])
    client = ScriptClient(
        {"zz_deer": [_draft("zz_deer")], "zz_owl": [bad, _draft("zz_owl")]}
    )
    result = AnimalsStage().run(_ctx(tmp_path, client, world))
    assert result["rounds"] == {"zz_deer": 1, "zz_owl": 2}
    assert "not in region food" in client.prompts[-1]


def test_schema_break_fixed_per_species(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    nameless = _draft("zz_owl")
    del nameless["name"]
    client = ScriptClient(
        {"zz_deer": [_draft("zz_deer")], "zz_owl": [nameless, _draft("zz_owl")]}
    )
    result = AnimalsStage().run(_ctx(tmp_path, client, world))
    assert result["rounds"] == {"zz_deer": 1, "zz_owl": 2}
    assert "animal schema" in client.prompts[-1]


def test_rounds_cap_per_species(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    bad = [_draft("zz_owl", diet=["meat"]) for _ in range(8)]
    client = ScriptClient({"zz_deer": [_draft("zz_deer")], "zz_owl": bad})
    pipeline = Pipeline([AnimalsStage()], tmp_path / "state.json")
    with pytest.raises(StageFailedError, match="stage animals failed"):
        pipeline.run(_ctx(tmp_path, client, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "zz_owl spent its 6 rounds" in state["failed"]["error"]


def test_crash_keeps_paid_work(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    bad = [_draft("zz_owl", diet=["meat"]) for _ in range(8)]
    client = ScriptClient({"zz_deer": [_draft("zz_deer")], "zz_owl": bad})
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
        json.dumps(_draft("zz_deer")), encoding="utf-8"
    )
    (animals_dir / "zz_owl.json").write_text(
        json.dumps(_draft("zz_owl", diet=["meat"])), encoding="utf-8"
    )
    (animals_dir / "progress.json").write_text(
        json.dumps({"rounds": {"zz_deer": 1, "zz_owl": 1}, "passed": ["zz_deer"]}),
        encoding="utf-8",
    )
    client = ScriptClient({"zz_owl": [_draft("zz_owl")]})
    result = AnimalsStage().run(_ctx(tmp_path, client, world))
    assert result["rounds"] == {"zz_deer": 1, "zz_owl": 2}
    assert len(client.prompts) == 1
    progress = json.loads((animals_dir / "progress.json").read_text())
    assert sorted(progress["passed"]) == ["zz_deer", "zz_owl"]


def test_taxon_reaches_the_prompt(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient(
        {"zz_deer": [_draft("zz_deer")], "zz_owl": [_draft("zz_owl")]}
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
        {"zz_deer": [_draft("zz_deer")], "zz_owl": [_draft("zz_owl")]}
    )
    AnimalsStage().run(_ctx(tmp_path, client, world))
    assert "predator" in client.prompts[0]
    assert "never animal ids" in client.prompts[0]


def test_role_examples_in_prompt(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient(
        {"zz_deer": [_draft("zz_deer")], "zz_owl": [_draft("zz_owl")]}
    )
    AnimalsStage().run(_ctx(tmp_path, client, world))
    by_species = {
        re.search(r'"species": "([^"]+)"', p).group(1): p for p in client.prompts
    }  # type: ignore[union-attr]
    assert "mountain_hare" in by_species["zz_deer"]
    assert "Vulpes vulpes" in by_species["zz_owl"]
