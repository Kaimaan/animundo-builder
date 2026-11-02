"""Place jobs, stage A: research, one stop, Kai's yes. No network."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fake_store import FakeStore
from fake_world import boreal_3x3, make_world, write_grids

from builder.config import Config
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
    assert job["price"] == 0.50
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
    assert job["price"] == 0.0
    assert worker.chat.calls == 0


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
