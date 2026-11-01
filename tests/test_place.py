"""Place survey (T97): one call, checked answers, files. No network."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

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


def _habitat_grid() -> HabitatGrid:
    grid_dir = Path(__file__).resolve().parent.parent / "data" / "habitat-grid"
    return HabitatGrid.load(grid_dir / "z10.json.gz")


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
        data, "Great Barrier Reef", _habitat_grid(), world, animals, [], 8.0, None
    )
    assert survey["tile"] == "z10_0927_0560"
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


def test_recorded_gbr_survey_keeps_what_the_world_lacks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#170.1: the reef replay's missing holds unfiled finds only."""
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

    data, _searches = ask_survey(
        ctx, "Great Barrier Reef", animals, list_homes(world), [], None
    )
    survey = check_survey(
        data, "Great Barrier Reef", _habitat_grid(), world, animals, [], 8.0, None
    )
    missing = survey["missing"]
    assert missing
    assert len(missing) <= 12
    for entry in missing:
        assert set(entry) == {"kind", "what", "part"}
    animals_dir = world / "animals"
    for entry in missing:
        if entry["kind"] == "animal":
            assert not (animals_dir / f"{entry['what']}.json").is_file()
    assert survey["wanted"] == []


def test_survey_missing_lists_unfiled_finds(tmp_path: Path) -> None:
    """#170.1: kept finds with no file or model, in wanted shape."""
    import gzip

    world = tmp_path / "world"
    (world / "homes").mkdir(parents=True)
    (world / "homes" / "reef.json").write_text(json.dumps({"habitat": "sea"}))
    (world / "animals").mkdir()
    (world / "animals" / "clownfish.json").write_text(json.dumps({"id": "clownfish"}))
    (world / "models" / "plants").mkdir(parents=True)
    (world / "models" / "plants" / "paddle_grass.json").write_text(
        json.dumps({"id": "paddle_grass", "latin": "Halophila ovalis"})
    )
    grid_path = tmp_path / "z10.json.gz"
    with gzip.open(grid_path, "wt", encoding="utf-8") as handle:
        json.dump(
            {
                "grid": "z10",
                "cells": {"927,560": [150, 1.0, None, 0.0, 1, 0.28, 0.0, 165.6]},
            },
            handle,
        )
    habitat_grid = HabitatGrid.load(grid_path)
    animals = [
        {"id": "clownfish", "gbif_key": 1, "habitats": ["sea"]},
        {"id": "hammerhead", "gbif_key": 2, "habitats": ["sea"]},
    ]
    data = {
        "tile": "z10_0927_0560",
        "habitat": "reef",
        "home_from": "reef",
        "name": {"en": "Test Reef", "fi": "Testiriutta"},
        "country": "Australia",
        "region_id": "xx_test_reef",
        "animals": [
            {"id": "clownfish", "source": "https://example.org/c"},
            {"id": "hammerhead", "source": "https://example.org/h"},
        ],
        "plants": [
            {"name": "Paddle grass (Halophila ovalis)", "source": "https://e.org/p"},
            {"name": "Dugong grass (Halodule uninervis)", "source": "https://e.org/d"},
        ],
        "food": [{"name": "zooplankton", "source": "https://example.org/f"}],
    }
    survey = check_survey(
        data,
        "Test Reef",
        habitat_grid,
        world,
        animals,
        [],
        8.0,
        None,
        recorded={
            "counts": {"clownfish": 5, "hammerhead": 5},
            "kingdoms": {
                "Paddle grass (Halophila ovalis)": "Plantae",
                "Dugong grass (Halodule uninervis)": "Plantae",
            },
        },
    )
    assert survey["missing"] == [
        {"kind": "animal", "what": "hammerhead", "part": "sea"},
        {"kind": "plant", "what": "dugong_grass_halodule_uninervis", "part": "sea"},
    ]


def _file_world(tmp_path: Path) -> Path:
    world = tmp_path / "world"
    places = world / "places"
    places.mkdir(parents=True)
    (places / "xx_test_reef.json").write_text(
        json.dumps(
            {
                "id": "xx_test_reef",
                "name": "Test Reef",
                "center": {"lat": -16.7, "lon": 145.9},
                "tiles": [[927, 560]],
            }
        )
    )
    return world


