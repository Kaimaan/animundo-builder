"""The one-shot animal maker: write, build, check, at most one fix. No key."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from builder.pipeline import BudgetTracker, JobContext
from builder.prompts import PROMPTS_DIR
from builder.schemas import ROOT
from builder.stages.maker import (
    EDITS_FACTORS,
    NEWSHAPE_ROUNDS,
    AnimalMakerStage,
    HomeMakerStage,
    _ask_with_room,
    _choices_files,
    _closest_home,
    _code_choices,
    _description,
    _edits_files,
    _edits_schema,
    _edits_sizes,
    _foods_text,
    _guide,
    _home_choices_schema,
    _measure_errors,
    _measure_spec,
    _measurements,
    _offered_factors,
    _on_back_legs,
    _part_axis_along,
    _part_list,
    _pruned_regions,
    _reference_line,
    _reference_looks,
    _reference_path,
    _reference_row,
    _reference_spec,
    _run_adapt,
    _run_adapt_home,
    _size_errors,
    _size_misses,
    _sizing_edits,
    _sourced_sizes,
    _Stopped,
    _surroundings_text,
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
    log = (
        f"BUILT {spec}: ok\n"
        f'  for animals/{spec}.json: "length": 0.233 (real size), "yaw": 0.0'
    )
    return {"ok": True, "summary": f"{spec}: built", "log": log}


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
        "model": {
            "file": f"res://assets/animals/{animal_id}.glb",
            "length": 0.61,
            "yaw": 0.0,
            "anims": {"idle": "idle"},
        },
    }
    animal.update(overrides)
    return json.dumps(animal)


def _nameless_answer(animal_id: str) -> str:
    """An animal answer missing `name`: the checker's schema error."""
    animal = json.loads(_animal_answer(animal_id))
    del animal["name"]
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
            _nameless_answer("lynx"),
            _animal_answer("lynx"),
            _edits_answer(),
        ]
    )
    result = _stage().run(_ctx(tmp_path, world, client))
    assert result["animal_files"] == ["animals/lynx.json"]
    assert len(client.prompts) == 3
    assert "Change only what these problems name" in client.prompts[1]
    assert "'name' is a required property" in client.prompts[1]
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
            _nameless_answer("lynx"),
            _nameless_answer("lynx"),
        ]
    )
    with pytest.raises(_Stopped, match="its file still failing"):
        _stage().run(_ctx(tmp_path, world, client))
    assert len(client.prompts) == 2
    assert not (tmp_path / "checks" / "oneshot.json").is_file()


_REAL_TOOLS = (
    "check_draft.py",
    "check_animals.py",
    "validate_region.py",
    "check_taxa.py",
    "check_models.py",
)


def _sibling_world() -> Path:
    """The real checkout next door, or a skip without it (#67.2)."""
    world = ROOT.parent / "animundo-world"
    needed = [world / "tools" / name for name in _REAL_TOOLS] + [
        world / "animals" / "archetypes.json",
        world / "regions" / "fi_nuuksio.json",
        world / "assets" / "animals" / "fox.glb",
    ]
    if not all(path.is_file() for path in needed):
        pytest.skip(f"needs a real world checkout at {world}")
    return world


def _real_world(tmp_path: Path) -> Path:
    """A fixture world that runs the real checkers."""
    sibling = _sibling_world()
    world = _world(tmp_path)
    for name in _REAL_TOOLS:
        (world / "tools" / name).write_bytes((sibling / "tools" / name).read_bytes())
    (world / "animals" / "archetypes.json").write_bytes(
        (sibling / "animals" / "archetypes.json").read_bytes()
    )
    (world / "models" / "plants").mkdir(parents=True, exist_ok=True)
    (world / "regions").mkdir(parents=True, exist_ok=True)
    (world / "poisonous-plants.json").write_text(json.dumps({"plants": []}))
    return world


def _real_answer(animal_id: str, latin: str, key: int) -> str:
    """An animal file the real check_animals and check_taxa accept."""
    return json.dumps(
        {
            "id": animal_id,
            "name": animal_id.capitalize(),
            "latin": latin,
            "archetype": "land_mammal",
            "taxon": {"gbif_key": key, "rank": "SPECIES", "name": latin},
            "diet": ["grass"],
            "text": {"dig": f"The {animal_id} digs."},
            "year": {
                "winter": "active",
                "shelter": "It sleeps in a den.",
                "young": "Young are born in spring.",
                "food": {
                    "spring": "Fresh grass.",
                    "summer": "Grass and herbs.",
                    "autumn": "Seeds.",
                    "winter": "Dry grass.",
                },
            },
            "model": {
                "file": f"res://assets/animals/{animal_id}.glb",
                "length": 0.61,
                "yaw": 0.0,
                "anims": {"idle": "idle"},
            },
        }
    )


def _build_real_glb(sandbox: Path, spec: str) -> dict[str, Any]:
    """The model build, stubbed with a real shipped .glb's bytes."""
    data = (_sibling_world() / "assets" / "animals" / "fox.glb").read_bytes()
    animals = sandbox / "assets" / "animals"
    animals.mkdir(parents=True, exist_ok=True)
    (animals / f"{spec}.glb").write_bytes(data)
    previews = sandbox / "models" / "previews"
    previews.mkdir(parents=True, exist_ok=True)
    (previews / f"{spec}.png").write_bytes(b"png-bytes")
    log = (
        f"BUILT {spec}: ok\n"
        f'  for animals/{spec}.json: "length": 0.233 (real size), "yaw": 0.0'
    )
    return {"ok": True, "summary": f"{spec}: built", "log": log}


def _recast_region(tmp_path: Path) -> None:
    """The shipped Nuuksio, seating only the hunter, the prey and its fish."""
    sibling = _sibling_world()
    region = json.loads((sibling / "regions" / "fi_nuuksio.json").read_text())
    region["animals"] = {"playable": ["lynx"], "predators": [], "prey": ["capybara"]}
    region["fish"] = [{"kind": "lynx", "count": 3}]
    region["ground_cover"]["plants"] = []
    regions = tmp_path / "regions"
    (regions / "fi_test.json").unlink()
    (regions / "fi_nuuksio.json").write_text(json.dumps(region))


def test_prey_passes_the_real_checks_before_its_hunter(tmp_path: Path) -> None:
    world = _real_world(tmp_path)
    _inputs(tmp_path, minset=_minset_with_prey())
    _recast_region(tmp_path)
    (tmp_path / "models").mkdir(exist_ok=True)
    (tmp_path / "models" / "capybara.glb").write_bytes(
        (_sibling_world() / "assets" / "animals" / "fox.glb").read_bytes()
    )
    client = ScriptClient(
        [
            _real_answer("capybara", "Hydrochoerus hydrochaeris", 2437585),
            _edits_answer(),
            _real_answer("lynx", "Lynx lynx", 2435240),
            _edits_answer(),
        ]
    )
    result = _stage(build=_build_real_glb).run(_ctx(tmp_path, world, client))
    assert result["animal_files"] == ["animals/capybara.json", "animals/lynx.json"]
    assert len(client.prompts) == 4
    region = json.loads((tmp_path / "regions" / "fi_nuuksio.json").read_text())
    assert region["animals"]["playable"] == ["lynx"]
    assert region["fish"] == [{"kind": "lynx", "count": 3}]


def test_unpruned_region_fails_both_wordings(tmp_path: Path) -> None:
    world = _real_world(tmp_path)
    _inputs(tmp_path, minset=_minset_with_prey())
    _recast_region(tmp_path)
    (tmp_path / "animals").mkdir(exist_ok=True)
    (tmp_path / "animals" / "capybara.json").write_text(
        _real_answer("capybara", "Hydrochoerus hydrochaeris", 2437585)
    )
    (tmp_path / "models").mkdir(exist_ok=True)
    (tmp_path / "models" / "capybara.glb").write_bytes(
        (_sibling_world() / "assets" / "animals" / "fox.glb").read_bytes()
    )
    ctx = SimpleNamespace(job_dir=tmp_path)
    errors = AnimalMakerStage()._check_animal(world, ctx, "capybara", 1, set())
    assert any("has no file animals/lynx.json" in e for e in errors)
    assert any('has unknown animal "lynx"' in e for e in errors)
    assert any("fish[0].kind" in e for e in errors)
    assert AnimalMakerStage()._check_animal(world, ctx, "capybara", 1, {"lynx"}) == []


