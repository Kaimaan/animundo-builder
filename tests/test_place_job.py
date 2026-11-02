"""Place jobs, stage A: research, one stop, Kai's yes. No network."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fake_store import FakeStore
from fake_world import boreal_3x3, make_world, write_grids

from builder.config import Config
from builder.grid import center, tile_at
from builder.habitats import HabitatGrid
from builder.place import tile_wild, wild_spot
from builder.worker import Worker

A, B = "z10_0581_0294", "z10_0582_0294"


class _ScriptRespond:
    """Canned survey answers, counting calls, spending nothing."""

    replays = True

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.calls = 0

    def responses_create(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        self.calls += 1
        part = SimpleNamespace(type="output_text", text=self.answers.pop(0))
        message = SimpleNamespace(type="message", phase=None, content=[part])
        return SimpleNamespace(output=[message])


def _worker(tmp_path: Path, store: FakeStore) -> Worker:
    world = make_world(tmp_path)
    (world / "biomes").mkdir(parents=True)
    (world / "biomes" / "boreal_forest.json").write_text(json.dumps({"id": "x"}))
    listed = json.loads((world / "animal-list.json").read_text())
    for entry in listed["animals"]:
        entry.setdefault("name", entry["id"].replace("_", " ").title())
        entry.setdefault("latin", "Panthera leo")
    (world / "animal-list.json").write_text(json.dumps(listed))
    return Worker(
        store=store,
        jobs_dir=tmp_path / "jobs",
        world=world,
        config=Config(ledger_path=str(tmp_path / "ledger.json")),
        grid_dir=write_grids(tmp_path / "grids", boreal_3x3()),
    )


def _place_job(**over: Any) -> dict[str, Any]:
    job: dict[str, Any] = {
        "format": 1,
        "kind": "place",
        "place": "Boreal forest in southern Finland",
        "want": ["lion"],
        "items": [],
        "budget_usd": 1.5,
        "visibility": "public",
        "created_by": "admin",
        "status": "queued",
        "cost_usd": 0.0,
    }
    job.update(over)
    return job


def _survey(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "centre": {"lat": 60.5, "lon": 24.5},
        "tiles": [A, B],
        "tiles_why": "The start clearing sits across the A/B tile side.",
        "biome": "boreal_forest",
        "name": {"en": "Nuuksio", "fi": "Nuuksio", "fi_gen": "Nuuksion"},
        "wikidata": "Q1",
        "animals": [{"id": "lion", "source": "https://example.org/lion"}],
        "plants": [],
    }
    data.update(over)
    return data


def test_place_research_stops_at_waiting_yes(tmp_path: Path) -> None:
    """#222.2: the surveyed place waits with research, price and spend."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    worker.chat = _ScriptRespond([json.dumps(_survey())])
    store.build_jobs["j1"] = _place_job()
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "waiting_yes"
    assert job["research"]["tiles"] == [A, B]
    assert job["research"]["biome"] == "boreal_forest"
    assert job["research"]["animals"] == [
        {"id": "lion", "source": "https://example.org/lion"}
    ]
    assert job["research"]["missing"] == [
        {"kind": "animal", "what": "lion", "part": "boreal_forest"}
    ]
    assert job["price"] == 1.00
    assert job["spent_usd"] == 0.0
    assert "tile" not in job
    saved = json.loads((tmp_path / "jobs" / "j1" / "survey.json").read_text())
    assert saved["wikidata"] == "Q1"


def test_place_no_biome_names_its_land(tmp_path: Path) -> None:
    """#222.4: the research lands, then the job fails naming its land."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    worker.chat = _ScriptRespond([json.dumps(_survey(biome="amazon_rainforest"))])
    store.build_jobs["j1"] = _place_job()
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "failed"
    assert job["error"] == "no_biome"
    assert job["needed_land"] == "amazon_rainforest"
    assert job["research"]["biome"] == "amazon_rainforest"


def test_place_bad_rectangle_fails_loud(tmp_path: Path) -> None:
    """#222.2: three tiles are no rectangle."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    worker.chat = _ScriptRespond([json.dumps(_survey(tiles=[A, B, "z10_0590_0298"]))])
    store.build_jobs["j1"] = _place_job()
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "failed"
    assert job["error"] == "bad_survey"


