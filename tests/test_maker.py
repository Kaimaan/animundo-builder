"""The one-shot animal maker: write, build, check, at most one fix. No key."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from builder.pipeline import BudgetTracker, JobContext
from builder.stages.maker import AnimalMakerStage, HomeMakerStage, _Stopped

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
        json.dumps({"id": "fox", "parts": [{"name": "body"}]})
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

    def chat(self, messages: list[dict], **kwargs: Any) -> SimpleNamespace:
        self.prompts.append(messages[0]["content"])
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


def _answer(animal_id: str, **overrides: Any) -> str:
    animal: dict[str, Any] = {
        "id": animal_id,
        "name": "Good",
        "latin": "X y",
        "diet": ["grass"],
        "text": {"title": "T"},
    }
    animal.update(overrides)
    return json.dumps(
        {"animal": animal, "model": {"id": animal_id, "parts": [{"name": "body"}]}}
    )


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
    client = ScriptClient([_answer("lynx")])
    result = AnimalMakerStage(build=_build_ok).run(_ctx(tmp_path, world, client))
    assert result["animal_files"] == ["animals/lynx.json"]
    assert (tmp_path / "animals" / "lynx.json").is_file()
    assert (tmp_path / "models" / "lynx.json").is_file()
    assert (tmp_path / "models" / "lynx.glb").is_file()
    assert (tmp_path / "models" / "previews" / "lynx.png").is_file()
    assert (tmp_path / "tries" / "lynx" / "1" / "answer.json").is_file()
    assert (tmp_path / "tries" / "lynx" / "1" / "build.log").is_file()
    checks = json.loads((tmp_path / "checks" / "oneshot.json").read_text())
    assert checks["made"] == ["lynx"]
    assert checks["cost_usd"] == result["cost_usd"]
    assert len(client.prompts) == 1


def test_prompt_is_one_shot(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient([_answer("lynx")])
    AnimalMakerStage(build=_build_ok).run(_ctx(tmp_path, world, client))
    prompt = client.prompts[0]
    assert '"species": "lynx"' in prompt
    assert '"gbif_key": 2435240' in prompt
    assert "Vulpes vulpes" in prompt
    assert "only one example: one shot" in prompt
    assert '{"animal": <the animal file>, "model": <the model spec>}' in prompt
    assert "$FOR_CHILDREN" not in prompt
    assert "Young animals" in prompt


def test_fix_round_repairs_check_errors(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient(
        [_answer("lynx", diet=["nectar"]), _answer("lynx", diet=["grass"])]
    )
    result = AnimalMakerStage(build=_build_ok).run(_ctx(tmp_path, world, client))
    assert result["animal_files"] == ["animals/lynx.json"]
    assert len(client.prompts) == 2
    assert "PREVIOUS TRY" in client.prompts[1]
    assert "nectar" in client.prompts[1]
    assert (tmp_path / "tries" / "lynx" / "2" / "answer.json").is_file()


def test_bad_json_gets_the_fix_round(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient(["not json at all", _answer("lynx")])
    result = AnimalMakerStage(build=_build_ok).run(_ctx(tmp_path, world, client))
    assert result["animal_files"] == ["animals/lynx.json"]
    assert "not JSON" in client.prompts[1]


def test_still_failing_stops_for_a_person(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient(
        [_answer("lynx", diet=["nectar"]), _answer("lynx", diet=["nectar"])]
    )
    with pytest.raises(_Stopped, match="a person makes it"):
        AnimalMakerStage(build=_build_ok).run(_ctx(tmp_path, world, client))
    assert len(client.prompts) == 2
    assert not (tmp_path / "checks" / "oneshot.json").is_file()


def test_prey_comes_before_the_animal(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path, minset=_minset_with_prey())
    client = ScriptClient([_answer("capybara"), _answer("lynx")])
    result = AnimalMakerStage(build=_build_ok).run(_ctx(tmp_path, world, client))
    assert result["animal_files"] == ["animals/capybara.json", "animals/lynx.json"]
    assert '"species": "capybara"' in client.prompts[0]
    assert '"species": "lynx"' in client.prompts[1]
    assert (tmp_path / "animals" / "capybara.json").is_file()


def test_prey_matches_by_species_key(tmp_path: Path) -> None:
    world = _world(tmp_path)
    minset = _minset_with_prey()
    minset["make_animal"] = False
    minset["prey"] = {"name": "Water hog", "size_cm": 110, "gbif_key": 2437585}
    _inputs(tmp_path, minset=minset)
    client = ScriptClient([_answer("capybara")])
    result = AnimalMakerStage(build=_build_ok).run(_ctx(tmp_path, world, client))
    assert result["animal_files"] == ["animals/capybara.json"]
    assert '"species": "capybara"' in client.prompts[0]


def test_skips_already_made_prey(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path, minset=_minset_with_prey())
    (tmp_path / "animals").mkdir()
    (tmp_path / "animals" / "capybara.json").write_text("{}")
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "capybara.json").write_text("{}")
    client = ScriptClient([_answer("lynx")])
    result = AnimalMakerStage(build=_build_ok).run(_ctx(tmp_path, world, client))
    assert result["animal_files"] == ["animals/lynx.json"]
    assert len(client.prompts) == 1


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
    result = AnimalMakerStage(build=_build_ok).run(_ctx(tmp_path, world, client))
    assert result["animal_files"] == []
    checks = json.loads((tmp_path / "checks" / "oneshot.json").read_text())
    assert checks == {"cost_usd": 0.0, "made": []}


def test_build_with_no_output_is_blender_not_the_spec(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient([_answer("lynx")])

    def _build_empty(sandbox: Path, spec: str) -> dict[str, Any]:
        return {"ok": False, "summary": "failed", "log": ""}

    with pytest.raises(_Stopped, match="Blender itself did not run"):
        AnimalMakerStage(build=_build_empty).run(_ctx(tmp_path, world, client))


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
        json.dumps({"id": "bilberry", "parts": [{"name": "bush"}]})
    )
    (plants / "wood_anemone.json").write_text(
        json.dumps({"id": "wood_anemone", "parts": [{"name": "leaves"}]})
    )
    (world / "models" / "README.md").write_text("# Models\n\n## Plants\n\nGrow.\n")
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
        "ground_cover": {
            "plants": [
                {"model": "bilberry", "density": 0.2},
                {"model": "water_lettuce", "density": 0.3},
            ]
        },
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
    assert "PREVIOUS TRY" in client.prompts[1]
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
    assert models == ["bilberry", "bilberry"]
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


def test_newhome_skips_a_made_home(tmp_path: Path) -> None:
    world = _home_world(tmp_path)
    _home_inputs(tmp_path)
    (tmp_path / "homes").mkdir()
    (tmp_path / "homes" / "rainforest.json").write_text("{}")
    client = ScriptClient([])
    stage = _home_stage()
    result = stage.run(_ctx(tmp_path, world, client))
    assert result == {"home_files": ["homes/rainforest.json"], "cost_usd": 0.0}
