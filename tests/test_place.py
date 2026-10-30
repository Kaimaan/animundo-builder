"""Place survey (T97): one call, checked answers, files. No network."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from builder.grid import parse_tile
from builder.habitats import HabitatGrid
from builder.place import (
    SurveyError,
    ask_survey,
    cells_within,
    check_survey,
    gbif_near,
    improve_tile,
    plan_place,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "place"


def _world() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "animundo-world"


def _grids() -> dict[str, HabitatGrid]:
    grid_dir = Path(__file__).resolve().parent.parent / "data" / "habitat-grid"
    return {
        name: HabitatGrid.load(grid_dir / f"{name}.json.gz", name)
        for name in ("ease2_n25", "ease2_s25")
    }


def _animals(world: Path) -> list[dict[str, Any]]:
    from builder.animal_list import find_list, load_list

    return load_list(find_list(world))["animals"]


def _needs_world() -> Path:
    world = _world()
    if not (world / "animal-list.json").is_file():
        pytest.skip("needs an animundo-world checkout")
    return world


def _gbr_answer() -> dict[str, Any]:
    entry = json.loads((FIXTURES / "gbr-survey.jsonl").read_text().splitlines()[0])
    texts = [
        part["text"]
        for item in entry["response"]["output"]
        if item.get("type") == "message" and item.get("phase") != "commentary"
        for part in item["content"]
        if part.get("type") == "output_text"
    ]
    if not texts:
        raise AssertionError("no survey text in the recording")
    return json.loads(texts[-1])


#: The live survey's animals with GBIF records near the reef: the
#: answer names 12, the manta ray and the octopus have none (#145).
_LIVE_GBR = {
    "clownfish",
    "green_sea_turtle",
    "blue_tang",
    "reef_shark",
    "bottlenose_dolphin",
    "humpback_whale",
    "whale_shark",
    "lionfish",
    "butterflyfish",
    "jellyfish",
}


def _reef_counts(monkeypatch: pytest.MonkeyPatch, recorded: set[str]) -> list[str]:
    """Stub GBIF: `recorded` ids have records near the reef, nothing else."""
    import builder.place as place_mod

    seen: list[str] = []

    def fake_fetch(cell: str, animals: list[dict[str, Any]]) -> dict[str, int]:
        seen.append(cell)
        return {a["id"]: 5 for a in animals if a["id"] in recorded}

    monkeypatch.setattr(place_mod, "fetch_raw", fake_fetch)
    monkeypatch.setattr(place_mod, "match_kingdom", lambda latin: "Plantae")
    return seen


def test_recorded_gbr_survey_surveys_the_reef(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T97's done-when: the recorded GBR survey, replayed with no key."""
    from builder.pipeline import BudgetTracker, JobContext
    from builder.records import ReplayClient

    world = _needs_world()
    animals = _animals(world)
    _reef_counts(monkeypatch, _LIVE_GBR)
    records = tmp_path / "records"
    records.mkdir()
    shutil.copy(FIXTURES / "gbr-survey.jsonl", records / "survey.jsonl")
    client = ReplayClient(records)
    client.set_stage("survey")
    ctx = JobContext(
        job_id="gbr",
        job_dir=tmp_path,
        client=client,
        tracker=BudgetTracker(budget_usd=8.0),
        world_path=world,
    )
    from builder.place import list_homes

    data, searches = ask_survey(
        ctx, "Great Barrier Reef", animals, list_homes(world), [], None
    )
    assert searches == 6
    survey = check_survey(
        data, "Great Barrier Reef", _grids(), world, animals, [], 8.0, None
    )
    assert survey["tile"] == "s_530_612"
    assert survey["home_from"] == "examples/reef"
    assert survey["name"] == {"en": "Great Barrier Reef", "fi": "Iso Valliriutta"}
    assert survey["country"] == "Australia"
    assert survey["region_id"] == "au_great_barrier_reef"
    found = [a["id"] for a in survey["animals"]]
    assert found == [
        "clownfish",
        "green_sea_turtle",
        "blue_tang",
        "reef_shark",
        "bottlenose_dolphin",
        "humpback_whale",
        "whale_shark",
        "lionfish",
        "butterflyfish",
        "jellyfish",
    ]
    assert all(a["source"] for a in survey["animals"])
    assert "manta_ray" not in found
    assert "octopus" not in found
    assert [p["name"] for p in survey["plants"]] == [
        "Dugong grass (Halodule uninervis)",
        "Paddle grass (Halophila ovalis)",
        "Green algae (Halimeda)",
        "Brown seaweed (Sargassum)",
    ]
    client.verify_consumed()