def test_place_one_tile_passes(tmp_path: Path) -> None:
    """#228.1: a survey on one tile passes, priced as one tile."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    worker.chat = _ScriptRespond([json.dumps(_survey(tiles=[A]))])
    store.build_jobs["j1"] = _place_job()
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "waiting_yes"
    assert job["research"]["tiles"] == [A]
    assert job["research"]["tiles_why"] == ""
    assert job["price"] == 0.50


def test_place_crashed_before_its_survey_runs_it_again(tmp_path: Path) -> None:
    """#230: a `building` place job with no survey re-surveys cleanly."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    worker.chat = _ScriptRespond([json.dumps(_survey(tiles=[A]))])
    store.build_jobs["j1"] = _place_job(status="building")
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "waiting_yes"
    assert job["research"]["tiles"] == [A]
    assert job["spent_usd"] == 0.0
    assert worker.chat.calls == 1


def test_place_two_tiles_without_why_fail(tmp_path: Path) -> None:
    """#228.2: two tiles with no reason fail the survey check."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    data = _survey()
    del data["tiles_why"]
    worker.chat = _ScriptRespond([json.dumps(data)])
    store.build_jobs["j1"] = _place_job()
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "failed"
    assert job["error"] == "bad_survey"


def test_place_two_tiles_with_why_pass(tmp_path: Path) -> None:
    """#228.2: two tiles with a reason pass, priced as two tiles."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    worker.chat = _ScriptRespond([json.dumps(_survey())])
    store.build_jobs["j1"] = _place_job()
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "waiting_yes"
    assert job["research"]["tiles"] == [A, B]
    assert (
        job["research"]["tiles_why"]
        == "The start clearing sits across the A/B tile side."
    )
    assert job["price"] == 1.00


def test_place_no_turns_down(tmp_path: Path) -> None:
    """#222.3: a no consumes the answer and fails turned_down."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    store.build_jobs["j1"] = _place_job(
        status="waiting_yes", place_no={"at": "t"}, research={}, price=1.0
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "failed"
    assert job["error"] == "turned_down"
    assert "place_no" not in job


def test_place_yes_parks_approved_under_its_cap(tmp_path: Path) -> None:
    """#222.3: a yes is consumed; the build waits for stage C."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    store.build_jobs["j1"] = _place_job(
        status="waiting_yes",
        place_yes={"cap": 2.5, "at": "t"},
        research={},
        price=1.0,
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "building"
    assert job["phase"] == "approved"
    assert job["cap_usd"] == 2.5
    assert "place_yes" not in job
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["phase"] == "approved"


def test_place_yes_needs_a_sane_cap(tmp_path: Path) -> None:
    """#224.2: a cap outside $0.01-$10 stays waiting, unconsumed."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    store.build_jobs["j1"] = _place_job(
        status="waiting_yes", place_yes={"cap": 99, "at": "t"}
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "waiting_yes"
    assert job["place_yes"] == {"cap": 99, "at": "t"}


def test_place_bad_request_fails_loud(tmp_path: Path) -> None:
    """#224.1: no words or no research budget is no place job."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    worker.chat = _ScriptRespond([])
    store.build_jobs["j1"] = _place_job(place="")
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["error"] == "bad_place"
    store.build_jobs["j2"] = _place_job(budget_usd=0)
    worker.handle_build_job("j2")
    assert store.build_jobs["j2"]["error"] == "bad_place"
    assert worker.chat.calls == 0


