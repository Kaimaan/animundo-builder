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
    for item in entry["response"]["output"]:
        if item.get("type") == "message":
            for part in item["content"]:
                if part.get("type") == "output_text":
                    return json.loads(part["text"])
    raise AssertionError("no survey text in the recording")


def _reef_counts(monkeypatch: pytest.MonkeyPatch, recorded: set[str]) -> list[str]:
    """Stub GBIF: `recorded` ids have records near the reef, nothing else."""
    import builder.place as place_mod

    seen: list[str] = []

    def fake_fetch(cell: str, animals: list[dict[str, Any]]) -> dict[str, int]:
        seen.append(cell)
        return {a["id"]: 5 for a in animals if a["id"] in recorded}

    monkeypatch.setattr(place_mod, "fetch_raw", fake_fetch)
    return seen


def test_recorded_gbr_survey_surveys_the_reef(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T97's done-when: the recorded GBR survey, replayed with no key."""
    from builder.pipeline import BudgetTracker, JobContext
    from builder.records import ReplayClient

    world = _needs_world()
    animals = _animals(world)
    _reef_counts(
        monkeypatch, {"clownfish", "green_sea_turtle", "reef_shark", "manta_ray"}
    )
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
    assert searches == 2
    survey = check_survey(
        data, "Great Barrier Reef", _grids(), world, animals, [], 8.0, None
    )
    assert survey["tile"] == "s_530_612"
    assert survey["home_from"] == "examples/reef"
    assert survey["name"] == {"en": "Green Island Reef", "fi": "Green Islandin riutta"}
    first = [a["id"] for a in survey["animals"]][:3]
    assert first == ["clownfish", "green_sea_turtle", "reef_shark"]
    assert all(a["source"] for a in survey["animals"])
    assert "moray_eel" not in [a["id"] for a in survey["animals"]]
    client.verify_consumed()


def test_survey_drops_animals_without_gbif_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _needs_world()
    animals = _animals(world)
    seen = _reef_counts(monkeypatch, {"clownfish"})
    counts = gbif_near("s_530_612", animals)
    assert counts.get("clownfish", 0) > 0
    assert counts.get("moray_eel", 0) == 0
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
        ["moray_eel"],
        8.0,
        None,
    )
    assert [a["id"] for a in survey["animals"]][:2] == ["moray_eel", "clownfish"]
    assert survey["wanted"] == ["moray_eel"]


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
    _reef_counts(
        monkeypatch, {"clownfish", "green_sea_turtle", "reef_shark", "manta_ray"}
    )
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
    _reef_counts(
        monkeypatch, {"clownfish", "green_sea_turtle", "reef_shark", "manta_ray"}
    )
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