def test_survey_blocks_a_plant_outside_plantae_and_fungi(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#146.2: Sargassum is Chromista, so the plan blocks it, not builds it."""
    import shutil

    import builder.place as place_mod
    from builder.pipeline import BudgetTracker, JobContext
    from builder.records import ReplayClient

    world = _needs_world()
    animals = _animals(world)
    _reef_counts(monkeypatch, _LIVE_GBR)
    matched: list[str] = []

    def fake_match(latin: str) -> str | None:
        matched.append(latin)
        return "Chromista" if latin == "Sargassum" else "Plantae"

    monkeypatch.setattr(place_mod, "match_kingdom", fake_match)
    records = tmp_path / "records"
    records.mkdir()
    shutil.copy(FIXTURES / "gbr-survey.jsonl", records / "survey.jsonl")
    client = ReplayClient(records)
    client.set_stage("survey")
    ctx = JobContext(
        job_id="gbr",
        job_dir=tmp_path,
        client=client,
        tracker=BudgetTracker(budget_usd=8.0),
        world_path=world,
    )
    from builder.place import list_homes

    data, _ = ask_survey(
        ctx, "Great Barrier Reef", animals, list_homes(world), [], None
    )
    survey = check_survey(
        data, "Great Barrier Reef", _grids(), world, animals, [], 8.0, None
    )
    assert matched == [
        "Halodule uninervis",
        "Halophila ovalis",
        "Halimeda",
        "Sargassum",
    ]
    assert [p["kingdom"] for p in survey["plants"]] == [
        "Plantae",
        "Plantae",
        "Plantae",
        "Chromista",
    ]
    plan = plan_place(survey, world, "abc123")
    assert {"what": "brown_seaweed_sargassum", "reason": "not_a_plant"} in plan[
        "blocked"
    ]
    assert "brown_seaweed_sargassum" not in [i["what"] for i in plan["items"]]
    client.verify_consumed()


def test_plan_keeps_plants_without_a_matched_kingdom(tmp_path: Path) -> None:
    """#146.2: a plant no Latin name matched stays buildable."""
    import fake_world

    world = fake_world.make_world(tmp_path)
    survey = {
        "place": "Nuuksio",
        "tile": "n_413_478",
        "habitat": "boreal forest",
        "home_from": "boreal_forest",
        "name": {"en": "Nuuksio", "fi": "Nuuksio"},
        "country": "Finland",
        "region_id": "fi_nuuksio",
        "animals": [],
        "plants": [
            {"name": "arctic raspberry", "source": "x", "kingdom": None},
            {
                "name": "Bilberry (Vaccinium myrtillus)",
                "source": "x",
                "kingdom": "Plantae",
            },
        ],
        "food": [],
        "wanted": [],
        "budget_usd": 8.0,
    }
    plan = plan_place(survey, world, "abc123")
    whats = [i["what"] for i in plan["items"]]
    assert "arctic_raspberry" in whats
    assert "bilberry_vaccinium_myrtillus" in whats
    assert plan["blocked"] == []


def test_survey_drops_animals_without_gbif_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _needs_world()
    animals = _animals(world)
    seen = _reef_counts(monkeypatch, {"clownfish"})
    counts = gbif_near("s_530_612", animals)
    assert counts.get("clownfish", 0) > 0
    assert counts.get("manta_ray", 0) == 0
    assert len(seen) == 25
    survey = check_survey(
        _gbr_answer(), "Great Barrier Reef", _grids(), world, animals, [], 8.0, None
    )
    assert [a["id"] for a in survey["animals"]] == ["clownfish"]


def test_survey_keeps_wanted_first_without_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _needs_world()
    animals = _animals(world)
    _reef_counts(monkeypatch, {"clownfish"})
    survey = check_survey(
        _gbr_answer(),
        "Great Barrier Reef",
        _grids(),
        world,
        animals,
        ["manta_ray"],
        8.0,
        None,
    )
    assert [a["id"] for a in survey["animals"]][:2] == ["manta_ray", "clownfish"]
    assert survey["wanted"] == ["manta_ray"]


def test_survey_refuses_a_tile_off_the_grid() -> None:
    world = _needs_world()
    from builder.animal_list import find_list, load_list

    animals = load_list(find_list(world))["animals"]
    data = dict(_gbr_answer(), tile="n_000_000")
    data["tile"] = "s_999_999"
    with pytest.raises(SurveyError, match="no tile id|no baked cell"):
        check_survey(
            data, "Great Barrier Reef", _grids(), world, animals, [], 8.0, None
        )


def test_survey_refuses_a_missing_home() -> None:
    world = _needs_world()
    from builder.animal_list import find_list, load_list

    animals = load_list(find_list(world))["animals"]
    data = dict(_gbr_answer(), home_from="examples/atlantis")
    with pytest.raises(SurveyError, match="no home file"):
        check_survey(
            data, "Great Barrier Reef", _grids(), world, animals, [], 8.0, None
        )


def test_survey_refuses_an_animal_off_the_list() -> None:
    world = _needs_world()
    from builder.animal_list import find_list, load_list

    animals = load_list(find_list(world))["animals"]
    data = json.loads(json.dumps(_gbr_answer()))
    data["animals"].append({"id": "kraken", "source": "sailors"})
    with pytest.raises(SurveyError, match="not on the animal list"):
        check_survey(
            data, "Great Barrier Reef", _grids(), world, animals, [], 8.0, None
        )


def test_survey_refuses_an_animal_without_a_source() -> None:
    world = _needs_world()
    from builder.animal_list import find_list, load_list

    animals = load_list(find_list(world))["animals"]
    data = json.loads(json.dumps(_gbr_answer()))
    data["animals"][0]["source"] = ""
    with pytest.raises(SurveyError, match="has no source"):
        check_survey(
            data, "Great Barrier Reef", _grids(), world, animals, [], 8.0, None
        )


def test_survey_with_improve_keeps_the_places_tile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _needs_world()
    animals = _animals(world)
    _reef_counts(monkeypatch, _LIVE_GBR)
    survey = check_survey(
        _gbr_answer(),
        "Great Barrier Reef",
        _grids(),
        world,
        animals,
        [],
        8.0,
        "s_530_612",
    )
    assert survey["tile"] == "s_530_612"
    with pytest.raises(SurveyError, match="not s_530_611"):
        check_survey(
            _gbr_answer(),
            "Great Barrier Reef",
            _grids(),
            world,
            animals,
            [],
            8.0,
            "s_530_611",
        )


def test_place_survey_replays_from_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T97: `place survey --replay` writes survey.json with no key."""
    from builder.cli import main

    world = _needs_world()
    _reef_counts(monkeypatch, _LIVE_GBR)
    records = tmp_path / "gbr" / "records"
    records.mkdir(parents=True)
    shutil.copy(FIXTURES / "gbr-survey.jsonl", records / "survey.jsonl")
    code = main(
        [
            "place",
            "survey",
            "Great Barrier Reef",
            "--budget",
            "8",
            "--out",
            str(tmp_path / "gbr"),
            "--world",
            str(world),
            "--replay",
        ]
    )
    assert code == 0
    survey = json.loads((tmp_path / "gbr" / "survey.json").read_text())
    assert survey["tile"] == "s_530_612"
    assert survey["home_from"] == "examples/reef"


def _replay_survey(
    tmp_path: Path,
    recording: str,
    place: str,
    want: list[str],
    budget: float,
    improve: str | None,
    world: Path,
    animals: list[dict[str, Any]],
) -> dict[str, Any]:
    from builder.pipeline import BudgetTracker, JobContext
    from builder.place import ask_survey, check_survey, list_homes
    from builder.records import ReplayClient

    records = tmp_path / "records"
    records.mkdir(exist_ok=True)
    shutil.copy(FIXTURES / recording, records / "survey.jsonl")
    client = ReplayClient(records)
    client.set_stage("survey")
    ctx = JobContext(
        job_id="place",
        job_dir=tmp_path,
        client=client,
        tracker=BudgetTracker(budget_usd=budget),
        world_path=world,
    )
    data, _ = ask_survey(ctx, place, animals, list_homes(world), want, improve)
    survey = check_survey(data, place, _grids(), world, animals, want, budget, improve)
    client.verify_consumed()
    return survey


def test_reef_plan_lists_home_fish_shark_and_turtle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T98's done-when: the reef plan from the recorded GBR survey."""
    from builder.place import plan_place

    world = _needs_world()
    animals = _animals(world)
    _reef_counts(monkeypatch, _LIVE_GBR)
    survey = _replay_survey(
        tmp_path,
        "gbr-survey.jsonl",
        "Great Barrier Reef",
        [],
        8.0,
        None,
        world,
        animals,
    )
    plan = plan_place(survey, world, "abc123")
    by_id = {i["id"]: i for i in plan["items"]}
    assert by_id["home_reef"] == {
        "id": "home_reef",
        "kind": "home",
        "what": "reef",
        "from": "examples/reef",
        "estimate_usd": 0.2,
    }
    assert by_id["animal_clownfish"]["from"] == "perch"
    assert by_id["animal_reef_shark"]["from"] == "reef_shark"
    assert by_id["animal_blue_tang"]["from"] == "bream"
    assert by_id["animal_bottlenose_dolphin"]["from"] == "harbour_porpoise"
    assert "animal_green_sea_turtle" in by_id
    turtle = next(a for a in animals if a["id"] == "green_sea_turtle")
    assert turtle["shape"] == "new"
    assert plan["blocked"] == [
        {"what": "humpback_whale", "reason": "not_makeable"},
        {"what": "whale_shark", "reason": "not_makeable"},
        {"what": "jellyfish", "reason": "not_makeable"},
    ]
    assert plan["estimate_usd"] == 1.85
    assert plan["survey"] == "survey.json"
    assert plan["improve"] == []
    assert plan["country"] == "Australia"
    assert plan["region_id"] == "au_great_barrier_reef"
    assert [i["kind"] for i in plan["items"]] == (
        ["home"] + ["animal"] * 7 + ["plant"] * 4
    )
    assert by_id["plant_dugong_grass_halodule_uninervis"]["from"] == "wavy_hair_grass"


def test_improve_plan_puts_the_bear_first_and_stops_at_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T98's done-when: improve fi_nuuksio, bear first, $1 stops it."""
    from builder.place import plan_place

    world = _needs_world()
    animals = _animals(world)
    _reef_counts(
        monkeypatch,
        {
            "brown_bear",
            "lynx",
            "wolverine",
            "arctic_fox",
            "otter",
            "beaver",
            "pine_marten",
            "raccoon_dog",
        },
    )
    survey = _replay_survey(
        tmp_path,
        "nuuksio-survey.jsonl",
        "Nuuksio National Park",
        ["brown_bear"],
        1.0,
        "n_414_479",
        world,
        animals,
    )
    plan = plan_place(survey, world, "abc123")
    assert [i["kind"] for i in plan["items"]] == ["animal"] * 6
    assert plan["items"][0]["id"] == "animal_brown_bear"
    assert plan["estimate_usd"] == 0.9
    assert len(plan["coming"]) >= 1


def test_improve_with_room_plans_the_missing_plant_from_its_kind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#144: the plant without a model, from a plant of its kind."""
    from builder.place import plan_place

    world = _needs_world()
    animals = _animals(world)
    _reef_counts(
        monkeypatch,
        {
            "brown_bear",
            "lynx",
            "wolverine",
            "arctic_fox",
            "otter",
            "beaver",
            "pine_marten",
            "raccoon_dog",
        },
    )
    survey = _replay_survey(
        tmp_path,
        "nuuksio-survey.jsonl",
        "Nuuksio National Park",
        ["brown_bear"],
        8.0,
        "n_414_479",
        world,
        animals,
    )
    plan = plan_place(survey, world, "abc123")
    plants = [i for i in plan["items"] if i["kind"] == "plant"]
    assert plants == [
        {
            "id": "plant_arctic_raspberry",
            "kind": "plant",
            "what": "arctic_raspberry",
            "from": "wild_raspberry",
            "estimate_usd": 0.15,
        }
    ]


def test_plant_from_prefers_words_then_grounds_then_first() -> None:
    """#144.2: a coral from a coral, seabed from seabed, else first."""
    import json

    from builder.plan import closest_plant

    world = _needs_world()
    have = sorted(p.stem for p in (world / "models" / "plants").glob("*.json"))
    assert closest_plant("ghost_coral", [], {}, have) == "brain_coral"
    reef = json.loads(
        (world / "homes" / "examples" / "reef.json").read_text(encoding="utf-8")
    )
    assert (
        closest_plant("neptune_cup", ["seabed"], reef, have)
        == "branching_staghorn_coral"
    )
    assert closest_plant("neptune_cup", [], {}, have) == have[0]


def test_unmakeable_animals_are_blocked_not_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T98: no reference and no new shape blocks; unmakeable blocks."""
    import builder.animal_list as list_mod
    from builder.place import plan_place

    world = _needs_world()
    real_load = list_mod.load_list

    def fake_load(path: Path) -> dict[str, Any]:
        data = real_load(path)
        data["animals"] = [
            *data["animals"],
            {"id": "test_newt", "can_make": True, "gbif_key": 123},
        ]
        return data

    monkeypatch.setattr(list_mod, "load_list", fake_load)
    survey = {
        "place": "Great Barrier Reef",
        "tile": "s_530_612",
        "habitat": "coral reef",
        "home_from": "examples/reef",
        "name": {"en": "Green Island Reef", "fi": "Green Islandin riutta"},
        "country": "Australia",
        "region_id": "au_great_barrier_reef",
        "animals": [
            {"id": "blue_whale", "source": "x"},
            {"id": "test_newt", "source": "x"},
            {"id": "clownfish", "source": "x"},
        ],
        "plants": [],
        "food": [],
        "wanted": [],
        "budget_usd": 8.0,
    }
    plan = plan_place(survey, world, "abc123")
    assert {"what": "blue_whale", "reason": "not_makeable"} in plan["blocked"]
    assert {"what": "test_newt", "reason": "needs_reference"} in plan["blocked"]
    assert "animal_clownfish" in [i["id"] for i in plan["items"]]


def test_place_plan_writes_plan_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T98: `place plan` writes a §7 plan.json next to the survey."""
    from builder.cli import main
    from builder.place import write_survey

    world = _needs_world()
    animals = _animals(world)
    _reef_counts(monkeypatch, _LIVE_GBR)
    survey = _replay_survey(
        tmp_path,
        "gbr-survey.jsonl",
        "Great Barrier Reef",
        [],
        8.0,
        None,
        world,
        animals,
    )
    out = tmp_path / "gbr"
    write_survey(out, survey)
    code = main(["place", "plan", str(out), "--world", str(world)])
    assert code == 0
    plan = json.loads((out / "plan.json").read_text())
    assert plan["tile"] == "s_530_612"
    assert plan["items"][0]["kind"] == "home"


def test_cells_within_two() -> None:
    cells = cells_within("n_413_478")
    assert len(cells) == 25
    assert "n_413_478" in cells
    assert "n_411_476" in cells
    assert "n_415_480" in cells
    edge = cells_within("n_000_000")
    assert len(edge) == 9
    assert "n_000_000" in edge


def test_improve_tile_from_region() -> None:
    world = _needs_world()
    assert improve_tile("s_530_612", world) == "s_530_612"
    tile = improve_tile("fi_nuuksio", world)
    grid, col, row = parse_tile(tile)
    assert grid == "ease2_n25"
    assert 405 <= col <= 420 and 470 <= row <= 485
    with pytest.raises(SurveyError, match="neither a tile nor a region"):
        improve_tile("atlantis", world)


def _grid_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "habitat-grid"


def test_replayed_reef_home_job_reaches_checking_with_a_passing_region(
    tmp_path: Path,
) -> None:
    """T99's done-when: the reef home replays, the region passes, take lays out."""
    from builder.place import check_region_file, replay_place, take_place_job

    world = _needs_world()
    reports, region_path = replay_place(
        FIXTURES / "reef-home", tmp_path / "out", world, _grid_dir()
    )
    assert list(reports) == ["home_reef"]
    assert reports["home_reef"].is_file()
    job_dir = tmp_path / "out" / "replay"
    assert (job_dir / "home_reef" / "homes" / "reef.json").is_file()
    assert (job_dir / "home_reef" / "checks" / "newhome.json").is_file()
    assert region_path == job_dir / "regions" / "au_great_barrier_reef.json"
    region = json.loads(region_path.read_text())
    assert region["id"] == "au_great_barrier_reef"
    assert region["name"] == "Great Barrier Reef"
    assert region["status"] == "draft"
    assert region["animals"]["playable"] == []
    assert check_region_file(region_path, world) == []
    dest = take_place_job(job_dir, tmp_path / "take", world)
    assert (dest / "regions" / "au_great_barrier_reef.json").is_file()
    assert (dest / "homes" / "reef.json").is_file()
    assert (dest / "report.html").is_file()


def test_write_region_derives_groups_and_fish_from_filed_animals(
    tmp_path: Path,
) -> None:
    """T99: playable in survey order, hunters and hunted, fish swim."""
    from builder.place import check_region_file, write_region
    from builder.stages.region import seed_for_area

    world = _needs_world()
    home = json.loads((world / "homes" / "sea.json").read_text())
    survey = {
        "tile": "n_415_479",
        "name": {"en": "Helsinki shore", "fi": "Helsingin ranta"},
        "country": "Finland",
        "region_id": "fi_test_shore",
        "animals": [
            {"id": "perch", "source": "x"},
            {"id": "pike", "source": "x"},
            {"id": "frog", "source": "x"},
            {"id": "clownfish", "source": "x"},
        ],
        "plants": [],
    }
    region = write_region(home, survey, tmp_path, world)
    assert region["animals"] == {
        "playable": ["perch", "pike", "frog"],
        "predators": ["pike"],
        "prey": ["frog"],
    }
    assert region["fish"] == [
        {"kind": "perch", "count": 6},
        {"kind": "pike", "count": 6},
    ]
    assert region["terrain"]["seed"] == seed_for_area("fi_test_shore")
    assert region["status"] == "draft"
    for stale in ("about", "based_on", "ecoregions", "habitat"):
        assert stale not in region
    path = tmp_path / "fi_test_shore.json"
    path.write_text(json.dumps(region, indent=2))
    assert check_region_file(path, world) == []


def test_write_region_leaves_out_a_nameless_lake() -> None:
    """#151: a home lake named "none" never reaches the region."""
    from builder.place import write_region

    world = _needs_world()
    home = json.loads((world / "homes" / "sea.json").read_text())
    home["lakes"] = [{"name": "none", "x": 0.5, "z": 0.36}]
    survey = {
        "tile": "n_415_479",
        "name": {"en": "Helsinki shore", "fi": "Helsingin ranta"},
        "country": "Finland",
        "region_id": "fi_test_shore",
        "animals": [],
        "plants": [],
    }
    region = write_region(home, survey, Path.cwd(), world)
    assert region["lakes"] == []


def test_request_build_local_rejects_a_split_answers_dir(tmp_path: Path) -> None:
    """T99: with --local the answers live under the place folder itself."""
    from builder.cli import main

    world = _needs_world()
    code = main(
        [
            "request",
            "build",
            "--local",
            str(FIXTURES / "reef-home"),
            "--replay",
            str(tmp_path),
            "--out",
            str(tmp_path / "out"),
            "--world",
            str(world),
        ]
    )
    assert code == 1
