"""The worker: it plans requests, queues jobs, alerts loudly. No Firebase."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fake_store import FakeStore
from fake_world import boreal_3x3, make_world, write_grids

from builder.config import Config
from builder.worker import Listener, Worker
from builder.worker_store import STORE_METHODS, FirebaseStore


def _worker(tmp_path: Path, store: FakeStore) -> Worker:
    return Worker(
        store=store,
        jobs_dir=tmp_path / "jobs",
        world=tmp_path / "world",
        config=Config(ledger_path=str(tmp_path / "ledger.json")),
    )


def test_stores_expose_every_worker_method() -> None:
    assert len(STORE_METHODS) == len(set(STORE_METHODS))
    for name in STORE_METHODS:
        assert callable(getattr(FakeStore, name, None)), f"fake lacks {name}"
        assert callable(getattr(FirebaseStore, name, None)), f"real lacks {name}"


def test_note_request_queues_once(tmp_path: Path) -> None:
    worker = _worker(tmp_path, FakeStore())
    assert worker.note_request("n_413_296", {}) is True
    worker.in_flight.add(("request", "n_413_296"))
    assert worker.note_request("n_413_296", {}) is False


def test_note_build_job_skips_done_and_in_flight(tmp_path: Path) -> None:
    worker = _worker(tmp_path, FakeStore())
    assert worker.note_build_job("j1", {"status": "queued"}) is True
    assert worker.note_build_job("j2", {"status": "published"}) is False
    assert worker.note_build_job("j3", {"status": "failed"}) is False
    worker.in_flight.add(("build_job", "j1"))
    assert worker.note_build_job("j1", {"status": "queued"}) is False


def test_claim_build_job_leaves_one_winner() -> None:
    store = FakeStore()
    store.build_jobs["j1"] = {"status": "queued"}
    assert store.claim_build_job("j1", "w1") is True
    assert store.claim_build_job("j1", "w2") is False
    assert store.build_jobs["j1"]["worker_id"] == "w1"


def test_claim_build_job_needs_queued() -> None:
    store = FakeStore()
    assert store.claim_build_job("missing", "w1") is False
    store.build_jobs["j1"] = {"status": "building"}
    assert store.claim_build_job("j1", "w1") is False


def _planned_worker(tmp_path: Path, store: FakeStore, monkeypatch: Any) -> Worker:
    def fake_fetch(tile: str, candidates: list[dict[str, Any]]) -> dict[str, float]:
        return {"fox": 5.0, "vole": 20.0}

    monkeypatch.setattr("builder.plan.fetch_smoothed", fake_fetch)
    world = make_world(tmp_path)
    grid_dir = write_grids(tmp_path / "grids", boreal_3x3())
    worker = _worker(tmp_path, store)
    worker.world = world
    worker.grid_dir = grid_dir
    worker.world_commit = "abc123"
    worker.species = {
        "place": "Boreal forest in southern Finland",
        "present": ["fox", "vole"],
        "name_en": "Nuuksio forest",
        "name_fi": "Nuuksion metsä",
    }
    return worker


def test_handle_request_plans_into_store(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    store = FakeStore()
    store.requests["n_413_478"] = {"tile": "n_413_478"}
    worker = _planned_worker(tmp_path, store, monkeypatch)
    worker.handle_request("n_413_478")
    plan = store.plans["n_413_478"]
    assert plan["code_only"] is True
    assert plan["world"] == "abc123"
    assert "planned code_only=True" in capsys.readouterr().out


def test_handle_request_skips_when_off(tmp_path: Path, monkeypatch: Any) -> None:
    store = FakeStore()
    store.enabled = False
    store.requests["n_413_478"] = {"tile": "n_413_478"}
    worker = _planned_worker(tmp_path, store, monkeypatch)
    worker.handle_request("n_413_478")
    assert "n_413_478" not in store.plans


def test_handle_request_alerts_when_unplannable(
    tmp_path: Path, monkeypatch: Any
) -> None:
    store = FakeStore()
    store.requests["xx"] = {"tile": "xx"}
    worker = _planned_worker(tmp_path, store, monkeypatch)
    worker.handle_request("xx")
    assert "xx" not in store.plans
    assert store.alerts[-1]["kind"] == "plan_failed"


def test_sync_world_replans_on_move(tmp_path: Path, monkeypatch: Any) -> None:
    store = FakeStore()
    store.requests["n_413_478"] = {"tile": "n_413_478"}
    worker = _planned_worker(tmp_path, store, monkeypatch)
    worker.handle_request("n_413_478")
    assert store.plans["n_413_478"]["world"] == "abc123"
    worker.sync_world("def456")
    assert worker.world_commit == "def456"
    assert store.plans["n_413_478"]["world"] == "def456"


def test_sync_world_ignores_same_and_unknown(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    store = FakeStore()
    worker = _planned_worker(tmp_path, store, monkeypatch)
    worker.sync_world("abc123")
    worker.sync_world("unknown")
    assert worker.world_commit == "abc123"
    assert "re-planning" not in capsys.readouterr().out


def test_dispatch_alerts_on_crash(tmp_path: Path) -> None:
    store = FakeStore()
    worker = _worker(tmp_path, store)

    def boom(tile: str) -> None:
        raise RuntimeError("kaboom")

    worker.handle_request = boom  # type: ignore[method-assign]
    listener = Listener(worker=worker, store=store)
    listener._dispatch("request", "n_413_296")
    assert ("request", "n_413_296") not in worker.in_flight
    assert store.alerts[-1]["kind"] == "request_failed"


def test_handle_build_job_leaves_it_queued(tmp_path: Path) -> None:
    store = FakeStore()
    store.build_jobs["j1"] = {"status": "queued", "tile": "n_413_296"}
    worker = _worker(tmp_path, store)
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "queued"
    assert len(store.pending_build_jobs()) == 1


def test_dispatch_runs_and_releases(tmp_path: Path) -> None:
    worker = _worker(tmp_path, FakeStore())
    listener = Listener(worker=worker, store=FakeStore())
    listener._dispatch("request", "n_413_296")
    assert ("request", "n_413_296") not in worker.in_flight
    listener._dispatch("build_job", "j1")
    assert ("build_job", "j1") not in worker.in_flight


def test_dispatch_survives_unknown_kinds(tmp_path: Path, capsys: Any) -> None:
    worker = _worker(tmp_path, FakeStore())
    listener = Listener(worker=worker, store=FakeStore())
    listener._dispatch("dragon", "d1")
    assert "unknown kind" in capsys.readouterr().err
    assert ("dragon", "d1") not in worker.in_flight


def test_listen_reports_a_dead_backend(tmp_path: Path, monkeypatch, capsys) -> None:
    from builder import worker_store
    from builder.worker import listen

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise worker_store.StoreError("no credentials")

    monkeypatch.setattr(worker_store, "FirebaseStore", boom)
    code = listen(
        Config(ledger_path=str(tmp_path / "ledger.json")),
        tmp_path / "jobs",
        tmp_path / "world",
        "demo",
    )
    assert code == 1
    assert "no credentials" in capsys.readouterr().err


def _flow_worker(
    tmp_path: Path, store: FakeStore, tile_cells: dict[str, Any]
) -> Worker:
    from fake_world import make_world, write_grids

    worker = _worker(tmp_path, store)
    worker.world = make_world(tmp_path)
    worker.grid_dir = write_grids(tmp_path / "grids", tile_cells)
    worker.world_commit = "abc123"
    worker.species = {
        "place": "Boreal forest in southern Finland",
        "present": ["fox", "vole"],
        "name_en": "Nuuksio forest",
        "name_fi": "Nuuksion metsä",
    }
    return worker


def _code_plan(tile: str) -> dict[str, Any]:
    return {
        "format": 1,
        "tile": tile,
        "code_only": True,
        "items": [],
        "improve": [
            {
                "id": "improve_lion",
                "kind": "animal",
                "what": "lion",
                "from": "fox",
                "estimate_usd": 0.15,
            }
        ],
        "estimate_usd": 0.15,
        "name": {"en": "Boreal forest", "fi": "Havumetsä"},
        "coming": ["improve_lion"],
        "world": "abc123",
        "planned_at": "2026-10-28T00:00:00+00:00",
    }


def _code_job() -> dict[str, Any]:
    return {
        "format": 1,
        "tile": "n_413_478",
        "budget_usd": 0.0,
        "kind": "code",
        "status": "queued",
        "items": ["improve_lion"],
        "visibility": "public",
        "created_by": "policy",
        "progress": [],
        "cost_usd": 0.0,
    }


def test_code_job_publishes_with_no_model_call(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#118.10: a code request becomes ready with no live call."""
    import builder.species

    def no_model(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a recorded answer makes no live call")

    monkeypatch.setattr(builder.species, "ask", no_model)
    store = FakeStore()
    store.plans["n_413_478"] = _code_plan("n_413_478")
    store.build_jobs["j1"] = _code_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["cost_usd"] == 0.0
    assert job["worker_id"] == worker.worker_id
    assert "tiles/v1/ease2_n25/51_59.json" in store.blobs
    assert "tiles/v1/index.json" in store.blobs


def test_code_job_fails_when_tile_blocks(tmp_path: Path, monkeypatch: Any) -> None:
    store = FakeStore()
    store.plans["n_413_478"] = _code_plan("n_413_478")
    store.build_jobs["j1"] = _code_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.species = {
        "place": "Boreal forest in southern Finland",
        "present": [],
        "name_en": "Nuuksio forest",
        "name_fi": "Nuuksion metsä",
    }
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "failed"
    assert job["error"] == "no_animal"
    assert store.alerts[-1]["kind"] == "job_failed"


def test_pending_request_builds_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    """#118.10: a pending or turned-down request builds nothing."""
    store = FakeStore()
    store.requests["n_413_478"] = {"tile": "n_413_478", "turned_down": "far"}
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    monkeypatch.setattr("builder.plan.fetch_smoothed", lambda tile, c: {})
    worker.handle_request("n_413_478")
    assert "n_413_478" in store.plans
    assert store.build_jobs == {}
    assert store.blobs == {}


def test_request_alerts_plan_failed_when_gbif_is_down(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A GBIF outage is a plan failure, loud and unretried."""
    store = FakeStore()
    store.requests["n_413_478"] = {"tile": "n_413_478"}

    def down(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("GBIF timed out")

    monkeypatch.setattr("builder.worker.plan_tile", down)
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_request("n_413_478")
    assert "n_413_478" not in store.plans
    assert store.alerts[-1]["kind"] == "plan_failed"


def test_plant_item_builds_through_stages(tmp_path: Path, monkeypatch: Any) -> None:
    """T76 finished: a plant item briefs, scaffolds and checks."""
    import json

    from builder import build as build_mod

    store = FakeStore()
    plan = _code_plan("n_413_478")
    plan["code_only"] = False
    plan["items"] = [
        {
            "id": "plant_cloudberry",
            "kind": "plant",
            "what": "cloudberry",
            "from": "bilberry",
            "estimate_usd": 0.02,
        }
    ]
    plan["improve"] = []
    store.plans["n_413_478"] = plan
    store.build_jobs["j1"] = {
        "format": 1,
        "tile": "n_413_478",
        "budget_usd": 8.0,
        "kind": "build",
        "status": "queued",
        "items": ["plant_cloudberry"],
        "visibility": "public",
        "created_by": "admin",
        "progress": [],
        "cost_usd": 0.0,
    }
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    home_path = worker.world / "homes" / "boreal_forest.json"
    home = json.loads(home_path.read_text())
    home["food"] = [{"kind": "berries", "names": ["cloudberry"]}]
    home_path.write_text(json.dumps(home))

    made: list[str] = []

    class Pipe:
        def run(self, ctx: Any) -> dict[str, Any]:
            item_dir = tmp_path / "jobs" / "j1" / "plant_cloudberry"
            models = item_dir / "models" / "plants"
            models.mkdir(parents=True)
            (models / "cloudberry.json").write_text(json.dumps({"id": "cloudberry"}))
            made.append("cloudberry")
            return {}

    monkeypatch.setattr(build_mod, "item_pipeline", lambda **k: Pipe())
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert made == ["cloudberry"]
    assert job["status"] == "checking"
    assert job["progress"] == [
        {
            "id": "plant_cloudberry",
            "status": "checking",
            "report": job["progress"][0]["report"],
            "cost_usd": 0.0,
        }
    ]
    item_dir = tmp_path / "jobs" / "j1" / "plant_cloudberry"
    brief = json.loads((item_dir / "home.json").read_text())
    assert brief["plant"] == "cloudberry"
    assert brief["names"] == ["cloudberry"]
    region = json.loads((item_dir / "regions" / "zz_work.json").read_text())
    assert region["ground_cover"]["plants"] == [
        {"model": "cloudberry", "names": ["cloudberry"]}
    ]
    survey = json.loads((item_dir / "survey.json").read_text())
    assert survey["facts"] == [{"topic": "plant", "text": "cloudberry grows here"}]
    assert (
        store.blobs["review/j1/plant_cloudberry/models/plants/cloudberry.json"]
        == b'{"id": "cloudberry"}'
    )


def test_private_job_writes_judge_preview(tmp_path: Path, monkeypatch: Any) -> None:
    """T84: a private job's checked items become its judge's pack."""
    store = FakeStore()
    plan = _code_plan("n_413_478")
    plan["code_only"] = False
    plan["items"] = [
        {
            "id": "animal_lion",
            "kind": "animal",
            "what": "lion",
            "from": "fox",
            "estimate_usd": 0.15,
        }
    ]
    plan["improve"] = []
    store.plans["n_413_478"] = plan
    store.build_jobs["j1"] = {
        "format": 1,
        "tile": "n_413_478",
        "budget_usd": 8.0,
        "kind": "build",
        "status": "queued",
        "items": ["animal_lion"],
        "visibility": "private",
        "created_by": "policy",
        "progress": [],
        "cost_usd": 0.0,
        "private_to": "judge1",
    }
    worker = _flow_worker(tmp_path, store, boreal_3x3())

    def fake_stages(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
    ) -> None:
        folder = item_dir / "animals"
        folder.mkdir(parents=True)
        (folder / "lion.json").write_bytes(b'{"id": "lion"}')

    monkeypatch.setattr(worker, "_run_stages", fake_stages)
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "checking"
    assert job["preview"] == {"pack": "previews/j1/1/"}
    assert "previews/j1/1/manifest.json" in store.blobs
    assert "previews/j1/1/animals/lion.json" in store.blobs


def test_build_job_fails_without_its_tile(tmp_path: Path) -> None:
    store = FakeStore()
    store.plans["n_413_478"] = _code_plan("n_413_478")
    job = _code_job()
    del job["tile"]
    store.build_jobs["j1"] = job
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "failed"
    assert store.build_jobs["j1"]["error"] == "no_tile"


def test_build_job_fails_without_its_plan(tmp_path: Path) -> None:
    store = FakeStore()
    store.build_jobs["j1"] = _code_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "failed"
    assert store.build_jobs["j1"]["error"] == "no_plan"


def test_build_job_stops_at_zero_budget(tmp_path: Path) -> None:
    """#118.10: a judge build stops at its cap."""
    store = FakeStore()
    plan = _code_plan("n_401_434")
    plan["code_only"] = False
    plan["items"] = [
        {
            "id": "home_774",
            "kind": "home",
            "what": "774",
            "from": "tundra",
            "estimate_usd": 0.02,
        }
    ]
    plan["improve"] = []
    store.plans["n_401_434"] = plan
    store.build_jobs["j1"] = {
        "format": 1,
        "tile": "n_401_434",
        "budget_usd": 0.0,
        "kind": "build",
        "status": "queued",
        "items": ["home_774"],
        "visibility": "private",
        "created_by": "policy",
        "progress": [],
        "cost_usd": 0.0,
        "private_to": "judge1",
    }
    worker = _flow_worker(
        tmp_path, store, {"401,434": [774, 1.0, None, 0.0, 11, 1.0, 0.0, 200.0]}
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "failed"
    assert job["error"] == "budget"
    assert job["progress"] == []
    assert store.blobs == {}
    assert "preview" not in job


def test_failed_item_does_not_stop_others(tmp_path: Path, monkeypatch: Any) -> None:
    """#118.10: a failed item doesn't stop the others."""
    store = FakeStore()
    plan = _code_plan("n_401_434")
    plan["code_only"] = False
    plan["items"] = [
        {
            "id": "home_774",
            "kind": "home",
            "what": "774",
            "from": "tundra",
            "estimate_usd": 0.02,
        },
        {
            "id": "animal_lion",
            "kind": "animal",
            "what": "lion",
            "from": "fox",
            "estimate_usd": 0.15,
        },
    ]
    plan["improve"] = []
    store.plans["n_401_434"] = plan
    store.build_jobs["j1"] = {
        "format": 1,
        "tile": "n_401_434",
        "budget_usd": 8.0,
        "kind": "build",
        "status": "queued",
        "items": ["home_774", "animal_lion"],
        "visibility": "public",
        "created_by": "admin",
        "progress": [],
        "cost_usd": 0.0,
    }
    calls: list[str] = []
    worker = _flow_worker(
        tmp_path, store, {"401,434": [774, 1.0, None, 0.0, 11, 1.0, 0.0, 200.0]}
    )

    def flaky(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
    ) -> None:
        calls.append(item["id"])
        if item["id"] == "home_774":
            raise RuntimeError("boom")

    monkeypatch.setattr(worker, "_run_stages", flaky)
    worker.handle_build_job("j1")
    assert calls == ["home_774", "animal_lion"]
    assert store.build_jobs["j1"]["status"] == "checking"
    by_id = {e["id"]: e["status"] for e in store.build_jobs["j1"]["progress"]}
    assert by_id == {"home_774": "failed", "animal_lion": "checking"}


def test_item_needing_a_failed_home_is_skipped(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#135.3: no home, no start: skipped with needs, job names the home."""
    store = FakeStore()
    plan = _code_plan("n_401_434")
    plan["code_only"] = False
    plan["items"] = [
        {
            "id": "home_774",
            "kind": "home",
            "what": "774",
            "from": "tundra",
            "estimate_usd": 0.02,
        },
        {
            "id": "animal_fox",
            "kind": "animal",
            "what": "fox",
            "from": "fox",
            "estimate_usd": 0.15,
        },
    ]
    plan["improve"] = []
    store.plans["n_401_434"] = plan
    store.build_jobs["j1"] = {
        "format": 1,
        "tile": "n_401_434",
        "budget_usd": 8.0,
        "kind": "build",
        "status": "queued",
        "items": ["home_774", "animal_fox"],
        "visibility": "public",
        "created_by": "admin",
        "progress": [],
        "cost_usd": 0.0,
    }
    worker = _flow_worker(
        tmp_path, store, {"401,434": [774, 1.0, None, 0.0, 11, 1.0, 0.0, 200.0]}
    )
    real_run_stages = worker._run_stages

    def flaky(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
    ) -> None:
        if item["id"] == "home_774":
            raise RuntimeError("boom")
        return real_run_stages(ctx, job_id, tile, plan, item, item_dir)

    monkeypatch.setattr(worker, "_run_stages", flaky)
    worker.handle_build_job("j1")
    by_id = {e["id"]: e["status"] for e in store.build_jobs["j1"]["progress"]}
    assert by_id == {"home_774": "failed", "animal_fox": "skipped"}
    report = (tmp_path / "jobs" / "j1" / "animal_fox" / "report.html").read_text()
    assert "needs home_774" in report
    assert store.build_jobs["j1"]["status"] == "failed"
    assert store.build_jobs["j1"]["error"] == "home_774"


def test_close_item_alerts_on_spending_crossings(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from builder.pipeline import BudgetTracker

    store = FakeStore()
    store.build_jobs["j1"] = {
        "area": "a",
        "budget_usd": 10.0,
        "kind": "build",
        "status": "building",
        "items": ["animal_lion"],
        "progress": [],
        "cost_usd": 4.0,
    }
    worker = _worker(tmp_path, store)
    item_dir = tmp_path / "jobs" / "j1" / "animal_lion"
    item_dir.mkdir(parents=True)
    tracker = BudgetTracker(budget_usd=10.0)
    tracker.spent_usd = 9.0
    ctx = SimpleNamespace(tracker=tracker)
    worker._close_item("j1", {"id": "animal_lion"}, item_dir, ctx, 0.0, 4.0, 10.0, None)
    job = store.build_jobs["j1"]
    assert job["cost_usd"] == 13.0
    assert [a["pct"] for a in store.alerts] == [50, 80, 100]


def test_poll_resumes_builds_on_reenable(tmp_path: Path, monkeypatch: Any) -> None:
    store = FakeStore()
    store.build_jobs["j1"] = {
        "area": "a",
        "budget_usd": 8.0,
        "kind": "build",
        "status": "building",
        "items": [],
        "progress": [],
        "cost_usd": 0.0,
    }
    worker = _worker(tmp_path, store)
    monkeypatch.setattr(Worker, "pull_world", lambda self: False)
    store.enabled = False
    assert worker.poll() == []
    store.enabled = True
    assert worker.poll() == ["j1"]
    assert worker.poll() == []


def test_guard_stops_pipeline_at_next_stage(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from builder.pipeline import OffSwitch, Pipeline

    ran: list[str] = []

    class Step:
        def __init__(self, name: str) -> None:
            self.name = name

        def run(self, ctx: Any) -> dict[str, Any]:
            ran.append(self.name)
            return {}

    ctx = SimpleNamespace(
        job_dir=tmp_path, client=None, open_client=None, check_budget=lambda: None
    )

    def guard(stage: str) -> None:
        if stage == "b":
            raise OffSwitch("off")

    pipeline = Pipeline(
        [Step("a"), Step("b")],  # type: ignore[list-item]
        state_path=tmp_path / "state.json",
        guard=guard,
    )
    try:
        pipeline.run(ctx)
    except OffSwitch:
        pass
    assert ran == ["a"]


def _build_job(tile: str, items: list[str], **over: Any) -> dict[str, Any]:
    job = {
        "format": 1,
        "tile": tile,
        "budget_usd": 8.0,
        "kind": "build",
        "status": "queued",
        "items": items,
        "visibility": "public",
        "created_by": "admin",
        "progress": [],
        "cost_usd": 0.0,
    }
    job.update(over)
    return job


def _file_item(item_id: str, kind: str, what: str, ref: str) -> dict[str, Any]:
    return {
        "id": item_id,
        "kind": kind,
        "what": what,
        "from": ref,
        "estimate_usd": 0.15,
    }


def test_build_job_stops_second_item_at_remaining_budget(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#133.3: each item gets what's left; the job never overspends."""
    from builder.pipeline import BudgetExceeded

    store = FakeStore()
    plan = _code_plan("n_413_478")
    plan["code_only"] = False
    plan["items"] = [
        _file_item("animal_lion", "animal", "lion", "fox"),
        _file_item("animal_tiger", "animal", "tiger", "fox"),
    ]
    plan["improve"] = []
    store.plans["n_413_478"] = plan
    store.build_jobs["j1"] = _build_job(
        "n_413_478", ["animal_lion", "animal_tiger"], budget_usd=1.0
    )
    worker = _flow_worker(tmp_path, store, boreal_3x3())

    def pricey(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
    ) -> None:
        # One $0.60 call, reserved the way MetaClient reserves it:
        # refused when the worst case no longer fits what's left.
        if ctx.tracker.spent_usd + 0.6 > ctx.tracker.budget_usd:
            raise BudgetExceeded("a $0.60 call no longer fits")
        ctx.tracker.spent_usd += 0.6

    monkeypatch.setattr(worker, "_run_stages", pricey)
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["cost_usd"] == 0.6
    assert job["cost_usd"] <= job["budget_usd"]
    assert job["error"] == "budget"
    by_id = {e["id"]: e["status"] for e in job["progress"]}
    assert by_id == {"animal_lion": "checking", "animal_tiger": "failed"}


def _fake_animal_stages(worker: Worker, monkeypatch: Any, tmp_path: Path) -> None:
    def fake_stages(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
    ) -> None:
        folder = item_dir / "animals"
        folder.mkdir(parents=True)
        (folder / f"{item['what']}.json").write_bytes(
            b'{"id": "' + item["what"].encode() + b'"}'
        )

    monkeypatch.setattr(worker, "_run_stages", fake_stages)


def test_review_job_publishes_shipped_items(tmp_path: Path, monkeypatch: Any) -> None:
    """#133.6: a reviewed job ships its items and publishes the tile."""
    store = FakeStore()
    plan = _code_plan("n_413_478")
    plan["code_only"] = False
    plan["items"] = [_file_item("animal_lion", "animal", "lion", "fox")]
    plan["improve"] = []
    store.plans["n_413_478"] = plan
    store.build_jobs["j1"] = _build_job("n_413_478", ["animal_lion"])
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    _fake_animal_stages(worker, monkeypatch, tmp_path)
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "checking"
    (worker.world / "animals" / "lion.json").write_text('{"id": "lion"}')
    store.build_jobs["j1"]["review"] = {"animal_lion": {"result": "shipped"}}
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert [e["id"] for e in job["progress"]] == ["animal_lion"]
    assert "tiles/v1/ease2_n25/51_59.json" in store.blobs


def test_review_job_drops_not_accepted_and_ships_the_rest(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#133.6: a turned-down item drops while the others ship."""
    store = FakeStore()
    plan = _code_plan("n_413_478")
    plan["code_only"] = False
    plan["items"] = [
        _file_item("animal_lion", "animal", "lion", "fox"),
        _file_item("animal_tiger", "animal", "tiger", "fox"),
    ]
    plan["improve"] = []
    store.plans["n_413_478"] = plan
    store.build_jobs["j1"] = _build_job("n_413_478", ["animal_lion", "animal_tiger"])
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    _fake_animal_stages(worker, monkeypatch, tmp_path)
    worker.handle_build_job("j1")
    (worker.world / "animals" / "lion.json").write_text('{"id": "lion"}')
    store.build_jobs["j1"]["review"] = {
        "animal_lion": {"result": "shipped"},
        "animal_tiger": {"result": "not_accepted"},
    }
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert [e["id"] for e in job["progress"]] == ["animal_lion", "animal_tiger"]


def test_private_preview_is_rewritten_after_a_review(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#133.6: a private job's preview moves on as reviews land."""
    store = FakeStore()
    plan = _code_plan("n_413_478")
    plan["code_only"] = False
    plan["items"] = [_file_item("animal_lion", "animal", "lion", "fox")]
    plan["improve"] = []
    store.plans["n_413_478"] = plan
    store.build_jobs["j1"] = _build_job(
        "n_413_478",
        ["animal_lion"],
        visibility="private",
        private_to="judge1",
    )
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    _fake_animal_stages(worker, monkeypatch, tmp_path)
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["preview"] == {"pack": "previews/j1/1/"}
    store.build_jobs["j1"]["review"] = {"animal_lion": {"result": "shipped"}}
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "checking"  # the world lacks lion.json yet
    assert job["preview"] == {"pack": "previews/j1/2/"}
    assert "previews/j1/2/manifest.json" in store.blobs


def test_claim_snapshot_survives_a_replan(tmp_path: Path, monkeypatch: Any) -> None:
    """#133.5: a re-plan to code_only strands no claimed job."""
    import json

    store = FakeStore()
    plan = _code_plan("n_413_478")
    plan["code_only"] = False
    plan["items"] = [_file_item("animal_lion", "animal", "lion", "fox")]
    plan["improve"] = []
    store.plans["n_413_478"] = plan
    store.build_jobs["j1"] = _build_job("n_413_478", ["animal_lion"])
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    _fake_animal_stages(worker, monkeypatch, tmp_path)
    worker.handle_build_job("j1")
    snapshot = json.loads((tmp_path / "jobs" / "j1" / "plan.json").read_text())
    assert [i["id"] for i in snapshot["items"]] == ["animal_lion"]
    store.plans["n_413_478"] = _code_plan("n_413_478")  # the world moved on
    (worker.world / "animals" / "lion.json").write_text('{"id": "lion"}')
    store.build_jobs["j1"]["review"] = {"animal_lion": {"result": "shipped"}}
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "published"


def test_plant_item_ships_and_publishes(tmp_path: Path, monkeypatch: Any) -> None:
    """#133.4: a shipped plant's file in the world publishes the tile."""
    import json

    from builder import build as build_mod

    store = FakeStore()
    plan = _code_plan("n_413_478")
    plan["code_only"] = False
    plan["items"] = [
        {
            "id": "plant_cloudberry",
            "kind": "plant",
            "what": "cloudberry",
            "from": "bilberry",
            "estimate_usd": 0.02,
        }
    ]
    plan["improve"] = []
    store.plans["n_413_478"] = plan
    store.build_jobs["j1"] = _build_job("n_413_478", ["plant_cloudberry"])
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    home_path = worker.world / "homes" / "boreal_forest.json"
    home = json.loads(home_path.read_text())
    home["food"] = [{"kind": "grass"}, {"kind": "berries", "names": ["cloudberry"]}]
    home_path.write_text(json.dumps(home))

    class Pipe:
        def run(self, ctx: Any) -> dict[str, Any]:
            return {}

    monkeypatch.setattr(build_mod, "item_pipeline", lambda **k: Pipe())
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "checking"
    (worker.world / "models" / "plants" / "cloudberry.json").write_text(
        '{"id": "cloudberry"}'
    )
    store.build_jobs["j1"]["review"] = {"plant_cloudberry": {"result": "shipped"}}
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "published"


def test_code_job_fails_on_excluded_tile(tmp_path: Path, monkeypatch: Any) -> None:
    """#133.7: a code job on an excluded cell fails, never publishes."""
    import json

    store = FakeStore()
    store.plans["n_413_478"] = _code_plan("n_413_478")
    store.build_jobs["j1"] = _code_job()
    (tmp_path / "exclusions.json").write_text(json.dumps({"tiles": ["n_413_478"]}))
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.builder_root = tmp_path
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "failed"
    assert job["error"] == "excluded"
    assert store.alerts[-1]["kind"] == "job_failed"


def test_start_line_reports_deploy_facts(tmp_path: Path) -> None:
    """#133.2: the start log names the host, ledger, world and switch."""
    import json

    from builder.worker import start_line

    store = FakeStore()
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"jobs": {}, "days": {}, "total": 14.5}))
    (tmp_path / "ledger-baseten.json").write_text(
        json.dumps({"jobs": {}, "days": {}, "total": 3.25})
    )
    config = Config(open_model_host="baseten", ledger_path=str(ledger))
    line = start_line(config, store, tmp_path / "world")
    assert "\n" not in line
    assert "open_host=baseten" in line
    assert f"ledger={ledger}" in line
    assert "meta_total=$14.50" in line
    assert "baseten_total=$3.25" in line
    assert "world=unknown" in line
    assert "enabled=true" in line


def test_listen_prints_the_start_line(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    """#133.2: `builder worker --listen` opens with the start line."""
    from builder import worker as worker_mod
    from builder import worker_store
    from builder.worker import listen

    started: list[str] = []

    class QuietListener:
        def __init__(self, worker: Any, store: Any, poll_seconds: float = 0) -> None:
            started.append("listening")

        def start(self) -> None:
            pass

    monkeypatch.setattr(worker_store, "FirebaseStore", lambda *a, **k: FakeStore())
    monkeypatch.setattr(worker_mod, "Listener", QuietListener)
    code = listen(
        Config(ledger_path=str(tmp_path / "ledger.json")),
        tmp_path / "jobs",
        tmp_path / "world",
        "demo",
    )
    assert code == 0
    assert started == ["listening"]
    assert "worker start:" in capsys.readouterr().out


def test_no_player_field_anywhere(tmp_path: Path, monkeypatch: Any) -> None:
    """#118.10: no player field anywhere the worker writes."""
    import json

    store = FakeStore()
    store.requests["n_413_478"] = {
        "tile": "n_413_478",
        "uid": "kid-1",
        "player": {"device": "x", "ip": "1.2.3.4"},
        "email": "kid@example.org",
    }
    store.plans["n_413_478"] = _code_plan("n_413_478")
    store.build_jobs["j1"] = _code_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    monkeypatch.setattr("builder.plan.fetch_smoothed", lambda tile, c: {})
    worker.handle_request("n_413_478")
    worker.handle_build_job("j1")

    def keys_below(data: Any) -> set[str]:
        found = set()
        if isinstance(data, dict):
            for key, value in data.items():
                found.add(str(key))
                found |= keys_below(value)
        elif isinstance(data, list):
            for value in data:
                found |= keys_below(value)
        return found

    written = [
        store.plans["n_413_478"],
        store.build_jobs["j1"],
        *[json.loads(v.decode("utf-8")) for v in store.blobs.values()],
    ]
    leaked = keys_below(written) & {
        "uid",
        "player",
        "device",
        "ip",
        "email",
        "child",
        "kid",
        "parent",
        "family",
        "address",
        "phone",
    }
    assert leaked == set()
