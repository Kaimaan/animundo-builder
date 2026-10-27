"""The one-shot animal maker: write, build, check, at most one fix. No key."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from builder.pipeline import BudgetTracker, JobContext
from builder.prompts import PROMPTS_DIR
from builder.stages.maker import (
    EDITS_FACTORS,
    AnimalMakerStage,
    HomeMakerStage,
    _description,
    _edits_files,
    _edits_schema,
    _edits_sizes,
    _guide,
    _measure_errors,
    _measure_spec,
    _measurements,
    _part_list,
    _reference_line,
    _reference_looks,
    _reference_row,
    _run_adapt,
    _size_misses,
    _sourced_sizes,
    _Stopped,
    _tool_marks,
)
from builder.strict import assert_strict_subset

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
animals_problems = []
# World-owned problems arrive split out as `existing`, never in
# `problems`: everything left is the draft's and feeds back.
existing = ["ERROR: bee: model file not found: res://assets/animals/bee.glb"]
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
    "existing": existing,
    "results": [
        {"check": "locale", "status": "pass", "problems": []},
        {
            "check": "schema",
            "status": "fail" if schema_problems else "pass",
            "problems": schema_problems,
        },
        {"check": "taxa", "status": "pass", "problems": []},
        {"check": "region", "status": "pass", "problems": []},
        {
            "check": "animals",
            "status": "fail" if animals_problems else "pass",
            "problems": animals_problems,
        },
        {"check": "models", "status": "pass", "problems": []},
    ],
}
print(json.dumps(report))
"""

LYNX = {
    "id": "lynx",
    "name": "Eurasian lynx",
    "latin": "Lynx lynx",
    "gbif_key": 2435240,
    "start_from": "fox",
    "habitats": ["boreal_forest"],
    "can_make": True,
}

CAPYBARA = {
    "id": "capybara",
    "name": "Capybara",
    "latin": "Hydrochoerus hydrochaeris",
    "gbif_key": 2437585,
    "start_from": "fox",
    "habitats": ["rainforest"],
    "can_make": True,
}


def _world(tmp_path: Path) -> Path:
    world = tmp_path / "world"
    (world / "tools").mkdir(parents=True)
    (world / "tools" / "check_draft.py").write_text(DRAFT_STUB)
    (world / "tools" / "build_model.sh").write_text("#!/bin/sh\n")
    (world / "CONTRIBUTING.md").write_text(
        "# World\n\n## Adding animals\n\nName it well.\n\n## Next\n\nMore.\n"
    )
    (world / "ART.md").write_text("# Art\n\nReal first.\n")
    (world / "models").mkdir(parents=True)
    (world / "models" / "README.md").write_text("# Models\n\nOverlap.\n")
    (world / "models" / "fox.json").write_text(
        json.dumps(
            {
                "id": "fox",
                "colors": {"fur": "#c4622b", "belly": "#efe3cf"},
                "bones": [
                    {
                        "name": "body",
                        "head": [0, 0.32, -0.18],
                        "tail": [0, 0.34, 0.15],
                        "role": "body",
                    },
                    {
                        "name": "leg_f_l",
                        "head": [0, 0.3, 0.1],
                        "tail": [0, 0, 0.1],
                        "role": "leg",
                    },
                    {
                        "name": "tail_1",
                        "head": [0, 0.32, -0.18],
                        "tail": [0, 0.3, -0.5],
                        "role": "tail",
                    },
                ],
                "parts": [
                    {
                        "name": "body",
                        "shape": "ball",
                        "size": [0.17, 0.19, 0.46],
                        "at": [0, 0.32, 0],
                        "color": "fur",
                        "bone": "body",
                    },
                    {
                        "name": "arm_l",
                        "shape": "ball",
                        "size": [0.06, 0.18, 0.07],
                        "at": [0.06, 0.24, 0.1],
                        "color": "fur",
                        "bone": "leg_f_l",
                        "mirror": True,
                    },
                    {
                        "name": "tail_1",
                        "shape": "ball",
                        "size": [0.09, 0.09, 0.1],
                        "at": [0, 0.31, -0.3],
                        "color": "fur",
                        "bone": "tail_1",
                    },
                ],
            }
        )
    )
    animals = world / "animals"
    animals.mkdir(parents=True)
    (animals / "fox.json").write_text(
        json.dumps({"latin": "Vulpes vulpes", "tags": ["predator"]})
    )
    (animals / "archetypes.json").write_text(json.dumps({"land_mammal": {}}))
    (world / "schemas").mkdir(parents=True)
    (world / "schemas" / "animal.schema.json").write_text(
        '{"type": "object", "required": ["name"]}'
    )
    (world / "schemas" / "model.schema.json").write_text(
        '{"type": "object", "required": ["id", "parts"]}'
    )
    (world / "animal-list.json").write_text(
        json.dumps(
            {
                "homes": ["boreal_forest"],
                "not_yet": {},
                "animals": [LYNX, CAPYBARA],
            }
        )
    )
    return world


def _inputs(
    tmp_path: Path,
    minset: dict[str, Any] | None = None,
    brief: dict[str, Any] | None = None,
) -> None:
    (tmp_path / "home.json").write_text(
        json.dumps(
            brief
            or {
                "species_key": 2435240,
                "id": "lynx",
                "latin": "Lynx lynx",
                "name": "Eurasian lynx",
                "in_library": None,
                "start_from": "fox",
                "habitats": ["boreal_forest"],
                "home": "boreal_forest",
                "needs_home": False,
            }
        )
    )
    (tmp_path / "research.json").write_text(
        json.dumps(
            {
                "size": {"length_cm": 110, "weight_kg": 22},
                "year": {
                    "winter": "active",
                    "shelter": "It dens in rocky hollows.",
                    "young": "Kittens are born in spring.",
                    "food": "It hunts at dusk.",
                },
                "main_food": {"name": "roe deer", "kind": "prey", "size_cm": 130},
                "other_foods": [{"name": "bilberries", "kind": "berries"}],
                "surroundings": [
                    {"name": "spruce", "kind": "tree"},
                    {"name": "moss", "kind": "ground"},
                    {"name": "pond", "kind": "water"},
                ],
                "sources": ["https://example.org/lynx"],
            }
        )
    )
    (tmp_path / "minset.json").write_text(
        json.dumps(
            minset
            or {
                "make_animal": True,
                "animal_reason": "not in the library yet",
                "make_home": False,
                "home_reason": "boreal_forest is built",
                "home_habitat": "boreal_forest",
                "prey": None,
                "prey_reason": "a library prey shares its habitat",
            }
        )
    )
    regions = tmp_path / "regions"
    regions.mkdir(parents=True, exist_ok=True)
    (regions / "fi_test.json").write_text(
        json.dumps(
            {
                "id": "fi_test",
                "animals": {"playable": ["lynx"], "prey": ["capybara"]},
                "food": [{"kind": "grass"}, {"kind": "berries"}],
            }
        )
    )