def test_place_reuses_its_survey_json(tmp_path: Path) -> None:
    """A crash between survey and summary spends no second call."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    worker.chat = _ScriptRespond([])
    job_dir = tmp_path / "jobs" / "j1"
    job_dir.mkdir(parents=True)
    (job_dir / "survey.json").write_text(
        json.dumps(
            {
                "tiles": [A],
                "biome": "boreal_forest",
                "centre": {"lat": 60.5, "lon": 24.5},
                "name": {"en": "N", "fi": "N", "fi_gen": "N"},
                "wikidata": "Q1",
                "animals": [],
                "plants": [],
                "missing": [],
            }
        )
    )
    store.build_jobs["j1"] = _place_job(status="building")
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "waiting_yes"
    assert job["price"] == 0.50
    assert job["research"]["tiles_why"] == ""
    assert worker.chat.calls == 0


def _write_landcover(grid_dir: Path, cells: dict[str, list[Any]]) -> None:
    """A land-cover file beside the fake grid."""
    with gzip.open(grid_dir / "landcover.json.gz", "wt", encoding="utf-8") as handle:
        json.dump({"cells": cells}, handle)


def _boreal_7x7(col: int = 581, row: int = 294) -> dict[str, list[Any]]:
    """A 7 by 7 boreal block for the 3-tile wild move."""
    cell = [717, 1.0, None, 0.0, 6, 1.0, 0.0, 80.0]
    return {
        f"{c},{r}": list(cell)
        for r in range(row - 3, row + 4)
        for c in range(col - 3, col + 4)
    }


def test_tile_wild_thresholds() -> None:
    """#238: wild below 2 % built and 25 % built plus crop."""
    grid = HabitatGrid(
        cells={},
        landcover={
            "581,294": [0.019, 0.0, "l" * 25],
            "582,294": [0.02, 0.0, "b" * 25],
            "581,295": [0.01, 0.239, "l" * 25],
            "582,295": [0.01, 0.24, "c" * 25],
        },
    )
    assert tile_wild("z10_0581_0294", grid) is True
    assert tile_wild("z10_0582_0294", grid) is False
    assert tile_wild("z10_0581_0295", grid) is True
    assert tile_wild("z10_0582_0295", grid) is False
    assert tile_wild("z10_0580_0294", grid) is None


def test_wild_spot_snaps_to_wild_land() -> None:
    """#238: the start spot moves onto a wild mask cell."""
    grid = HabitatGrid(
        cells=boreal_3x3(),
        landcover={"581,294": [0.0, 0.0, "b" * 24 + "l"]},
    )
    lat, lon = center(A)
    moved_lat, moved_lon = wild_spot(A, lat, lon, grid)
    assert moved_lat < lat
    assert moved_lon > lon
    assert tile_at(moved_lat, moved_lon) == A
    grid = HabitatGrid(cells=boreal_3x3(), landcover={"581,294": [0.0, 0.0, "b" * 25]})
    assert wild_spot(A, lat, lon, grid) == (lat, lon)
    assert wild_spot(A, lat, lon, HabitatGrid(cells=boreal_3x3())) == (lat, lon)


def test_place_city_tile_moves_to_wild_neighbour(tmp_path: Path) -> None:
    """#238: a built tile moves to the nearest wild one with moved filled."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    _write_landcover(
        tmp_path / "grids",
        {
            "581,294": [0.31, 0.0, "b" * 25],
            "580,293": [0.31, 0.0, "b" * 25],
            "581,293": [0.31, 0.0, "b" * 25],
            "582,293": [0.31, 0.0, "b" * 25],
            "580,294": [0.31, 0.0, "b" * 25],
            "582,294": [0.0, 0.0, "l" * 25],
            "580,295": [0.31, 0.0, "b" * 25],
            "581,295": [0.31, 0.0, "b" * 25],
            "582,295": [0.31, 0.0, "b" * 25],
        },
    )
    lat, lon = center(A)
    worker.chat = _ScriptRespond(
        [json.dumps(_survey(tiles=[A], tiles_why="", centre={"lat": lat, "lon": lon}))]
    )
    store.build_jobs["j1"] = _place_job()
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "waiting_yes"
    assert job["research"]["tiles"] == [B]
    assert job["research"]["moved"] == [
        {"from": A, "to": B, "why": f"{A} is 31 % built-up"}
    ]
    spot = job["research"]["centre"]
    assert tile_at(spot["lat"], spot["lon"]) == B


def test_place_without_wild_nearby_fails_no_wild_tile(tmp_path: Path) -> None:
    """#238: nothing wild within 3 fails with the research kept."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    write_grids(tmp_path / "grids", _boreal_7x7())
    _write_landcover(
        tmp_path / "grids",
        {
            f"{c},{r}": [0.5, 0.0, "b" * 25]
            for r in range(291, 298)
            for c in range(578, 585)
        },
    )
    worker.chat = _ScriptRespond([json.dumps(_survey(tiles=[A], tiles_why=""))])
    store.build_jobs["j1"] = _place_job()
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "failed"
    assert job["error"] == "no_wild_tile"
    assert job["research"]["tiles"] == [A]


