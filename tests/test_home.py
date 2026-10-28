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
from builder.stages.home import HomeStage, _main_food, _parse_facets
from builder.stages.research import ResearchStage

FOX = {"gbif_key": 5219243, "rank": "SPECIES", "name": "Vulpes vulpes"}
LYNX = {"gbif_key": 2435240, "rank": "SPECIES", "name": "Lynx lynx"}
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
        for kind in (args.food or ["berries", "carrion", "grass"])
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
                "latin": "Vulpes vulpes",
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
                "latin": "Lynx lynx",
                "diet": ["carrion"],
                "tags": ["predator", "mammal"],
                "hunts": ["mammal"],
            }
        )
    )
    (animals / "hare.json").write_text(
        json.dumps(
            {
                "taxon": HARE,
                "latin": "Lepus timidus",
                "diet": ["grass"],
                "tags": ["rodent"],
            }
        )
    )
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
                        "gbif_key": 5219243,
                        "habitats": ["boreal_forest"],
                    },
                    {
                        "id": "lynx",
                        "name": "Eurasian lynx",
                        "latin": "Lynx lynx",
                        "gbif_key": 2435240,
                        "habitats": ["boreal_forest"],
                    },
                    {
                        "id": "mountain_hare",
                        "name": "Mountain hare",
                        "latin": "Lepus timidus",
                        "gbif_key": 2436756,
                        "habitats": ["boreal_forest"],
                    },
                ],
            }
        )
    )
    if tool:
        tools = world / "tools"
        tools.mkdir(parents=True)
        (tools / "homes.py").write_text(STUB_TOOL)
    return world


