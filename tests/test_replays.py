"""Whole builds from the review jobs' real inputs (#54).

Each test replays a saved job's research recording with no key
and runs its build as far as it goes: the fox to its pack
(nothing made), the lynx through `oneshot` to its pack, the
anaconda through `newhome` and two `oneshot` runs to its pack.
Maker answers are committed stand-ins (see
`tests/fixtures/replays/README.md`); builds and the homes
check are stubs that verify the files land where they should.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from builder.pipeline import BudgetTracker, JobContext
from builder.records import ReplayClient
from builder.stages.capability import NotPossibleError
from builder.stages.home import HomeStage
from builder.stages.maker import AnimalMakerStage, HomeMakerStage
from builder.stages.minimum import MinimumStage
from builder.stages.pack import PackStage
from builder.stages.research import ResearchStage
from builder.stages.words import WordsStage

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "replays"

FOX_KEY = 5219243
HARE_KEY = 2436756

HOMES_TOOL = """\
import argparse, json
parser = argparse.ArgumentParser()
parser.add_argument("command")
parser.add_argument("home")
parser.add_argument("--id", required=True)
parser.add_argument("--name", required=True)
parser.add_argument("--country", required=True)
parser.add_argument("--lat", required=True)
parser.add_argument("--lon", required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()
kinds = ["grass", "bark", "carrion", "clover", "berries", "insects",
         "water_bugs", "browse", "water_plants", "cones", "bottom_bugs", "seeds"]
region = {
    "id": args.id,
    "name": args.name,
    "country": args.country,
    "center": {"lat": float(args.lat), "lon": float(args.lon)},
    "animals": {"playable": [], "predators": [], "prey": []},
    "fish": [],
    "food": [{"kind": kind, "amount": 10, "names": [kind]} for kind in kinds],
    "places": [],
    "sources": ["https://example.org/template"],
}
open(args.out, "w").write(json.dumps(region))
"""

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
problems = []
for path in sorted(glob.glob(os.path.join(draft, "animals", "*.json"))):
    animal_id = os.path.basename(path)[:-5]
    data = json.load(open(path))
    for kind in data.get("diet", []):
        if kind not in foods:
            problems.append(
                f'ERROR: {animal_id}: diet kind "{kind}" not in region food'
            )
    built = os.path.join(draft, "assets", "animals", animal_id + ".glb")
    if not os.path.isfile(built):
        problems.append(f"ERROR: {animal_id}: model file not found: {built}")
report = {
    "ok": not problems,
    "draft_files": [],
    "results": [
        {"check": "locale", "status": "pass", "problems": []},
        {"check": "schema", "status": "pass", "problems": []},
        {"check": "taxa", "status": "pass", "problems": []},
        {"check": "region", "status": "pass", "problems": []},
        {
            "check": "animals",
            "status": "fail" if problems else "pass",
            "problems": problems,
        },
        {"check": "models", "status": "pass", "problems": []},
    ],
}
print(json.dumps(report))
"""


def _world(tmp_path: Path) -> Path:
    world = tmp_path / "world"
    animals = world / "animals"
    animals.mkdir(parents=True)
    (animals / "fox.json").write_text(
        json.dumps(
            {
                "latin": "Vulpes vulpes",
                "taxon": {
                    "gbif_key": FOX_KEY,
                    "rank": "SPECIES",
                    "name": "Vulpes vulpes",
                },
                "diet": ["berries", "insects", "carrion"],
                "tags": ["predator"],
                "hunts": ["small_mammal", "small_bird", "frog"],
                "playable": True,
            }
        )
    )
    (animals / "mountain_hare.json").write_text(
        json.dumps(
            {
                "latin": "Lepus timidus",
                "taxon": {
                    "gbif_key": HARE_KEY,
                    "rank": "SPECIES",
                    "name": "Lepus timidus",
                },
                "diet": ["grass", "clover", "browse", "bark"],
                "tags": ["small_mammal"],
                "playable": True,
            }
        )
    )
    # Prompt examples only: no taxon, so seating never sees them.
    (animals / "vole.json").write_text(json.dumps({"latin": "Myodes glareolus"}))
    (animals / "grass_snake.json").write_text(json.dumps({"latin": "Natrix natrix"}))
    (animals / "archetypes.json").write_text("{}")
    (world / "animal-list.json").write_text(
        json.dumps(
            {
                "homes": ["boreal_forest"],
                "not_yet": {},
                "animals": [
                    {
                        "id": "fox",
                        "name": "Red fox",
                        "latin": "Vulpes vulpes",
                        "gbif_key": FOX_KEY,
                        "habitats": ["boreal_forest"],
                        "in_library": "fox",
                    },
                    {
                        "id": "mountain_hare",
                        "name": "Mountain hare",
                        "latin": "Lepus timidus",
                        "gbif_key": HARE_KEY,
                        "habitats": ["boreal_forest", "tundra", "mountain"],
                        "in_library": "mountain_hare",
                    },
                    {
                        "id": "lynx",
                        "name": "Eurasian lynx",
                        "latin": "Lynx lynx",
                        "gbif_key": 2435240,
                        "start_from": "fox",
                        "habitats": ["boreal_forest", "mountain"],
                        "can_make": True,
                    },
                    {
                        "id": "roe_deer",
                        "name": "Roe deer",
                        "latin": "Capreolus capreolus",
                        "gbif_key": 5220126,
                        "start_from": "moose",
                        "habitats": ["boreal_forest", "grassland"],
                        "can_make": True,
                    },
                    {
                        "id": "anaconda",
                        "name": "Green anaconda",
                        "latin": "Eunectes murinus",
                        "gbif_key": 5225712,
                        "start_from": "grass_snake",
                        "habitats": ["rainforest", "wetland", "river"],
                        "can_make": True,
                    },
                    {
                        "id": "capybara",
                        "name": "Capybara",
                        "latin": "Hydrochoerus hydrochaeris",
                        "gbif_key": 5786666,
                        "start_from": "vole",
                        "habitats": ["rainforest", "wetland", "river"],
                        "can_make": True,
                    },
                ],
            }
        )
    )
    (world / "homes").mkdir(parents=True)
    (world / "homes" / "boreal_forest.json").write_text(
        json.dumps({"id": "boreal_forest", "name": "Northern forest home"})
    )
    (world / "models").mkdir(parents=True)
    for stem in ("fox", "vole", "grass_snake"):
        (world / "models" / f"{stem}.json").write_text(
            json.dumps({"id": stem, "parts": [{"name": "body"}]})
        )
    (world / "models" / "README.md").write_text("# Models\n\n## Plants\n\nGrow.\n")
    plants = world / "models" / "plants"
    plants.mkdir(parents=True)
    (plants / "bilberry.json").write_text(
        json.dumps({"id": "bilberry", "parts": [{"name": "bush"}]})
    )
    (plants / "wood_anemone.json").write_text(
        json.dumps({"id": "wood_anemone", "parts": [{"name": "leaves"}]})
    )
    (world / "schemas").mkdir(parents=True)
    (world / "schemas" / "animal.schema.json").write_text(
        '{"type": "object", "required": ["latin"]}'
    )
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
    (world / "CONTRIBUTING.md").write_text(
        "# World\n\n## The region file\n\nFields here.\n"
        "## Adding animals\n\nName it well.\n"
        "## Adding plants\n\n### The rules\n\nGrow well.\n"
    )
    (world / "ART.md").write_text("# Art\n\nReal first.\n")
    tools = world / "tools"
    tools.mkdir(parents=True)
    (tools / "homes.py").write_text(HOMES_TOOL)
    (tools / "check_draft.py").write_text(DRAFT_STUB)
    (tools / "build_model.sh").write_text("#!/bin/sh\n")
    return world


def _job(tmp_path: Path, animal: str) -> Path:
    job = tmp_path / f"job-{animal}"
    job.mkdir(parents=True)
    src = FIXTURES / animal
    shutil.copy(src / "home.json", job / "home.json")
    shutil.copytree(src / "records", job / "records")
    return job


def _answers(*names: str, animal: str) -> list[str]:
    return [(FIXTURES / animal / name).read_text(encoding="utf-8") for name in names]


class ScriptClient:
    """Committed maker answers, played back in order. No key."""

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


def _recorded(lat: float, lon: float, key: int) -> bool:
    """The stub world's GBIF: the fox and the hare live in Finland."""
    if lat < 0:
        return False
    return key in {FOX_KEY, HARE_KEY}


def _build_animal(sandbox: Path, species_id: str) -> dict[str, Any]:
    (sandbox / "assets" / "animals").mkdir(parents=True, exist_ok=True)
    (sandbox / "assets" / "animals" / f"{species_id}.glb").write_bytes(b"glb-bytes")
    (sandbox / "models" / "previews").mkdir(parents=True, exist_ok=True)
    (sandbox / "models" / "previews" / f"{species_id}.png").write_bytes(b"png-bytes")
    return {
        "ok": True,
        "summary": f"{species_id}: built",
        "log": f"BUILT {species_id}: ok",
    }


def _build_plant(sandbox: Path, plant_id: str) -> dict[str, Any]:
    (sandbox / "assets" / "plants").mkdir(parents=True, exist_ok=True)
    (sandbox / "assets" / "plants" / f"{plant_id}.glb").write_bytes(b"glb-bytes")
    previews = sandbox / "models" / "previews" / "plants"
    previews.mkdir(parents=True, exist_ok=True)
    (previews / f"{plant_id}.png").write_bytes(b"png-bytes")
    return {"ok": True, "summary": f"{plant_id}: built", "log": f"BUILT {plant_id}: ok"}


def _check_homes(sandbox: Path, habitat: str) -> list[str]:
    """The made home parses, is itself, and feeds the fox check."""
    path = sandbox / "homes" / f"{habitat}.json"
    try:
        home = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [f"homes/{habitat}.json is missing or not JSON"]
    errors = []
    if home.get("id") != habitat:
        errors.append(f"home.id is not {habitat!r}")
    kinds = {food.get("kind") for food in home.get("food", [])}
    for kind in ("berries", "insects", "carrion"):
        if kind not in kinds:
            errors.append(f"the home's food has no {kind}")
    return errors


def _check_models(sandbox: Path, plant_ids: list[str]) -> list[str]:
    """Every new plant spec, build and preview is in the sandbox."""
    errors = []
    for plant_id in plant_ids:
        for rel in (
            f"models/plants/{plant_id}.json",
            f"assets/plants/{plant_id}.glb",
            f"models/previews/plants/{plant_id}.png",
        ):
            if not (sandbox / rel).is_file():
                errors.append(f"{rel} is missing from the build")
    return errors


def _match_taxon(world: Path, latin: str) -> dict[str, Any]:
    taxon = {"gbif_key": 2870583, "rank": "SPECIES", "name": latin}
    return {"taxon": taxon, "files": []}


def _research(tmp_path: Path, job: Path, world: Path, tracker: BudgetTracker) -> None:
    ctx = JobContext(
        job_id=job.name,
        job_dir=job,
        client=ReplayClient(job / "records", "research"),
        tracker=tracker,
        world_path=world,
    )
    ResearchStage().run(ctx)


def _region(job: Path) -> dict[str, Any]:
    path = next((job / "regions").glob("*.json"))
    return json.loads(path.read_text(encoding="utf-8"))


def test_fox_replays_to_its_pack(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    world = _world(tmp_path)
    job = _job(tmp_path, "fox")
    tracker = BudgetTracker(budget_usd=8.0)
    _research(tmp_path, job, world, tracker)
    assert capsys.readouterr().err == ""
    ctx = JobContext(
        job_id=job.name, job_dir=job, client=None, tracker=tracker, world_path=world
    )
    assert MinimumStage().run(ctx)["make"] == []
    result = HomeStage(recorded=_recorded).run(ctx)
    assert result["animal"] == "fox"
    assert AnimalMakerStage(build=_build_animal).run(ctx)["animal_files"] == []
    assert WordsStage().run(ctx)["locale_files"] == []
    PackStage().run(ctx)
    manifest = json.loads((job / "pack" / "manifest.json").read_text())
    assert manifest["place_id"] == result["place_id"]
    assert manifest["new_species"] == []
    assert "fox" in manifest["reused_species"]
    region = _region(job)
    assert region["welcome"].endswith("Go and find a mountain hare to eat!")


def test_lynx_replays_through_oneshot_to_its_pack(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    world = _world(tmp_path)
    job = _job(tmp_path, "lynx")
    tracker = BudgetTracker(budget_usd=8.0)
    _research(tmp_path, job, world, tracker)
    assert capsys.readouterr().err == ""
    ctx = JobContext(
        job_id=job.name, job_dir=job, client=None, tracker=tracker, world_path=world
    )
    assert MinimumStage().run(ctx)["make"] == ["animal"]
    result = HomeStage(recorded=_recorded).run(ctx)
    assert result["animal"] == "lynx"
    region = _region(job)
    assert "lynx" in region["animals"]["playable"]
    maker = JobContext(
        job_id=job.name,
        job_dir=job,
        client=ScriptClient(_answers("oneshot.json", animal="lynx")),
        tracker=tracker,
        world_path=world,
    )
    made = AnimalMakerStage(build=_build_animal).run(maker)
    assert made["animal_files"] == ["animals/lynx.json"]
    assert WordsStage().run(ctx)["locale_files"] == []
    PackStage().run(ctx)
    manifest = json.loads((job / "pack" / "manifest.json").read_text())
    assert manifest["new_species"] == ["lynx"]
    assert (job / "pack" / "animals" / "lynx.json").is_file()
    assert (job / "pack" / "models" / "lynx.glb").is_file()
    assert region["welcome"].endswith("Go and find a mountain hare to eat!")


def test_anaconda_replays_through_both_makers_to_its_pack(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    world = _world(tmp_path)
    job = _job(tmp_path, "anaconda")
    tracker = BudgetTracker(budget_usd=8.0)
    _research(tmp_path, job, world, tracker)
    assert capsys.readouterr().err == ""
    ctx = JobContext(
        job_id=job.name, job_dir=job, client=None, tracker=tracker, world_path=world
    )
    with pytest.raises(NotPossibleError, match="home is made next"):
        MinimumStage().run(ctx)
    minset = json.loads((job / "minset.json").read_text())
    assert minset["make_animal"] is True
    assert minset["make_home"] is True
    assert minset["prey"]["name"] == "capybara"
    maker = JobContext(
        job_id=job.name,
        job_dir=job,
        client=ScriptClient(_answers("newhome.json", animal="anaconda")),
        tracker=tracker,
        world_path=world,
    )
    home_result = HomeMakerStage(
        build=_build_plant,
        check_homes=_check_homes,
        check_models=_check_models,
        match=_match_taxon,
    ).run(maker)
    assert home_result["home_files"] == ["homes/rainforest.json"]
    result = HomeStage(recorded=_recorded).run(ctx)
    assert result["animal"] == "anaconda"
    region = _region(job)
    assert "anaconda" in region["animals"]["playable"]
    maker = JobContext(
        job_id=job.name,
        job_dir=job,
        client=ScriptClient(
            _answers(
                "oneshot-capybara.json", "oneshot-anaconda.json", animal="anaconda"
            )
        ),
        tracker=tracker,
        world_path=world,
    )
    made = AnimalMakerStage(build=_build_animal).run(maker)
    assert made["animal_files"] == ["animals/capybara.json", "animals/anaconda.json"]
    assert WordsStage().run(ctx)["locale_files"] == []
    PackStage().run(ctx)
    manifest = json.loads((job / "pack" / "manifest.json").read_text())
    assert manifest["new_species"] == ["anaconda", "capybara"]
    assert (job / "pack" / "animals" / "anaconda.json").is_file()
    assert (job / "pack" / "animals" / "capybara.json").is_file()