def _file_survey(tmp_path: Path, **over: Any) -> Path:
    survey_dir = tmp_path / "request"
    survey_dir.mkdir(exist_ok=True)
    survey = {
        "tile": "z10_0927_0560",
        "region_id": "xx_test_reef",
        "missing": [
            {"kind": "animal", "what": "hammerhead", "part": "sea"},
            {
                "kind": "plant",
                "what": "dugong_grass_halodule_uninervis",
                "part": "sea",
            },
        ],
        "surveyed_at": "2026-10-30",
    }
    survey.update(over)
    (survey_dir / "survey.json").write_text(json.dumps(survey))
    return survey_dir


def test_file_places_writes_missing_under_places(tmp_path: Path) -> None:
    """#170.2: the wanted list lands under places; the tile stays put."""
    from fake_store import FakeStore

    from builder.grid import group_path
    from builder.place import file_places

    world = _file_world(tmp_path)
    survey_dir = _file_survey(tmp_path)
    store = FakeStore()
    blob = group_path("z10_0927_0560")
    store.blobs[blob] = json.dumps(
        {
            "format": 1,
            "grid": "z10",
            "group": [115, 70],
            "version": 3,
            "tiles": {
                "z10_0927_0560": {
                    "id": "z10_0927_0560",
                    "kind": "place",
                    "place": "xx_test_reef",
                    "version": 3,
                }
            },
            "blocked": {},
        }
    ).encode("utf-8")
    store.blobs["tiles/v2/index.json"] = json.dumps(
        {
            "format": 1,
            "made_at": "",
            "world": "",
            "groups": {},
            "counts": {"ready": 1, "blocked": {}},
        }
    ).encode("utf-8")
    paths = file_places(store, world, survey_dir, "abc123")
    assert paths == [blob]
    data = json.loads(store.blobs[blob].decode("utf-8"))
    assert data["version"] == 4
    assert data["tiles"]["z10_0927_0560"]["kind"] == "place"
    assert data["places"] == {
        "z10_0927_0560": {
            "region": "xx_test_reef",
            "wanted": [
                {"kind": "animal", "what": "hammerhead", "part": "sea"},
                {
                    "kind": "plant",
                    "what": "dugong_grass_halodule_uninervis",
                    "part": "sea",
                },
            ],
            "surveyed_at": "2026-10-30",
        }
    }
    index = json.loads(store.blobs["tiles/v2/index.json"].decode("utf-8"))
    assert index["counts"] == {"ready": 1, "blocked": {}}
    assert index["groups"] == {"z10/115_070": 4}


def test_file_places_falls_back_to_the_centre_tile(tmp_path: Path) -> None:
    """#170.2: a place without tiles files under its centre."""
    from fake_store import FakeStore

    from builder.grid import tile_at
    from builder.place import file_places, place_tiles

    world = tmp_path / "world"
    places = world / "places"
    places.mkdir(parents=True)
    (places / "xx_test_reef.json").write_text(
        json.dumps(
            {
                "id": "xx_test_reef",
                "center": {"lat": -16.7, "lon": 145.9},
            }
        )
    )
    survey_dir = _file_survey(tmp_path)
    store = FakeStore()
    assert place_tiles(world, "xx_test_reef", "z10_0927_0560") == [
        tile_at(-16.7, 145.9)
    ]
    assert place_tiles(world, "xx_missing", "z10_0927_0560") == ["z10_0927_0560"]
    paths = file_places(store, world, survey_dir, "abc123")
    assert len(paths) == 1
    data = json.loads(store.blobs[paths[0]].decode("utf-8"))
    assert data["version"] == 1
    assert list(data["places"]) == [tile_at(-16.7, 145.9)]


def test_file_places_dates_an_old_survey_from_its_plan(tmp_path: Path) -> None:
    """#170.3: without surveyed_at, the plan's date, else today."""
    from datetime import UTC, datetime

    from fake_store import FakeStore

    from builder.place import file_places

    world = _file_world(tmp_path)
    survey_dir = _file_survey(tmp_path)
    survey = json.loads((survey_dir / "survey.json").read_text())
    del survey["surveyed_at"]
    (survey_dir / "survey.json").write_text(json.dumps(survey))
    (survey_dir / "plan.json").write_text(
        json.dumps({"planned_at": "2026-10-30T12:00:00+00:00"})
    )
    store = FakeStore()
    (path,) = file_places(store, world, survey_dir, "abc123")
    data = json.loads(store.blobs[path].decode("utf-8"))
    assert data["places"]["z10_0927_0560"]["surveyed_at"] == "2026-10-30"
    (survey_dir / "plan.json").unlink()
    store = FakeStore()
    (path,) = file_places(store, world, survey_dir, "abc123")
    data = json.loads(store.blobs[path].decode("utf-8"))
    assert data["places"]["z10_0927_0560"]["surveyed_at"] == (
        datetime.now(UTC).date().isoformat()
    )