def _brief(**overrides: Any) -> dict[str, Any]:
    brief: dict[str, Any] = {
        "id": "fox",
        "latin": "Vulpes vulpes",
        "name": "Red fox",
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


def _ctx(tmp_path: Path, client: ScriptClient, world: Path) -> JobContext:
    return JobContext(
        job_id="job-1",
        job_dir=tmp_path,
        client=client,
        tracker=BudgetTracker(budget_usd=8.0),
        world_path=world,
    )


def _research(**overrides: Any) -> dict[str, Any]:
    research: dict[str, Any] = {
        "main_food": {"name": "mountain hare", "kind": "prey", "size_cm": 55},
        "other_foods": [{"name": "bilberries", "kind": "berries"}],
        "surroundings": [
            {"name": "timothy grass", "kind": "plant", "food_kind": "grass"},
            {"name": "mossy rocks", "kind": "ground"},
            {"name": "forest pond", "kind": "water"},
        ],
        "size": {"length_cm": 70},
        "year": {
            "winter": "active",
            "shelter": "x",
            "young": "y",
            "food": "z",
        },
        "sources": ["https://example.org/fox"],
    }
    research.update(overrides)
    return research


def _write_inputs(tmp_path: Path, **brief_over: Any) -> None:
    (tmp_path / "home.json").write_text(json.dumps(_brief(**brief_over)))
    (tmp_path / "research.json").write_text(json.dumps(_research()))


def _facets(*taxa: dict[str, Any]) -> Any:
    """A facet answer recording these taxa, three sightings each."""
    counts = {taxon["gbif_key"]: 3 for taxon in taxa}
    return lambda lat, lon, radius_km: dict(counts)


def test_home_seats_cast_and_writes_plan(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _write_inputs(tmp_path)
    client = ScriptClient([])
    result = HomeStage(facets=_facets(FOX, LYNX, HARE)).run(
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
    assert region["welcome"] == (
        "This is a new world, and not many animals live here yet. "
        "Play as the fox, and add more animals later. "
        "Go and find some bilberries to eat!"
    )
    assert len(region["welcome"]) <= 300
    assert region["sources"] == ["https://example.org/fox", "GBIF.org"]
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


def test_home_uses_intake_place_id_and_wikidata(tmp_path: Path) -> None:
    """Inbox 2026-10-28 #87.5/#88.3: the region id and wikidata come along."""
    world = _world(tmp_path)
    _write_inputs(
        tmp_path,
        new_place_id="fi_saimaa",
        place={
            "wikidata_id": "Q192770",
            "name": "Saimaa",
            "kind": "lake",
            "radius_km": 42.0,
        },
    )
    seen: list[tuple[float, float, float]] = []

    def facets(lat: float, lon: float, radius_km: float) -> dict[int, int]:
        seen.append((lat, lon, radius_km))
        return {}

    result = HomeStage(facets=facets).run(_ctx(tmp_path, ScriptClient([]), world))
    assert result["place_id"] == "fi_saimaa"
    assert seen == [(60.15, 24.95, 42.0)]
    region = json.loads((tmp_path / result["region_file"]).read_text(encoding="utf-8"))
    assert region["id"] == "fi_saimaa"
    assert region["wikidata"] == "Q192770"


def test_home_keeps_only_facet_matched_neighbours(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _write_inputs(tmp_path)
    result = HomeStage(facets=_facets(HARE)).run(
        _ctx(tmp_path, ScriptClient([]), world)
    )
    assert result["neighbours"] == ["hare"]
    region = json.loads((tmp_path / result["region_file"]).read_text(encoding="utf-8"))
    assert region["animals"]["predators"] == []
    assert region["animals"]["prey"] == ["hare"]


def test_home_drops_neighbours_the_home_cannot_feed(tmp_path: Path) -> None:
    world = _world(tmp_path)
    (world / "tools" / "homes.py").write_text(
        STUB_TOOL.replace('["berries", "carrion", "grass"]', '["berries", "carrion"]')
    )
    _write_inputs(tmp_path)
    result = HomeStage(facets=_facets(FOX, LYNX, HARE)).run(
        _ctx(tmp_path, ScriptClient([]), world)
    )
    assert result["neighbours"] == ["lynx"]
    region = json.loads((tmp_path / result["region_file"]).read_text(encoding="utf-8"))
    assert region["animals"]["prey"] == []


def test_home_drops_unsound_taxa(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _write_inputs(tmp_path)
    hare = json.loads((world / "animals" / "hare.json").read_text(encoding="utf-8"))
    hare["taxon"]["name"] = "Lepus something"
    (world / "animals" / "hare.json").write_text(json.dumps(hare))
    result = HomeStage(facets=_facets(FOX, LYNX, HARE)).run(
        _ctx(tmp_path, ScriptClient([]), world)
    )
    assert result["neighbours"] == ["lynx"]


def test_home_caps_neighbours_at_eight_in_one_search(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _write_inputs(tmp_path)
    animals = world / "animals"
    counts: dict[int, int] = {}
    for number in range(10):
        key = 700000 + number
        counts[key] = 10 - number
        (animals / f"n{number}.json").write_text(
            json.dumps(
                {
                    "taxon": {
                        "gbif_key": key,
                        "rank": "SPECIES",
                        "name": f"Rodent {number}",
                    },
                    "latin": f"Rodent {number}",
                    "diet": ["berries"],
                    "tags": ["rodent"],
                    "playable": number == 0,
                }
            )
        )
    seen: list[tuple[float, float, float]] = []

    def facets(lat: float, lon: float, radius_km: float) -> dict[int, int]:
        seen.append((lat, lon, radius_km))
        return dict(counts)

    result = HomeStage(facets=facets).run(_ctx(tmp_path, ScriptClient([]), world))
    assert seen == [(60.15, 24.95, 10)]
    assert len(result["neighbours"]) == 8
    assert set(result["neighbours"]) == {f"n{number}" for number in range(8)}
    region = json.loads((tmp_path / result["region_file"]).read_text(encoding="utf-8"))
    assert region["animals"]["playable"] == ["fox", "n0"]


def test_home_picked_stays_first_in_playable(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _write_inputs(tmp_path)
    lynx = json.loads((world / "animals" / "lynx.json").read_text(encoding="utf-8"))
    lynx["playable"] = True
    (world / "animals" / "lynx.json").write_text(json.dumps(lynx))
    result = HomeStage(facets=_facets(FOX, LYNX, HARE)).run(
        _ctx(tmp_path, ScriptClient([]), world)
    )
    region = json.loads((tmp_path / result["region_file"]).read_text(encoding="utf-8"))
    assert region["animals"]["playable"] == ["fox", "lynx"]


def test_parse_facets_reads_species_keys() -> None:
    payload = {
        "count": 4,
        "facets": [
            {
                "field": "SPECIES_KEY",
                "counts": [
                    {"name": "5219243", "count": 9},
                    {"name": "nope", "count": 2},
                ],
            },
            {"field": "COUNTRY", "counts": [{"name": "FI", "count": 4}]},
        ],
    }
    assert _parse_facets(payload) == {5219243: 9}


def test_home_strips_the_country_from_old_place_names(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _write_inputs(tmp_path, place_name="Fox copse, Finland")
    client = ScriptClient([])
    result = HomeStage(facets=_facets(FOX, LYNX, HARE)).run(
        _ctx(tmp_path, client, world)
    )
    region = json.loads((tmp_path / result["region_file"]).read_text(encoding="utf-8"))
    assert region["name"] == "Fox copse"


def test_home_new_animal_waits_for_one_shot(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _write_inputs(tmp_path, in_library=None, latin="Vulpes zerda")
    with pytest.raises(NotPossibleError, match="one-shot"):
        HomeStage(facets=_facets(FOX, LYNX, HARE)).run(
            _ctx(tmp_path, ScriptClient([]), world)
        )


def test_home_missing_tool_stops(tmp_path: Path) -> None:
    world = _world(tmp_path, tool=False)
    _write_inputs(tmp_path)
    with pytest.raises(Exception, match="no home templates"):
        HomeStage(facets=_facets(FOX, LYNX, HARE)).run(
            _ctx(tmp_path, ScriptClient([]), world)
        )


def _made_vole(tmp_path: Path, diet: list[str]) -> None:
    made = tmp_path / "animals"
    made.mkdir(exist_ok=True)
    (made / "vole.json").write_text(
        json.dumps(
            {
                "taxon": {
                    "gbif_key": 999001,
                    "rank": "SPECIES",
                    "name": "Made vole",
                },
                "latin": "Made vole",
                "diet": diet,
                "tags": ["rodent"],
            }
        )
    )


def test_home_names_missing_foods_from_research(tmp_path: Path) -> None:
    world = _world(tmp_path)
    (world / "tools" / "homes.py").write_text(
        STUB_TOOL.replace('["berries", "carrion", "grass"]', '["berries"]')
    )
    _write_inputs(tmp_path)
    _made_vole(tmp_path, ["grass"])
    result = HomeStage(facets=_facets(FOX, LYNX, HARE)).run(
        _ctx(tmp_path, ScriptClient([]), world)
    )
    assert result["foods_added"] == ["grass"]
    region = json.loads((tmp_path / result["region_file"]).read_text(encoding="utf-8"))
    by_kind = {food["kind"]: food for food in region["food"]}
    assert by_kind["grass"]["names"] == ["timothy grass"]


def test_home_unnamed_kind_stops(tmp_path: Path) -> None:
    world = _world(tmp_path)
    (world / "tools" / "homes.py").write_text(
        STUB_TOOL.replace('["berries", "carrion", "grass"]', '["berries"]')
    )
    _write_inputs(tmp_path)
    _made_vole(tmp_path, ["lichen"])
    with pytest.raises(Exception, match="named no food for lichen"):
        HomeStage(facets=_facets(FOX, LYNX, HARE)).run(
            _ctx(tmp_path, ScriptClient([]), world)
        )


def test_home_unchecked_neighbours_stay_visible(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _write_inputs(tmp_path)

    def down(lat: float, lon: float, radius_km: float) -> dict[int, int]:
        raise RuntimeError("GBIF is down")

    result = HomeStage(facets=down).run(_ctx(tmp_path, ScriptClient([]), world))
    assert result["neighbours"] == []
    assert result["backfill_unchecked"] == ["hare", "lynx"]


def test_home_welcome_names_a_seated_fish_prey(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _write_inputs(tmp_path)
    (world / "animals" / "pike.json").write_text(
        json.dumps({"id": "pike", "tags": ["predator"]})
    )
    (world / "animal-list.json").write_text(
        json.dumps(
            {
                "homes": ["boreal_forest"],
                "not_yet": {},
                "animals": [{"id": "pike", "name": "Northern pike"}],
            }
        )
    )
    research = _research(
        main_food={"name": "northern pike", "kind": "prey", "size_cm": 60},
        other_foods=[],
    )
    (tmp_path / "research.json").write_text(json.dumps(research))
    region = {
        "animals": {"playable": ["fox"], "predators": [], "prey": []},
        "fish": [{"kind": "pike", "count": 5}],
        "food": [{"kind": "berries", "names": ["bilberry"]}],
        "welcome": "old",
    }
    food = _main_food(world, tmp_path, region, research, "fox", None)
    assert food == "northern pike"


def test_home_drops_seasons_in_the_tropics(tmp_path: Path) -> None:
    world = _world(tmp_path, tool=False)
    _write_inputs(tmp_path, center={"lat": -3.45, "lon": -61.95})
    homes = tmp_path / "homes"
    homes.mkdir()
    (homes / "boreal_forest.json").write_text(
        json.dumps(
            {
                "animals": {"playable": [], "predators": [], "prey": []},
                "fish": [],
                "food": [
                    {"kind": "berries", "amount": 10, "names": ["bilberry"]},
                    {"kind": "carrion", "amount": 10, "names": ["remains"]},
                    {"kind": "grass", "amount": 10, "names": ["grass"]},
                ],
                "places": [],
                "sources": [],
                "seasons": {"snow": False},
            }
        )
    )
    run = HomeStage(facets=_facets(FOX, LYNX, HARE)).run
    first = run(_ctx(tmp_path, ScriptClient([]), world))
    region = json.loads((tmp_path / first["region_file"]).read_text(encoding="utf-8"))
    assert "seasons" not in region
    _write_inputs(tmp_path, center={"lat": 60.15, "lon": 24.95})
    second = run(_ctx(tmp_path, ScriptClient([]), world))
    region = json.loads((tmp_path / second["region_file"]).read_text(encoding="utf-8"))
    assert region["seasons"] == {"snow": False}


def test_home_uses_made_template(tmp_path: Path) -> None:
    world = _world(tmp_path, tool=False)
    _write_inputs(tmp_path)
    homes = tmp_path / "homes"
    homes.mkdir()
    (homes / "boreal_forest.json").write_text(
        json.dumps(
            {
                "animals": {"playable": [], "predators": [], "prey": []},
                "fish": [],
                "food": [
                    {"kind": "berries", "amount": 10, "names": ["bilberry"]},
                    {"kind": "carrion", "amount": 10, "names": ["remains"]},
                    {"kind": "grass", "amount": 10, "names": ["grass"]},
                ],
                "places": [],
                "sources": [],
            }
        )
    )
    result = HomeStage(facets=_facets(FOX, LYNX, HARE)).run(
        _ctx(tmp_path, ScriptClient([]), world)
    )
    region = json.loads((tmp_path / result["region_file"]).read_text(encoding="utf-8"))
    assert region["id"] == result["place_id"]
    assert region["name"] == "Fox copse"
    assert region["animals"]["playable"] == ["fox"]


def test_home_drops_the_builders_fields_from_a_made_template(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path, tool=False)
    _write_inputs(tmp_path)
    homes = tmp_path / "homes"
    homes.mkdir()
    (homes / "boreal_forest.json").write_text(
        json.dumps(
            {
                "about": "Home template.",
                "based_on": "boreal_forest",
                "animals": {"playable": [], "predators": [], "prey": []},
                "fish": [],
                "food": [
                    {"kind": "berries", "amount": 10, "names": ["bilberry"]},
                    {"kind": "carrion", "amount": 10, "names": ["remains"]},
                    {"kind": "grass", "amount": 10, "names": ["grass"]},
                ],
                "places": [],
                "sources": [],
            }
        )
    )
    result = HomeStage(facets=_facets(FOX, LYNX, HARE)).run(
        _ctx(tmp_path, ScriptClient([]), world)
    )
    region = json.loads((tmp_path / result["region_file"]).read_text(encoding="utf-8"))
    assert "about" not in region
    assert "based_on" not in region


def test_home_welcome_names_seated_prey(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _write_inputs(tmp_path)
    (world / "animal-list.json").write_text(
        json.dumps(
            {
                "homes": ["boreal_forest"],
                "not_yet": {},
                "animals": [
                    {
                        "id": "hare",
                        "name": "Mountain hare",
                        "in_library": "hare",
                    }
                ],
            }
        )
    )
    result = HomeStage(facets=_facets(FOX, LYNX, HARE)).run(
        _ctx(tmp_path, ScriptClient([]), world)
    )
    region = json.loads((tmp_path / result["region_file"]).read_text(encoding="utf-8"))
    assert region["welcome"].endswith("Go and find a mountain hare to eat!")


def test_home_welcome_names_prey_the_minset_will_make(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _write_inputs(tmp_path)
    (world / "animals" / "hare.json").unlink()
    (world / "animal-list.json").write_text(
        json.dumps(
            {
                "homes": ["boreal_forest"],
                "not_yet": {},
                "animals": [{"id": "hare", "name": "Mountain hare"}],
            }
        )
    )
    run = HomeStage(facets=_facets(FOX, LYNX, HARE)).run
    first = run(_ctx(tmp_path, ScriptClient([]), world))
    region = json.loads((tmp_path / first["region_file"]).read_text(encoding="utf-8"))
    assert "hare" not in region["animals"]["prey"]
    assert region["welcome"].endswith("Go and find some bilberries to eat!")
    (tmp_path / "minset.json").write_text(
        json.dumps(
            {
                "make_animal": False,
                "make_home": False,
                "prey": {"name": "mountain hare", "size_cm": 55},
            }
        )
    )
    second = run(_ctx(tmp_path, ScriptClient([]), world))
    region = json.loads((tmp_path / second["region_file"]).read_text(encoding="utf-8"))
    assert region["welcome"].endswith("Go and find a mountain hare to eat!")


def test_home_seats_prey_by_species_key(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _write_inputs(tmp_path)
    research = _research(
        main_food={
            "name": "blue hare",
            "kind": "prey",
            "size_cm": 55,
            "latin": "Lepus timidus",
        }
    )
    (tmp_path / "research.json").write_text(json.dumps(research))
    (world / "animal-list.json").write_text(
        json.dumps(
            {
                "homes": ["boreal_forest"],
                "not_yet": {},
                "animals": [
                    {
                        "id": "hare",
                        "name": "Mountain hare",
                        "gbif_key": 2436756,
                        "in_library": "hare",
                    }
                ],
            }
        )
    )
    (world / "tools" / "taxon.py").write_text(
        "import json, sys\n"
        'print(json.dumps({"gbif_key": 2436756, "rank": "SPECIES", '
        '"name": sys.argv[-1]}))\n'
    )
    result = HomeStage(facets=_facets(FOX, LYNX, HARE)).run(
        _ctx(tmp_path, ScriptClient([]), world)
    )
    region = json.loads((tmp_path / result["region_file"]).read_text(encoding="utf-8"))
    assert region["welcome"].endswith("Go and find a blue hare to eat!")


def test_home_seats_an_animal_the_minset_will_make(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _write_inputs(tmp_path)
    (tmp_path / "home.json").write_text(
        json.dumps(
            _brief(
                id="lynx",
                latin="Lynx lynx",
                name="Eurasian lynx",
                species_key=2435240,
                in_library=None,
                start_from="fox",
            )
        )
    )
    (tmp_path / "minset.json").write_text(
        json.dumps({"make_animal": True, "make_home": False, "prey": None})
    )
    (world / "animals" / "lynx.json").unlink()
    result = HomeStage(facets=_facets(FOX, LYNX, HARE)).run(
        _ctx(tmp_path, ScriptClient([]), world)
    )
    region = json.loads((tmp_path / result["region_file"]).read_text(encoding="utf-8"))
    assert "lynx" in region["animals"]["playable"]
    plan = json.loads((tmp_path / "species-plan.json").read_text(encoding="utf-8"))
    assert plan["species"][0]["decision"] == "new"
    assert plan["species"][0]["taxon"]["gbif_key"] == 2435240


def test_home_seats_made_animals_unasked(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _write_inputs(tmp_path)
    made = tmp_path / "animals"
    made.mkdir()
    (made / "vole.json").write_text(
        json.dumps(
            {
                "taxon": {
                    "gbif_key": 999001,
                    "rank": "SPECIES",
                    "name": "Made vole",
                },
                "diet": ["grass"],
                "tags": ["rodent"],
            }
        )
    )
    seen: list[tuple[float, float, float]] = []

    def facets(lat: float, lon: float, radius_km: float) -> dict[int, int]:
        seen.append((lat, lon, radius_km))
        return {}

    result = HomeStage(facets=facets).run(_ctx(tmp_path, ScriptClient([]), world))
    assert result["neighbours"] == ["vole"]
    assert seen == [(60.15, 24.95, 10)]
    plan = json.loads((tmp_path / "species-plan.json").read_text(encoding="utf-8"))
    vole = next(e for e in plan["species"] if e["species"] == "vole")
    assert vole["decision"] == "new"


FIXTURES = Path(__file__).resolve().parent / "fixtures"

REPLAY_CASES = [
    ("fox", "Red fox", "Vulpes vulpes", 5219243, "61.5_26.0", ["rodent"]),
    ("lynx", "Eurasian lynx", "Lynx lynx", 2435240, "61.5_26.0", ["mammal"]),
    (
        "anaconda",
        "Green anaconda",
        "Eunectes murinus",
        5225712,
        "-3.1_-60.1",
        ["mammal"],
    ),
]


def _replay_world(
    tmp_path: Path, stem: str, latin: str, key: int, tags: list[str]
) -> Path:
    world = tmp_path / "world"
    animals = world / "animals"
    animals.mkdir(parents=True)
    (animals / f"{stem}.json").write_text(
        json.dumps(
            {
                "taxon": {"gbif_key": key, "rank": "SPECIES", "name": latin},
                "latin": latin,
                "diet": ["berries"],
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
                    "name": "Lepus timidus",
                },
                "latin": "Lepus timidus",
                "diet": ["grass"],
                "tags": tags,
            }
        )
    )
    (animals / "archetypes.json").write_text("{}")
    (world / "animal-list.json").write_text(
        json.dumps(
            {
                "homes": ["boreal_forest"],
                "not_yet": {},
                "animals": [
                    {
                        "id": stem,
                        "name": latin,
                        "latin": latin,
                        "gbif_key": key,
                        "habitats": ["boreal_forest"],
                    }
                ],
            }
        )
    )
    tools = world / "tools"
    tools.mkdir(parents=True)
    (tools / "homes.py").write_text(STUB_TOOL)
    return world


def _empty_template(world: Path) -> None:
    (world / "tools" / "homes.py").write_text(
        STUB_TOOL.replace('["berries", "carrion", "grass"]', "[]")
    )


@pytest.mark.parametrize("stem,name,latin,key,cell,tags", REPLAY_CASES)
def test_home_replays_saved_research(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    stem: str,
    name: str,
    latin: str,
    key: int,
    cell: str,
    tags: list[str],
) -> None:
    """Research replays the fixture, home feeds from it: no model calls."""
    world = _replay_world(tmp_path, stem, latin, key, tags)
    _empty_template(world)
    south, west = (round(float(part) + 0.05, 3) for part in cell.split("_"))
    (tmp_path / "home.json").write_text(
        json.dumps(
            {
                "id": stem,
                "name": name,
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
    shutil.copy(FIXTURES / f"research-{stem}.jsonl", records / "research.jsonl")
    ctx = JobContext(
        job_id="job-1",
        job_dir=tmp_path,
        client=ReplayClient(records, "research"),
        tracker=BudgetTracker(budget_usd=8.0),
        world_path=world,
    )
    ResearchStage().run(ctx)
    assert capsys.readouterr().err == ""
    comp = {"gbif_key": 2436756}
    result = HomeStage(facets=_facets(comp)).run(ctx)
    # The empty template has no grass, so the grass-eating comp is no
    # neighbour; only the picked animal's berries are added (#82.4).
    assert result["neighbours"] == []
    assert result["foods_added"] == ["berries"]
    assert ctx.tracker.spent_usd == 0
    region = json.loads((tmp_path / result["region_file"]).read_text(encoding="utf-8"))
    by_kind = {food["kind"]: food for food in region["food"]}
    assert by_kind["berries"]["names"]
    assert "grass" not in by_kind
    assert "Play as the" in region["welcome"]
    assert (tmp_path / "species-plan.json").is_file()


@pytest.mark.parametrize("stem", ["fox", "lynx", "anaconda"])
def test_research_fixtures_validate(stem: str) -> None:
    line = (FIXTURES / f"research-{stem}.jsonl").read_text(encoding="utf-8")
    entry = json.loads(line)
    assert entry["method"] == "responses_create"
    text = entry["response"]["output"][0]["content"][0]["text"]
    validate("research", json.loads(text))