def test_pruned_regions_drops_targets_from_cast_and_fish(tmp_path: Path) -> None:
    regions = tmp_path / "regions"
    regions.mkdir()
    (regions / "a.json").write_text(
        json.dumps(
            {
                "animals": {
                    "playable": ["lynx"],
                    "predators": ["fox"],
                    "prey": ["capybara"],
                },
                "fish": [
                    {"kind": "lynx", "count": 3},
                    {"kind": "roach", "count": 5},
                    "lynx",
                ],
            }
        )
    )
    (regions / "b.json").write_text(json.dumps({"animals": {"playable": ["fox"]}}))
    dest = tmp_path / "out"
    dest.mkdir()
    out = _pruned_regions(tmp_path, {"lynx"}, dest)
    assert sorted(out) == ["regions/a.json"]
    pruned = json.loads(out["regions/a.json"].read_text())
    assert pruned["animals"] == {
        "playable": [],
        "predators": ["fox"],
        "prey": ["capybara"],
    }
    assert pruned["fish"] == [{"kind": "roach", "count": 5}]
    assert "lynx" in (regions / "a.json").read_text()


def _anaconda_target() -> dict[str, Any]:
    """The anaconda: a model-only `start_from`, a game `file_from`."""
    return {
        "id": "anaconda",
        "entry": {
            "id": "anaconda",
            "name": "Green anaconda",
            "latin": "Eunectes murinus",
            "gbif_key": 2462858,
            "start_from": "grass_snake",
            "file_from": "lizard",
        },
        "start_from": "grass_snake",
        "prey": False,
        "size_cm": None,
    }


def test_animal_example_comes_from_file_from(tmp_path: Path) -> None:
    world = _world(tmp_path)
    (world / "animals" / "lizard.json").write_text(
        json.dumps({"id": "lizard", "note": "a scaly example"})
    )
    assert not (world / "animals" / "grass_snake.json").is_file()
    _inputs(tmp_path)
    brief = json.loads((tmp_path / "home.json").read_text())
    research = json.loads((tmp_path / "research.json").read_text())
    region = json.loads((tmp_path / "regions" / "fi_test.json").read_text())
    prompt = AnimalMakerStage()._animal_prompt(
        world, brief, research, region, _anaconda_target()
    )
    assert "a scaly example" in prompt
    assert "grass_snake" not in prompt


def test_animal_example_falls_back_to_start_from(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    brief = json.loads((tmp_path / "home.json").read_text())
    research = json.loads((tmp_path / "research.json").read_text())
    region = json.loads((tmp_path / "regions" / "fi_test.json").read_text())
    target = _anaconda_target()
    del target["entry"]["file_from"]
    (world / "animals" / "grass_snake.json").write_text(
        json.dumps({"id": "grass_snake", "note": "an old-list example"})
    )
    prompt = AnimalMakerStage()._animal_prompt(world, brief, research, region, target)
    assert "an old-list example" in prompt


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


def test_oneshot_skips_home_items_without_regions(tmp_path: Path) -> None:
    world = _world(tmp_path)
    minset = _minset_with_prey()
    minset["make_animal"] = False
    minset["prey"] = None
    _inputs(tmp_path, minset=minset)
    shutil.rmtree(tmp_path / "regions")
    client = ScriptClient([])
    result = _stage().run(_ctx(tmp_path, world, client))
    assert result == {"animal_files": [], "cost_usd": 0.0}
    assert client.prompts == []


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
    assert result["animal_files"] == ["animals/capybara.json", "animals/lynx.json"]
    assert len(client.prompts) == 2
    region = json.loads(
        next((tmp_path / "regions").glob("*.json")).read_text(encoding="utf-8")
    )
    assert "capybara" in region["animals"]["prey"]
    assert "lynx" in region["animals"]["playable"]


def test_resume_skips_a_passed_animal_file(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path, minset=_minset_with_prey())
    placed = json.loads(_animal_answer("capybara"))
    placed["model"]["length"] = 0.233
    (tmp_path / "animals").mkdir()
    (tmp_path / "animals" / "capybara.json").write_text(json.dumps(placed))
    attempt = tmp_path / "tries" / "capybara" / "animal" / "1"
    attempt.mkdir(parents=True)
    (attempt / "answer.json").write_text(_animal_answer("capybara"))
    (attempt / "animal.json").write_text(json.dumps(placed))
    (attempt / "errors.json").write_text("[]")
    client = ScriptClient([_edits_answer(), _animal_answer("lynx"), _edits_answer()])
    result = _stage().run(_ctx(tmp_path, world, client))
    assert result["animal_files"] == ["animals/capybara.json", "animals/lynx.json"]
    assert len(client.prompts) == 3
    assert json.loads((tmp_path / "animals" / "capybara.json").read_text()) == placed


def _strip_cast(tmp_path: Path) -> None:
    path = next((tmp_path / "regions").glob("*.json"))
    region = json.loads(path.read_text(encoding="utf-8"))
    region["animals"] = {"playable": [], "predators": [], "prey": []}
    path.write_text(json.dumps(region), encoding="utf-8")


def _seed_plan(tmp_path: Path, *species: str) -> None:
    taxa = {
        "lynx": (2435240, "Lynx lynx"),
        "capybara": (2437585, "Hydrochoerus hydrochaeris"),
    }
    (tmp_path / "species-plan.json").write_text(
        json.dumps(
            {
                "species": [
                    {
                        "species": name,
                        "decision": "new",
                        "reason": "made one-shot for this home",
                        "wild": True,
                        "taxon": {
                            "gbif_key": taxa[name][0],
                            "rank": "SPECIES",
                            "name": taxa[name][1],
                        },
                    }
                    for name in species
                ],
                "food_web_ok": True,
                "missing_food": [],
                "added_young": [],
            }
        )
    )


def test_prey_made_before_its_hunter_is_seated_and_packed(
    tmp_path: Path,
) -> None:
    from builder.stages.pack import PackStage

    world = _world(tmp_path)
    _inputs(tmp_path, minset=_minset_with_prey())
    _strip_cast(tmp_path)
    _seed_plan(tmp_path, "lynx")
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
    region = json.loads(
        next((tmp_path / "regions").glob("*.json")).read_text(encoding="utf-8")
    )
    assert "capybara" in region["animals"]["prey"]
    assert "lynx" in region["animals"]["playable"]
    plan = json.loads((tmp_path / "species-plan.json").read_text(encoding="utf-8"))
    assert [(e["species"], e["decision"]) for e in plan["species"]] == [
        ("lynx", "new"),
        ("capybara", "new"),
    ]
    (tmp_path / "models" / "capybara.glb").write_bytes(b"glb")
    (tmp_path / "models" / "lynx.glb").write_bytes(b"glb")
    PackStage().run(_ctx(tmp_path, world, client))
    manifest = json.loads((tmp_path / "pack" / "manifest.json").read_text())
    assert manifest["new_species"] == ["capybara", "lynx"]
    assert (tmp_path / "pack" / "animals" / "capybara.json").is_file()
    assert (tmp_path / "pack" / "models" / "capybara.glb").is_file()
    pack_region = json.loads(
        next((tmp_path / "pack" / "regions").glob("*.json")).read_text()
    )
    assert "capybara" in pack_region["animals"]["prey"]


def test_resume_seats_and_plans_earlier_made_prey(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path, minset=_minset_with_prey())
    _strip_cast(tmp_path)
    _seed_plan(tmp_path, "lynx")
    (tmp_path / "animals").mkdir()
    (tmp_path / "animals" / "capybara.json").write_text(
        json.dumps({"id": "capybara", "name": "Good", "tags": ["rodent"]})
    )
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "capybara.json").write_text("{}")
    client = ScriptClient([_animal_answer("lynx"), _edits_answer()])
    result = _stage().run(_ctx(tmp_path, world, client))
    assert result["animal_files"] == ["animals/capybara.json", "animals/lynx.json"]
    assert len(client.prompts) == 2
    region = json.loads(
        next((tmp_path / "regions").glob("*.json")).read_text(encoding="utf-8")
    )
    assert "capybara" in region["animals"]["prey"]
    plan = json.loads((tmp_path / "species-plan.json").read_text(encoding="utf-8"))
    assert [(e["species"], e["decision"]) for e in plan["species"]] == [
        ("lynx", "new"),
        ("capybara", "new"),
    ]