def test_place_wild_tile_stays_without_moved(tmp_path: Path) -> None:
    """#238: a wild tile passes unchanged with no moved entry."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    _write_landcover(
        tmp_path / "grids",
        {
            f"{c},{r}": [0.0, 0.0, "l" * 25]
            for r in (293, 294, 295)
            for c in (580, 581, 582)
        },
    )
    lat, lon = center(A)
    worker.chat = _ScriptRespond(
        [json.dumps(_survey(tiles=[A], tiles_why="", centre={"lat": lat, "lon": lon}))]
    )
    store.build_jobs["j1"] = _place_job()
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "waiting_yes"
    assert job["research"]["tiles"] == [A]
    assert "moved" not in job["research"]
    spot = job["research"]["centre"]
    assert tile_at(spot["lat"], spot["lon"]) == A


def _real_worker(tmp_path: Path, store: FakeStore) -> Worker | None:
    """A worker on the committed grid, None without the baked cover."""
    from builder.schemas import ROOT

    grid_dir = ROOT / "data" / "habitat-grid"
    if not (grid_dir / "landcover.json.gz").is_file():
        return None
    world = make_world(tmp_path)
    (world / "biomes").mkdir(parents=True)
    (world / "biomes" / "rainforest.json").write_text(json.dumps({"id": "x"}))
    return Worker(
        store=store,
        jobs_dir=tmp_path / "jobs",
        world=world,
        config=Config(ledger_path=str(tmp_path / "ledger.json")),
        grid_dir=grid_dir,
    )


def _amazon_survey(tile: str, lat: float, lon: float) -> dict[str, Any]:
    """A recorded place survey over one Amazon tile."""
    return {
        "centre": {"lat": lat, "lon": lon},
        "tiles": [tile],
        "tiles_why": "",
        "biome": "rainforest",
        "name": {"en": "Amazon", "fi": "Amazon", "fi_gen": "Amazonin"},
        "wikidata": "Q3783",
        "animals": [],
        "plants": [],
    }


def test_place_manaus_replay_moves_off_the_city(tmp_path: Path) -> None:
    """#238: the Manaus replay moves to a wild tile with moved filled."""
    import pytest

    from builder.habitats import load_grid
    from builder.schemas import ROOT

    store = FakeStore()
    worker = _real_worker(tmp_path, store)
    if worker is None:
        pytest.skip("needs the baked land-cover file")
    worker.chat = _ScriptRespond(
        [json.dumps(_amazon_survey("z10_0341_0520", -3.1, -60.02))]
    )
    store.build_jobs["j1"] = _place_job(place="Amazon rainforest")
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "waiting_yes"
    assert len(job["research"]["tiles"]) == 1
    target = job["research"]["tiles"][0]
    assert target != "z10_0341_0520"
    grid = load_grid(ROOT / "data" / "habitat-grid")
    assert tile_wild(target, grid) is True
    assert grid.habitat_at(target) == grid.habitat_at("z10_0341_0520")
    moved = job["research"]["moved"]
    assert len(moved) == 1
    assert moved[0]["from"] == "z10_0341_0520"
    assert moved[0]["to"] == target
    assert moved[0]["why"].startswith("z10_0341_0520 is ")
    assert "built-up" in moved[0]["why"]


def test_place_mamiraua_replay_passes_unchanged(tmp_path: Path) -> None:
    """#238: the Mamirauá replay passes with no moved entry."""
    import pytest

    store = FakeStore()
    worker = _real_worker(tmp_path, store)
    if worker is None:
        pytest.skip("needs the baked land-cover file")
    worker.chat = _ScriptRespond(
        [json.dumps(_amazon_survey("z10_0325_0518", -2.26, -65.68))]
    )
    store.build_jobs["j1"] = _place_job(place="Mamirauá")
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "waiting_yes"
    assert job["research"]["tiles"] == ["z10_0325_0518"]
    assert "moved" not in job["research"]


def test_place_job_validates() -> None:
    """The #224.1 request shape validates."""
    from builder.schemas import validate

    validate(
        "build-job",
        {
            "format": 1,
            "kind": "place",
            "place": "Amazon rainforest",
            "want": ["capybara"],
            "items": [],
            "budget_usd": 1.5,
            "visibility": "public",
            "created_by": "admin",
            "status": "queued",
            "cost_usd": 0.0,
            "created_at": "t",
            "updated_at": "t",
        },
    )
