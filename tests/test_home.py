"""Home stage: one animal's home from its template. No network."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from builder.pipeline import BudgetTracker, JobContext
from builder.records import ReplayClient
from builder.schemas import validate
from builder.stages.capability import NotPossibleError
from builder.stages.home import HomeStage

FOX = {"gbif_key": 5219243, "rank": "SPECIES", "name": "Vulpes vulpes"}
LYNX = {"gbif_key": 2435239, "rank": "SPECIES", "name": "Lynx lynx"}
HARE = {"gbif_key": 2436756, "rank": "SPECIES", "name": "Lepus timidus"}

STUB_TOOL = """\
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
parser.add_argument("--food", action="append", default=[])
args = parser.parse_args()
region = {
    "id": args.id,
    "name": args.name,
    "animals": {"playable": [], "predators": [], "prey": []},
    "fish": [],
    "food": [
        {"kind": kind, "amount": 10, "names": [kind]}
        for kind in (args.food or ["berries", "meat", "grass"])
    ],
    "places": [],
    "sources": ["https://example.org/template"],
}
open(args.out, "w").write(json.dumps(region))
"""


def _world(tmp_path: Path, *, tool: bool = True) -> Path:
    world = tmp_path / "world"
    animals = world / "animals"
    animals.mkdir(parents=True)
    (animals / "fox.json").write_text(
        json.dumps(
            {
                "taxon": FOX,
                "diet": ["berries"],
                "playable": True,
                "tags": ["mammal"],
                "hunts": ["rodent"],
            }
        )
    )
    (animals / "lynx.json").write_text(
        json.dumps(
            {
                "taxon": LYNX,
                "diet": ["meat"],
                "tags": ["predator", "mammal"],
                "hunts": ["mammal"],
            }
        )
    )
    (animals / "hare.json").write_text(
        json.dumps({"taxon": HARE, "diet": ["grass"], "tags": ["rodent"]})
    )
    (animals / "archetypes.json").write_text("{}")
    if tool:
        tools = world / "tools"
        tools.mkdir(parents=True)
        (tools / "homes.py").write_text(STUB_TOOL)
    return world


def _brief(**overrides: Any) -> dict[str, Any]:
    brief: dict[str, Any] = {
        "latin": "Vulpes vulpes",
        "species_key": 5219243,
        "home": "boreal_forest",
        "place_name": "Fox copse",
        "country_name": "Finland",
        "country": "FI",
        "center": {"lat": 60.15, "lon": 24.95},
        "cell": "60.1_24.9",
        "in_library": "fox",
    }
    brief.update(overrides)
    return brief


class ScriptClient:
    model = "fake-test-model"

    def __init__(self, answers: list[SimpleNamespace]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def responses_create(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        self.prompts.append(str(args[0]))
        return self.answers.pop(0)


def _response(text: str, searches: int = 1) -> SimpleNamespace:
    parts = [SimpleNamespace(type="output_text", text=text, annotations=[])]
    output = [SimpleNamespace(type="message", content=parts)]
    output += [SimpleNamespace(type="web_search_call") for _ in range(searches)]
    return SimpleNamespace(
        model="fake-test-model",
        output=output,
        usage=SimpleNamespace(
            prompt_tokens=100, completion_tokens=50, prompt_tokens_details=None
        ),
    )


def _ctx(tmp_path: Path, client: ScriptClient, world: Path) -> JobContext:
    return JobContext(
        job_id="job-1",
        job_dir=tmp_path,
        client=client,
        tracker=BudgetTracker(budget_usd=8.0),
        world_path=world,
    )


def test_home_seats_cast_and_writes_plan(tmp_path: Path) -> None:
    world = _world(tmp_path)
    (tmp_path / "home.json").write_text(json.dumps(_brief()))
    client = ScriptClient([])
    result = HomeStage(recorded=lambda lat, lon, key: True).run(
        _ctx(tmp_path, client, world)
    )
    assert result["animal"] == "fox"
    assert result["neighbours"] == ["hare", "lynx"]
    assert result["backfill_unchecked"] == []
    assert result["foods_added"] == []
    assert client.prompts == []
    region = json.loads((tmp_path / result["region_file"]).read_text(encoding="utf-8"))
    assert region["animals"]["playable"] == ["fox"]
    assert region["animals"]["predators"] == ["lynx"]
    assert region["animals"]["prey"] == ["hare"]
    plan = json.loads((tmp_path / "species-plan.json").read_text(encoding="utf-8"))
    assert [entry["species"] for entry in plan["species"]] == [
        "fox",
        "hare",
        "lynx",
    ]
    assert all(entry["decision"] == "reuse" for entry in plan["species"])
    kept = json.loads((tmp_path / "home.json").read_text(encoding="utf-8"))
    assert kept["place_id"] == result["place_id"]
    assert kept["stem"] == "fox"


def test_home_new_animal_waits_for_one_shot(tmp_path: Path) -> None:
    world = _world(tmp_path)
    (tmp_path / "home.json").write_text(
        json.dumps(_brief(in_library=None, latin="Vulpes zerda"))
    )
    with pytest.raises(NotPossibleError, match="one-shot"):
        HomeStage(recorded=lambda lat, lon, key: True).run(
            _ctx(tmp_path, ScriptClient([]), world)
        )


def test_home_missing_tool_stops(tmp_path: Path) -> None:
    world = _world(tmp_path, tool=False)
    (tmp_path / "home.json").write_text(json.dumps(_brief()))
    with pytest.raises(Exception, match="no home templates"):
        HomeStage(recorded=lambda lat, lon, key: True).run(
            _ctx(tmp_path, ScriptClient([]), world)
        )


def test_home_researches_missing_food_names(tmp_path: Path) -> None:
    world = _world(tmp_path)
    (world / "tools" / "homes.py").write_text(
        STUB_TOOL.replace('["berries", "meat", "grass"]', '["berries"]')
    )
    (tmp_path / "home.json").write_text(json.dumps(_brief()))
    answer = _response(
        json.dumps(
            {
                "foods": {"meat": ["mountain hare"], "grass": ["timothy grass"]},
                "sources": ["https://example.org/foods"],
            }
        )
    )
    result = HomeStage(recorded=lambda lat, lon, key: True).run(
        _ctx(tmp_path, ScriptClient([answer]), world)
    )
    assert result["foods_added"] == ["grass", "meat"]
    region = json.loads((tmp_path / result["region_file"]).read_text(encoding="utf-8"))
    by_kind = {food["kind"]: food for food in region["food"]}
    assert by_kind["meat"]["names"] == ["mountain hare"]
    assert "https://example.org/foods" in region["sources"]


def test_home_bad_research_stops(tmp_path: Path) -> None:
    world = _world(tmp_path)
    (world / "tools" / "homes.py").write_text(
        STUB_TOOL.replace('["berries", "meat", "grass"]', '["berries"]')
    )
    (tmp_path / "home.json").write_text(json.dumps(_brief()))
    with pytest.raises(Exception, match="unparsable"):
        HomeStage(recorded=lambda lat, lon, key: True).run(
            _ctx(tmp_path, ScriptClient([_response("not json")]), world)
        )


def test_home_unchecked_neighbours_stay_visible(tmp_path: Path) -> None:
    world = _world(tmp_path)
    (tmp_path / "home.json").write_text(json.dumps(_brief()))

    def recorded(lat: float, lon: float, key: int) -> bool:
        if key == LYNX["gbif_key"]:
            raise RuntimeError("GBIF is down")
        return True

    result = HomeStage(recorded=recorded).run(_ctx(tmp_path, ScriptClient([]), world))
    assert result["neighbours"] == ["hare"]
    assert result["backfill_unchecked"] == ["lynx"]


def test_home_research_past_its_round_stops(tmp_path: Path) -> None:
    world = _world(tmp_path)
    (world / "tools" / "homes.py").write_text(
        STUB_TOOL.replace('["berries", "meat", "grass"]', '["berries"]')
    )
    (tmp_path / "home.json").write_text(json.dumps(_brief()))
    answer = _response(
        json.dumps({"foods": {"meat": ["hare"]}, "sources": []}), searches=11
    )
    with pytest.raises(Exception, match="past its round"):
        HomeStage(recorded=lambda lat, lon, key: True).run(
            _ctx(tmp_path, ScriptClient([answer]), world)
        )


FIXTURES = Path(__file__).resolve().parent / "fixtures"

REPLAY_CASES = [
    ("fox", "Vulpes vulpes", 5219243, "61.5_26.0", ["rodent"], "mountain hare"),
    ("lynx", "Lynx lynx", 2435239, "61.5_26.0", ["mammal"], "mountain hare"),
    (
        "anaconda",
        "Eunectes murinus",
        5225712,
        "-3.1_-60.1",
        ["mammal"],
        "capybara",
    ),
]


def _replay_world(tmp_path: Path, stem: str, key: int, tags: list[str]) -> Path:
    world = tmp_path / "world"
    animals = world / "animals"
    animals.mkdir(parents=True)
    (animals / f"{stem}.json").write_text(
        json.dumps(
            {
                "taxon": {"gbif_key": key, "rank": "SPECIES", "name": "X"},
                "diet": ["meat"],
                "playable": True,
                "tags": ["mammal"],
                "hunts": tags,
            }
        )
    )
    (animals / "comp.json").write_text(
        json.dumps(
            {
                "taxon": {
                    "gbif_key": 2436756,
                    "rank": "SPECIES",
                    "name": "Y",
                },
                "diet": ["grass"],
                "tags": tags,
            }
        )
    )
    (animals / "archetypes.json").write_text("{}")
    tools = world / "tools"
    tools.mkdir(parents=True)
    (tools / "homes.py").write_text(
        STUB_TOOL.replace('["berries", "meat", "grass"]', '["berries"]')
    )
    return world


@pytest.mark.parametrize("stem,latin,key,cell,tags,first_meat", REPLAY_CASES)
def test_home_replays_saved_research(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    stem: str,
    latin: str,
    key: int,
    cell: str,
    tags: list[str],
    first_meat: str,
) -> None:
    """The free home run: no model client at all, only the fixture."""
    world = _replay_world(tmp_path, stem, key, tags)
    south, west = (round(float(part) + 0.05, 3) for part in cell.split("_"))
    (tmp_path / "home.json").write_text(
        json.dumps(
            {
                "latin": latin,
                "species_key": key,
                "home": "boreal_forest",
                "place_name": f"{stem} home",
                "country_name": "Testland",
                "country": "TT",
                "center": {"lat": south, "lon": west},
                "cell": cell,
                "in_library": stem,
            }
        )
    )
    records = tmp_path / "records"
    records.mkdir()
    shutil.copy(FIXTURES / f"research-{stem}.jsonl", records / "home.jsonl")
    ctx = JobContext(
        job_id="job-1",
        job_dir=tmp_path,
        client=ReplayClient(records, "home"),
        tracker=BudgetTracker(budget_usd=8.0),
        world_path=world,
    )
    result = HomeStage(recorded=lambda lat, lon, key: True).run(ctx)
    assert result["foods_added"] == ["grass", "meat"]
    assert ctx.tracker.spent_usd == 0
    assert capsys.readouterr().err == ""
    region = json.loads((tmp_path / result["region_file"]).read_text(encoding="utf-8"))
    by_kind = {food["kind"]: food for food in region["food"]}
    assert by_kind["meat"]["names"][0] == first_meat
    assert (tmp_path / "species-plan.json").is_file()


@pytest.mark.parametrize("stem", ["fox", "lynx", "anaconda"])
def test_research_fixtures_validate(stem: str) -> None:
    line = (FIXTURES / f"research-{stem}.jsonl").read_text(encoding="utf-8")
    entry = json.loads(line)
    assert entry["method"] == "responses_create"
    text = entry["response"]["output"][0]["content"][0]["text"]
    validate("home-research", json.loads(text))