def _hare_world(tmp_path: Path) -> Path:
    world = _world(tmp_path)
    (world / "animals" / "hare.json").write_text(
        json.dumps({"id": "hare", "tags": ["rodent", "mammal"]})
    )
    (world / "animal-list.json").write_text(
        json.dumps(
            {
                "homes": ["boreal_forest"],
                "not_yet": {},
                "animals": [
                    LYNX,
                    CAPYBARA,
                    {
                        "id": "hare",
                        "name": "Mountain hare",
                        "latin": "Lepus timidus",
                        "gbif_key": 2436756,
                        "in_library": "hare",
                    },
                ],
            }
        )
    )
    return world


def _hare_research(tmp_path: Path) -> None:
    research = json.loads((tmp_path / "research.json").read_text(encoding="utf-8"))
    research["main_food"] = {"name": "mountain hare", "kind": "prey", "size_cm": 55}
    (tmp_path / "research.json").write_text(json.dumps(research))


def test_hunter_keeps_its_preys_tags_through_the_fix_round(
    tmp_path: Path,
) -> None:
    world = _hare_world(tmp_path)
    _inputs(tmp_path)
    _hare_research(tmp_path)
    path = next((tmp_path / "regions").glob("*.json"))
    region = json.loads(path.read_text(encoding="utf-8"))
    region["animals"]["prey"] = ["hare"]
    path.write_text(json.dumps(region), encoding="utf-8")
    client = ScriptClient(
        [
            _nameless_answer("lynx"),
            _animal_answer("lynx", hunts=[]),
            _edits_answer(),
        ]
    )
    _stage().run(_ctx(tmp_path, world, client))
    assert len(client.prompts) == 3
    made = json.loads((tmp_path / "animals" / "lynx.json").read_text())
    assert made["hunts"] == ["mammal", "rodent"]


def _gull_research(tmp_path: Path) -> None:
    """Mussels and fish over a sea region: the herring gull's case."""
    research = json.loads((tmp_path / "research.json").read_text(encoding="utf-8"))
    research["main_food"] = {"name": "blue mussels", "kind": "bottom_bugs"}
    research["other_foods"] = [{"name": "fish", "kind": "prey", "size_cm": 20}]
    (tmp_path / "research.json").write_text(json.dumps(research))
    path = next((tmp_path / "regions").glob("*.json"))
    region = json.loads(path.read_text(encoding="utf-8"))
    region["food"].append({"kind": "bottom_bugs"})
    path.write_text(json.dumps(region), encoding="utf-8")


def test_diet_comes_from_the_research(tmp_path: Path) -> None:
    """#140.3: mussels and fish mean bottom_bugs, not the model's berries."""
    world = _world(tmp_path)
    _inputs(tmp_path)
    _gull_research(tmp_path)
    client = ScriptClient([_animal_answer("lynx", diet=["berries"]), _edits_answer()])
    _stage().run(_ctx(tmp_path, world, client))
    made = json.loads((tmp_path / "animals" / "lynx.json").read_text())
    assert made["diet"] == ["bottom_bugs"]


def test_pure_hunter_gets_an_empty_diet(tmp_path: Path) -> None:
    """#140.3: all prey in the research, nothing in the diet."""
    world = _world(tmp_path)
    _inputs(tmp_path)
    research = json.loads((tmp_path / "research.json").read_text(encoding="utf-8"))
    research["other_foods"] = []
    (tmp_path / "research.json").write_text(json.dumps(research))
    client = ScriptClient([_animal_answer("lynx"), _edits_answer()])
    _stage().run(_ctx(tmp_path, world, client))
    made = json.loads((tmp_path / "animals" / "lynx.json").read_text())
    assert made["diet"] == []


def test_prey_keeps_the_models_diet(tmp_path: Path) -> None:
    """#140.3: the research is the hunter's; its prey keeps its own diet."""
    world = _world(tmp_path)
    _inputs(tmp_path, minset=_minset_with_prey())
    client = ScriptClient(
        [
            _animal_answer("capybara", diet=["grass"]),
            _edits_answer(),
            _animal_answer("lynx"),
            _edits_answer(),
        ]
    )
    _stage().run(_ctx(tmp_path, world, client))
    made = json.loads((tmp_path / "animals" / "capybara.json").read_text())
    assert made["diet"] == ["grass"]
    hunter = json.loads((tmp_path / "animals" / "lynx.json").read_text())
    assert hunter["diet"] == ["berries"]


def test_length_comes_from_the_build(tmp_path: Path) -> None:
    """#140.3: the file's length is the build's measurement, not 0.61."""
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient(
        [
            _animal_answer("lynx", box=[0.45, 0.45, 0.65]),
            _edits_answer(),
        ]
    )
    _stage().run(_ctx(tmp_path, world, client))
    made = json.loads((tmp_path / "animals" / "lynx.json").read_text())
    assert made["model"]["length"] == 0.233
    assert made["box"] == [0.45, 0.45, 0.65]


def test_build_without_a_measured_length_stops(tmp_path: Path) -> None:
    """#140.3: a good build with no length line is a person, not a retry."""

    def _build_silent(sandbox: Path, spec: str) -> dict[str, Any]:
        (sandbox / "assets" / "animals").mkdir(parents=True, exist_ok=True)
        (sandbox / "assets" / "animals" / f"{spec}.glb").write_bytes(b"glb-bytes")
        return {"ok": True, "log": f"BUILT {spec}: ok"}

    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient([_animal_answer("lynx"), _edits_answer()])
    with pytest.raises(_Stopped, match="no measured length"):
        _stage(build=_build_silent).run(_ctx(tmp_path, world, client))


def test_hunter_stops_when_its_prey_is_not_seated(tmp_path: Path) -> None:
    world = _hare_world(tmp_path)
    _inputs(tmp_path)
    _hare_research(tmp_path)
    _strip_cast(tmp_path)
    client = ScriptClient([])
    with pytest.raises(_Stopped, match="isn't seated"):
        _stage().run(_ctx(tmp_path, world, client))
    assert client.prompts == []


