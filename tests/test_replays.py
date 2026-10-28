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
        json.dumps(
            {
                "id": "boreal_forest",
                "name": "Northern forest home",
                "description": "A northern forest.",
                "palette": {"ground_lush": "#5f9445"},
                "trees": {"density": 0.02, "mix": []},
                "lake_life": {"reeds": 0.1, "lilies": 0.0, "pondweed": 0.0},
            }
        )
    )
    (world / "models").mkdir(parents=True)
    colours = {
        "fox": {"fur": "#c4622b", "belly": "#efe3cf"},
        "vole": {"fur": "#7a6a55", "belly": "#cfc0a8"},
        "grass_snake": {"scales": "#4a5d3a", "belly": "#cfc39a"},
    }
    for stem in ("fox", "vole", "grass_snake"):
        (world / "models" / f"{stem}.json").write_text(
            json.dumps(
                {"id": stem, "colors": colours[stem], "parts": [{"name": "body"}]}
            )
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


def _facets(cell: str) -> dict[int, int]:
    """The stub world's GBIF: the fox and the hare live in Finland."""
    if float(cell.split("_")[0]) < 0:
        return {}
    return {FOX_KEY: 3, HARE_KEY: 3}


def _adapt_sizes(
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


def _adapt_home(
    world: Path, base: Path, choices_path: Path, out_path: Path
) -> list[str]:
    """The adapt tool, stubbed: the recorded made home, as the tool left it."""
    out_path.write_text(
        (FIXTURES / "anaconda" / "newhome-made.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return []


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
    result = HomeStage(facets=_facets).run(ctx)
    assert result["animal"] == "fox"
    assert (
        AnimalMakerStage(build=_build_animal, adapt=_adapt_sizes).run(ctx)[
            "animal_files"
        ]
        == []
    )
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
    result = HomeStage(facets=_facets).run(ctx)
    assert result["animal"] == "lynx"
    region = _region(job)
    assert "lynx" in region["animals"]["playable"]
    maker = JobContext(
        job_id=job.name,
        job_dir=job,
        client=ScriptClient(
            _answers("oneshot.json", "oneshot-edits.json", animal="lynx")
        ),
        tracker=tracker,
        world_path=world,
    )
    made = AnimalMakerStage(build=_build_animal, adapt=_adapt_sizes).run(maker)
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
    home_result = HomeMakerStage(adapt=_adapt_home).run(maker)
    assert home_result["home_files"] == ["homes/rainforest.json"]
    tried = json.loads(
        (job / "tries" / "rainforest" / "1" / "choices.json").read_text()
    )
    assert tried["id"] == "rainforest"
    assert tried["tropical"] is True
    assert tried["land"] == "flat" and tried["water"] == "river"
    assert tried["sources"][-1] == "GBIF.org"
    result = HomeStage(facets=_facets).run(ctx)
    assert result["animal"] == "anaconda"
    region = _region(job)
    assert "anaconda" in region["animals"]["playable"]
    maker = JobContext(
        job_id=job.name,
        job_dir=job,
        client=ScriptClient(
            _answers(
                "oneshot-capybara.json",
                "oneshot-capybara-edits.json",
                "oneshot-anaconda.json",
                "oneshot-anaconda-edits.json",
                animal="anaconda",
            )
        ),
        tracker=tracker,
        world_path=world,
    )
    made = AnimalMakerStage(build=_build_animal, adapt=_adapt_sizes).run(maker)
    assert made["animal_files"] == ["animals/capybara.json", "animals/anaconda.json"]
    assert WordsStage().run(ctx)["locale_files"] == []
    PackStage().run(ctx)
    manifest = json.loads((job / "pack" / "manifest.json").read_text())
    assert manifest["new_species"] == ["anaconda", "capybara"]
    assert (job / "pack" / "animals" / "anaconda.json").is_file()
    assert (job / "pack" / "animals" / "capybara.json").is_file()


def _replayed(tmp_path: Path, animal: str) -> tuple[Path, Path, BudgetTracker]:
    """Round one of a replayed job, through the makers. For improves."""
    world = _world(tmp_path)
    job = _job(tmp_path, animal)
    tracker = BudgetTracker(budget_usd=8.0)
    _research(tmp_path, job, world, tracker)
    ctx = JobContext(
        job_id=job.name, job_dir=job, client=None, tracker=tracker, world_path=world
    )
    try:
        MinimumStage().run(ctx)
    except NotPossibleError:
        pass
    if animal == "anaconda":
        maker = JobContext(
            job_id=job.name,
            job_dir=job,
            client=ScriptClient(_answers("newhome.json", animal=animal)),
            tracker=tracker,
            world_path=world,
        )
        HomeMakerStage(adapt=_adapt_home).run(maker)
    HomeStage(facets=_facets).run(ctx)
    if animal == "lynx":
        names = ["oneshot.json", "oneshot-edits.json"]
    elif animal == "anaconda":
        names = [
            "oneshot-capybara.json",
            "oneshot-capybara-edits.json",
            "oneshot-anaconda.json",
            "oneshot-anaconda-edits.json",
        ]
    else:
        names = []
    maker = JobContext(
        job_id=job.name,
        job_dir=job,
        client=ScriptClient(_answers(*names, animal=animal)),
        tracker=tracker,
        world_path=world,
    )
    AnimalMakerStage(build=_build_animal, adapt=_adapt_sizes).run(maker)
    return world, job, tracker


def _improve(
    job: Path,
    choices: list[dict[str, Any]],
    note: str | None = None,
    round_no: int = 2,
) -> None:
    (job / "improve.json").write_text(
        json.dumps({"round": round_no, "choices": choices, "note": note})
    )


def _maker_ctx(
    job: Path, world: Path, tracker: BudgetTracker, client: ScriptClient
) -> JobContext:
    return JobContext(
        job_id=job.name,
        job_dir=job,
        client=client,
        tracker=tracker,
        world_path=world,
    )


def test_improve_looks_wrong_fixes_the_made_animal(tmp_path: Path) -> None:
    world, job, tracker = _replayed(tmp_path, "lynx")
    note = "a longer tail, please"
    _improve(job, [{"choice": "animal_looks_wrong", "species": "lynx"}], note)
    client = ScriptClient(_answers("oneshot-edits.json", animal="lynx"))
    result = AnimalMakerStage(build=_build_animal, adapt=_adapt_sizes).run(
        _maker_ctx(job, world, tracker, client)
    )
    assert result["improved"] == ["lynx"]
    assert result["summary"] == "fixed lynx"
    assert len(client.prompts) == 1
    prompt = client.prompts[0]
    assert "## The previous form" in prompt
    assert "## The grown-up's words" in prompt
    assert note in prompt
    assert "Writing for children" in prompt
    previous = (job / "tries" / "lynx" / "edits" / "1" / "answer.json").read_text()
    assert previous[:200] in prompt
    assert (job / "tries" / "lynx" / "edits-r2" / "1" / "answer.json").is_file()


def test_improve_library_animal_says_so(tmp_path: Path) -> None:
    world, job, tracker = _replayed(tmp_path, "lynx")
    _improve(job, [{"choice": "animal_looks_wrong", "species": "fox"}])
    client = ScriptClient([])
    result = AnimalMakerStage(build=_build_animal, adapt=_adapt_sizes).run(
        _maker_ctx(job, world, tracker, client)
    )
    assert result["improved"] == []
    assert result["summary"] == "Red fox is from the library and can't be changed here"
    assert result["cost_usd"] == 0.0
    assert client.prompts == []


def test_improve_fox_reports_nothing_to_change(tmp_path: Path) -> None:
    world, job, tracker = _replayed(tmp_path, "fox")
    _improve(job, [{"choice": "animal_looks_wrong", "species": "fox"}])
    client = ScriptClient([])
    result = AnimalMakerStage(build=_build_animal, adapt=_adapt_sizes).run(
        _maker_ctx(job, world, tracker, client)
    )
    assert result["improved"] == []
    assert result["summary"] == "nothing to change here"
    assert result["cost_usd"] == 0.0
    assert client.prompts == []


def test_improve_colours_runs_a_choices_fix(tmp_path: Path) -> None:
    world, job, tracker = _replayed(tmp_path, "anaconda")
    note = "bluer water"
    _improve(job, [{"choice": "colours_off"}], note)
    client = ScriptClient(_answers("newhome.json", animal="anaconda"))
    ctx = _maker_ctx(job, world, tracker, client)
    maker = HomeMakerStage(adapt=_adapt_home)
    result = HomeStage(facets=_facets, maker=maker).run(ctx)
    assert result["improved"] == ["home"]
    assert result["summary"] == "fixed the home's colours"
    assert len(client.prompts) == 1
    prompt = client.prompts[0]
    assert "## The previous form" in prompt
    assert note in prompt
    assert "Writing for children" in prompt
    assert (job / "tries" / "rainforest" / "r2-1" / "answer.json").is_file()
    assert (job / "tries" / "rainforest" / "1" / "answer.json").is_file()


def test_improve_library_home_says_so(tmp_path: Path) -> None:
    world, job, tracker = _replayed(tmp_path, "lynx")
    _improve(job, [{"choice": "colours_off"}])
    ctx = _maker_ctx(job, world, tracker, ScriptClient([]))
    result = HomeStage(facets=_facets).run(ctx)
    assert result["improved"] == []
    assert result["summary"] == (
        "the boreal_forest home is from the library and can't be changed here"
    )


def test_improve_too_few_raises_the_cap(tmp_path: Path) -> None:
    world, job, tracker = _replayed(tmp_path, "lynx")
    animals = world / "animals"
    counts = {FOX_KEY: 2, HARE_KEY: 1}
    for number in range(8):
        key = 600000 + number
        counts[key] = 10 - number
        (animals / f"nx{number}.json").write_text(
            json.dumps(
                {
                    "taxon": {
                        "gbif_key": key,
                        "rank": "SPECIES",
                        "name": f"Extra {number}",
                    },
                    "latin": f"Extra {number}",
                    "diet": ["berries"],
                    "tags": ["mammal"],
                    "playable": True,
                }
            )
        )
    ctx = _maker_ctx(job, world, tracker, ScriptClient([]))
    plain = HomeStage(facets=lambda cell: dict(counts)).run(ctx)
    assert len(plain["neighbours"]) == 8
    assert "improved" not in plain
    _improve(job, [{"choice": "too_few_animals"}])
    raised = HomeStage(facets=lambda cell: dict(counts)).run(ctx)
    assert len(raised["neighbours"]) == 10
    assert raised["improved"] == []


def test_improve_other_with_animal_fixes_it(tmp_path: Path) -> None:
    world, job, tracker = _replayed(tmp_path, "lynx")
    _improve(job, [{"choice": "other", "species": "lynx"}], "rounder ears")
    client = ScriptClient(_answers("oneshot-edits.json", animal="lynx"))
    result = AnimalMakerStage(build=_build_animal, adapt=_adapt_sizes).run(
        _maker_ctx(job, world, tracker, client)
    )
    assert result["improved"] == ["lynx"]
    assert "rounder ears" in client.prompts[0]


def test_improve_other_without_animal_reruns_home(tmp_path: Path) -> None:
    world, job, tracker = _replayed(tmp_path, "lynx")
    _improve(job, [{"choice": "other"}], "whatever you think")
    home_result = HomeStage(facets=_facets).run(
        _maker_ctx(job, world, tracker, ScriptClient([]))
    )
    assert home_result["improved"] == []
    assert "summary" not in home_result
    client = ScriptClient([])
    one_result = AnimalMakerStage(build=_build_animal, adapt=_adapt_sizes).run(
        _maker_ctx(job, world, tracker, client)
    )
    assert one_result["improved"] == []
    assert "summary" not in one_result
    assert client.prompts == []


def test_improve_note_cut_to_300_in_prompt(tmp_path: Path) -> None:
    world, job, tracker = _replayed(tmp_path, "lynx")
    note = "y" * 400
    _improve(job, [{"choice": "animal_looks_wrong", "species": "lynx"}], note)
    client = ScriptClient(_answers("oneshot-edits.json", animal="lynx"))
    AnimalMakerStage(build=_build_animal, adapt=_adapt_sizes).run(
        _maker_ctx(job, world, tracker, client)
    )
    prompt = client.prompts[0]
    assert "y" * 300 in prompt
    assert "y" * 400 not in prompt