def test_improve_survey_writes_missing_too(tmp_path: Path) -> None:
    """#170.3: an improve survey keeps finds the world still lacks."""
    import gzip

    world = tmp_path / "world"
    (world / "homes").mkdir(parents=True)
    (world / "homes" / "reef.json").write_text(json.dumps({"habitat": "sea"}))
    (world / "animals").mkdir()
    (world / "models" / "plants").mkdir(parents=True)
    (world / "models" / "plants" / "paddle_grass.json").write_text(
        json.dumps({"id": "paddle_grass"})
    )
    grid_path = tmp_path / "z10.json.gz"
    with gzip.open(grid_path, "wt", encoding="utf-8") as handle:
        json.dump(
            {
                "grid": "z10",
                "cells": {"927,560": [150, 1.0, None, 0.0, 1, 0.28, 0.0, 165.6]},
            },
            handle,
        )
    habitat_grid = HabitatGrid.load(grid_path)
    animals = [{"id": "hammerhead", "gbif_key": 2, "habitats": ["sea"]}]
    data = {
        "tile": "z10_0927_0560",
        "habitat": "reef",
        "home_from": "reef",
        "name": {"en": "Test Reef", "fi": "Testiriutta"},
        "country": "Australia",
        "region_id": "xx_test_reef",
        "animals": [{"id": "hammerhead", "source": "https://example.org/h"}],
        "plants": [
            {"name": "Paddle grass (Halophila ovalis)", "source": "https://e.org/p"},
            {"name": "Dugong grass (Halodule uninervis)", "source": "https://e.org/d"},
        ],
        "food": [{"name": "zooplankton", "source": "https://example.org/f"}],
    }
    survey = check_survey(
        data,
        "Test Reef",
        habitat_grid,
        world,
        animals,
        [],
        8.0,
        "z10_0927_0560",
        recorded={
            "counts": {"hammerhead": 5},
            "kingdoms": {
                "Paddle grass (Halophila ovalis)": "Plantae",
                "Dugong grass (Halodule uninervis)": "Plantae",
            },
        },
    )
    assert survey["missing"] == [
        {"kind": "animal", "what": "hammerhead", "part": "sea"},
        {"kind": "plant", "what": "dugong_grass_halodule_uninervis", "part": "sea"},
    ]


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
        data, "Great Barrier Reef", _habitat_grid(), world, animals, [], 8.0, None
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
        "tile": "z10_0581_0294",
        "habitat": "boreal forest",
        "home_from": "boreal_forest",
        "name": {"en": "Nuuksio", "fi": "Nuuksio"},
        "country": "Finland",
        "region_id": "fi_nuuksio",
        "animals": [],
        "plants": [
            {"name": "arctic raspberry", "source": "x", "kingdom": None},
            {
                "name": "Cloudberry (Rubus chamaemorus)",
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
    # Bilberry would skip now: the fake world files it (#167.1).
    assert "cloudberry_rubus_chamaemorus" in whats
    assert plan["blocked"] == []


def test_survey_drops_animals_without_gbif_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _needs_world()
    animals = _animals(world)
    seen = _reef_counts(monkeypatch, {"clownfish"})
    counts = gbif_near("z10_0927_0560", animals)
    assert counts.get("clownfish", 0) > 0
    assert counts.get("manta_ray", 0) == 0
    assert len(seen) == 25
    survey = check_survey(
        _gbr_answer(),
        "Great Barrier Reef",
        _habitat_grid(),
        world,
        animals,
        [],
        8.0,
        None,
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
        _habitat_grid(),
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
    data = dict(_gbr_answer(), tile="z10_0000_0000")
    data["tile"] = "s_999_999"
    with pytest.raises(SurveyError, match="no tile id|no baked cell"):
        check_survey(
            data, "Great Barrier Reef", _habitat_grid(), world, animals, [], 8.0, None
        )


def test_survey_refuses_a_missing_home() -> None:
    world = _needs_world()
    from builder.animal_list import find_list, load_list

    animals = load_list(find_list(world))["animals"]
    data = dict(_gbr_answer(), home_from="examples/atlantis")
    with pytest.raises(SurveyError, match="no home file"):
        check_survey(
            data, "Great Barrier Reef", _habitat_grid(), world, animals, [], 8.0, None
        )


def test_survey_refuses_an_animal_off_the_list() -> None:
    world = _needs_world()
    from builder.animal_list import find_list, load_list

    animals = load_list(find_list(world))["animals"]
    data = json.loads(json.dumps(_gbr_answer()))
    data["animals"].append({"id": "kraken", "source": "sailors"})
    with pytest.raises(SurveyError, match="not on the animal list"):
        check_survey(
            data, "Great Barrier Reef", _habitat_grid(), world, animals, [], 8.0, None
        )


def test_survey_refuses_an_animal_without_a_source() -> None:
    world = _needs_world()
    from builder.animal_list import find_list, load_list

    animals = load_list(find_list(world))["animals"]
    data = json.loads(json.dumps(_gbr_answer()))
    data["animals"][0]["source"] = ""
    with pytest.raises(SurveyError, match="has no source"):
        check_survey(
            data, "Great Barrier Reef", _habitat_grid(), world, animals, [], 8.0, None
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
        _habitat_grid(),
        world,
        animals,
        [],
        8.0,
        "z10_0927_0560",
    )
    assert survey["tile"] == "z10_0927_0560"
    with pytest.raises(SurveyError, match="not z10_0926_0560"):
        check_survey(
            _gbr_answer(),
            "Great Barrier Reef",
            _habitat_grid(),
            world,
            animals,
            [],
            8.0,
            "z10_0926_0560",
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
    assert survey["tile"] == "z10_0927_0560"
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
    survey = check_survey(
        data, place, _habitat_grid(), world, animals, want, budget, improve
    )
    client.verify_consumed()
    return survey


def test_reef_plan_lists_home_whale_and_plants(
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
    # #167.2: the reef exists, so no home item and no terrain change.
    assert "home_reef" not in by_id
    # The live reef filed its fish and its whale, and #167.1
    # skips its own plants: only the sargassum needs making.
    assert "animal_humpback_whale" not in by_id
    whale = next(a for a in animals if a["id"] == "humpback_whale")
    assert whale["shape"] == "new"
    assert plan["blocked"] == [
        {"what": "whale_shark", "reason": "not_makeable"},
        {"what": "jellyfish", "reason": "not_makeable"},
    ]
    assert plan["estimate_usd"] == 0.13
    assert plan["survey"] == "survey.json"
    assert plan["improve"] == []
    assert plan["country"] == "Australia"
    assert plan["region_id"] == "au_great_barrier_reef"
    assert [i["kind"] for i in plan["items"]] == ["plant"]
    assert "plant_brown_seaweed_sargassum" in by_id


def test_new_place_plan_still_lists_a_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#167.2: only existing places skip the home item."""
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
    survey["region_id"] = "au_reef_new"
    plan = plan_place(survey, world, "abc123")
    by_id = {i["id"]: i for i in plan["items"]}
    assert by_id["home_reef"]["kind"] == "home"
    assert plan["looked_for"] == []


def test_improve_plan_puts_the_bear_first_and_stops_at_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T98's done-when: improve fi_nuuksio, bear first, $2.60 stops it."""
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
        2.60,
        "z10_0581_0295",
        world,
        animals,
    )
    plan = plan_place(survey, world, "abc123")
    assert [i["kind"] for i in plan["items"]] == ["animal"] * 6
    assert plan["items"][0]["id"] == "animal_brown_bear"
    assert plan["estimate_usd"] == 2.52
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
        "z10_0581_0295",
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
            "estimate_usd": 0.13,
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


def test_closest_plant_matches_reef_and_seabed_grounds() -> None:
    """#184.1: reef rock counts as seabed when matching grounds."""
    from builder.plan import closest_plant

    home = {
        "plants": [
            {"model": "dugong_grass", "on": ["seabed"]},
            {"model": "table_coral", "on": ["reef"]},
        ]
    }
    have = ["beach_naupaka", "dugong_grass", "table_coral"]
    assert closest_plant("neptune_cup", ["reef"], home, have) == "table_coral"
    assert closest_plant("neptune_cup", ["seabed"], home, have) == "dugong_grass"
    assert closest_plant("neptune_cup", ["sand"], home, have) == have[0]
    grass_only = {"plants": [{"model": "dugong_grass", "on": ["seabed"]}]}
    assert closest_plant("neptune_cup", ["reef"], grass_only, have) == "dugong_grass"


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
        "tile": "z10_0927_0560",
        "habitat": "coral reef",
        "home_from": "examples/reef",
        "name": {"en": "Green Island Reef", "fi": "Green Islandin riutta"},
        "country": "Australia",
        "region_id": "au_great_barrier_reef",
        "animals": [
            {"id": "blue_whale", "source": "x"},
            {"id": "test_newt", "source": "x"},
            {"id": "manta_ray", "source": "x"},
        ],
        "plants": [],
        "food": [],
        "wanted": [],
        "budget_usd": 8.0,
    }
    plan = plan_place(survey, world, "abc123")
    assert {"what": "blue_whale", "reason": "not_makeable"} in plan["blocked"]
    assert {"what": "test_newt", "reason": "needs_reference"} in plan["blocked"]
    assert "animal_manta_ray" in [i["id"] for i in plan["items"]]


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
    assert plan["tile"] == "z10_0927_0560"
    # #167.2: the reef exists, so the first item is a plant, not a home.
    assert plan["items"][0]["id"] == "plant_brown_seaweed_sargassum"


def test_cells_within_two() -> None:
    cells = cells_within("z10_0581_0294")
    assert len(cells) == 25
    assert "z10_0581_0294" in cells
    assert "z10_0579_0292" in cells
    assert "z10_0583_0296" in cells
    edge = cells_within("z10_0000_0000")
    assert len(edge) == 15
    assert "z10_0000_0000" in edge
    assert "z10_1023_0000" in edge
    assert "z10_0000_0002" in edge


def test_improve_tile_from_place() -> None:
    world = _needs_world()
    assert improve_tile("z10_0927_0560", world) == "z10_0927_0560"
    tile = improve_tile("fi_nuuksio", world)
    assert tile in {
        "z10_0581_0295",
        "z10_0582_0295",
        "z10_0581_0296",
        "z10_0582_0296",
    }
    with pytest.raises(SurveyError, match="neither a tile nor a place"):
        improve_tile("atlantis", world)


def test_missing_plants_match_names_and_latin(tmp_path: Path) -> None:
    """#167.1: the world's own plants are never planned again."""
    from builder.place import _missing_plants, _plant_latins

    world = tmp_path / "world"
    plants_dir = world / "models" / "plants"
    plants_dir.mkdir(parents=True)
    (plants_dir / "dugong_grass.json").write_text(
        json.dumps({"id": "dugong_grass", "latin": "Halodule uninervis"})
    )
    (plants_dir / "halimeda.json").write_text(
        json.dumps({"id": "halimeda", "latin": "Halimeda"})
    )
    survey = {
        "plants": [
            {"name": "Dugong grass (Halodule uninervis)"},
            {"name": "Green algae (Halimeda)"},
            {"name": "Brown seaweed (Sargassum)"},
        ]
    }
    missing, blocked = _missing_plants(
        {},
        survey,
        ["dugong_grass", "halimeda"],
        latin_have=_plant_latins(world),
    )
    assert [slug for slug, _ in missing] == ["brown_seaweed_sargassum"]
    assert blocked == []


def test_reef_improve_replay_plans_only_what_it_lacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#167.1 + #167.2: no own plants, no home on the reef improve replay."""
    world = _needs_world()
    animals = _animals(world)
    _reef_counts(monkeypatch, _LIVE_GBR)
    data = _gbr_answer()
    survey = check_survey(
        data,
        "Great Barrier Reef",
        _habitat_grid(),
        world,
        animals,
        [],
        2.0,
        "z10_0927_0560",
    )
    plan = plan_place(survey, world, "test")
    ids = [item["id"] for item in plan["items"]]
    assert "plant_dugong_grass_halodule_uninervis" not in ids
    assert "plant_paddle_grass_halophila_ovalis" not in ids
    assert "plant_green_algae_halimeda" not in ids
    assert not [i for i in ids if i.startswith("home_")]
    assert "plant_brown_seaweed_sargassum" in ids
    assert set(plan["looked_for"]) >= {
        "parrotfish",
        "moray_eel",
        "hammerhead",
        "manta_ray",
    }
    assert "clownfish" not in plan["looked_for"]
    assert "lionfish" not in plan["looked_for"]
    assert "lion" not in plan["looked_for"]


def test_habitat_first_asks_matching_animals_first() -> None:
    """#167.4: the improve prompt leads with the place's habitats."""
    from builder.place import habitat_first

    animals = [
        {"id": "lion", "habitats": ["savanna"]},
        {"id": "parrotfish", "habitats": ["reef"]},
        {"id": "camel", "habitats": ["desert"]},
        {"id": "moray_eel", "habitats": ["reef", "sea"]},
    ]
    ordered = habitat_first(animals, {"reef", "sea"})
    assert [a["id"] for a in ordered] == [
        "parrotfish",
        "moray_eel",
        "lion",
        "camel",
    ]


def test_improve_habitats_from_place_and_tile() -> None:
    """#167.4: residents' habitats for a place, the cell's for a tile."""
    from builder.place import improve_habitats

    world = _needs_world()
    animals = _animals(world)
    habitats = improve_habitats(
        animals, world, "au_great_barrier_reef", _habitat_grid()
    )
    assert {"reef", "sea"} <= habitats
    assert improve_habitats(animals, world, "z10_0927_0560", _habitat_grid()) == {
        "open_ocean"
    }
    assert improve_habitats(animals, world, "xx_nowhere", _habitat_grid()) == set()


def test_place_residents_read_the_cast(tmp_path: Path) -> None:
    """#167.4: a place carries its species lists, nothing else."""
    from builder.place import place_residents

    world = tmp_path / "world"
    (world / "places").mkdir(parents=True)
    (world / "places" / "xx_place.json").write_text(
        json.dumps(
            {
                "id": "xx_place",
                "species": {
                    "playable": ["fox", "perch"],
                    "prey": ["perch"],
                    "predators": ["fox"],
                },
            }
        )
    )
    assert place_residents(world, "xx_place") == {"fox", "perch"}
    assert place_residents(world, "xx_nowhere") == set()


def test_recorded_gbif_counts_keep_replay_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#167.3: a replay replays counts and kingdoms, never calling GBIF."""
    import builder.place as place_mod

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("GBIF must not be called on a replay")

    monkeypatch.setattr(place_mod, "fetch_raw", boom)
    monkeypatch.setattr(place_mod, "match_kingdom", boom)
    world = _needs_world()
    animals = _animals(world)
    data = _gbr_answer()
    recorded = {
        "counts": {animal_id: 5 for animal_id in _LIVE_GBR},
        "kingdoms": {
            "Dugong grass (Halodule uninervis)": "Plantae",
            "Paddle grass (Halophila ovalis)": "Plantae",
            "Green algae (Halimeda)": "Plantae",
            "Brown seaweed (Sargassum)": "Plantae",
        },
    }
    survey = check_survey(
        data,
        "Great Barrier Reef",
        _habitat_grid(),
        world,
        animals,
        [],
        2.0,
        "z10_0927_0560",
        recorded,
    )
    assert sorted(a["id"] for a in survey["animals"]) == sorted(_LIVE_GBR)
    assert survey["gbif_counts"] == recorded["counts"]
    assert all(p["kingdom"] == "Plantae" for p in survey["plants"])


def test_survey_replay_reads_prior_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    """#167.3: place survey --replay runs offline off the prior survey."""
    import builder.place as place_mod
    from builder.cli import main

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("GBIF must not be called on a replay")

    monkeypatch.setattr(place_mod, "fetch_raw", boom)
    monkeypatch.setattr(place_mod, "match_kingdom", boom)
    world = _needs_world()
    out = tmp_path / "gbr"
    records = out / "records"
    records.mkdir(parents=True)
    shutil.copy(FIXTURES / "gbr-survey.jsonl", records / "survey.jsonl")
    (out / "survey.json").write_text(
        json.dumps(
            {
                "gbif_counts": {animal_id: 5 for animal_id in _LIVE_GBR},
                "plants": [
                    {"name": name, "kingdom": "Plantae"}
                    for name in (
                        "Dugong grass (Halodule uninervis)",
                        "Paddle grass (Halophila ovalis)",
                        "Green algae (Halimeda)",
                        "Brown seaweed (Sargassum)",
                    )
                ],
            }
        )
    )
    grid_dir = Path(__file__).resolve().parent.parent / "data" / "habitat-grid"
    code = main(
        [
            "place",
            "survey",
            "Great Barrier Reef",
            "--budget",
            "2",
            "--out",
            str(out),
            "--world",
            str(world),
            "--habitat-grid",
            str(grid_dir),
            "--replay",
        ]
    )
    assert code == 0
    survey = json.loads((out / "survey.json").read_text())
    assert sorted(a["id"] for a in survey["animals"]) == sorted(_LIVE_GBR)
    assert "no recorded GBIF counts" not in capsys.readouterr().err


def test_survey_improve_reorders_prompt_animals(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    """#167.4: --improve asks the place's habitats first (prompt changes)."""
    import builder.place as place_mod
    from builder.cli import main

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("GBIF must not be called on a replay")

    monkeypatch.setattr(place_mod, "fetch_raw", boom)
    monkeypatch.setattr(place_mod, "match_kingdom", boom)
    world = _needs_world()
    out = tmp_path / "gbr"
    records = out / "records"
    records.mkdir(parents=True)
    shutil.copy(FIXTURES / "gbr-survey.jsonl", records / "survey.jsonl")
    (out / "survey.json").write_text(
        json.dumps(
            {
                "gbif_counts": {animal_id: 5 for animal_id in _LIVE_GBR},
                "plants": [
                    {"name": name, "kingdom": "Plantae"}
                    for name in (
                        "Dugong grass (Halodule uninervis)",
                        "Paddle grass (Halophila ovalis)",
                        "Green algae (Halimeda)",
                        "Brown seaweed (Sargassum)",
                    )
                ],
            }
        )
    )
    grid_dir = Path(__file__).resolve().parent.parent / "data" / "habitat-grid"
    code = main(
        [
            "place",
            "survey",
            "Great Barrier Reef",
            "--budget",
            "2",
            "--out",
            str(out),
            "--world",
            str(world),
            "--habitat-grid",
            str(grid_dir),
            "--improve",
            "au_great_barrier_reef",
            "--replay",
        ]
    )
    assert code == 0
    # The reordered prompt no longer matches the old recording, and the
    # replay says so while playing it anyway.
    assert "differs from the recorded one" in capsys.readouterr().err


def _grid_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "habitat-grid"


def test_replayed_reef_home_job_reaches_checking_with_a_passing_place(
    tmp_path: Path,
) -> None:
    """T99's done-when: the reef home replays, the place passes, take lays out."""
    from builder.place import check_place_file, replay_place, take_place_job

    world = _needs_world()
    reports, place_path = replay_place(
        FIXTURES / "reef-home", tmp_path / "out", world, _grid_dir()
    )
    assert list(reports) == ["home_reef"]
    assert reports["home_reef"].is_file()
    job_dir = tmp_path / "out" / "replay"
    assert (job_dir / "home_reef" / "homes" / "reef.json").is_file()
    assert (job_dir / "home_reef" / "checks" / "newhome.json").is_file()
    assert place_path == job_dir / "places" / "au_great_barrier_reef.json"
    place = json.loads(place_path.read_text())
    assert place["id"] == "au_great_barrier_reef"
    assert place["name"] == {"en": "Great Barrier Reef", "fi": "Iso Valliriutta"}
    assert place["country"] == "Australia"
    assert place["tiles"] == [[927, 560]]
    assert place["live"] is False
    assert place["points"] == [
        {
            "kind": "start",
            "id": "default",
            "lat": place["center"]["lat"],
            "lon": place["center"]["lon"],
            "facing": "north",
        }
    ]
    # The live reef filed its animals, whale since: surveyed
    # and filed means playable, in survey order.
    assert place["species"]["playable"] == [
        "clownfish",
        "green_sea_turtle",
        "blue_tang",
        "reef_shark",
        "bottlenose_dolphin",
        "humpback_whale",
        "lionfish",
        "butterflyfish",
    ]
    # #162.1: the replayed reef home has the deep sea.
    home = json.loads((job_dir / "home_reef" / "homes" / "reef.json").read_text())
    assert home["terrain"]["water_level"] == 40
    assert home["terrain"]["sea_depth"] == 35
    assert home["lakes"][0]["depth"] == 35
    assert check_place_file(place_path, world) == []
    dest = take_place_job(job_dir, tmp_path / "take", world)
    assert (dest / "places" / "au_great_barrier_reef.json").is_file()
    assert (dest / "homes" / "reef.json").is_file()
    assert (dest / "report.html").is_file()


def test_replayed_place_files_its_missing_on_ship(tmp_path: Path) -> None:
    """#188.4: shipping a replayed place writes places[tile] itself."""
    import shutil

    from fake_store import FakeStore

    from builder.grid import group_path
    from builder.place import replay_place

    world = _needs_world()
    place_dir = tmp_path / "reef-home"
    shutil.copytree(FIXTURES / "reef-home", place_dir)
    survey_path = place_dir / "survey.json"
    survey = json.loads(survey_path.read_text())
    survey["surveyed_at"] = "2026-11-01"
    survey["missing"] = [
        {"kind": "animal", "what": "hammerhead", "part": "sea"},
        {"kind": "plant", "what": "table_coral", "part": "sea"},
    ]
    survey_path.write_text(json.dumps(survey))
    store = FakeStore()
    reports, _place_path = replay_place(
        place_dir, tmp_path / "out", world, _grid_dir(), store, "abc123"
    )
    assert list(reports) == ["home_reef"]
    data = json.loads(store.blobs[group_path("z10_0927_0560")].decode("utf-8"))
    assert data["places"] == {
        "z10_0927_0560": {
            "region": "au_great_barrier_reef",
            "wanted": survey["missing"],
            "surveyed_at": "2026-11-01",
        }
    }


def test_write_place_derives_groups_from_filed_animals(
    tmp_path: Path,
) -> None:
    """T99: playable in survey order, hunters and hunted, no land."""
    from builder.place import check_place_file, write_place

    world = _needs_world()
    survey = {
        "tile": "z10_0582_0296",
        "name": {"en": "Helsinki shore", "fi": "Helsingin ranta"},
        "country": "Finland",
        "region_id": "fi_test_shore",
        "animals": [
            {"id": "perch", "source": "x"},
            {"id": "pike", "source": "x"},
            {"id": "frog", "source": "x"},
            {"id": "manta_ray", "source": "x"},
        ],
        "plants": [],
    }
    place = write_place(survey, tmp_path, world)
    assert place["id"] == "fi_test_shore"
    assert place["name"] == {"en": "Helsinki shore", "fi": "Helsingin ranta"}
    assert place["country"] == "Finland"
    assert place["tiles"] == [[582, 296]]
    assert place["live"] is False
    assert place["species"] == {
        "playable": ["perch", "pike", "frog"],
        "predators": ["pike"],
        "prey": ["frog"],
    }
    assert place["points"][0]["kind"] == "start"
    for land in ("terrain", "plants", "trees", "fish", "food", "animals", "status"):
        assert land not in place
    path = tmp_path / "fi_test_shore.json"
    path.write_text(json.dumps(place, indent=2))
    assert check_place_file(path, world) == []


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


def test_replayed_whale_makes_no_prey_and_reports_no_food(tmp_path: Path) -> None:
    """#175.2: the lab whale replayed: no herring, `no_food` reported."""
    from builder.place import replay_place
    from builder.records import ReplayError
    from builder.report import build_report, report_text

    world = _needs_world()
    with pytest.raises(ReplayError, match="out of answers"):
        replay_place(FIXTURES / "whale-noprey", tmp_path / "out", world, _grid_dir())
    item_dir = tmp_path / "out" / "replay" / "animal_humpback_whale"
    minset = json.loads((item_dir / "minset.json").read_text())
    assert minset["home_habitat"] == "reef"
    assert minset["prey"] is None
    assert "never makes prey" in minset["prey_reason"]
    assert minset["food"] == "no_food"
    assert "krill" in minset["food_reason"]
    made = [p.name for p in (item_dir / "draft" / "animals").glob("*.json")]
    assert made == ["humpback_whale.json"], "no herring was made"
    stray = [p for p in item_dir.rglob("*herring*") if "sandbox" not in p.parts]
    assert stray == []
    assert "no_food" in build_report(item_dir).read_text()
    assert "no_food" in report_text(item_dir)
