"""Place jobs, stage A: research, one stop, Kai's yes. No network."""

from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fake_store import FakeStore
from fake_world import boreal_3x3, make_world, write_grids

from builder.config import Config
from builder.grid import center, group_path, tile_at
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


class _ResearchRespond:
    """Canned research answers with usage, spending for real."""

    model = "fake-test-model"
    replays = False

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)

    def responses_create(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        parts = [
            SimpleNamespace(
                type="output_text", text=self.answers.pop(0), annotations=[]
            )
        ]
        output = [SimpleNamespace(type="message", content=parts)]
        return SimpleNamespace(
            model="fake-test-model",
            output=output,
            usage=SimpleNamespace(
                prompt_tokens=100, completion_tokens=50, prompt_tokens_details=None
            ),
        )


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
    assert job["price"] == 1.42
    assert job["research"]["price_parts"]["total"] == 1.42
    assert job["research"]["price_parts"]["animals"] == [
        {"id": "lion", "usd": 0.42, "from": "fixed"}
    ]
    assert job["spent_usd"] == 0.0
    assert "tile" not in job
    saved = json.loads((tmp_path / "jobs" / "j1" / "survey.json").read_text())
    assert saved["wikidata"] == "Q1"


def test_place_research_seeds_cost_with_spend(tmp_path: Path) -> None:
    """#269.2: stage A starts cost_usd where spent_usd starts."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    worker.chat = _ScriptRespond([json.dumps(_survey())])
    store.build_jobs["j1"] = _place_job()
    del store.build_jobs["j1"]["cost_usd"]
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "waiting_yes"
    assert job["cost_usd"] == job["spent_usd"]


def test_place_survey_spend_joins_cost_and_spent(tmp_path: Path) -> None:
    """#269.2: tile surveys join both running totals, once each."""
    from builder.ledger import Caps, FileLedger

    store = FakeStore()
    worker = _worker(tmp_path, store)
    assert worker.world is not None
    store.build_jobs["j1"] = _place_job(
        status="building", phase="approved", research=_build_research()
    )
    assert worker._claim("j1")
    ledger = FileLedger(str(tmp_path / "ledger.json"))
    receipt = ledger.reserve(f"survey:{A}", 0.03, Caps(100.0, 1000.0, 10000.0))
    ledger.settle(receipt, 0.03)
    worker._record_place_survey_spend("j1", store.build_jobs["j1"], 0.0)
    job = store.build_jobs["j1"]
    assert job["survey_usd"] == 0.03
    assert job["cost_usd"] == 0.03
    assert job["spent_usd"] == 0.03


def test_place_survey_spend_ignores_another_job(tmp_path: Path) -> None:
    """#275: a rerun that paid nothing adds nothing, even shared tiles."""
    from builder.ledger import Caps, FileLedger

    store = FakeStore()
    worker = _worker(tmp_path, store)
    assert worker.world is not None
    store.build_jobs["j1"] = _place_job(
        status="building", phase="approved", research=_build_research()
    )
    assert worker._claim("j1")
    ledger = FileLedger(str(tmp_path / "ledger.json"))
    receipt = ledger.reserve(f"survey:{A}", 0.05, Caps(100.0, 1000.0, 10000.0))
    ledger.settle(receipt, 0.05)
    worker._record_place_survey_spend("j1", store.build_jobs["j1"], 0.05)
    job = store.build_jobs["j1"]
    assert "survey_usd" not in job
    assert job.get("cost_usd", 0.0) == 0.0
    assert job["survey_cached"] is True


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
    """#228.1: a survey on one tile passes, priced as one tile plus parts."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    worker.chat = _ScriptRespond([json.dumps(_survey(tiles=[A]))])
    store.build_jobs["j1"] = _place_job()
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "waiting_yes"
    assert job["research"]["tiles"] == [A]
    assert job["research"]["tiles_why"] == ""
    assert job["price"] == 0.92
    assert job["research"]["price_parts"]["tiles"] == {
        "n": 1,
        "usd": 0.50,
        "from": "fixed",
    }


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
    """#228.2: two tiles with a reason pass, priced as two tiles plus parts."""
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
    assert job["price"] == 1.42


def test_waiting_place_reprices_before_the_answer(tmp_path: Path) -> None:
    """#239.2: a waiting job without parts re-prices, then waits."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    store.build_jobs["j1"] = _place_job(
        status="waiting_yes",
        research={
            "tiles": [A],
            "biome": "boreal_forest",
            "animals": [{"id": "lion", "source": "https://example.org/lion"}],
            "plants": [],
        },
        price=0.50,
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "waiting_yes"
    assert job["price"] == 0.92
    assert job["research"]["price_parts"]["total"] == 0.92


def test_waiting_place_with_stale_parts_reprices(tmp_path: Path) -> None:
    """#264: a quote naming an unmakable animal re-prices without it."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    assert worker.world is not None
    listing = json.loads((worker.world / "animal-list.json").read_text())
    listing["animals"].append(
        {"id": "sloth", "gbif_key": 9, "habitats": ["boreal_forest"]}
    )
    (worker.world / "animal-list.json").write_text(json.dumps(listing))
    store.build_jobs["j1"] = _place_job(
        status="waiting_yes",
        research={
            "tiles": [A],
            "biome": "boreal_forest",
            "animals": [
                {"id": "lion", "source": "s"},
                {"id": "sloth", "source": "s"},
            ],
            "plants": [],
            "price_parts": {
                "tiles": {"n": 1, "usd": 0.50, "from": "fixed"},
                "animals": [
                    {"id": "lion", "usd": 0.42, "from": "fixed"},
                    {"id": "sloth", "usd": 0.42, "from": "fixed"},
                ],
                "plants": [],
                "total": 1.34,
            },
        },
        price=1.34,
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["price"] == 0.92
    assert job["research"]["price_parts"]["animals"] == [
        {"id": "lion", "usd": 0.42, "from": "fixed"}
    ]


def test_waiting_place_with_fresh_parts_keeps_them(tmp_path: Path) -> None:
    """#264: a current quote is left alone, medians drift or not."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    parts = {
        "tiles": {"n": 1, "usd": 0.50, "from": "fixed"},
        "animals": [{"id": "lion", "usd": 0.42, "from": "fixed"}],
        "plants": [],
        "total": 0.92,
        "note": "stays",
    }
    store.build_jobs["j1"] = _place_job(
        status="waiting_yes",
        research={
            "tiles": [A],
            "biome": "boreal_forest",
            "animals": [{"id": "lion", "source": "s"}],
            "plants": [],
            "price_parts": dict(parts),
        },
        price=0.92,
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["research"]["price_parts"] == parts
    assert job["price"] == 0.92


def test_waiting_place_reprice_honours_plant_swaps(tmp_path: Path) -> None:
    """#264.3: the re-price builds the swapped plant, not the researched."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    store.build_jobs["j1"] = _place_job(
        status="waiting_yes",
        research={
            "tiles": [A],
            "biome": "boreal_forest",
            "animals": [],
            "plants": [{"name": "Euterpe oleracea", "source": "s"}],
            "plant_swaps": {"euterpe_oleracea": "euterpe_precatoria"},
        },
        price=0.50,
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["price"] == 0.63
    assert job["research"]["price_parts"]["plants"] == [
        {"id": "euterpe_precatoria", "usd": 0.13, "from": "fixed"}
    ]


def test_reprice_drops_a_yes_its_cap_no_longer_covers(tmp_path: Path) -> None:
    """#239.2: a stale yes goes loudly; a covering yes stands."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    research = {
        "tiles": [A],
        "biome": "boreal_forest",
        "animals": [{"id": "lion", "source": "https://example.org/lion"}],
        "plants": [],
    }
    store.build_jobs["j1"] = _place_job(
        status="waiting_yes",
        place_yes={"cap": 0.50, "at": "t"},
        research=dict(research),
        price=0.50,
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "waiting_yes"
    assert "place_yes" not in job
    assert job["price"] == 0.92
    store.build_jobs["j2"] = _place_job(
        status="waiting_yes",
        place_yes={"cap": 2.50, "at": "t"},
        research=dict(research),
        price=0.50,
    )
    worker.handle_build_job("j2")
    job = store.build_jobs["j2"]
    assert job["status"] == "building"
    assert job["phase"] == "approved"


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


def test_wild_moves_share_no_target(tmp_path: Path) -> None:
    """#246: two city tiles move to two different wild tiles."""
    from builder.place import ensure_wild_tiles

    grid = HabitatGrid(
        cells=_boreal_7x7(),
        landcover={
            "581,294": [0.5, 0.0, "b" * 25],
            "582,294": [0.5, 0.0, "b" * 25],
            "580,293": [0.5, 0.0, "b" * 25],
            "581,293": [0.0, 0.0, "l" * 25],
            "582,293": [0.0, 0.0, "l" * 25],
        },
    )
    out, moved = ensure_wild_tiles(["z10_0581_0294", "z10_0582_0294"], grid)
    assert out == ["z10_0581_0293", "z10_0582_0293"]
    assert [m["to"] for m in moved] == out


def test_wild_repeats_collapse_to_one(tmp_path: Path) -> None:
    """#246: the same wild tile twice surveys once."""
    from builder.place import ensure_wild_tiles

    grid = HabitatGrid(
        cells=_boreal_7x7(),
        landcover={"581,293": [0.0, 0.0, "l" * 25]},
    )
    out, moved = ensure_wild_tiles(["z10_0581_0293", "z10_0581_0293"], grid)
    assert out == ["z10_0581_0293"]
    assert moved == []


def test_place_price_follows_final_tiles(tmp_path: Path) -> None:
    """#246: a repeated survey tile prices once, plus the species parts."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    assert worker.grid_dir is not None
    _write_landcover(worker.grid_dir, {"581,294": [0.0, 0.0, "l" * 25]})
    worker.chat = _ScriptRespond([json.dumps(_survey(tiles=[A, A]))])
    store.build_jobs["j1"] = _place_job()
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "waiting_yes"
    assert job["research"]["tiles"] == [A]
    assert job["price"] == 0.92
    assert job["research"]["price_parts"]["tiles"]["n"] == 1


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


def test_place_files_wanted_after_build(tmp_path: Path) -> None:
    """#224.5: found-but-unmade species land on the tiles' wanted lists."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    job = _place_job(want=["lion", "dragon"])
    store.build_jobs["j1"] = job
    research = {
        "tiles": [A],
        "biome": "boreal_forest",
        "animals": [
            {"id": "lion", "source": "s"},
            {"id": "fox", "source": "s"},
        ],
        "plants": [{"name": "Cloudberry", "source": "s"}],
    }
    worker._place_file_wanted("j1", job, research, "fi_nuuksio")
    data = json.loads(store.read_blob(group_path(A))[0].decode("utf-8"))
    entry = data["places"][A]
    assert entry["region"] == "fi_nuuksio"
    assert entry["surveyed_at"]
    assert {"kind": "animal", "what": "lion", "part": "boreal_forest"} in entry[
        "wanted"
    ]
    assert {"kind": "plant", "what": "cloudberry", "part": "boreal_forest"} in entry[
        "wanted"
    ]
    assert all(item["what"] != "fox" for item in entry["wanted"])
    assert sum(1 for item in entry["wanted"] if item["what"] == "lion") == 1
    assert all(item["what"] != "dragon" for item in entry["wanted"])


def test_place_wanted_skips_species_the_build_made(tmp_path: Path) -> None:
    """#224.5: made species stay off wanted; all made files it empty."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    made = tmp_path / "jobs" / "j1" / "animal_lion" / "animals"
    made.mkdir(parents=True)
    (made / "lion.json").write_text("{}")
    greens = tmp_path / "jobs" / "j1" / "plant_cloudberry" / "models" / "plants"
    greens.mkdir(parents=True)
    (greens / "cloudberry.json").write_text("{}")
    job = _place_job(want=[])
    store.build_jobs["j1"] = job
    research = {
        "tiles": [A],
        "biome": "boreal_forest",
        "animals": [{"id": "lion", "source": "s"}],
        "plants": [{"name": "Cloudberry", "source": "s"}],
    }
    worker._place_file_wanted("j1", job, research, "fi_nuuksio")
    data = json.loads(store.read_blob(group_path(A))[0].decode("utf-8"))
    assert data["places"][A]["wanted"] == []
    assert store.build_jobs["j1"].get("status") != "failed"


def test_place_ship_species_fails_a_failed_item(tmp_path: Path) -> None:
    """#222.5: an item whose make failed fails the place, never ships."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    job = _place_job(status="building", phase="approved")
    job["research"] = {"tiles": [A], "biome": "boreal_forest"}
    job["progress"] = [
        {"id": "animal_lion", "status": "failed", "error": "maker: no spec"}
    ]
    store.build_jobs["j1"] = job
    assert worker._claim("j1")
    item = {"id": "animal_lion", "kind": "animal", "what": "lion"}
    assert worker._place_ship_species("j1", job, item) is False
    assert store.build_jobs["j1"]["status"] == "failed"
    assert store.build_jobs["j1"]["error"] == "item_failed"


def test_place_item_failed_reads_the_progress_verdict() -> None:
    """#269.1: a checking item passes the gate; failed or missing fails."""
    assert (
        Worker._place_item_failed(
            {"progress": [{"id": "a", "status": "checking"}]}, "a"
        )
        is None
    )
    assert (
        Worker._place_item_failed(
            {"progress": [{"id": "a", "status": "failed", "error": "maker: x"}]},
            "a",
        )
        == "maker: x"
    )
    assert Worker._place_item_failed({"progress": []}, "a") == "it never ran"


def _lion_answer() -> dict[str, Any]:
    """A tile survey with only the unfiled lion present (#266.3)."""
    return {
        "parts": [{"kind": "forest", "species": ["lion"]}],
        "present": ["lion"],
        "date": "2026-11-03",
        "plants": [],
        "trees": [],
        "place": "Boreal forest",
    }


def _rich_biome(world: Path) -> None:
    (world / "biomes" / "boreal_forest.json").write_text(
        json.dumps(
            {
                "habitat": "boreal_forest",
                "ecoregions": [717],
                "plants": {
                    "trees": [{"id": "pine", "share": 1.0}],
                    "cover": [],
                    "water": [],
                },
            }
        )
    )


def _build_research(**over: Any) -> dict[str, Any]:
    research: dict[str, Any] = {
        "tiles": [A],
        "biome": "boreal_forest",
        "centre": {"lat": 60.5, "lon": 24.5},
        "name": {"en": "Nuuksio", "fi": "Nuuksio", "fi_gen": "Nuuksion"},
        "short": {"en": "Nuuksio", "fi": "Nuuksio", "fi_gen": "Nuuksion"},
        "country": "fi",
        "animals": [{"id": "lion", "source": "s"}],
        "plants": [],
        "price_parts": {
            "tiles": {"n": 1, "usd": 0.50, "from": "fixed"},
            "animals": [{"id": "lion", "usd": 0.42, "from": "fixed"}],
            "plants": [],
            "total": 0.92,
        },
    }
    research.update(over)
    return research


def _stub_make_lion(worker: Worker, job_id: str, item: dict[str, Any]) -> None:
    """A made species as run_item would leave it: files plus progress."""
    item_dir = worker.jobs_dir / job_id / item["id"]
    if item.get("kind") == "plant":
        greens = item_dir / "models" / "plants"
        greens.mkdir(parents=True)
        (greens / "pine.json").write_text(json.dumps({"id": "pine"}))
        (greens / "pine.glb").write_bytes(b"GLB")
    else:
        (item_dir / "animals").mkdir(parents=True)
        (item_dir / "animals" / "lion.json").write_text(
            json.dumps(
                {
                    "id": "lion",
                    "playable": True,
                    "habitats": ["boreal_forest"],
                    "hunts": ["vole"],
                    "diet": [],
                }
            )
        )
        (item_dir / "models").mkdir(parents=True)
        (item_dir / "models" / "lion.glb").write_bytes(b"GLB")
        (item_dir / "models" / "lion.png").write_bytes(b"PNG")
    job = worker.store.get_build_job(job_id) or {}
    progress = [e for e in job.get("progress", []) if e.get("id") != item["id"]]
    progress.append({"id": item["id"], "status": "checking", "cost_usd": 0.0})
    worker.store.update_build_job(job_id, {"progress": progress})


def test_place_builds_species_first_then_publishes_together(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#266.3: past the tile on an empty habitat; tile and species together."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    assert worker.world is not None
    _rich_biome(worker.world)
    monkeypatch.setattr(
        "builder.tiles.fetch_species", lambda *args, **kwargs: _lion_answer()
    )

    def fake_run_item(
        self: Worker,
        job_id: str,
        job: dict[str, Any],
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        budget: float,
    ) -> None:
        _stub_make_lion(self, job_id, item)

    monkeypatch.setattr(Worker, "run_item", fake_run_item)
    store.build_jobs["j1"] = _place_job(
        status="building",
        phase="approved",
        cap_usd=10.0,
        research=_build_research(),
    )
    assert worker._claim("j1")
    worker._place_build("j1", store.build_jobs["j1"])
    job = store.build_jobs["j1"]
    assert job["status"] == "published", job.get("error")
    data = json.loads(store.read_blob(group_path(A))[0].decode("utf-8"))
    assert "lion" in data["tiles"][A]["species"]["playable"]
    overlay = store.read_blob("packs/animal_lion/1/additions/tiles.json")[0]
    assert overlay is not None
    assert A in json.loads(overlay.decode("utf-8"))["tiles"]
    assert store.read_blob("packs/animal_lion/1/animals/lion.json")[0] is not None
    assert store.read_blob("packs/animal_lion/1/models/lion.glb")[0] is not None
    assert (
        store.read_blob("packs/animal_lion/1/models/previews/lion.png")[0] is not None
    )
    assert store.read_blob("packs/plant_pine/1/models/plants/pine.glb")[0] is not None
    assert store.read_blob("packs/plant_pine/1/additions/tiles.json")[0] is None
    place_blob = store.read_blob("packs/place_fi_nuuksio/1/places/fi_nuuksio.json")[0]
    assert place_blob is not None


def test_place_species_files_ship_own_id_only(tmp_path: Path) -> None:
    """#275.1: prey copies in the item dir never ship."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    item_dir = tmp_path / "jobs" / "j1" / "animal_lion"
    (item_dir / "animals").mkdir(parents=True)
    (item_dir / "animals" / "lion.json").write_text("{}")
    (item_dir / "animals" / "vole.json").write_text("{}")
    (item_dir / "models").mkdir(parents=True)
    (item_dir / "models" / "lion.glb").write_bytes(b"GLB")
    item = {"id": "animal_lion", "kind": "animal", "what": "lion"}
    files = worker._place_species_files("j1", item)
    assert sorted(files) == ["animals/lion.json", "models/lion.glb"]
    with pytest.raises(ValueError, match="no animals/tiger.json to ship"):
        worker._place_species_files(
            "j1", {"id": "animal_tiger", "kind": "animal", "what": "tiger"}
        )


def test_place_test_build_publishes_nothing_but_reports(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#275: a test build runs cleanly but puts nothing live."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    assert worker.world is not None
    _rich_biome(worker.world)
    monkeypatch.setattr(
        "builder.tiles.fetch_species", lambda *args, **kwargs: _lion_answer()
    )

    def fake_run_item(
        self: Worker,
        job_id: str,
        job: dict[str, Any],
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        budget: float,
    ) -> None:
        _stub_make_lion(self, job_id, item)

    monkeypatch.setattr(Worker, "run_item", fake_run_item)
    doc = _place_job(
        status="building",
        phase="approved",
        cap_usd=10.0,
        research=_build_research(),
    )
    doc["test_build"] = True
    store.build_jobs["j1"] = doc
    assert worker._claim("j1")
    worker._place_build("j1", store.build_jobs["j1"])
    job = store.build_jobs["j1"]
    assert job["status"] == "published", job.get("error")
    assert "pack" not in job
    assert [n for n in store.blobs if n.startswith("packs/")] == []
    assert [n for n in store.blobs if n.startswith("tiles/")] == []
    assert any(n.startswith("review/j1/") for n in store.blobs)
    assert job.get("cost", {}).get("actual", 0.0) >= 0.0
    place_entry = next(e for e in job["progress"] if e["id"] == "place")
    assert place_entry["status"] == "tested"


def test_place_wanted_lists_a_prey_no_item_makes(tmp_path: Path) -> None:
    """#275.2: a researched prey nobody makes lands on wanted."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    assert worker.world is not None
    listed_path = worker.world / "animal-list.json"
    listed = json.loads(listed_path.read_text())
    listed["animals"].append(
        {"id": "puma", "name": "Mountain lion", "habitats": ["boreal_forest"]}
    )
    listed_path.write_text(json.dumps(listed))
    job = _place_job(want=[])
    store.build_jobs["j1"] = job
    item_dir = tmp_path / "jobs" / "j1" / "animal_lion"
    item_dir.mkdir(parents=True)
    (item_dir / "research.json").write_text(
        json.dumps(
            {
                "main_food": {"kind": "prey", "name": "mountain lion"},
                "other_foods": [],
            }
        )
    )
    research = {
        "tiles": [A],
        "biome": "boreal_forest",
        "animals": [{"id": "fox", "source": "s"}],
        "plants": [],
    }
    wanted = worker._wanted_drops("j1", job, research)
    assert wanted == [{"kind": "animal", "what": "puma", "part": "boreal_forest"}]


def test_place_build_failing_animal_publishes_nothing(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#266.3: an animal that fails keeps the tile off Storage too."""
    from builder.stages.maker import _Stopped

    store = FakeStore()
    worker = _worker(tmp_path, store)
    assert worker.world is not None
    _rich_biome(worker.world)
    monkeypatch.setattr(
        "builder.tiles.fetch_species", lambda *args, **kwargs: _lion_answer()
    )

    def fail_stages(self: Worker, *args: Any, **kwargs: Any) -> None:
        raise _Stopped("lion: no reference spec")

    monkeypatch.setattr(Worker, "_run_stages", fail_stages)
    store.build_jobs["j1"] = _place_job(
        status="building",
        phase="approved",
        cap_usd=10.0,
        research=_build_research(),
    )
    assert worker._claim("j1")
    worker._place_build("j1", store.build_jobs["j1"])
    job = store.build_jobs["j1"]
    assert job["status"] == "failed"
    assert job["error"] == "item_failed"
    assert store.read_blob(group_path(A))[0] is None
    assert [n for n in store.blobs if n.startswith("packs/")] == []


def test_place_make_species_skips_a_checking_item(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#269.3: a rerun reuses the passed item's files, spending nothing."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    assert worker.world is not None
    _rich_biome(worker.world)
    job = _place_job(
        status="building",
        phase="approved",
        cap_usd=10.0,
        research=_build_research(),
    )
    job["progress"] = [
        {"id": "animal_lion", "status": "checking", "cost_usd": 0.42},
        {"id": "plant_pine", "status": "checking", "cost_usd": 0.13},
    ]
    store.build_jobs["j1"] = job

    def no_remake(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a checking item must not remake")

    monkeypatch.setattr(Worker, "run_item", no_remake)
    biome = {
        "plants": {"trees": [{"id": "pine", "share": 1.0}], "cover": [], "water": []}
    }
    items = worker._place_make_species(
        "j1", store.build_jobs["j1"], job["research"], biome, "z10_0581_0294", 10.0
    )
    assert items is not None
    assert [item["id"] for item in items] == ["animal_lion", "plant_pine"]


def test_place_make_species_remakes_a_stale_checking_item(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#269.4: a checking pass older than the maker remakes on rerun."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    assert worker.world is not None
    _rich_biome(worker.world)
    job = _place_job(
        status="building",
        phase="approved",
        cap_usd=10.0,
        research=_build_research(),
    )
    job["progress"] = [
        {"id": "animal_lion", "status": "checking", "cost_usd": 0.42},
        {"id": "plant_pine", "status": "checking", "cost_usd": 0.13},
    ]
    store.build_jobs["j1"] = job
    assert worker._claim("j1")
    lion_dir = tmp_path / "jobs" / "j1" / "animal_lion"
    lion_dir.mkdir(parents=True)
    (lion_dir / "state.json").write_text(
        json.dumps(
            {
                "completed": ["research", "oneshot", "plants"],
                "results": {
                    "oneshot": {
                        "animal_files": ["animals/lion.json"],
                        "cost_usd": 0.42,
                    }
                },
                "failed": None,
            }
        )
    )
    calls: list[str] = []

    def record_run(self: Worker, job_id: str, job: dict[str, Any], *rest: Any) -> None:
        calls.append(job_id)
        _stub_make_lion(self, job_id, rest[2])

    monkeypatch.setattr(Worker, "run_item", record_run)
    biome = {
        "plants": {"trees": [{"id": "pine", "share": 1.0}], "cover": [], "water": []}
    }
    items = worker._place_make_species(
        "j1", store.build_jobs["j1"], job["research"], biome, "z10_0581_0294", 10.0
    )
    assert items is not None
    assert calls == ["j1"]


def test_place_build_no_short_sets_waiting_then_clears(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#266.2: a waiting place names its reason; moving on clears it."""
    from builder.worker import _Off

    store = FakeStore()
    worker = _worker(tmp_path, store)
    assert worker.world is not None
    _rich_biome(worker.world)
    research = _build_research()
    del research["short"]
    store.build_jobs["j1"] = _place_job(
        status="building",
        phase="approved",
        cap_usd=10.0,
        research=research,
    )
    assert worker._claim("j1")
    worker._place_build("j1", store.build_jobs["j1"])
    job = store.build_jobs["j1"]
    assert job["status"] == "building"
    assert job["waiting"] == "no_short"
    assert store.read_blob(group_path(A))[0] is None
    research["short"] = {"en": "Nuuksio", "fi": "Nuuksio", "fi_gen": "Nuuksion"}
    store.build_jobs["j1"]["research"] = research

    def stop_now(*args: Any, **kwargs: Any) -> None:
        raise _Off()

    monkeypatch.setattr(Worker, "run_item", stop_now)
    worker._place_build("j1", store.build_jobs["j1"])
    job = store.build_jobs["j1"]
    assert "waiting" not in job


def test_place_skip_refreshes_stale_checks_failed(tmp_path: Path) -> None:
    """#275.4: skipping a done item re-reads its checks, not its old label."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    store.build_jobs["j1"] = _place_job(
        progress=[
            {
                "id": "animal_lion",
                "status": "checking",
                "report": "review/j1/animal_lion/report.html",
                "cost_usd": 0.2,
                "checks_failed": ["oneshot-lion-animal-1"],
            }
        ]
    )
    checks = tmp_path / "jobs" / "j1" / "animal_lion" / "checks"
    checks.mkdir(parents=True)
    (checks / "oneshot-lion-animal-1.json").write_text(
        json.dumps({"results": [{"check": "animals"}], "ok": False})
    )
    (checks / "oneshot-lion-animal-2.json").write_text(
        json.dumps({"results": [{"check": "animals"}], "ok": True})
    )
    assert worker._claim("j1")
    job = store.build_jobs["j1"]
    worker._refresh_done_item("j1", job, "animal_lion")
    (entry,) = store.build_jobs["j1"]["progress"]
    assert "checks_failed" not in entry
    assert entry["status"] == "checking"
    assert entry["cost_usd"] == 0.2
    (checks / "oneshot-lion-edits-2.json").write_text(
        json.dumps({"results": [{"check": "schema"}], "ok": False})
    )
    worker._refresh_done_item("j1", store.build_jobs["j1"], "animal_lion")
    (entry,) = store.build_jobs["j1"]["progress"]
    assert entry["checks_failed"] == ["oneshot-lion-edits-2"]
    shutil.rmtree(tmp_path / "jobs" / "j1" / "animal_lion")
    worker._refresh_done_item("j1", store.build_jobs["j1"], "animal_lion")
    (entry,) = store.build_jobs["j1"]["progress"]
    assert entry["checks_failed"] == ["oneshot-lion-edits-2"]


def test_place_run_item_brief_names_the_place(tmp_path: Path, monkeypatch: Any) -> None:
    """#275.1: a place item's brief carries region_id, never prey."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    store.build_jobs["j1"] = _place_job(
        status="building", research={"region_id": "fi_nuuksio"}
    )
    assert worker._claim("j1")

    class _Pipe:
        def run(self, ctx: Any) -> dict[str, Any]:
            return {}

    monkeypatch.setattr("builder.build.item_pipeline", lambda **kwargs: _Pipe())
    item = {
        "id": "animal_lion",
        "kind": "animal",
        "what": "lion",
        "from": "fox",
        "estimate_usd": 0.42,
    }
    worker.run_item(
        "j1",
        store.build_jobs["j1"],
        A,
        {"items": [item], "world": "test", "region_id": "fi_nuuksio"},
        item,
        10.0,
    )
    brief = json.loads(
        (tmp_path / "jobs" / "j1" / "animal_lion" / "home.json").read_text()
    )
    assert brief["region_id"] == "fi_nuuksio"


def test_place_run_item_keeps_earlier_stage_cost(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#275.1: the entry counts stages an earlier call finished."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    store.build_jobs["j1"] = _place_job(
        status="building", research={"region_id": "fi_nuuksio"}
    )
    assert worker._claim("j1")

    class _Pipe:
        def run(self, ctx: Any) -> dict[str, Any]:
            return {}

    monkeypatch.setattr("builder.build.item_pipeline", lambda **kwargs: _Pipe())
    item = {
        "id": "animal_lion",
        "kind": "animal",
        "what": "lion",
        "from": "fox",
        "estimate_usd": 0.42,
    }
    item_dir = tmp_path / "jobs" / "j1" / "animal_lion"
    item_dir.mkdir(parents=True)
    (item_dir / "state.json").write_text(
        json.dumps(
            {
                "completed": ["research", "minimum"],
                "results": {
                    "research": {"cost_usd": 0.05},
                    "minimum": {"minset_file": "minset.json"},
                },
                "failed": None,
            }
        )
    )
    worker.run_item(
        "j1",
        store.build_jobs["j1"],
        A,
        {"items": [item], "world": "test"},
        item,
        10.0,
    )
    (entry,) = store.build_jobs["j1"]["progress"]
    assert entry["cost_usd"] == 0.05


def test_place_intake_sweeps_siblings_and_lays_reference(
    tmp_path: Path,
) -> None:
    """#275.1: each animal starts clean, with its prey as reference."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    item_dir = tmp_path / "jobs" / "j1" / "animal_lion"
    (item_dir / "animals").mkdir(parents=True)
    (item_dir / "animals" / "lion.json").write_text("{}")
    (item_dir / "animals" / "vole.json").write_text("{}")
    (item_dir / "models").mkdir(parents=True)
    (item_dir / "models" / "lion.json").write_text("{}")
    (item_dir / "models" / "vole.json").write_text("{}")
    (item_dir / "models" / "vole.glb").write_bytes(b"GLB")
    (item_dir / "models" / "previews").mkdir(parents=True)
    (item_dir / "models" / "previews" / "vole.png").write_bytes(b"PNG")
    sib = tmp_path / "jobs" / "j1" / "animal_vole" / "animals"
    sib.mkdir(parents=True)
    (sib / "vole.json").write_text(json.dumps({"id": "vole", "tags": ["vole"]}))
    job = {"research": {"animals": [{"id": "lion"}, {"id": "vole"}]}}
    item = {"id": "animal_lion", "kind": "animal", "what": "lion"}
    worker._lay_place_references("j1", job, item_dir, item)
    assert (item_dir / "animals" / "lion.json").is_file()
    assert not (item_dir / "animals" / "vole.json").exists()
    assert (item_dir / "models" / "lion.json").is_file()
    assert not (item_dir / "models" / "vole.json").exists()
    assert not (item_dir / "models" / "vole.glb").exists()
    assert not (item_dir / "models" / "previews" / "vole.png").exists()
    ref = json.loads((item_dir / "reference" / "animals" / "vole.json").read_text())
    assert ref["tags"] == ["vole"]
    assert not (item_dir / "reference" / "animals" / "lion.json").exists()
    cast = json.loads((item_dir / "place_cast.json").read_text())
    assert cast == ["lion", "vole"]


def test_place_orders_hunters_after_their_prey(tmp_path: Path) -> None:
    """#275.1: prey-first order from the items' own research."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    lion_dir = tmp_path / "jobs" / "j1" / "animal_lion"
    lion_dir.mkdir(parents=True)
    (lion_dir / "research.json").write_text(
        json.dumps(
            {
                "main_food": {"kind": "prey", "name": "Vole"},
                "other_foods": [{"kind": "berries", "name": "Bilberry"}],
            }
        )
    )
    vole_dir = tmp_path / "jobs" / "j1" / "animal_vole"
    vole_dir.mkdir(parents=True)
    (vole_dir / "research.json").write_text(
        json.dumps(
            {
                "main_food": {"kind": "grass", "name": "Grass"},
                "other_foods": [],
            }
        )
    )
    items = [
        {"id": "animal_lion", "kind": "animal", "what": "lion"},
        {"id": "animal_vole", "kind": "animal", "what": "vole"},
        {"id": "plant_pine", "kind": "plant", "what": "pine"},
    ]
    ordered = worker._place_order_species("j1", items)
    assert [i["id"] for i in ordered] == [
        "animal_vole",
        "animal_lion",
        "plant_pine",
    ]


def test_place_research_pass_runs_before_making(tmp_path: Path) -> None:
    """#275.1: research and minimum land before any animal is made."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    store.build_jobs["j1"] = _place_job(
        status="building",
        research={"region_id": "fi_nuuksio", "tiles": [A], "biome": "boreal_forest"},
    )
    assert worker._claim("j1")
    answer = {
        "main_food": {"name": "Vole", "kind": "prey", "size_cm": 30},
        "other_foods": [{"name": "Bilberries", "kind": "berries"}],
        "surroundings": [
            {"name": "Norway spruce", "kind": "tree"},
            {"name": "Bilberry", "kind": "plant", "food_kind": "berries"},
            {"name": "Timothy grass", "kind": "plant", "food_kind": "grass"},
        ],
        "size": {"length_cm": 110, "largest_cm": 130, "weight_kg": 22},
        "year": {
            "winter": "active",
            "shelter": "Sleeps in a den.",
            "young": "Born in spring.",
            "food": "Hunts all year.",
        },
        "sources": ["https://example.org/lion"],
    }
    worker.chat = _ResearchRespond([json.dumps(answer)])
    item = {
        "id": "animal_lion",
        "kind": "animal",
        "what": "lion",
        "from": "fox",
        "estimate_usd": 0.42,
    }
    plan = {"items": [item], "world": "test", "region_id": "fi_nuuksio"}
    assert worker._place_research_species(
        "j1", store.build_jobs["j1"], A, plan, [item], 10.0
    )
    item_dir = tmp_path / "jobs" / "j1" / "animal_lion"
    saved = json.loads((item_dir / "research.json").read_text())
    assert saved["main_food"]["name"] == "Vole"
    minset = json.loads((item_dir / "minset.json").read_text())
    assert minset["prey"] is None
    assert "place item never makes prey" in minset["prey_reason"]
    state = json.loads((item_dir / "state.json").read_text())
    assert state["completed"] == ["research", "minimum"]
    assert state["results"]["research"]["cost_usd"] > 0


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