def _ctx(tmp_path: Path, world: Path, client: Any) -> JobContext:
    return JobContext(
        job_id="job-1",
        job_dir=tmp_path,
        client=client,
        tracker=BudgetTracker(budget_usd=8.0),
        world_path=world,
    )


class ScriptClient:
    """Recorded answers, replayed in order. No key, no network."""

    model = "fake-test-model"

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.calls: list[list[dict]] = []
        self.options: list[dict[str, Any]] = []

    def chat(self, messages: list[dict], **kwargs: Any) -> SimpleNamespace:
        self.calls.append(messages)
        self.options.append(kwargs)
        content = messages[-1]["content"]
        self.prompts.append(content if isinstance(content, str) else content[0]["text"])
        message = SimpleNamespace(content=self.answers.pop(0), tool_calls=None)
        return SimpleNamespace(
            model="fake-test-model",
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=10, prompt_tokens_details=None
            ),
        )


def _build_ok(sandbox: Path, spec: str) -> dict[str, Any]:
    (sandbox / "assets" / "animals").mkdir(parents=True, exist_ok=True)
    (sandbox / "assets" / "animals" / f"{spec}.glb").write_bytes(b"glb-bytes")
    (sandbox / "models" / "previews").mkdir(parents=True, exist_ok=True)
    (sandbox / "models" / "previews" / f"{spec}.png").write_bytes(b"png-bytes")
    return {"ok": True, "summary": f"{spec}: built", "log": f"BUILT {spec}: ok"}


def _adapt_ok(
    world: Path, start_from: str, edits_path: Path, out_path: Path
) -> dict[str, Any]:
    """The adapt tool, stubbed: a spec measuring the edits' sizes."""
    edits = json.loads(edits_path.read_text(encoding="utf-8"))
    length = edits.get("length_m", 1.0)
    spec = {
        "id": edits.get("id", "x"),
        "bones": [
            {
                "name": "body",
                "head": [0, length / 2, -length / 2],
                "tail": [0, length / 2, length / 2],
                "role": "body",
            }
        ],
        "parts": [
            {
                "name": "body",
                "shape": "ball",
                "size": [0.2, 0.2, length],
                "at": [0, length / 2, 0],
                "color": "fur",
                "bone": "body",
            }
        ],
    }
    out_path.write_text(json.dumps(spec), encoding="utf-8")
    return {"ok": True, "log": "adapted"}


def _animal_answer(animal_id: str, **overrides: Any) -> str:
    animal: dict[str, Any] = {
        "id": animal_id,
        "name": "Good",
        "latin": "X y",
        "diet": ["grass"],
        "text": {"title": "T"},
    }
    animal.update(overrides)
    return json.dumps(animal)


def _edits_answer(**overrides: Any) -> str:
    edits: dict[str, Any] = {
        "colors": {"fur": "#8b7355", "belly": "#f0e8d8"},
        "new_colors": [],
        "repaint": [],
        "unmakable": [],
    }
    for name in EDITS_FACTORS:
        edits[name] = 1.0
    edits.update(overrides)
    return json.dumps(edits)


def _stage(**over: Any) -> AnimalMakerStage:
    args: dict[str, Any] = {"build": _build_ok, "adapt": _adapt_ok}
    args.update(over)
    return AnimalMakerStage(**args)


def _minset_with_prey() -> dict[str, Any]:
    return {
        "make_animal": True,
        "animal_reason": "not in the library yet",
        "make_home": False,
        "home_reason": "boreal_forest is built",
        "home_habitat": "boreal_forest",
        "prey": {"name": "Capybara", "size_cm": 110},
        "prey_reason": "smallest-bodied named prey",
    }