def test_oneshot_refreshes_a_stale_welcome(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    research = json.loads((tmp_path / "research.json").read_text(encoding="utf-8"))
    research["main_food"] = {"name": "capybara", "kind": "prey", "size_cm": 110}
    (tmp_path / "research.json").write_text(json.dumps(research))
    path = next((tmp_path / "regions").glob("*.json"))
    region = json.loads(path.read_text(encoding="utf-8"))
    region["welcome"] = "Go and find some kapok leaves to eat!"
    path.write_text(json.dumps(region), encoding="utf-8")
    client = ScriptClient([_animal_answer("lynx"), _edits_answer()])
    _stage().run(_ctx(tmp_path, world, client))
    region = json.loads(path.read_text(encoding="utf-8"))
    assert region["welcome"].endswith("Go and find a capybara to eat!")


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


def test_oneshot_keeps_a_reused_places_welcome(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(
        tmp_path,
        minset={
            "make_animal": False,
            "animal_reason": "lynx is in the library",
            "make_home": False,
            "home_reason": "tt_park is built",
            "home_habitat": "boreal_forest",
            "prey": None,
            "prey_reason": "a library prey shares its habitat",
        },
    )
    brief = json.loads((tmp_path / "home.json").read_text(encoding="utf-8"))
    brief["reuse_place_id"] = "tt_park"
    (tmp_path / "home.json").write_text(json.dumps(brief))
    path = next((tmp_path / "regions").glob("*.json"))
    region = json.loads(path.read_text(encoding="utf-8"))
    region["welcome"] = "Old words stay."
    path.write_text(json.dumps(region), encoding="utf-8")
    client = ScriptClient([])
    _stage().run(_ctx(tmp_path, world, client))
    region = json.loads(path.read_text(encoding="utf-8"))
    assert region["welcome"] == "Old words stay."
    assert client.prompts == []


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
    homes = world / "homes"
    homes.mkdir(parents=True)
    for stem in ("boreal_forest", "boreal_lake"):
        (homes / f"{stem}.json").write_text(
            json.dumps(
                {
                    "id": stem,
                    "description": f"A northern {stem.split('_')[1]}.",
                    "palette": {"ground_lush": "#5f9445"},
                    "trees": {"density": 0.02, "mix": []},
                    "lake_life": {
                        "reeds": 0.1,
                        "lilies": 0.0,
                        "pondweed": 0.0,
                    },
                }
            )
        )
    plants = world / "models" / "plants"
    plants.mkdir(parents=True)
    (plants / "bilberry.json").write_text(json.dumps({"id": "bilberry"}))
    (plants / "wood_anemone.json").write_text(json.dumps({"id": "wood_anemone"}))
    return world


def _home_inputs(
    tmp_path: Path, make_home: bool = True, habitat: str = "rainforest"
) -> None:
    (tmp_path / "home.json").write_text(
        json.dumps(
            {
                "species_key": 5225712,
                "id": "anaconda",
                "latin": "Eunectes murinus",
                "name": "Green anaconda",
                "home": habitat,
                "needs_home": True,
                "center": {"lat": -3.45, "lon": -61.95},
                "cell": "-3.5_-62.0",
            }
        )
    )
    (tmp_path / "research.json").write_text(
        json.dumps(
            {
                "surroundings": [
                    {"name": "kapok tree", "kind": "tree", "food_kind": "browse"},
                    {
                        "name": "water lettuce",
                        "kind": "plant",
                        "food_kind": "water_plants",
                    },
                    {"name": "muddy bank", "kind": "ground"},
                ],
                "main_food": {"name": "capybara", "kind": "prey", "size_cm": 110},
                "other_foods": [{"name": "spectacled caiman", "kind": "prey"}],
                "sources": ["https://example.org/anaconda"],
            }
        )
    )
    (tmp_path / "minset.json").write_text(
        json.dumps(
            {
                "make_animal": True,
                "make_home": make_home,
                "home_habitat": habitat,
                "prey": None,
            }
        )
    )


def _choices_answer(**over: Any) -> str:
    choices: dict[str, Any] = {
        "description": "Flat muddy rainforest beside a slow river.",
        "land": "flat",
        "water": "river",
        "wetness": "very_wet",
        "water_name": "Slow river",
        "ice": "none",
        "caves": "none",
        "seabed": "mud",
        "palette": {
            "ground_lush": "#2f7d2c",
            "ground_dry": "#6f9a3f",
            "ground_high": "#2a6b2e",
            "dirt": "#5e422e",
            "rock": "#7d7a72",
            "sand": "#c2ad85",
            "water_deep": "#3f4a2c",
            "water_shallow": "#7a7e5e",
        },
        "trees": {
            "density": "dense",
            "mix": [
                {
                    "shape": "round",
                    "leaves": "#2e8b3d",
                    "trunk": "#5f4632",
                    "weight": 100,
                    "food": "browse",
                    "autumn": None,
                }
            ],
        },
        "grass": "some",
        "flowers": [{"name": "heliconia", "color": "#e8452c"}],
        "plants": [],
        "water_life": {"reeds": "some", "lilies": "many", "pondweed": "some"},
        "food": [{"kind": "browse", "names": ["kapok leaves"]}],
    }
    choices.update(over)
    return json.dumps(choices)


class _AdaptStub:
    """The world's adapt tool, stubbed: a canned home, or errors in order."""

    def __init__(self, errors: list[list[str]] | None = None) -> None:
        self.errors = list(errors or [])
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, world: Path, base: Path, choices_path: Path, out_path: Path
    ) -> list[str]:
        self.calls.append(json.loads(Path(choices_path).read_text()))
        Path(out_path).write_text(
            json.dumps({"id": "rainforest", "name": "Rainforest home"})
        )
        if self.errors:
            return self.errors.pop(0)
        return []


def _home_stage(**over: Any) -> HomeMakerStage:
    args: dict[str, Any] = {"adapt": _AdaptStub()}
    args.update(over)
    return HomeMakerStage(**args)


def test_newhome_makes_home_first_try(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path)
    adapt = _AdaptStub()
    client = ScriptClient([_choices_answer()])
    result = HomeMakerStage(adapt=adapt).run(_ctx(tmp_path, world, client))
    assert result["home_files"] == ["homes/rainforest.json"]
    assert result["plant_files"] == []
    home = json.loads((tmp_path / "homes" / "rainforest.json").read_text())
    assert home["id"] == "rainforest"
    tried = json.loads(
        (tmp_path / "tries" / "rainforest" / "1" / "choices.json").read_text()
    )
    assert tried["id"] == "rainforest"
    assert tried["tropical"] is True
    assert tried["sources"] == ["https://example.org/anaconda", "GBIF.org"]
    assert adapt.calls == [tried]
    checks = json.loads((tmp_path / "checks" / "newhome.json").read_text())
    assert checks["made"] == "rainforest"
    assert checks["plants"] == []
    assert len(client.prompts) == 1
    assert client.options[0]["response_format"]["json_schema"]["name"] == (
        "HomeChoices"
    )


def _fresh_clone(tmp_path: Path) -> Path:
    """A fresh worker clone: the adapt tool and library, no built models."""
    real = _adapt_world()
    world = tmp_path / "world"
    (world / "tools").mkdir(parents=True)
    shutil.copy(real / "tools" / "adapt_home.py", world / "tools" / "adapt_home.py")
    shutil.copy(
        real / "tools" / "validate_region.py", world / "tools" / "validate_region.py"
    )
    shutil.copytree(real / "homes", world / "homes")
    shutil.copytree(real / "animals", world / "animals")
    shutil.copytree(real / "models" / "plants", world / "models" / "plants")
    return world


def _fake_glb() -> bytes:
    """A .glb the triangle budget can read: 100 triangles, no geometry."""
    import struct

    gltf = json.dumps(
        {
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
            "accessors": [{"count": 300}],
        }
    ).encode()
    return (
        b"glTF"
        + struct.pack("<II", 2, 12 + 8 + len(gltf))
        + struct.pack("<I", len(gltf))
        + b"JSON"
        + gltf
    )


def _fake_builder(
    built: list[str],
) -> Any:
    """A plant/tree build that writes a readable fake, recording specs."""

    def fake_build(target_world: Path, spec: str) -> dict[str, Any]:
        from builder.stages.models import _target_for_spec

        target_rel = _target_for_spec(spec)
        assert target_rel is not None, spec
        target = target_world / target_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix == ".glb":
            target.write_bytes(_fake_glb())
        else:
            target.write_text(json.dumps({"id": target.stem}))
        built.append(spec)
        return {"ok": True, "log": f"BUILT {spec}: fake"}

    return fake_build


def test_newhome_builds_missing_plant_models(tmp_path: Path) -> None:
    """#139.1: a fresh clone with no .glb still adapts a home from the forest."""
    world = _fresh_clone(tmp_path)
    _home_inputs(tmp_path, habitat="boreal_forest")
    built: list[str] = []
    fake_build = _fake_builder(built)

    answer = _choices_answer(plants=[{"model": "heather"}])
    client = ScriptClient([answer, answer])
    stage = HomeMakerStage(plant_cache=tmp_path / "cache", plant_build=fake_build)
    result = stage.run(_ctx(tmp_path, world, client))
    assert result["home_files"] == ["homes/boreal_forest.json"]
    assert built == ["models/plants/heather.json"]
    assert (world / "assets" / "plants" / "heather.glb").is_file()
    home = json.loads((tmp_path / "homes" / "boreal_forest.json").read_text())
    assert home["ground_cover"]["plants"][0]["model"] == "heather"


def test_plant_cache_restores_without_rebuilding(tmp_path: Path) -> None:
    """#139.1: after a restart the cache copies, it does not rebuild."""
    from builder.stages.models import ensure_plant_models

    world = _fresh_clone(tmp_path)
    cache = tmp_path / "cache"
    calls: list[str] = []
    fake_build = _fake_builder(calls)

    assert ensure_plant_models(world, ["heather", "heather"], cache, fake_build) == []
    assert calls == ["models/plants/heather.json"]
    shutil.rmtree(world / "assets")
    assert ensure_plant_models(world, ["heather"], cache, fake_build) == []
    assert calls == ["models/plants/heather.json"]
    assert (world / "assets" / "plants" / "heather.glb").is_file()

    def failing_build(target_world: Path, plant_id: str) -> dict[str, Any]:
        return {"ok": False, "log": "Blender missing"}

    shutil.rmtree(world / "assets")
    shutil.rmtree(cache)
    errors = ensure_plant_models(world, ["heather"], cache, failing_build)
    assert len(errors) == 1 and "heather" in errors[0]


def test_newhome_retries_a_build_first_without_a_fix_round(
    tmp_path: Path,
) -> None:
    """#139.3: a tree spec builds in code; the model is not asked again."""
    world = _home_world(tmp_path)
    _home_inputs(tmp_path)
    tree_err = (
        '"trees.mix[0]": build it first with tools/build_model.sh models/trees/fir.json'
    )
    adapt = _AdaptStub([[tree_err]])
    client = ScriptClient([_choices_answer()])
    built: list[str] = []
    stage = HomeMakerStage(
        adapt=adapt, plant_cache=tmp_path / "cache", plant_build=_fake_builder(built)
    )
    result = stage.run(_ctx(tmp_path, world, client))
    assert result["home_files"] == ["homes/rainforest.json"]
    assert built == ["models/trees/fir.json"]
    assert len(adapt.calls) == 2
    assert len(client.prompts) == 1


def test_newhome_build_failure_is_the_workers_job(tmp_path: Path) -> None:
    """#139.3: still missing after a rebuild is not a person's job."""
    world = _home_world(tmp_path)
    _home_inputs(tmp_path)
    tree_err = (
        '"trees.mix[0]": build it first with tools/build_model.sh models/trees/fir.json'
    )
    adapt = _AdaptStub([[tree_err]] * 4)
    client = ScriptClient([_choices_answer(), _choices_answer()])

    def failing_build(target_world: Path, spec: str) -> dict[str, Any]:
        return {"ok": False, "log": "Blender missing"}

    stage = HomeMakerStage(adapt=adapt, plant_build=failing_build)
    with pytest.raises(_Stopped, match="the worker could not build it"):
        stage.run(_ctx(tmp_path, world, client))


def test_newhome_border_home_carries_plan_item(tmp_path: Path) -> None:
    """#130.4: ecoregion, habitat and based_on set by code, never the model."""
    world = _home_world(tmp_path)
    (world / "homes" / "tundra.json").write_text(
        json.dumps(
            {
                "id": "tundra",
                "description": "Treeless northern land.",
                "palette": {"ground_lush": "#5f9445"},
                "trees": {"density": 0.0, "mix": []},
                "lake_life": {"reeds": 0.0, "lilies": 0.0, "pondweed": 0.0},
            }
        )
    )
    _home_inputs(tmp_path, habitat="tundra")
    brief = json.loads((tmp_path / "home.json").read_text())
    brief.update(
        {
            "home_id": "tundra_774",
            "ecoregion": 774,
            "base_home": "tundra",
            "based_on": "tundra",
        }
    )
    (tmp_path / "home.json").write_text(json.dumps(brief))
    adapt = _AdaptStub()
    client = ScriptClient([_choices_answer()])
    result = HomeMakerStage(adapt=adapt).run(_ctx(tmp_path, world, client))
    assert result["home_files"] == ["homes/tundra_774.json"]
    tried = json.loads(
        (tmp_path / "tries" / "tundra" / "1" / "choices.json").read_text()
    )
    assert tried["id"] == "tundra_774"
    assert tried["ecoregions"] == [774]
    assert tried["habitat"] == "tundra"
    assert tried["based_on"] == "tundra"
    assert "ecoregion" not in client.prompts[0]


def test_newhome_prompt_carries_the_inputs(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path)
    client = ScriptClient([_choices_answer()])
    _home_stage().run(_ctx(tmp_path, world, client))
    prompt = client.prompts[0]
    assert "for the rainforest looks" in prompt
    assert "the Green anaconda lives near -3.45, -61.95" in prompt
    assert "- kapok tree (tree), animals eat it as browse" in prompt
    assert "- muddy bank (ground)" in prompt
    assert "capybara (prey), spectacled caiman (prey)" in prompt
    assert "A northern forest." in prompt
    assert "bilberry, wood_anemone" in prompt


def test_newhome_prompt_adds_the_plant_looks(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path)
    (tmp_path / "looks.json").write_text(
        json.dumps(
            {
                "plants": {
                    "water lettuce": {
                        "colours": ["light green"],
                        "marks": ["floating rosette"],
                    }
                }
            }
        )
    )
    client = ScriptClient([_choices_answer()])
    _home_stage().run(_ctx(tmp_path, world, client))
    assert "  - water lettuce: light green; floating rosette" in client.prompts[0]


def test_newhome_picks_the_lake_home_for_lake_habitats(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path, habitat="boreal_lake")
    client = ScriptClient([_choices_answer(water="lake")])
    result = _home_stage().run(_ctx(tmp_path, world, client))
    assert result["home_files"] == ["homes/boreal_lake.json"]
    assert "A northern lake." in client.prompts[0]


def test_newhome_non_tropical_home(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path, habitat="boreal_forest")
    brief = json.loads((tmp_path / "home.json").read_text())
    brief["center"] = {"lat": 61.2, "lon": 26.9}
    (tmp_path / "home.json").write_text(json.dumps(brief))
    adapt = _AdaptStub()
    client = ScriptClient([_choices_answer()])
    HomeMakerStage(adapt=adapt).run(_ctx(tmp_path, world, client))
    assert adapt.calls[0]["tropical"] is False


def test_newhome_fix_round_repairs_adapt_errors(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path)
    adapt = _AdaptStub([["the water must be a river here"]])
    client = ScriptClient([_choices_answer(), _choices_answer(water="river")])
    result = HomeMakerStage(adapt=adapt).run(_ctx(tmp_path, world, client))
    assert result["home_files"] == ["homes/rainforest.json"]
    assert "the water must be a river here" in client.prompts[1]
    assert client.calls[1][1]["role"] == "assistant"
    assert len(adapt.calls) == 2


def test_newhome_still_failing_stops_for_a_person(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path)
    adapt = _AdaptStub([["boom"], ["boom"]])
    client = ScriptClient([_choices_answer(), _choices_answer()])
    with pytest.raises(_Stopped, match="a person makes it"):
        HomeMakerStage(adapt=adapt).run(_ctx(tmp_path, world, client))


def test_newhome_nothing_to_make_calls_nothing(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path, make_home=False)
    client = ScriptClient([])
    result = _home_stage().run(_ctx(tmp_path, world, client))
    assert result == {"home_files": [], "cost_usd": 0.0}


def test_newhome_skips_a_made_home(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path)
    (tmp_path / "homes").mkdir()
    (tmp_path / "homes" / "rainforest.json").write_text("{}")
    (tmp_path / "checks").mkdir()
    (tmp_path / "checks" / "newhome.json").write_text("{}")
    client = ScriptClient([])
    stage = _home_stage()
    result = stage.run(_ctx(tmp_path, world, client))
    assert result == {"home_files": ["homes/rainforest.json"], "cost_usd": 0.0}


def test_newhome_retries_after_a_failed_try(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path)
    failing = HomeMakerStage(adapt=_AdaptStub([["boom"], ["boom"]]))
    with pytest.raises(_Stopped, match="a person makes it"):
        failing.run(_ctx(tmp_path, world, ScriptClient([_choices_answer()] * 2)))
    assert not (tmp_path / "checks" / "newhome.json").is_file()
    result = _home_stage().run(_ctx(tmp_path, world, ScriptClient([_choices_answer()])))
    assert result["home_files"] == ["homes/rainforest.json"]
    assert (tmp_path / "checks" / "newhome.json").is_file()


def test_newhome_skips_a_passing_home(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path)
    _home_stage().run(_ctx(tmp_path, world, ScriptClient([_choices_answer()])))
    result = _home_stage().run(_ctx(tmp_path, world, ScriptClient([])))
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


def test_reference_spec_falls_back_to_examples(tmp_path: Path) -> None:
    world = _world(tmp_path)
    assert _reference_spec(world, "grass_snake") is None
    example = world / "models" / "examples"
    example.mkdir()
    (example / "grass_snake.json").write_text(
        json.dumps({"id": "grass_snake", "name": "Grass snake coils"})
    )
    assert _reference_path(world, "grass_snake") == example / "grass_snake.json"
    assert _reference_spec(world, "grass_snake")["id"] == "grass_snake"
    assert _reference_looks(world, "grass_snake") == "Grass snake coils"
    (world / "models" / "grass_snake.json").write_text(
        json.dumps({"id": "grass_snake", "name": "Plain grass snake"})
    )
    assert _reference_path(world, "grass_snake") == (
        world / "models" / "grass_snake.json"
    )


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


def test_closest_home_picks_land_or_lake(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    assert _closest_home(world, "rainforest").name == "boreal_forest.json"
    assert _closest_home(world, "boreal_lake").name == "boreal_lake.json"
    assert _closest_home(world, "coral_sea").name == "boreal_lake.json"


def test_closest_home_missing_template_stops(tmp_path: Path) -> None:
    world = tmp_path / "world"
    (world / "homes").mkdir(parents=True)
    with pytest.raises(_Stopped, match="no home template"):
        _closest_home(world, "rainforest")


def test_home_choices_schema_is_strict() -> None:
    schema = _home_choices_schema(["bilberry", "bracken"], ["oak"])
    assert_strict_subset(schema)
    assert sorted(schema["required"]) == sorted(schema["properties"])
    assert schema["properties"]["land"]["enum"][-1] == "mountains"
    assert schema["properties"]["ice"]["enum"] == [
        "none",
        "frozen_in_winter",
        "glaciers",
        "polar",
    ]
    assert schema["properties"]["caves"]["enum"] == ["none", "some"]
    assert schema["properties"]["seabed"]["enum"] == ["mud", "sand"]
    assert "icebergs" in schema["properties"]["ice"]["description"]
    assert "dens in rock" in schema["properties"]["caves"]["description"]
    assert "mud otherwise" in schema["properties"]["seabed"]["description"]
    plants = schema["properties"]["plants"]["items"]["properties"]
    assert plants["model"]["enum"] == ["bilberry", "bracken"]
    assert plants["on"]["enum"] == ["ground", "seabed"]
    assert sorted(schema["properties"]["plants"]["items"]["required"]) == [
        "model",
        "on",
    ]
    shapes = schema["properties"]["trees"]["properties"]["mix"]["items"]["properties"][
        "shape"
    ]
    assert shapes["enum"] == ["spruce", "pine", "birch", "round", "rowan", "oak"]
    assert schema["properties"]["trees"]["properties"]["mix"]["maxItems"] == 4
    bare = _home_choices_schema([], [])
    props = bare["properties"]
    assert props["plants"]["items"]["properties"]["model"]["enum"] == ["none"]
    assert props["trees"]["properties"]["mix"]["items"]["properties"]["shape"][
        "enum"
    ] == ["spruce", "pine", "birch", "round", "rowan"]


def test_choices_files_drops_none_plants() -> None:
    choices, errors = _choices_files(
        json.dumps({"plants": [{"model": "none"}, {"model": "bilberry"}]})
    )
    assert errors == []
    assert choices == {"plants": [{"model": "bilberry"}]}
    assert _choices_files("not json")[0] is None
    assert _choices_files("[]")[0] is None


def test_choices_files_strips_stray_spaces() -> None:
    """#135.1: a stray space fails no colour, enum or id."""
    choices, errors = _choices_files(
        json.dumps(
            {
                "palette": {"trunk": " #5a4433 "},
                "ice": " none ",
                "plants": [{"model": " bilberry ", "on": " ground "}],
            }
        )
    )
    assert errors == []
    assert choices == {
        "palette": {"trunk": "#5a4433"},
        "ice": "none",
        "plants": [{"model": "bilberry"}],
    }


def _cut_response(content: str, out_tokens: int = 8000) -> SimpleNamespace:
    return SimpleNamespace(
        model="fake-test-model",
        choices=[
            SimpleNamespace(
                finish_reason="length",
                message=SimpleNamespace(content=content, tool_calls=None),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=100, output_tokens=out_tokens, completion_tokens=0
        ),
    )


def _whole_response(content: str) -> SimpleNamespace:
    cut = _cut_response(content)
    cut.choices[0].finish_reason = "stop"
    return cut


def test_ask_with_room_retries_a_cut_answer() -> None:
    """#135.2: a length stop retries once with more tokens."""
    calls: list[dict[str, Any]] = []
    answers = [_cut_response('{"palette": {"tr'), _whole_response('{"palette": {}}')]

    def chat(messages: list[dict[str, Any]], **kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return answers.pop(0)

    request = {"type": "json_schema", "json_schema": {"name": "HomeChoices"}}
    response = _ask_with_room(
        chat, [{"role": "user", "content": "hi"}], response_format=request
    )
    assert response.choices[0].message.content == '{"palette": {}}'
    assert calls[0] == {"response_format": request}
    assert calls[1] == {"response_format": request, "max_tokens": 16384}


def test_ask_with_room_plays_a_truncated_recording(tmp_path: Path) -> None:
    """#135.2: the cut answer and its retry record and replay."""
    from builder.records import RecordingClient, ReplayClient

    records = tmp_path / "records"

    class Scripted:
        model = "fake-test-model"

        def __init__(self, answers: list[SimpleNamespace]) -> None:
            self.answers = answers

        def chat(
            self, messages: list[dict[str, Any]], **kwargs: Any
        ) -> SimpleNamespace:
            return self.answers.pop(0)

    messages = [{"role": "user", "content": "hi"}]
    recorded = RecordingClient(
        Scripted([_cut_response('{"a": 1'), _whole_response('{"a": 2}')]),
        records,
        "newhome",
    )
    assert (
        _ask_with_room(recorded.chat, messages).choices[0].message.content == '{"a": 2}'
    )
    replay = ReplayClient(records, "newhome")
    assert (
        _ask_with_room(replay.chat, messages).choices[0].message.content == '{"a": 2}'
    )
    replay.verify_consumed()


def test_choices_files_drops_ground_but_keeps_seabed() -> None:
    choices, errors = _choices_files(
        json.dumps(
            {
                "plants": [
                    {"model": "bilberry", "on": "ground"},
                    {"model": "seaweed", "on": "seabed"},
                ]
            }
        )
    )
    assert errors == []
    assert choices == {
        "plants": [{"model": "bilberry"}, {"model": "seaweed", "on": "seabed"}]
    }


def test_code_choices_adds_id_tropics_sources() -> None:
    choices: dict[str, Any] = {}
    _code_choices(
        choices,
        "rainforest",
        {"center": {"lat": -3.45, "lon": -61.95}},
        {"sources": ["https://example.org/a"]},
    )
    assert choices == {
        "id": "rainforest",
        "tropical": True,
        "sources": ["https://example.org/a", "GBIF.org"],
        "ice": "none",
    }
    north: dict[str, Any] = {}
    _code_choices(north, "taiga", {"center": {"lat": 61.0}}, {"sources": []})
    assert north["tropical"] is False
    edge: dict[str, Any] = {}
    _code_choices(edge, "edge", {"center": {"lat": 23.0}}, {"sources": []})
    assert edge["tropical"] is False
    assert "ice" not in edge


def test_code_choices_clears_ice_in_the_tropics() -> None:
    choices: dict[str, Any] = {"ice": "glaciers"}
    _code_choices(choices, "rainforest", {"center": {"lat": -3.45}}, {"sources": []})
    assert choices["tropical"] is True
    assert choices["ice"] == "none"
    cold: dict[str, Any] = {"ice": "polar"}
    _code_choices(cold, "ice", {"center": {"lat": 78.25}}, {"sources": []})
    assert cold["ice"] == "polar"


def test_surroundings_text_lists_food_and_looks() -> None:
    research = {
        "surroundings": [
            {"name": "kapok tree", "kind": "tree", "food_kind": "browse"},
            {"name": "muddy bank", "kind": "ground"},
        ]
    }
    looks = {"plants": {"kapok": {"colours": ["green"], "marks": ["tall"]}}}
    assert _surroundings_text(research, looks) == (
        "- kapok tree (tree), animals eat it as browse\n"
        "- muddy bank (ground)\n"
        "  - kapok: green; tall"
    )
    assert "  - kapok" not in _surroundings_text(research, {})


def test_foods_text_names_the_main_food_first() -> None:
    research = {
        "main_food": {"name": "capybara", "kind": "prey"},
        "other_foods": [{"name": "caiman", "kind": "prey"}],
    }
    assert _foods_text(research) == "capybara (prey), caiman (prey)"


def test_run_adapt_home_reports_error_and_fail_lines(tmp_path: Path) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "adapt_home.py").write_text(
        "import sys\n"
        "print('ERROR: land must be flat, rolling or hilly')\n"
        "print('FAIL the land sits under the water')\n"
        "sys.exit(1)\n"
    )
    base = tmp_path / "base.json"
    base.write_text("{}")
    choices = tmp_path / "choices.json"
    choices.write_text("{}")
    errors = _run_adapt_home(tmp_path, base, choices, tmp_path / "home.json")
    assert errors == [
        "land must be flat, rolling or hilly",
        "the land sits under the water",
    ]


def test_run_adapt_home_passes_a_clean_run(tmp_path: Path) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "adapt_home.py").write_text(
        "import json, sys\njson.dump({'id': 'rainforest'}, open(sys.argv[-1], 'w'))\n"
    )
    base = tmp_path / "base.json"
    base.write_text("{}")
    choices = tmp_path / "choices.json"
    choices.write_text("{}")
    out = tmp_path / "home.json"
    assert _run_adapt_home(tmp_path, base, choices, out) == []
    assert json.loads(out.read_text()) == {"id": "rainforest"}


def test_run_adapt_home_breakage_stops(tmp_path: Path) -> None:
    with pytest.raises(_Stopped, match="adapt_home.py broke"):
        _run_adapt_home(
            tmp_path,
            tmp_path / "base.json",
            tmp_path / "c.json",
            tmp_path / "home.json",
        )


def test_home_prompt_asks_for_words_not_numbers() -> None:
    prompt = (PROMPTS_DIR / "home_choices.md").read_text(encoding="utf-8")
    flat = " ".join(prompt.split())
    assert "You choose words and colours only" in flat
    assert 'water "river", not "lake"' in flat
    assert "No real place names, no people" in flat
    assert "Special places:" in flat
    assert 'No trees ("none") where none grow' in flat
    assert "rowan (small tree)$TREES" in prompt


def test_home_prompt_appends_library_tree_ids(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    base = world / "homes" / "boreal_forest.json"
    brief = {"name": "Ibex", "center": {"lat": 46.5, "lon": 7.5}}
    research = {
        "surroundings": [],
        "main_food": {"name": "grass", "kind": "grass"},
        "other_foods": [],
    }
    prompt = HomeMakerStage()._prompt(
        brief, research, "mountain", base, [], ["oak"], {}
    )
    assert "rowan (small tree), oak. Floating" in " ".join(prompt.split())


_LAB_JOBS = {"ice": "lab-ice-polar-bear", "mountain": "lab-mountain-ibex"}


def _lab_job(habitat: str) -> tuple[Path, Path]:
    """A lab job's inputs plus the real world, or a skip without them."""
    world = ROOT.parent / "animundo-world"
    job = ROOT / "jobs" / _LAB_JOBS[habitat]
    needed = [
        job / "research.json",
        job / "home.json",
        world / "homes" / "examples" / f"{habitat}.json",
        world / "homes" / "boreal_forest.json",
    ]
    if not all(path.is_file() for path in needed):
        pytest.skip("needs the lab jobs and a real world checkout")
    return job, world


def _replay_lab_home(tmp_path: Path, habitat: str, height: int) -> None:
    """The lab's research and tested choices through the real adapt tool."""
    job, world = _lab_job(habitat)
    (tmp_path / "research.json").write_bytes((job / "research.json").read_bytes())
    (tmp_path / "home.json").write_bytes((job / "home.json").read_bytes())
    (tmp_path / "minset.json").write_text(
        json.dumps(
            {
                "make_animal": False,
                "make_home": True,
                "home_habitat": habitat,
                "prey": None,
            }
        )
    )
    example = json.loads((world / "homes" / "examples" / f"{habitat}.json").read_text())
    answer = {
        key: value
        for key, value in example.items()
        if key not in ("id", "tropical", "sources", "base")
    }
    client = ScriptClient([json.dumps(answer)])
    result = HomeMakerStage().run(_ctx(tmp_path, world, client))
    assert result["home_files"] == [f"homes/{habitat}.json"]
    assert len(client.prompts) == 1
    home = json.loads((tmp_path / "homes" / f"{habitat}.json").read_text())
    assert home["terrain"]["height"] == height


def test_newhome_replays_lab_ice(tmp_path: Path) -> None:
    _replay_lab_home(tmp_path, "ice", 32)


def test_newhome_replays_lab_mountain(tmp_path: Path) -> None:
    _replay_lab_home(tmp_path, "mountain", 64)


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
    client = ScriptClient([_choices_answer()])
    _home_stage().run(_ctx(tmp_path, world, client))
    assert (
        "  - water lettuce: pale green; rosette of ribbed leaves" in client.prompts[0]
    )


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
    first = _nameless_answer("lynx")
    client = ScriptClient([first, _animal_answer("lynx"), _edits_answer()])
    _stage().run(_ctx(tmp_path, world, client))
    assert len(client.calls) == 3
    assert client.calls[0] == [
        {"role": "user", "content": client.calls[0][0]["content"]}
    ]
    fix = client.calls[1]
    assert [message["role"] for message in fix] == ["user", "assistant", "user"]
    assert fix[0] == client.calls[0][0]
    assert fix[1]["content"] == first
    assert "'name' is a required property" in fix[2]["content"]
    assert "Change only what these problems name" in fix[2]["content"]


NEWSHAPE_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "newshape"

CRANE_REF = {
    "id": "crane",
    "colors": {"feather": "#999999"},
    "parts": [
        {"name": "body"},
        {"name": "neck"},
        {"name": "bill_upper"},
        {"name": "wing_l"},
    ],
    "bones": [],
}

FOX_PARTS_REF = {
    "id": "fox",
    "colors": {"fur": "#c4622b"},
    "parts": [{"name": "body"}, {"name": "muzzle"}, {"name": "tail_1"}],
    "bones": [],
}


def _adapt_world() -> Path:
    """The sibling checkout, for the real adapt tool only."""
    world = ROOT.parent / "animundo-world"
    if not (world / "tools" / "adapt_model.py").is_file():
        pytest.skip(f"needs a real world checkout at {world}")
    return world


def _run_sizing(
    animal: str, tmp_path: Path
) -> tuple[dict[str, float], dict[str, float], dict[str, Any]]:
    """The recorded loop-final spec through real code sizing."""
    fix = json.loads((NEWSHAPE_FIXTURES / f"{animal}.json").read_text())
    world = _adapt_world()
    ref_path = tmp_path / "ref.json"
    ref_path.write_text(json.dumps(fix["spec"]))
    sizes_path = tmp_path / "sizes.json"
    sizes_path.write_text(
        json.dumps(_sizing_edits(animal, animal, "test", fix["sourced"]))
    )
    out_path = tmp_path / "sized.json"
    adapted = _run_adapt(world, ref_path, sizes_path, out_path)
    assert adapted["ok"], adapted["log"]
    sized = json.loads(out_path.read_text())
    return _measure_spec(sized), fix["sourced"], sized


def test_newshape_sizes_anteater_within_10_percent(tmp_path: Path) -> None:
    """#132.6: the recorded anteater sizes to its sources."""
    found, sourced, _sized = _run_sizing("giant_anteater", tmp_path)
    for key in ("length_m", "shoulder_m", "tail_m"):
        assert abs(found[key] - sourced[key]) / sourced[key] <= 0.10, key


def test_newshape_sizes_kangaroo_within_10_percent(tmp_path: Path) -> None:
    """#134: the tail reaches its exact length; shoulders are skipped
    on back-leg standers, like the tool."""
    found, sourced, sized = _run_sizing("kangaroo", tmp_path)
    for key in ("length_m", "tail_m"):
        assert abs(found[key] - sourced[key]) / sourced[key] <= 0.10, key
    assert found["tail_m"] == pytest.approx(sourced["tail_m"], abs=1e-3)
    assert _on_back_legs(sized)
    assert _size_misses(found, sourced, sized) == {}


def test_part_axis_along_matches_the_tool() -> None:
    """#134: rotated parts resolve their long axis like long_axis."""
    cases = [
        ({"size": [0.2, 0.2, 0.6]}, [0, 0, -1], 2),
        (
            {"size": [0.243, 0.243, 0.397], "rot": [-30, 0, 0]},
            [0, -0.35, -0.94],
            2,
        ),
        ({"size": [0.1, 0.5, 0.1], "rot": [0, 90, 0]}, [1, 0, 0], 2),
        ({"size": [0.4, 0.1, 0.1], "rot": [0, 0, 90]}, [0, 1, 0], 0),
        ({"size": [0.3, 0.3, 0.3], "rot": [8, 0, 0]}, [0, 1, 0], 1),
    ]
    for part, direction, want in cases:
        assert _part_axis_along(part, direction) == want


def test_size_misses_skips_shoulder_on_back_legs() -> None:
    """#134: upright specs skip the shoulder; the rest still flag it."""
    found = {"length_m": 1.0, "shoulder_m": 2.0, "tail_m": 1.0}
    sourced = {"length_m": 1.0, "shoulder_m": 1.0, "tail_m": 1.0}
    upright = {"bones": [{"name": "leg_l", "role": "leg", "tail": [0.0, 0.0, -0.5]}]}
    assert _size_misses(found, sourced, upright) == {}
    assert _size_errors(found, sourced, upright) == []
    flat = {"bones": [{"name": "leg_l", "role": "leg", "tail": [0.0, 0.5, 0.3]}]}
    assert "shoulder_m" in _size_misses(found, sourced, flat)
    assert "shoulder_m" in _size_misses(found, sourced)


def test_sizing_edits_carry_no_factors() -> None:
    """#132.2: code sizing sends sizes only, never factors."""
    edits = _sizing_edits("roo", "Kangaroo", "test", {"length_m": 1.4, "tail_m": 1.0})
    assert edits == {
        "id": "roo",
        "name": "Kangaroo",
        "author": "test",
        "length_m": 1.4,
        "tail_m": 1.0,
    }


def _bird_edits_answer(ref: dict[str, Any], **factors: float) -> str:
    answer: dict[str, Any] = {
        "colors": {name: "#112233" for name in ref["colors"]},
        "new_colors": [],
        "repaint": [],
        "unmakable": [],
    }
    for name in _offered_factors(ref):
        answer[name] = factors.get(name, 1.0)
    return json.dumps(answer)


def test_heron_edits_offer_bill_and_neck() -> None:
    """#132.4: a bird reference offers neck, bill and bill depth."""
    schema = _edits_schema(CRANE_REF)
    assert_strict_subset(schema)
    for name in ("neck", "bill", "bill_depth"):
        assert name in schema["required"]
    assert schema["properties"]["bill_depth"]["maximum"] == 4.0
    edits, errors = _edits_files(_bird_edits_answer(CRANE_REF, bill=1.6), CRANE_REF)
    assert errors == []
    assert edits is not None
    assert edits["bill"] == 1.6


def test_mammal_edits_offer_no_bill() -> None:
    """#132.4: a mammal is never asked for a bill."""
    schema = _edits_schema(FOX_PARTS_REF)
    for name in ("neck", "bill", "bill_depth"):
        assert name not in schema["required"]
    edits, errors = _edits_files(_bird_edits_answer(FOX_PARTS_REF), FOX_PARTS_REF)
    assert errors == []
    assert edits is not None


def test_model_edits_prompt_names_muzzle_and_bill() -> None:
    """#132.4: the prompt tells a mammal's face from a bird's."""
    text = (PROMPTS_DIR / "model_edits.md").read_text(encoding="utf-8")
    assert "`muzzle` is a mammal's face" in text
    assert "a bird's is its `bill`" in text


def _newshape_target() -> dict[str, Any]:
    return {
        "id": "roo",
        "entry": {"id": "roo", "name": "Kangaroo", "latin": "Macropus rufus"},
        "start_from": "fox",
        "prey": False,
        "size_cm": None,
        "shape": "new",
    }


def _newshape_research() -> dict[str, Any]:
    return {
        "size": {"length_cm": 140},
        "looks": {"shoulder_cm": 150, "tail_cm": 100},
    }


def test_make_routes_newshape_targets_to_the_loop(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#132.1: `shape: new` loops a free spec; anything else edits."""
    stage = AnimalMakerStage()
    calls: list[str] = []
    monkeypatch.setattr(
        stage, "_make_model", lambda *a, **k: calls.append("edits") or []
    )
    monkeypatch.setattr(
        stage, "_make_newshape", lambda *a, **k: calls.append("newshape") or []
    )
    monkeypatch.setattr(stage, "_save_final", lambda *a, **k: None)
    (tmp_path / "animals").mkdir()
    (tmp_path / "animals" / "roo.json").write_text("{}")
    (tmp_path / "animals" / "lynx.json").write_text("{}")
    for animal_id in ("roo", "lynx"):
        passed = tmp_path / "tries" / animal_id / "animal" / "1"
        passed.mkdir(parents=True)
        (passed / "errors.json").write_text("[]")
    ctx = SimpleNamespace(job_dir=tmp_path)
    stage._make(ctx, tmp_path, {}, {}, {}, _newshape_target(), set())
    lynx = dict(_newshape_target(), id="lynx", shape=None)
    stage._make(ctx, tmp_path, {}, {}, {}, lynx, set())
    assert calls == ["newshape", "edits"]


def test_newshape_loop_passes_to_sizing(tmp_path: Path, monkeypatch: Any) -> None:
    """#132.1: the loop's spec is sized from the sourced numbers."""
    import builder.stages.maker as maker_module

    world = _world(tmp_path)
    job = tmp_path / "job"
    job.mkdir()
    seen: dict[str, Any] = {}

    class PassingLoop:
        def __init__(self, **kwargs: Any) -> None:
            seen.update(kwargs)

        def run(self, ctx: Any) -> dict[str, Any]:
            assert seen["reference"] == "none"
            assert seen["max_rounds"] == NEWSHAPE_ROUNDS
            assert seen["refs"]["roo"]["start"] == "fox"
            assert seen["refs"]["roo"]["kind"] == "mammal"
            round_dir = ctx.job_dir / "rounds" / "roo" / "1"
            round_dir.mkdir(parents=True)
            (round_dir / "spec.json").write_text("{}")
            (round_dir / "preview.png").write_bytes(b"png")
            reviews = ctx.job_dir / "reviews"
            reviews.mkdir(exist_ok=True)
            (reviews / "roo-1.json").write_text(json.dumps({"issues": []}))
            models = ctx.job_dir / "models"
            models.mkdir(exist_ok=True)
            fox = json.loads((world / "models" / "fox.json").read_text())
            fox["id"] = "roo"
            (models / "roo.json").write_text(json.dumps(fox))
            return {"model_files": ["models/roo.json"]}

    monkeypatch.setattr(maker_module, "ModelsStage", PassingLoop)
    fox_spec = json.loads((world / "models" / "fox.json").read_text())
    found = _measure_spec(fox_spec)
    research = {
        "size": {"length_cm": found["length_m"] * 100},
        "looks": {
            "shoulder_cm": found["shoulder_m"] * 100,
            "tail_cm": found["tail_m"] * 100,
        },
    }

    def fake_adapt(
        world: Path, ref: Path, edits_path: Path, out_path: Path
    ) -> dict[str, Any]:
        edits = json.loads(edits_path.read_text())
        assert set(edits) <= {
            "id",
            "name",
            "author",
            "length_m",
            "shoulder_m",
            "tail_m",
        }
        assert ref == job / "models" / "roo.json"
        sized = dict(fox_spec, id="roo")
        out_path.write_text(json.dumps(sized))
        return {"ok": True, "log": ""}

    stage = AnimalMakerStage(
        adapt=fake_adapt,
        build=lambda sandbox, animal_id: {"ok": True, "log": "BUILT roo"},
    )
    ctx = SimpleNamespace(job_dir=job)
    marks = stage._make_newshape(ctx, world, {}, research, _newshape_target())
    assert marks == []
    assert json.loads((job / "species-plan.json").read_text()) == {
        "species": [{"species": "roo", "decision": "new"}]
    }
    survey = json.loads((job / "survey.json").read_text())
    assert survey["species"][0]["latin"] == "Macropus rufus"
    measurements = json.loads((job / "measurements" / "roo.json").read_text())
    assert measurements["reference"] == "none"
    assert measurements["sourced"]["length_m"] == found["length_m"]
    checks = json.loads((job / "checks" / "newshape-roo.json").read_text())
    assert checks["ok"] is True
    assert checks["results"][0]["problems"] == []


def test_newshape_loop_failure_goes_to_review_not_passed(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#132.3: an unpassed loop keeps its last render, marked."""
    import builder.stages.maker as maker_module
    from builder.stages.models import _Stopped as LoopStopped

    world = _world(tmp_path)
    job = tmp_path / "job"
    job.mkdir()

    class DeadLoop:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def run(self, ctx: Any) -> dict[str, Any]:
            round_dir = ctx.job_dir / "rounds" / "roo" / "1"
            round_dir.mkdir(parents=True)
            (round_dir / "preview.png").write_bytes(b"last-render")
            reviews = ctx.job_dir / "reviews"
            reviews.mkdir(exist_ok=True)
            (reviews / "roo-1.json").write_text(
                json.dumps(
                    {
                        "issues": [
                            {
                                "severity": "blocking",
                                "part": "tail",
                                "problem": "too short",
                                "fix": "lengthen",
                            }
                        ]
                    }
                )
            )
            raise LoopStopped("still failing after 3 rounds")

    monkeypatch.setattr(maker_module, "ModelsStage", DeadLoop)
    stage = AnimalMakerStage()
    ctx = SimpleNamespace(job_dir=job)
    marks = stage._make_newshape(
        ctx, world, {}, _newshape_research(), _newshape_target()
    )
    assert marks == ["tail: too short (fix: lengthen)"]
    assert (job / "models" / "roo.png").read_bytes() == b"last-render"
    assert not (job / "models" / "roo.json").is_file()
    checks = json.loads((job / "checks" / "newshape-roo.json").read_text())
    assert checks["ok"] is False
    assert checks["results"][0]["status"] == "not passed"
    assert checks["results"][0]["problems"] == marks