def test_makes_animal_first_try(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient([_animal_answer("lynx"), _edits_answer()])
    result = _stage().run(_ctx(tmp_path, world, client))
    assert result["animal_files"] == ["animals/lynx.json"]
    assert (tmp_path / "animals" / "lynx.json").is_file()
    assert (tmp_path / "models" / "lynx.json").is_file()
    assert (tmp_path / "models" / "lynx.glb").is_file()
    assert (tmp_path / "models" / "previews" / "lynx.png").is_file()
    assert (tmp_path / "tries" / "lynx" / "animal" / "1" / "answer.json").is_file()
    assert (tmp_path / "tries" / "lynx" / "edits" / "1" / "edits.json").is_file()
    assert (tmp_path / "tries" / "lynx" / "edits" / "1" / "build.log").is_file()
    checks = json.loads((tmp_path / "checks" / "oneshot.json").read_text())
    assert checks["made"] == ["lynx"]
    assert checks["unmakable"] == {}
    assert checks["cost_usd"] == result["cost_usd"]
    assert len(client.prompts) == 2


def test_animal_prompt_carries_the_species(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient([_animal_answer("lynx"), _edits_answer()])
    _stage().run(_ctx(tmp_path, world, client))
    prompt = client.prompts[0]
    assert '"species": "lynx"' in prompt
    assert '"gbif_key": 2435240' in prompt
    assert "Vulpes vulpes" in prompt
    assert "only one example: one shot" in prompt
    assert "$FOR_CHILDREN" not in prompt
    assert "Young animals" in prompt
    assert client.options[0] == {}


def test_edits_prompt_carries_looks_and_parts(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    (tmp_path / "research.json").write_text(
        json.dumps(
            {
                "size": {"length_cm": 110, "weight_kg": 22},
                "year": {
                    "winter": "active",
                    "shelter": "It dens in rocky hollows.",
                    "young": "Kittens are born in spring.",
                    "food": "It hunts at dusk.",
                },
                "main_food": {"name": "roe deer", "kind": "prey", "size_cm": 130},
                "other_foods": [],
                "surroundings": [],
                "sources": ["https://example.org/lynx"],
                "looks": _looks_research()["looks"],
            }
        )
    )
    (world / "models" / "README.md").write_text(
        "# Models\n\n## Reference models\n\n"
        "| New animal | Start from | Change |\n"
        "|---|---|---|\n"
        "| Bear, lynx | `fox`, scaled up | lynx: short tail, ear tufts |\n"
    )
    client = ScriptClient([_animal_answer("lynx"), _edits_answer()])
    _stage().run(_ctx(tmp_path, world, client))
    prompt = client.prompts[1]
    assert "ear tufts" in prompt
    assert "lynx: short tail, ear tufts" in prompt
    assert "- body: fur, body" in prompt
    assert "- arm_l: fur, leg" in prompt
    request = client.options[1]["response_format"]
    assert request["type"] == "json_schema"
    assert request["json_schema"]["name"] == "ModelEdits"
    assert request["json_schema"]["strict"] is True
    assert_strict_subset(request["json_schema"]["schema"])


def test_fix_round_repairs_check_errors(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient(
        [
            _animal_answer("lynx", diet=["nectar"]),
            _animal_answer("lynx", diet=["grass"]),
            _edits_answer(),
        ]
    )
    result = _stage().run(_ctx(tmp_path, world, client))
    assert result["animal_files"] == ["animals/lynx.json"]
    assert len(client.prompts) == 3
    assert "Change only what these problems name" in client.prompts[1]
    assert "nectar" in client.prompts[1]
    assert (tmp_path / "tries" / "lynx" / "animal" / "2" / "answer.json").is_file()


def test_bad_json_gets_the_fix_round(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient(["not json at all", _animal_answer("lynx"), _edits_answer()])
    result = _stage().run(_ctx(tmp_path, world, client))
    assert result["animal_files"] == ["animals/lynx.json"]
    assert "not JSON" in client.prompts[1]


def test_still_failing_stops_for_a_person(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient(
        [
            _animal_answer("lynx", diet=["nectar"]),
            _animal_answer("lynx", diet=["nectar"]),
        ]
    )
    with pytest.raises(_Stopped, match="its file still failing"):
        _stage().run(_ctx(tmp_path, world, client))
    assert len(client.prompts) == 2
    assert not (tmp_path / "checks" / "oneshot.json").is_file()


def test_prey_comes_before_the_animal(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path, minset=_minset_with_prey())
    client = ScriptClient(
        [
            _animal_answer("capybara"),
            _edits_answer(),
            _animal_answer("lynx"),
            _edits_answer(),
        ]
    )
    result = _stage().run(_ctx(tmp_path, world, client))
    assert result["animal_files"] == ["animals/capybara.json", "animals/lynx.json"]
    assert '"species": "capybara"' in client.prompts[0]
    assert '"species": "lynx"' in client.prompts[2]
    assert (tmp_path / "animals" / "capybara.json").is_file()


def test_oneshot_adds_made_prey_to_the_plan(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path, minset=_minset_with_prey())
    (tmp_path / "species-plan.json").write_text(
        json.dumps(
            {
                "species": [
                    {
                        "species": "lynx",
                        "decision": "new",
                        "reason": "made one-shot for this home",
                        "wild": True,
                        "taxon": {
                            "gbif_key": 2435240,
                            "rank": "SPECIES",
                            "name": "Lynx lynx",
                        },
                    }
                ]
            }
        )
    )
    client = ScriptClient(
        [
            _animal_answer("capybara"),
            _edits_answer(),
            _animal_answer("lynx"),
            _edits_answer(),
        ]
    )
    _stage().run(_ctx(tmp_path, world, client))
    plan = json.loads((tmp_path / "species-plan.json").read_text())
    assert [(e["species"], e["decision"]) for e in plan["species"]] == [
        ("lynx", "new"),
        ("capybara", "new"),
    ]
    assert plan["species"][1]["taxon"]["gbif_key"] == 2437585


def test_prey_matches_by_species_key(tmp_path: Path) -> None:
    world = _world(tmp_path)
    minset = _minset_with_prey()
    minset["make_animal"] = False
    minset["prey"] = {"name": "Water hog", "size_cm": 110, "gbif_key": 2437585}
    _inputs(tmp_path, minset=minset)
    client = ScriptClient([_animal_answer("capybara"), _edits_answer()])
    result = _stage().run(_ctx(tmp_path, world, client))
    assert result["animal_files"] == ["animals/capybara.json"]
    assert '"species": "capybara"' in client.prompts[0]


def test_skips_already_made_prey(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path, minset=_minset_with_prey())
    (tmp_path / "animals").mkdir()
    (tmp_path / "animals" / "capybara.json").write_text("{}")
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "capybara.json").write_text("{}")
    client = ScriptClient([_animal_answer("lynx"), _edits_answer()])
    result = _stage().run(_ctx(tmp_path, world, client))
    assert result["animal_files"] == ["animals/lynx.json"]
    assert len(client.prompts) == 2


def test_nothing_to_make_calls_nothing(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(
        tmp_path,
        minset={
            "make_animal": False,
            "animal_reason": "fox is in the library",
            "make_home": False,
            "home_reason": "boreal_forest is built",
            "home_habitat": "boreal_forest",
            "prey": None,
            "prey_reason": "does not hunt",
        },
    )
    client = ScriptClient([])
    result = _stage().run(_ctx(tmp_path, world, client))
    assert result["animal_files"] == []
    checks = json.loads((tmp_path / "checks" / "oneshot.json").read_text())
    assert checks == {"cost_usd": 0.0, "made": [], "unmakable": {}}


def test_build_with_no_output_is_blender_not_the_spec(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient([_animal_answer("lynx"), _edits_answer()])

    def _build_empty(sandbox: Path, spec: str) -> dict[str, Any]:
        return {"ok": False, "summary": "failed", "log": ""}

    with pytest.raises(_Stopped, match="Blender itself did not run"):
        _stage(build=_build_empty).run(_ctx(tmp_path, world, client))


def _home_world(tmp_path: Path) -> Path:
    world = tmp_path / "world"
    (world / "tools").mkdir(parents=True)
    (world / "tools" / "build_model.sh").write_text("#!/bin/sh\n")
    (world / "CONTRIBUTING.md").write_text(
        "# World\n\n## The region file\n\nFields here.\n\n## Adding animals\n\nX.\n"
        "\n## Adding plants\n\n### The rules\n\nGrow well.\n\n## Next\n\nMore.\n"
    )
    (world / "ART.md").write_text("# Art\n\nReal first.\n")
    (world / "homes").mkdir(parents=True)
    (world / "homes" / "boreal_forest.json").write_text(
        json.dumps({"id": "boreal_forest", "name": "Northern forest home"})
    )
    plants = world / "models" / "plants"
    plants.mkdir(parents=True)
    (plants / "bilberry.json").write_text(
        json.dumps(
            {
                "id": "bilberry",
                "latin": "Vaccinium myrtillus",
                "taxon": {
                    "gbif_key": 2882833,
                    "rank": "SPECIES",
                    "name": "Vaccinium myrtillus",
                },
                "parts": [{"name": "bush"}],
            }
        )
    )
    (plants / "wood_anemone.json").write_text(
        json.dumps({"id": "wood_anemone", "parts": [{"name": "leaves"}]})
    )
    (world / "models" / "README.md").write_text("# Models\n\n## Plants\n\nGrow.\n")
    pages = world / "locale" / "en"
    pages.mkdir(parents=True)
    (pages / "plants.json").write_text(
        json.dumps({"plant.norway_spruce.title": "Norway spruce"})
    )
    animals = world / "animals"
    animals.mkdir(parents=True)
    (animals / "roach.json").write_text(
        json.dumps(
            {"taxon": {"gbif_key": 1, "rank": "SPECIES", "name": "Rutilus rutilus"}}
        )
    )
    (world / "schemas").mkdir(parents=True)
    (world / "schemas" / "model.schema.json").write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "https://example.org/model.schema.json",
                "type": "object",
                "required": ["id", "parts"],
            }
        )
    )
    return world


def _home_inputs(tmp_path: Path, make_home: bool = True) -> None:
    (tmp_path / "home.json").write_text(
        json.dumps(
            {
                "species_key": 5225712,
                "id": "anaconda",
                "latin": "Eunectes murinus",
                "home": "rainforest",
                "needs_home": True,
            }
        )
    )
    (tmp_path / "research.json").write_text(
        json.dumps(
            {
                "surroundings": [
                    {"name": "kapok tree", "kind": "tree"},
                    {"name": "water lettuce", "kind": "plant"},
                    {"name": "muddy bank", "kind": "ground"},
                ],
            }
        )
    )
    (tmp_path / "minset.json").write_text(
        json.dumps(
            {
                "make_animal": True,
                "make_home": make_home,
                "home_habitat": "rainforest",
                "prey": None,
            }
        )
    )


def _build_plant_ok(sandbox: Path, plant_id: str) -> dict[str, Any]:
    (sandbox / "assets" / "plants").mkdir(parents=True, exist_ok=True)
    (sandbox / "assets" / "plants" / f"{plant_id}.glb").write_bytes(b"glb-bytes")
    previews = sandbox / "models" / "previews" / "plants"
    previews.mkdir(parents=True, exist_ok=True)
    (previews / f"{plant_id}.png").write_bytes(b"png-bytes")
    return {"ok": True, "summary": f"{plant_id}: built", "log": f"BUILT {plant_id}: ok"}


def _match_new(world: Path, latin: str) -> dict[str, Any]:
    return {"taxon": {"gbif_key": 9, "rank": "SPECIES", "name": latin}, "files": []}


def _home_answer(plants: dict[str, Any] | None = None, **home_over: Any) -> str:
    home: dict[str, Any] = {
        "id": "rainforest",
        "name": "Rainforest home",
        "ground_cover": {"plants": [{"model": "water_lettuce", "density": 0.3}]},
    }
    home.update(home_over)
    if plants is None:
        plants = {
            "water_lettuce": {
                "id": "water_lettuce",
                "latin": "Pistia stratiotes",
                "parts": [{"name": "rosette"}],
            }
        }
    return json.dumps({"home": home, "plants": plants})


def _home_stage(**over: Any) -> HomeMakerStage:
    args: dict[str, Any] = {
        "build": _build_plant_ok,
        "check_homes": lambda s, h: [],
        "check_models": lambda s, p: [],
        "match": _match_new,
        "recorded": lambda lat, lon, key: False,
    }
    args.update(over)
    return HomeMakerStage(**args)


def test_newhome_makes_home_first_try(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path)
    client = ScriptClient([_home_answer()])
    stage = _home_stage()
    result = stage.run(_ctx(tmp_path, world, client))
    assert result["home_files"] == ["homes/rainforest.json"]
    assert result["plant_files"] == ["models/plants/water_lettuce.json"]
    assert (tmp_path / "homes" / "rainforest.json").is_file()
    assert (tmp_path / "models" / "plants" / "water_lettuce.json").is_file()
    assert (tmp_path / "models" / "plants" / "water_lettuce.glb").is_file()
    assert (tmp_path / "models" / "previews" / "plants" / "water_lettuce.png").is_file()
    assert (tmp_path / "tries" / "rainforest" / "1" / "answer.json").is_file()
    checks = json.loads((tmp_path / "checks" / "newhome.json").read_text())
    assert checks["made"] == "rainforest"
    assert checks["plants"] == ["water_lettuce"]
    assert len(client.prompts) == 1


def test_newhome_prompt_carries_the_inputs(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path)
    client = ScriptClient([_home_answer()])
    stage = _home_stage()
    stage.run(_ctx(tmp_path, world, client))
    prompt = client.prompts[0]
    assert "rainforest" in prompt
    assert "water lettuce" in prompt
    assert "Northern forest home" in prompt
    assert "bilberry" in prompt
    assert "The region file" in prompt
    assert "Young animals" in prompt
    assert "lake_life" in prompt
    assert "norway_spruce" in prompt
    assert "Nothing living carries over" in prompt


def test_newhome_fix_round_repairs_check_errors(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path)
    client = ScriptClient([_home_answer(), _home_answer()])
    calls: list[list[str]] = []

    def _models(sandbox: Path, plant_ids: list[str]) -> list[str]:
        calls.append(plant_ids)
        if len(calls) == 1:
            return ["water_lettuce: the preview sheet is missing"]
        return []

    stage = _home_stage(check_models=_models)
    result = stage.run(_ctx(tmp_path, world, client))
    assert result["home_files"] == ["homes/rainforest.json"]
    assert calls == [["water_lettuce"], ["water_lettuce"]]
    assert len(client.prompts) == 2
    assert "Change only what these problems name" in client.prompts[1]
    assert "the preview sheet is missing" in client.prompts[1]


def test_newhome_homes_errors_feed_the_fix_round(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path)
    client = ScriptClient([_home_answer(), _home_answer()])
    calls: list[str] = []

    def _check(sandbox: Path, habitat: str) -> list[str]:
        calls.append(habitat)
        return ["the canopy is too low"] if len(calls) == 1 else []

    stage = _home_stage(check_homes=_check)
    result = stage.run(_ctx(tmp_path, world, client))
    assert result["home_files"] == ["homes/rainforest.json"]
    assert "the canopy is too low" in client.prompts[1]


def test_newhome_still_failing_stops_for_a_person(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path)
    client = ScriptClient([_home_answer(), _home_answer()])
    stage = _home_stage(check_homes=lambda s, h: ["always broken"])
    with pytest.raises(_Stopped, match="a person makes it"):
        stage.run(_ctx(tmp_path, world, client))
    assert len(client.prompts) == 2
    assert not (tmp_path / "checks" / "newhome.json").is_file()


def test_newhome_reuses_a_spec_the_world_grows(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path)
    client = ScriptClient([_home_answer()])

    def _match_old(world: Path, latin: str) -> dict[str, Any]:
        return {
            "taxon": {"gbif_key": 7, "rank": "SPECIES", "name": latin},
            "files": ["models/plants/bilberry.json"],
        }

    stage = _home_stage(match=_match_old)
    result = stage.run(_ctx(tmp_path, world, client))
    assert result["plant_files"] == []
    home = json.loads((tmp_path / "homes" / "rainforest.json").read_text())
    models = [p["model"] for p in home["ground_cover"]["plants"]]
    assert models == ["bilberry"]
    assert not (tmp_path / "models" / "plants" / "water_lettuce.json").is_file()


def test_newhome_refuses_a_third_plant(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path)
    three = {
        f"plant_{i}": {"id": f"plant_{i}", "parts": [{"name": "leaf"}]}
        for i in range(3)
    }
    client = ScriptClient([_home_answer(plants=three), _home_answer()])
    stage = _home_stage()
    result = stage.run(_ctx(tmp_path, world, client))
    assert result["home_files"] == ["homes/rainforest.json"]
    assert "at most 2 new plants" in client.prompts[1]


def test_newhome_nothing_to_make_calls_nothing(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path, make_home=False)
    client = ScriptClient([])
    stage = _home_stage()
    result = stage.run(_ctx(tmp_path, world, client))
    assert result == {"home_files": [], "cost_usd": 0.0}


def _home_answer_with(home_patch: dict[str, Any]) -> str:
    wrapped = json.loads(_home_answer())
    wrapped["home"].update(home_patch)
    return json.dumps(wrapped)


def test_newhome_rejects_template_carryover_plants(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path)
    carried = _home_answer_with(
        {"ground_cover": {"plants": [{"model": "bilberry", "density": 0.2}]}}
    )
    client = ScriptClient([carried, _home_answer()])
    result = _home_stage().run(_ctx(tmp_path, world, client))
    assert result["home_files"] == ["homes/rainforest.json"]
    assert "grows in the template's land" in client.prompts[1]


def test_newhome_keeps_research_named_plants(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path)
    research = json.loads((tmp_path / "research.json").read_text())
    research["surroundings"].append({"name": "bilberry", "kind": "plant"})
    (tmp_path / "research.json").write_text(json.dumps(research))
    named = _home_answer_with(
        {"ground_cover": {"plants": [{"model": "bilberry", "density": 0.2}]}}
    )
    client = ScriptClient([named])
    result = _home_stage().run(_ctx(tmp_path, world, client))
    assert result["home_files"] == ["homes/rainforest.json"]
    assert result["plant_files"] == ["models/plants/water_lettuce.json"]


def test_newhome_keeps_recorded_plants(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path)
    named = _home_answer_with(
        {"ground_cover": {"plants": [{"model": "bilberry", "density": 0.2}]}}
    )
    client = ScriptClient([named])
    stage = _home_stage(recorded=lambda lat, lon, key: True)
    result = stage.run(_ctx(tmp_path, world, client))
    assert result["home_files"] == ["homes/rainforest.json"]


def test_newhome_rejects_unrecorded_fish(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path)
    fished = _home_answer_with({"fish": [{"kind": "roach", "count": 12}]})
    client = ScriptClient([fished, _home_answer()])
    result = _home_stage().run(_ctx(tmp_path, world, client))
    assert result["home_files"] == ["homes/rainforest.json"]
    assert "swims in the template's waters" in client.prompts[1]


def test_newhome_rejects_unknown_tree_plants(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path)
    treed = _home_answer_with(
        {"trees": {"mix": [{"shape": "round", "plant": "kapok_tree"}]}}
    )
    client = ScriptClient([treed, _home_answer()])
    result = _home_stage().run(_ctx(tmp_path, world, client))
    assert result["home_files"] == ["homes/rainforest.json"]
    assert "has no spec or page" in client.prompts[1]


def test_newhome_rejects_unnamed_tree_plants(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path)
    treed = _home_answer_with(
        {"trees": {"mix": [{"shape": "spruce", "plant": "norway_spruce"}]}}
    )
    client = ScriptClient([treed, _home_answer()])
    result = _home_stage().run(_ctx(tmp_path, world, client))
    assert result["home_files"] == ["homes/rainforest.json"]
    assert "named nowhere in the surroundings" in client.prompts[1]


def test_newhome_skips_a_made_home(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path)
    (tmp_path / "homes").mkdir()
    (tmp_path / "homes" / "rainforest.json").write_text("{}")
    client = ScriptClient([])
    stage = _home_stage()
    result = stage.run(_ctx(tmp_path, world, client))
    assert result == {"home_files": ["homes/rainforest.json"], "cost_usd": 0.0}


def _spec(**over: Any) -> dict[str, Any]:
    """A lynx-shaped spec: 1 m body, 55 cm shoulder, 23 cm tail."""
    spec: dict[str, Any] = {
        "id": "lynx",
        "bones": [
            {"name": "body", "head": [0, 0.55, -0.45], "tail": [0, 0.58, 0.45]},
            {"name": "leg_f_l", "head": [0, 0.5, 0.2], "tail": [0, 0, 0.2]},
            {"name": "tail_1", "head": [0, 0.55, -0.45], "tail": [0, 0.5, -0.67]},
        ],
        "parts": [
            {
                "name": "body",
                "shape": "ball",
                "size": [0.26, 0.3, 1.0],
                "at": [0, 0.55, 0],
                "bone": "body",
            },
            {
                "name": "arm_l",
                "shape": "ball",
                "size": [0.06, 0.5, 0.07],
                "at": [0.06, 0.31, 0.2],
                "bone": "leg_f_l",
            },
        ],
    }
    spec.update(over)
    return spec


def _looks_research() -> dict[str, Any]:
    return {
        "size": {"length_cm": 100, "weight_kg": 20},
        "looks": {
            "shoulder_cm": 60,
            "tail_cm": 15,
            "coat": {"main": "grey-brown", "belly": "white"},
            "marks": [{"mark": "ear tufts"}, {"mark": "short black-tipped tail"}],
        },
    }


def test_measure_spec_reads_shoulders_tail_and_legs() -> None:
    found = _measure_spec(_spec())
    assert found["shoulder_m"] == pytest.approx(0.55)
    assert found["length_m"] == pytest.approx(1.0)
    assert found["tail_m"] == pytest.approx(0.226, abs=0.001)
    assert found["leg_m"] == pytest.approx(0.56)
    assert found["depth_m"] == pytest.approx(1.0)


def test_measure_errors_flags_a_long_tail(tmp_path: Path) -> None:
    world = _world(tmp_path)
    errors = _measure_errors(
        _spec(),
        _looks_research(),
        {"prey": False, "start_from": "fox"},
        world,
    )
    assert errors == ["the spec's tail is 23 cm, sourced 15 cm: size it to the source"]


def test_measure_errors_passes_sizes_within_a_fifth(tmp_path: Path) -> None:
    world = _world(tmp_path)
    spec = _spec(
        bones=[
            {"name": "body", "head": [0, 0.55, -0.3], "tail": [0, 0.58, 0.25]},
            {
                "name": "tail_1",
                "head": [0, 0.55, -0.3],
                "tail": [0, 0.53, -0.44],
            },
        ]
    )
    errors = _measure_errors(
        spec,
        _looks_research(),
        {"prey": False, "start_from": "fox"},
        world,
    )
    assert errors == []


def test_measure_errors_flags_drifted_leg_ratio(tmp_path: Path) -> None:
    world = _world(tmp_path)
    (world / "models" / "fox.json").write_text(
        json.dumps(
            {
                "id": "fox",
                "bones": [
                    {
                        "name": "leg_f_l",
                        "head": [0, 0.3, 0.1],
                        "tail": [0, 0, 0.1],
                    }
                ],
                "parts": [
                    {
                        "name": "body",
                        "size": [0.17, 0.19, 0.46],
                        "at": [0, 0.32, 0],
                    },
                    {
                        "name": "arm_l",
                        "size": [0.06, 0.18, 0.07],
                        "at": [0.06, 0.24, 0.1],
                        "bone": "leg_f_l",
                    },
                ],
            }
        )
    )
    leggy = _spec(
        parts=[
            {
                "name": "body",
                "shape": "ball",
                "size": [0.26, 0.3, 1.0],
                "at": [0, 0.9, 0],
                "bone": "body",
            },
            {
                "name": "arm_l",
                "shape": "ball",
                "size": [0.06, 0.9, 0.07],
                "at": [0.06, 0.85, 0.2],
                "bone": "leg_f_l",
            },
        ]
    )
    errors = _measure_errors(
        leggy,
        {"size": {"length_cm": 100}},
        {"prey": False, "start_from": "fox"},
        world,
    )
    assert len(errors) == 1
    assert errors[0].startswith("legs are 1.3x the body's depth")
    assert "the fox's are 0.7x" in errors[0]


def test_measure_errors_skips_what_the_spec_does_not_name(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    minimal = {"id": "lynx", "parts": [{"name": "body"}]}
    assert (
        _measure_errors(
            minimal,
            _looks_research(),
            {"prey": False, "start_from": "fox"},
            world,
        )
        == []
    )


def test_reference_row_finds_the_lynx_line(tmp_path: Path) -> None:
    world = _world(tmp_path)
    (world / "models" / "README.md").write_text(
        "# Models\n\n## Reference models\n\n"
        "| New animal | Start from | Change |\n"
        "|---|---|---|\n"
        "| Bear, lynx | `fox`, scaled up | lynx: short tail, ear tufts |\n"
        "| Snakes, slow worm, eels | `examples/grass_snake` | Size, head |\n"
    )
    assert _reference_row(world, "fox", "Eurasian lynx") == (
        "Bear, lynx",
        "lynx: short tail, ear tufts",
    )


def test_reference_row_aliases_snake_kinds(tmp_path: Path) -> None:
    world = _world(tmp_path)
    (world / "models" / "README.md").write_text(
        "# Models\n\n## Reference models\n\n"
        "| New animal | Start from | Change |\n"
        "|---|---|---|\n"
        "| Snakes, slow worm, eels | `examples/grass_snake` | Size, head |\n"
    )
    assert _reference_row(world, "grass_snake", "Green anaconda") == (
        "Snakes, slow worm, eels",
        "Size, head",
    )
    assert _reference_row(world, "fox", "Green anaconda") is None


def test_reference_line_returns_the_row(tmp_path: Path) -> None:
    world = _world(tmp_path)
    (world / "models" / "README.md").write_text(
        "# Models\n\n## Reference models\n\n"
        "| New animal | Start from | Change |\n"
        "|---|---|---|\n"
        "| Bear, lynx | `fox`, scaled up | lynx: short tail, ear tufts |\n"
    )
    assert _reference_line(world, "fox", {"name": "Eurasian lynx"}) == (
        "What the `fox` model looks like (1.0 keeps this): tall pointed dark "
        "ears, a long bushy tail as long as half its body, a long narrow "
        "muzzle, thin black legs with small paws, a red coat with a white "
        "chest and tail tip\n"
        "Bear, lynx: lynx: short tail, ear tufts"
    )
    assert "not on the reference table" in _reference_line(
        world, "grass_snake", {"name": "Green anaconda"}
    )


def test_reference_looks_prefers_the_table_column(tmp_path: Path) -> None:
    world = _world(tmp_path)
    assert _reference_looks(world, "fox").startswith("tall pointed dark ears")
    assert _reference_looks(world, "grass_snake") is None
    (world / "models" / "README.md").write_text(
        "# Models\n\n## Reference models\n\n"
        "| New animal | Start from | Change | Looks |\n"
        "|---|---|---|---|\n"
        "| Snakes | `examples/grass_snake` | Size, head | long smooth coils |\n"
    )
    assert _reference_looks(world, "grass_snake") == "long smooth coils"


def test_guide_turns_a_missing_text_into_guidance() -> None:
    guided = _guide('lynx: digs, so it needs text "dig"')
    assert "write the text" in guided
    assert "`digs`" in guided
    assert "false" in guided


def test_guide_passes_other_errors_through() -> None:
    error = "the build failed: ERROR: bad bone"
    assert _guide(error) == error


def test_model_prompt_demands_visible_marks() -> None:
    prompt = (PROMPTS_DIR / "model_spec.md").read_text(encoding="utf-8")
    assert "15%" in prompt
    assert "not tiny balls" in prompt


def test_animal_prompt_takes_behaviour_from_the_survey() -> None:
    prompt = (PROMPTS_DIR / "animal_write.md").read_text(encoding="utf-8")
    flat = " ".join(prompt.split())
    assert "never from the example animal" in flat


def test_measurements_carries_the_looks_sizes() -> None:
    measurements = _measurements(_looks_research(), {"prey": False, "size_cm": None})
    assert measurements["length_cm"] == 100
    assert measurements["shoulder_cm"] == 60
    assert measurements["tail_cm"] == 15


def test_description_names_the_looks_marks() -> None:
    text = _description(
        {"name": "Eurasian lynx", "latin": "Lynx lynx"},
        _looks_research(),
        {"prey": False, "size_cm": None},
    )
    assert "ear tufts" in text
    assert "shoulder 60 cm" in text


def _example_tree(world: Path) -> None:
    trees = world / "models" / "examples" / "trees"
    trees.mkdir(parents=True)
    (trees / "umbrella_thorn.json").write_text(
        json.dumps(
            {
                "id": "umbrella_thorn",
                "name": "Umbrella thorn acacia",
                "latin": "Vachellia tortilis",
            }
        )
    )


def _thorn_inputs(tmp_path: Path) -> None:
    _home_inputs(tmp_path)
    (tmp_path / "research.json").write_text(
        json.dumps(
            {
                "surroundings": [
                    {"name": "umbrella thorn", "kind": "tree"},
                    {"name": "muddy bank", "kind": "ground"},
                ],
            }
        )
    )


def test_newhome_offers_tree_specs_in_prompt(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _example_tree(world)
    _home_inputs(tmp_path)
    client = ScriptClient([_home_answer()])
    _home_stage().run(_ctx(tmp_path, world, client))
    assert "umbrella_thorn" in client.prompts[0]
    assert "Vachellia tortilis" in client.prompts[0]
    assert "no tree specs yet" not in client.prompts[0].lower()


def test_newhome_carries_a_matched_tree_spec(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _example_tree(world)
    _thorn_inputs(tmp_path)
    answer = _home_answer(
        trees={"mix": [{"shape": "umbrella_thorn", "plant": "umbrella_thorn"}]}
    )
    client = ScriptClient([answer])
    result = _home_stage().run(_ctx(tmp_path, world, client))
    assert result["home_files"] == ["homes/rainforest.json"]
    assert (tmp_path / "models" / "trees" / "umbrella_thorn.json").is_file()


def test_newhome_rejects_unknown_tree_shapes(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _example_tree(world)
    _thorn_inputs(tmp_path)
    answer = _home_answer(trees={"mix": [{"shape": "palm"}]})
    client = ScriptClient([answer, _home_answer()])
    _home_stage().run(_ctx(tmp_path, world, client))
    assert "neither a shape nor" in client.prompts[1]


def test_newhome_rejects_spec_tree_with_other_plant(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _example_tree(world)
    _thorn_inputs(tmp_path)
    answer = _home_answer(
        trees={"mix": [{"shape": "umbrella_thorn", "plant": "norway_spruce"}]}
    )
    client = ScriptClient([answer, _home_answer()])
    _home_stage().run(_ctx(tmp_path, world, client))
    assert "is a spec, so its plant" in client.prompts[1]


def _write_looks_research(tmp_path: Path) -> None:
    (tmp_path / "research.json").write_text(
        json.dumps(
            {
                "size": {"length_cm": 110, "weight_kg": 22},
                "year": {
                    "winter": "active",
                    "shelter": "It dens in rocky hollows.",
                    "young": "Kittens are born in spring.",
                    "food": "It hunts at dusk.",
                },
                "main_food": {"name": "roe deer", "kind": "prey", "size_cm": 130},
                "other_foods": [],
                "surroundings": [],
                "sources": ["https://example.org/lynx"],
                "looks": _looks_research()["looks"],
            }
        )
    )


def _tail_adapt(factor: float | None):
    """An adapt stub with a systematic tail error, or a fixed one.

    A factor scales the edits' tail (code-fix converges); None
    writes a fixed wrong tail (the tool saturates).
    """

    def adapt(
        world: Path, start_from: str, edits_path: Path, out_path: Path
    ) -> dict[str, Any]:
        edits = json.loads(edits_path.read_text(encoding="utf-8"))
        tail = 0.30 if factor is None else edits.get("tail_m", 0.15) * factor
        spec = {
            "id": edits.get("id", "x"),
            "bones": [
                {
                    "name": "body",
                    "head": [0, 0.55, -0.45],
                    "tail": [0, 0.58, 0.45],
                    "role": "body",
                },
                {
                    "name": "tail_1",
                    "head": [0, 0.55, -0.45],
                    "tail": [0, 0.55, -0.45 - tail],
                    "role": "tail",
                },
            ],
            "parts": [
                {
                    "name": "body",
                    "shape": "ball",
                    "size": [0.26, 0.3, 1.1],
                    "at": [0, 0.55, 0],
                    "color": "fur",
                    "bone": "body",
                },
                {
                    "name": "tail_1",
                    "shape": "ball",
                    "size": [0.09, 0.09, tail],
                    "at": [0, 0.55, -0.45 - tail / 2],
                    "color": "fur",
                    "bone": "tail_1",
                },
            ],
        }
        out_path.write_text(json.dumps(spec), encoding="utf-8")
        return {"ok": True, "log": "adapted"}

    return adapt


def test_edits_schema_meets_the_strict_subset(tmp_path: Path) -> None:
    world = _world(tmp_path)
    ref = json.loads((world / "models" / "fox.json").read_text())
    schema = _edits_schema(ref)
    assert_strict_subset(schema)
    assert sorted(schema["properties"]["colors"]["required"]) == ["belly", "fur"]
    repaint = schema["properties"]["repaint"]["items"]["properties"]
    assert sorted(repaint["part"]["enum"]) == ["arm_l", "body", "tail_1"]
    assert sorted(repaint["color"]["enum"]) == ["belly", "fur"]


def test_edits_files_accepts_and_rejects(tmp_path: Path) -> None:
    world = _world(tmp_path)
    ref = json.loads((world / "models" / "fox.json").read_text())
    edits, errors = _edits_files(_edits_answer(), ref)
    assert errors == []
    assert edits is not None and edits["head"] == 1.0
    _, errors = _edits_files("not json", ref)
    assert errors[0].startswith("not JSON: ")
    _, errors = _edits_files(json.dumps({"colors": {}}), ref)
    assert "leave out" in errors[0]
    _, errors = _edits_files(
        _edits_answer(repaint=[{"part": "nose", "color": "fur"}]), ref
    )
    assert "no part 'nose'" in errors[0]


def test_tool_marks_map_words_to_the_tool() -> None:
    marks, unmatched = _tool_marks(
        ["ear tufts", "cheek ruff", "short black-tipped tail", "stripes"]
    )
    assert marks == ["ear_tufts", "ruff", "tail_tip"]
    assert unmatched == ["stripes"]


def test_edits_sizes_come_from_the_research() -> None:
    research = _looks_research()
    research["size"] = {"length_cm": 100, "weight_kg": 20}
    sizes = _edits_sizes(research, {"prey": False, "size_cm": None})
    assert sizes == {"length_m": 1.0, "shoulder_m": 0.6, "tail_m": 0.15}
    assert _edits_sizes({}, {"prey": True, "size_cm": 120}) == {"length_m": 1.2}
    bare = {"size": {"length_cm": 100}}
    assert _edits_sizes(bare, {"prey": False, "size_cm": None}) == {"length_m": 1.0}
    prey_looks = {
        "size_cm": 120.0,
        "shoulder_cm": 55.0,
        "tail_cm": None,
        "colours": ["brown"],
        "marks": ["blunt snout"],
    }
    assert _edits_sizes({}, {"prey": True, "size_cm": 120, "looks": prey_looks}) == {
        "length_m": 1.2,
        "shoulder_m": 0.55,
        "tail_m": 0.0,
    }
    short = {"size": {"length_cm": 100}, "looks": {"tail_cm": 2.0}}
    assert _edits_sizes(short, {"prey": False, "size_cm": None}) == {
        "length_m": 1.0,
        "tail_m": 0.0,
    }


def test_size_misses_names_code_owned_sizes() -> None:
    found = {"length_m": 1.0, "shoulder_m": 0.55, "tail_m": 0.23}
    sourced = _sourced_sizes(_looks_research(), {"prey": False, "size_cm": None})
    assert sourced["length_m"] == 1.0
    misses = _size_misses(found, sourced)
    assert list(misses) == ["tail_m"]
    assert misses["tail_m"] == (0.23, 0.15)


def test_part_list_names_colour_and_role(tmp_path: Path) -> None:
    world = _world(tmp_path)
    ref = json.loads((world / "models" / "fox.json").read_text())
    assert _part_list(ref) == (
        "- body: fur, body\n- arm_l: fur, leg\n- tail_1: fur, tail"
    )


def test_code_fix_rescales_a_missed_tail(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    _write_looks_research(tmp_path)
    calls: list[dict[str, Any]] = []
    inner = _tail_adapt(2.0)

    def adapt(
        world: Path, start_from: str, edits_path: Path, out_path: Path
    ) -> dict[str, Any]:
        calls.append(json.loads(edits_path.read_text(encoding="utf-8")))
        return inner(world, start_from, edits_path, out_path)

    client = ScriptClient([_animal_answer("lynx"), _edits_answer()])
    result = _stage(adapt=adapt).run(_ctx(tmp_path, world, client))
    assert result["animal_files"] == ["animals/lynx.json"]
    assert len(client.prompts) == 2, "no model call spent on sizes"
    assert len(calls) == 2
    assert calls[0]["tail_m"] == 0.15
    assert calls[1]["tail_m"] == pytest.approx(0.075)


def test_saturated_sizes_stop_for_a_person(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    _write_looks_research(tmp_path)
    client = ScriptClient([_animal_answer("lynx"), _edits_answer()])
    with pytest.raises(_Stopped, match="saturated its range"):
        _stage(adapt=_tail_adapt(None)).run(_ctx(tmp_path, world, client))
    assert len(client.prompts) == 2, "no fix call fixes code-owned sizes"


def test_unmakable_marks_land_in_checks(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient(
        [_animal_answer("lynx"), _edits_answer(unmakable=["stripes"])]
    )
    _stage().run(_ctx(tmp_path, world, client))
    checks = json.loads((tmp_path / "checks" / "oneshot.json").read_text())
    assert checks["unmakable"] == {"lynx": ["stripes"]}


def test_adapt_failure_gets_the_fix_round(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    calls = 0

    def adapt(
        world: Path, start_from: str, edits_path: Path, out_path: Path
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"ok": False, "log": "ERROR: unknown mark 'mane'"}
        return _adapt_ok(world, start_from, edits_path, out_path)

    client = ScriptClient([_animal_answer("lynx"), _edits_answer(), _edits_answer()])
    result = _stage(adapt=adapt).run(_ctx(tmp_path, world, client))
    assert result["animal_files"] == ["animals/lynx.json"]
    assert "adapt tool refused" in client.prompts[2]


def test_animal_phase_ignores_the_unbuilt_model(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    (world / "tools" / "check_draft.py").write_text(
        DRAFT_STUB.replace(
            "report = {",
            "animals_problems.append(\n"
            '    "ERROR: lynx: model file not found:'
            ' res://assets/animals/lynx.glb"\n'
            ")\n"
            "report = {",
        )
    )
    client = ScriptClient([_animal_answer("lynx"), _edits_answer()])
    result = _stage().run(_ctx(tmp_path, world, client))
    assert result["animal_files"] == ["animals/lynx.json"]
    assert len(client.prompts) == 2


def test_missing_reference_spec_stops(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    (world / "models" / "fox.json").unlink()
    client = ScriptClient([_animal_answer("lynx"), _edits_answer()])
    with pytest.raises(_Stopped, match="no reference spec 'fox'"):
        _stage().run(_ctx(tmp_path, world, client))


def test_model_fix_round_revises_the_edits(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    bad = _edits_answer(colors={"fur": "#8b7355"})
    client = ScriptClient([_animal_answer("lynx"), bad, _edits_answer()])
    result = _stage().run(_ctx(tmp_path, world, client))
    assert result["animal_files"] == ["animals/lynx.json"]
    assert "every reference colour" in client.prompts[2]
    fix = client.calls[2]
    assert [message["role"] for message in fix] == ["user", "assistant", "user"]
    assert fix[1]["content"] == bad


def test_run_adapt_runs_the_tool(tmp_path: Path) -> None:
    world = tmp_path / "world"
    (world / "tools").mkdir(parents=True)
    (world / "tools" / "adapt_model.py").write_text(
        "import json, sys\n"
        "edits = json.load(open(sys.argv[2]))\n"
        "json.dump({'id': edits['id']}, open(sys.argv[4], 'w'))\n"
    )
    (world / "models").mkdir(parents=True)
    (world / "models" / "fox.json").write_text("{}")
    edits_path = tmp_path / "edits.json"
    edits_path.write_text(json.dumps({"id": "lynx"}))
    out_path = tmp_path / "spec.json"
    assert _run_adapt(world, "fox", edits_path, out_path)["ok"] is True
    assert json.loads(out_path.read_text()) == {"id": "lynx"}
    (world / "tools" / "adapt_model.py").write_text("import sys; sys.exit(1)\n")
    failed = _run_adapt(world, "fox", edits_path, out_path)
    assert failed["ok"] is False


def test_prey_looks_reach_the_edits_prompt(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path, minset=_minset_with_prey())
    (tmp_path / "looks.json").write_text(
        json.dumps(
            {
                "prey": {
                    "latin": "Hydrochoerus hydrochaeris",
                    "size_cm": 120.0,
                    "colours": ["brown", "tan"],
                    "marks": ["blunt snout", "webbed feet"],
                    "sources": ["https://example.org/capybara"],
                },
                "plants": {},
            }
        )
    )
    client = ScriptClient(
        [
            _animal_answer("capybara"),
            _edits_answer(),
            _animal_answer("lynx"),
            _edits_answer(),
        ]
    )
    _stage().run(_ctx(tmp_path, world, client))
    assert "Colours: brown, tan." in client.prompts[1]
    assert "Marks: blunt snout, webbed feet." in client.prompts[1]


def test_plant_looks_reach_the_home_prompt(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path)
    (tmp_path / "looks.json").write_text(
        json.dumps(
            {
                "prey": None,
                "plants": {
                    "water lettuce": {
                        "latin": "Pistia stratiotes",
                        "size_cm": 15.0,
                        "colours": ["pale green"],
                        "marks": ["rosette of ribbed leaves"],
                        "year": "Leaves all year.",
                        "sources": ["https://example.org/lettuce"],
                    }
                },
            }
        )
    )
    client = ScriptClient([_home_answer()])
    _home_stage().run(_ctx(tmp_path, world, client))
    assert "colours: pale green" in client.prompts[0]
    assert "shape: rosette of ribbed leaves" in client.prompts[0]


def test_code_marks_come_from_the_research_words(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    _write_looks_research(tmp_path)
    client = ScriptClient([_animal_answer("lynx"), _edits_answer(unmakable=["mane"])])
    _stage().run(_ctx(tmp_path, world, client))
    edits = json.loads(
        (tmp_path / "tries" / "lynx" / "edits" / "1" / "edits.json").read_text()
    )
    assert edits["marks"] == ["ear_tufts", "tail_tip"]
    checks = json.loads((tmp_path / "checks" / "oneshot.json").read_text())
    assert checks["unmakable"] == {"lynx": ["mane"]}


def test_unmatched_mark_words_go_unmakable(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    _write_looks_research(tmp_path)
    research = json.loads((tmp_path / "research.json").read_text())
    research["looks"]["marks"].append(
        {"mark": "dark stripes", "source": "https://example.org/lynx"}
    )
    (tmp_path / "research.json").write_text(json.dumps(research))
    client = ScriptClient([_animal_answer("lynx"), _edits_answer()])
    _stage().run(_ctx(tmp_path, world, client))
    checks = json.loads((tmp_path / "checks" / "oneshot.json").read_text())
    assert checks["unmakable"] == {"lynx": ["dark stripes"]}


def test_fix_round_resends_the_answer_as_assistant(tmp_path: Path) -> None:
    """#59: user (first prompt), assistant (its answer), user (fixes)."""
    world = _world(tmp_path)
    _inputs(tmp_path)
    first = _animal_answer("lynx", diet=["nectar"])
    client = ScriptClient(
        [first, _animal_answer("lynx", diet=["grass"]), _edits_answer()]
    )
    _stage().run(_ctx(tmp_path, world, client))
    assert len(client.calls) == 3
    assert client.calls[0] == [
        {"role": "user", "content": client.calls[0][0]["content"]}
    ]
    fix = client.calls[1]
    assert [message["role"] for message in fix] == ["user", "assistant", "user"]
    assert fix[0] == client.calls[0][0]
    assert fix[1]["content"] == first
    assert "nectar" in fix[2]["content"]
    assert "Change only what these problems name" in fix[2]["content"]
