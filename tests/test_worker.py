"""The worker: it plans requests, queues jobs, alerts loudly. No Firebase."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
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


def test_job_without_status_counts_as_queued(tmp_path: Path, monkeypatch: Any) -> None:
    """#164: a status-less job is claimed, built, and reconciled."""
    store = FakeStore()
    job = _enrich_job()
    del job["status"]
    store.build_jobs["j1"] = job
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.worker_id = "wA"

    def fake_stages(*args: Any, **kwargs: Any) -> None:
        (args[5] / "animals").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worker, "_run_stages", fake_stages)
    monkeypatch.setattr(Worker, "pull_world", lambda self: False)
    store.enabled = False
    assert worker.poll() == []
    store.enabled = True
    assert ("build_job", "j1") in worker.poll()
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "checking"
    assert store.build_jobs["j1"]["progress"][0]["id"] == "enrich_lion"


def _owned_job(status: str = "building", owner: str = "wA") -> dict[str, Any]:
    job = _enrich_job(status=status)
    job["worker_id"] = owner
    job["lease_until"] = "2999-01-01T00:00:00+00:00"
    return job


def test_second_worker_runs_no_stages_for_a_live_job(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#155: a job owned live is untouched by another worker."""
    store = FakeStore()
    store.build_jobs["j1"] = _owned_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.worker_id = "wB"
    ran: list[str] = []

    def fake_stages(*args: Any, **kwargs: Any) -> None:
        ran.append("stages")

    monkeypatch.setattr(worker, "_run_stages", fake_stages)
    worker.handle_build_job("j1")
    assert ran == []
    assert store.build_jobs["j1"]["worker_id"] == "wA"
    assert store.build_jobs["j1"]["status"] == "building"


def test_expired_lease_can_be_recovered(tmp_path: Path, monkeypatch: Any) -> None:
    """#155: a dead owner's job builds on under the new owner."""
    store = FakeStore()
    job = _owned_job()
    job["lease_until"] = "2020-01-01T00:00:00+00:00"
    store.build_jobs["j1"] = job
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.worker_id = "wB"

    def fake_stages(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
        cast: Any = None,
        enrich_tiles: Any = None,
    ) -> None:
        (item_dir / "animals").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worker, "_run_stages", fake_stages)
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["worker_id"] == "wB"
    assert store.build_jobs["j1"]["status"] == "checking"
    assert store.build_jobs["j1"]["progress"][0]["id"] == "enrich_lion"


def test_stale_owner_writes_nothing(tmp_path: Path) -> None:
    """#155: after takeover the old owner neither writes nor publishes."""
    import pytest

    from builder.pipeline import StaleOwner

    store = FakeStore()
    store.build_jobs["j1"] = _owned_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.worker_id = "wA"
    assert worker._claim("j1") is True
    store.build_jobs["j1"]["lease_until"] = "2020-01-01T00:00:00+00:00"
    assert store.claim_build_job("j1", "wB") is True
    store.update_build_job("j1", {"progress": [{"id": "enrich_lion"}]})
    before = dict(store.build_jobs["j1"])
    blobs = dict(store.blobs)
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"] == before
    assert store.blobs == blobs
    with pytest.raises(StaleOwner):
        worker._set_status("j1", "failed")


def test_dead_same_host_owner_is_followed_at_once(tmp_path: Path) -> None:
    """#155: a restart's new pid takes over without waiting out the lease."""
    import os
    import socket
    import subprocess

    proc = subprocess.Popen(["true"])
    proc.wait()
    dead = f"{socket.gethostname()}:{proc.pid}"
    assert dead != f"{socket.gethostname()}:{os.getpid()}"
    store = FakeStore()
    store.build_jobs["j1"] = _owned_job(owner=dead)
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert worker._claim("j1") is True
    assert store.build_jobs["j1"]["worker_id"] == worker.worker_id


def test_restart_resumes_without_repeating_finished_items(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#155: the new owner skips finished items; their calls stay spent."""
    import pytest

    store = FakeStore()
    store.build_jobs["j1"] = {
        "format": 1,
        "kind": "build",
        "tile": "n_413_478",
        "status": "queued",
        "budget_usd": 10.0,
        "cost_usd": 0.0,
        "progress": [],
        "items": ["animal_fox", "animal_hare"],
    }
    store.plans["n_413_478"] = {
        "tile": "n_413_478",
        "items": [
            {"id": "animal_fox", "kind": "animal", "what": "fox", "estimate_usd": 1.0},
            {
                "id": "animal_hare",
                "kind": "animal",
                "what": "mountain_hare",
                "estimate_usd": 1.0,
            },
        ],
        "improve": [],
        "world": "abc123",
    }
    calls: list[str] = []
    seen: set[str] = set()

    def fake_stages(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
        cast: Any = None,
        enrich_tiles: Any = None,
    ) -> None:
        calls.append(item["id"])
        (item_dir / "animals").mkdir(parents=True, exist_ok=True)
        if item["id"] == "animal_hare" and "crashed" not in seen:
            seen.add("crashed")
            raise KeyboardInterrupt("the worker dies here")

    import dataclasses

    first = _flow_worker(tmp_path, store, boreal_3x3())
    first.worker_id = "wA"
    monkeypatch.setattr(first, "_run_stages", fake_stages)
    with pytest.raises(KeyboardInterrupt):
        first.handle_build_job("j1")
    assert calls == ["animal_fox", "animal_hare"]
    assert [e["id"] for e in store.build_jobs["j1"]["progress"]] == ["animal_fox"]
    store.build_jobs["j1"]["lease_until"] = "2020-01-01T00:00:00+00:00"
    second = dataclasses.replace(first, worker_id="wB")
    monkeypatch.setattr(second, "_run_stages", fake_stages)
    second.handle_build_job("j1")
    assert store.build_jobs["j1"]["worker_id"] == "wB"
    assert calls == ["animal_fox", "animal_hare", "animal_hare"]
    assert [e["id"] for e in store.build_jobs["j1"]["progress"]] == [
        "animal_fox",
        "animal_hare",
    ]


def test_claim_build_job_needs_queued_or_an_expired_lease() -> None:
    """#155: a live lease refuses; an expired one can be taken."""
    store = FakeStore()
    assert store.claim_build_job("missing", "w1") is False
    store.build_jobs["j1"] = {"status": "building"}
    assert store.claim_build_job("j1", "w1") is True
    assert store.build_jobs["j1"]["worker_id"] == "w1"
    assert store.claim_build_job("j1", "w2") is False
    assert store.claim_build_job("j1", "w1") is True
    store.build_jobs["j1"]["lease_until"] = "2020-01-01T00:00:00+00:00"
    assert store.claim_build_job("j1", "w2") is True
    assert store.build_jobs["j1"]["worker_id"] == "w2"
    store.build_jobs["j1"]["status"] = "published"
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
        "parts": [{"kind": "forest", "species": ["fox", "vole"]}],
        "plants": [],
        "trees": [],
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


def test_listener_stop_unsubscribes_both_watches(tmp_path: Path) -> None:
    """#150.3: stopping the listener unsubscribes, never crashes."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    listener = Listener(worker=worker, store=store)
    listener.stop.set()
    listener.start()
    assert store.unsubscribed == ["requests", "build_jobs"]


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
        "parts": [{"kind": "forest", "species": ["fox", "vole"]}],
        "plants": [],
        "trees": [],
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


def test_second_publish_carries_the_newer_version(tmp_path: Path) -> None:
    """#143.5: a tile published twice stamps 1, then 2 with new content."""
    import json

    store = FakeStore()
    store.build_jobs["j1"] = _code_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["version"] == 1
    assert worker.world is not None
    _file_lion(worker.world, ["grass"])
    worker.species = _forest_survey("fox", "mountain_hare", "vole", "lion")
    store.build_jobs["j2"] = _code_job()
    worker.handle_build_job("j2")
    assert store.build_jobs["j2"]["version"] == 2
    data = json.loads(store.blobs["tiles/v1/ease2_n25/51_59.json"].decode("utf-8"))
    assert data["tiles"]["n_413_478"]["version"] == 2
    assert "lion" in data["tiles"]["n_413_478"]["species"]["playable"]


def test_code_job_fails_when_tile_blocks(tmp_path: Path, monkeypatch: Any) -> None:
    store = FakeStore()
    store.plans["n_413_478"] = _code_plan("n_413_478")
    store.build_jobs["j1"] = _code_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.species = {
        "place": "Boreal forest in southern Finland",
        "parts": [{"kind": "forest", "species": []}],
        "plants": [],
        "trees": [],
        "name_en": "Nuuksio forest",
        "name_fi": "Nuuksion metsä",
    }
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "failed"
    assert job["error"] == "no_animal"
    assert store.alerts[-1]["kind"] == "job_failed"


def test_code_job_records_its_tile_history(tmp_path: Path) -> None:
    """#165.3: a publish lands one made row that stays put."""
    store = FakeStore()
    store.build_jobs["j1"] = _code_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_build_job("j1")
    (entry,) = store.tile_history
    assert entry["tile"] == "n_413_478"
    assert entry["event"] == "made"
    assert entry["job"] == "j1"
    assert entry["kind"] == "code"
    assert entry["world"] == "abc123"
    assert entry["version"] == 1
    assert entry["maker"]
    assert entry["at"]
    assert "reason" not in entry


def test_second_publish_appends_a_remake_row(tmp_path: Path) -> None:
    """#165.3: a remake bumps the version; both rows remain."""
    store = FakeStore()
    store.build_jobs["j1"] = _code_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_build_job("j1")
    assert worker.world is not None
    _file_lion(worker.world, ["grass"])
    worker.species = _forest_survey("fox", "mountain_hare", "vole", "lion")
    store.build_jobs["j2"] = _code_job()
    worker.handle_build_job("j2")
    assert [e["event"] for e in store.tile_history] == ["made", "remade"]
    assert [e["version"] for e in store.tile_history] == [1, 2]
    assert [e["job"] for e in store.tile_history] == ["j1", "j2"]


def test_blocked_code_job_records_its_reason(tmp_path: Path) -> None:
    """#165.3: a blocked tile lands a row with its reason, no version."""
    store = FakeStore()
    store.build_jobs["j1"] = _code_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.species = _forest_survey()
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "failed"
    (entry,) = store.tile_history
    assert entry["event"] == "blocked"
    assert entry["reason"] == "no_animal"
    assert entry["version"] is None


def test_broken_history_never_stops_the_tile(tmp_path: Path, monkeypatch: Any) -> None:
    """#165.3: a failing history write prints; the tile still ships."""

    def broken(item: dict[str, Any]) -> str:
        raise ValueError("firestore is down")

    store = FakeStore()
    store.build_jobs["j1"] = _code_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    monkeypatch.setattr(store, "add_tile_history", broken)
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "published"
    assert store.tile_history == []


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
        cast: Any = None,
        enrich_tiles: Any = None,
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
    job = _code_job()
    job["kind"] = "build"
    store = FakeStore()
    store.build_jobs["j1"] = job
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "failed"
    assert store.build_jobs["j1"]["error"] == "no_plan"


def test_code_job_needs_no_plan(tmp_path: Path) -> None:
    """#143.3: a code job makes its tile straight from the world."""
    store = FakeStore()
    store.build_jobs["j1"] = _code_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "published"


def _enrich_job(**over: Any) -> dict[str, Any]:
    job: dict[str, Any] = {
        "format": 1,
        "kind": "enrich",
        "tile": "n_413_478",
        "tiles": ["n_413_478", "n_413_479"],
        "items": ["enrich_lion"],
        "enrich": {"kind": "animal", "what": "lion", "from": "fox"},
        "budget_usd": 1.0,
        "visibility": "public",
        "created_by": "enrich",
        "status": "queued",
        "progress": [],
        "cost_usd": 0.0,
    }
    job.update(over)
    return job


def test_enrich_job_builds_its_item_to_checking(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#143.3: no plan document; the item comes from the job."""
    import json

    store = FakeStore()
    store.build_jobs["j1"] = _enrich_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())

    def fake_stages(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
        cast: Any = None,
        enrich_tiles: Any = None,
    ) -> None:
        assert tile == "n_413_478"
        assert item["id"] == "enrich_lion"
        folder = item_dir / "animals"
        folder.mkdir(parents=True)
        (folder / "lion.json").write_bytes(b'{"id": "lion"}')

    monkeypatch.setattr(worker, "_run_stages", fake_stages)
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "checking"
    assert [(e["id"], e["status"]) for e in job["progress"]] == [
        ("enrich_lion", "checking")
    ]
    assert "review/j1/enrich_lion/report.html" in store.blobs
    snapshot = json.loads((tmp_path / "jobs" / "j1" / "plan.json").read_text())
    assert snapshot["items"] == [
        {
            "id": "enrich_lion",
            "kind": "animal",
            "what": "lion",
            "from": "fox",
            "estimate_usd": 1.0,
        }
    ]


def test_enrich_item_finds_its_own_from(tmp_path: Path) -> None:
    """#143.3: the list's start_from, or the closest plant."""
    store = FakeStore()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    item = worker._enrich_item(_enrich_job(enrich={"kind": "animal", "what": "lion"}))
    assert item["id"] == "enrich_lion"
    assert item["from"] == "fox"
    item = worker._enrich_item(
        _enrich_job(
            items=["enrich_arctic_willow"],
            enrich={"kind": "plant", "what": "arctic_willow"},
        )
    )
    assert item["id"] == "enrich_arctic_willow"
    assert item["from"] == "bilberry"


def test_enrich_job_fails_cleanly_when_malformed(tmp_path: Path) -> None:
    """#143.3: a bad enrich spec fails the job, never crashes it."""
    store = FakeStore()
    store.build_jobs["j1"] = _enrich_job(enrich={"kind": "animal"})
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "failed"
    assert store.build_jobs["j1"]["error"] == "bad_enrich"
    store.build_jobs["j2"] = _enrich_job(items=["enrich_tiger"])
    worker.handle_build_job("j2")
    assert store.build_jobs["j2"]["error"] == "bad_enrich"


def _forest_survey(
    *species: str, name_en: str = "Fox woods", name_fi: str = "Kettumetsä"
) -> dict[str, Any]:
    return {
        "place": "Boreal forest in southern Finland",
        "parts": [{"kind": "forest", "species": list(species)}],
        "plants": [],
        "trees": [],
        "name_en": name_en,
        "name_fi": name_fi,
    }


class _AnswerClient:
    """One survey answer, on repeat."""

    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer

    def chat(self, **kwargs: Any) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(self.answer))
                )
            ]
        )


class _SilentClient:
    """A remake never calls, however live the worker."""

    def chat(self, **kwargs: Any) -> Any:
        raise AssertionError("a remake never calls")

    def responses_create(self, **kwargs: Any) -> Any:
        raise AssertionError("a remake never calls")


def _named_list(world: Path) -> None:
    """Names for the fake list's animals: the survey prompt needs them."""
    names = {
        "fox": ("Red fox", "Vulpes vulpes"),
        "mountain_hare": ("Mountain hare", "Lepus timidus"),
        "vole": ("Vole", "Microtus agrestis"),
        "lion": ("Lion", "Panthera leo"),
        "tiger": ("Tiger", "Panthera tigris"),
    }
    listed = json.loads((world / "animal-list.json").read_text())
    for entry in listed["animals"]:
        if entry["id"] in names:
            entry["name"], entry["latin"] = names[entry["id"]]
    (world / "animal-list.json").write_text(json.dumps(listed))


def _file_lion(world: Path, diet: list[str]) -> None:
    import json

    (world / "animals" / "lion.json").write_text(
        json.dumps({"playable": True, "tags": ["big_cat"], "diet": diet})
    )
    listed = json.loads((world / "animal-list.json").read_text())
    next(e for e in listed["animals"] if e["id"] == "lion")["in_library"] = "lion"
    (world / "animal-list.json").write_text(json.dumps(listed))


def test_enrich_review_remakes_only_the_tiles_that_list_it(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#143.4's lab: two tiles list the lion, one doesn't."""
    import json

    from builder.grid import group_path
    from builder.species import Source as SpeciesSource
    from builder.tiles import make_tile

    A, B, C = "n_413_478", "n_413_479", "n_421_478"
    block = boreal_3x3(413, 478)
    side = boreal_3x3(421, 478)
    # B's northern neighbours: the block's row above.
    edge = {f"{c},480": list(block["413,478"]) for c in (412, 413, 414)}
    cells = {**block, **edge, **side}
    store = FakeStore()
    worker = _flow_worker(tmp_path, store, cells)
    assert worker.world is not None
    _named_list(worker.world)
    cache = tmp_path / "cache"
    FOUR = ("fox", "mountain_hare", "vole", "lion")
    THREE = ("fox", "mountain_hare", "vole")
    made: dict[str, Any] = {}
    for tile, species in ((A, FOUR), (B, FOUR), (C, THREE)):
        tile_made, blocked = make_tile(
            tile,
            worker._grids(),
            worker.world,
            builder_root=tmp_path,
            species_source=SpeciesSource(
                client=_AnswerClient(_forest_survey(*species)), cache_dir=cache
            ),
        )
        assert blocked is None and tile_made is not None
        made[tile] = tile_made
    assert made[A]["wanted"] == [{"kind": "animal", "what": "lion", "part": "forest"}]
    assert "wanted" not in made[C]
    g1, g2 = group_path(A), group_path(C)
    assert g1 != g2 and group_path(B) == g1
    store.blobs[g1] = json.dumps(
        {
            "format": 1,
            "grid": "ease2_n25",
            "group": [51, 59],
            "version": 1,
            "tiles": {A: made[A], B: made[B]},
            "blocked": {},
        }
    ).encode("utf-8")
    store.blobs[g2] = json.dumps(
        {
            "format": 1,
            "grid": "ease2_n25",
            "group": [52, 59],
            "version": 1,
            "tiles": {C: made[C]},
            "blocked": {},
        }
    ).encode("utf-8")
    before_c = store.blobs[g2]
    store.blobs["tiles/v1/index.json"] = json.dumps(
        {
            "format": 1,
            "made_at": "",
            "world": "",
            "groups": {},
            "counts": {"ready": 3, "blocked": {}},
        }
    ).encode("utf-8")
    assert worker.world is not None
    _file_lion(worker.world, ["grass"])
    store.build_jobs["j1"] = _enrich_job(
        tile=A,
        tiles=[A, B, C],
        status="checking",
        progress=[
            {
                "id": "enrich_lion",
                "status": "checking",
                "report": "review/j1/enrich_lion/report.html",
            }
        ],
        review={"enrich_lion": {"by": "kai", "at": "t", "result": "shipped"}},
    )
    writes: list[str] = []
    real_write = store.write_blob
    monkeypatch.setattr(
        store,
        "write_blob",
        lambda path, data, *a, **k: (writes.append(path), real_write(path, data)),
    )
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["versions"] == {A: 2, B: 2}
    g1data = json.loads(store.blobs[g1].decode("utf-8"))
    assert g1data["version"] == 2
    assert "lion" in g1data["tiles"][A]["species"]["playable"]
    assert "lion" in g1data["tiles"][B]["species"]["playable"]
    assert "wanted" not in g1data["tiles"][A]
    assert store.blobs[g2] == before_c
    assert writes.count(g1) == 1
    assert writes.count(g2) == 0
    assert writes.count("tiles/v1/index.json") == 1
    index = json.loads(store.blobs["tiles/v1/index.json"].decode("utf-8"))
    assert index["counts"]["ready"] == 3


def test_enrich_intake_writes_converter_places(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#161.1: an enrich item checks against its tiles' places, no scaffold."""
    import json
    from types import SimpleNamespace

    import fake_world

    import builder.build as build_mod
    from builder.grid import group_path
    from builder.stages.maker import AnimalMakerStage

    T = "n_413_478"
    store = FakeStore()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert worker.world is not None
    _named_list(worker.world)
    fake_world.write_converter_tools(worker.world)
    cast = {
        "playable": ["mountain_hare", "vole"],
        "prey": ["vole"],
        "predators": [],
    }
    store.blobs[group_path(T)] = json.dumps(
        {
            "format": 1,
            "grid": "ease2_n25",
            "group": [51, 59],
            "version": 1,
            "tiles": {
                T: {
                    "id": T,
                    "kind": "code",
                    "version": 1,
                    "home": "boreal_forest",
                    "species": cast,
                }
            },
            "blocked": {},
        }
    ).encode("utf-8")

    class _Pipe:
        def run(self, ctx: Any) -> dict[str, Any]:
            return {}

    monkeypatch.setattr(build_mod, "item_pipeline", lambda **kwargs: _Pipe())
    item = {
        "id": "enrich_lion",
        "kind": "animal",
        "what": "lion",
        "from": "fox",
        "estimate_usd": 1.0,
    }
    plan = {
        "format": 1,
        "tile": T,
        "code_only": False,
        "items": [item],
        "improve": [],
        "estimate_usd": 1.0,
    }
    item_dir = tmp_path / "jobs" / "j1" / "enrich_lion"
    worker._write_enrich_places("j1", item_dir, [T], item)
    worker._run_stages(SimpleNamespace(), "j1", T, plan, item, item_dir, None, [T])
    assert not (item_dir / "regions" / "zz_work.json").exists()
    region = json.loads((item_dir / "regions" / f"tl_{T}.json").read_text())
    assert region["animals"]["playable"] == ["mountain_hare", "vole", "lion"]
    brief = json.loads((item_dir / "home.json").read_text())
    assert brief["enrich"] == {"tiles": [T]}
    research = {
        "main_food": {"kind": "prey", "name": "Mountain hare"},
        "other_foods": [],
    }
    tags = AnimalMakerStage()._code_hunts(
        worker.world, SimpleNamespace(job_dir=item_dir), research, region, "lion"
    )
    assert tags == ["small_mammal"]


def test_enrich_run_item_writes_places_for_its_tiles(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#161.1: an enrich item's intake converts its tiles, no cast."""
    import json

    import fake_world

    from builder.grid import group_path

    T = "n_413_478"
    store = FakeStore()
    store.build_jobs["j1"] = _enrich_job(status="building")
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert worker.world is not None
    fake_world.write_converter_tools(worker.world)
    cast = {"playable": ["fox"], "prey": [], "predators": ["fox"]}
    store.blobs[group_path(T)] = json.dumps(
        {
            "format": 1,
            "grid": "ease2_n25",
            "group": [51, 59],
            "version": 1,
            "tiles": {
                T: {
                    "id": T,
                    "kind": "code",
                    "version": 1,
                    "home": "boreal_forest",
                    "species": cast,
                }
            },
            "blocked": {},
        }
    ).encode("utf-8")
    seen: dict[str, Any] = {}

    def fake_stages(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
        cast: Any = None,
        enrich_tiles: Any = None,
    ) -> None:
        seen["cast"] = cast
        seen["tiles"] = enrich_tiles

    monkeypatch.setattr(worker, "_run_stages", fake_stages)
    assert store.claim_build_job("j1", worker.worker_id) is True
    job = store.build_jobs["j1"]
    item = {
        "id": "enrich_lion",
        "kind": "animal",
        "what": "lion",
        "from": "fox",
        "estimate_usd": 1.0,
    }
    worker.run_item("j1", job, T, {"items": [item]}, item, 1.0)
    assert seen["cast"] is None
    assert seen["tiles"] == ["n_413_478", "n_413_479"]
    region = json.loads(
        (
            tmp_path / "jobs" / "j1" / "enrich_lion" / "regions" / f"tl_{T}.json"
        ).read_text()
    )
    assert region["animals"]["playable"] == ["fox", "lion"]


def test_enrich_item_report_names_its_tile(tmp_path: Path, monkeypatch: Any) -> None:
    """#150.2: an enrich report shows the tile, its name and the kind."""
    T = "n_413_478"
    store = FakeStore()
    store.build_jobs["j1"] = _enrich_job(status="building")
    worker = _flow_worker(tmp_path, store, boreal_3x3())

    def fake_stages(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
        cast: Any = None,
        enrich_tiles: Any = None,
    ) -> None:
        (item_dir / "animals").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worker, "_run_stages", fake_stages)
    assert store.claim_build_job("j1", worker.worker_id) is True
    job = store.build_jobs["j1"]
    item = {
        "id": "enrich_lion",
        "kind": "animal",
        "what": "lion",
        "from": "fox",
        "estimate_usd": 1.0,
    }
    worker.run_item("j1", job, T, {"items": [item]}, item, 1.0)
    report = (tmp_path / "jobs" / "j1" / "enrich_lion" / "report.html").read_text()
    assert f"<td>{T} (" in report
    assert "Nuuksio" in report
    assert "<td>enrich</td>" in report


def test_build_item_report_keeps_old_labels(tmp_path: Path, monkeypatch: Any) -> None:
    """#150.2: a build item's report is unlabeled, as before."""
    store = FakeStore()
    store.build_jobs["j1"] = {
        "format": 1,
        "kind": "build",
        "tile": "n_413_478",
        "status": "building",
        "cost_usd": 0.0,
        "progress": [],
    }
    worker = _flow_worker(tmp_path, store, boreal_3x3())

    def fake_stages(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
        cast: Any = None,
        enrich_tiles: Any = None,
    ) -> None:
        (item_dir / "animals").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worker, "_run_stages", fake_stages)
    assert store.claim_build_job("j1", worker.worker_id) is True
    job = store.build_jobs["j1"]
    item = {
        "id": "animal_fox",
        "kind": "animal",
        "what": "fox",
        "from": "fox",
        "estimate_usd": 1.0,
    }
    worker.run_item("j1", job, "n_413_478", {"items": [item]}, item, 1.0)
    report = (tmp_path / "jobs" / "j1" / "animal_fox" / "report.html").read_text()
    assert "(unknown area)" in report
    assert "new_place" in report


def test_tile_doc_remakes_an_unpublished_tile(tmp_path: Path, monkeypatch: Any) -> None:
    """#161.1: with no published entry, the doc remakes by code."""
    import builder.tiles as tiles_mod

    T = "n_413_478"
    store = FakeStore()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    doc = {"id": T, "kind": "code", "species": {"playable": ["fox"]}}
    calls: list[str] = []

    def fake_make(tile: str, *args: Any, **kwargs: Any) -> tuple[Any, Any]:
        calls.append(tile)
        return doc, None

    monkeypatch.setattr(tiles_mod, "make_tile", fake_make)
    assert worker._tile_doc(T) == doc
    assert calls == [T]
    monkeypatch.setattr(tiles_mod, "make_tile", lambda *a, **k: (None, {}))
    with pytest.raises(ValueError, match="remakes blocked"):
        worker._tile_doc(T)


def test_enrich_review_skips_place_cells(tmp_path: Path, monkeypatch: Any) -> None:
    """#147.1: a remake never touches a place cell."""
    import json

    from builder.grid import group_path
    from builder.species import Source as SpeciesSource

    P = "n_413_478"
    store = FakeStore()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert worker.world is not None
    regions = worker.world / "regions"
    regions.mkdir()
    (regions / "fi_test.json").write_text(
        json.dumps(
            {"id": "fi_test", "grid": {"grid": "ease2_n25", "tiles": [[413, 478]]}}
        )
    )
    _named_list(worker.world)
    _file_lion(worker.world, ["grass"])
    blob = group_path(P)
    store.blobs[blob] = json.dumps(
        {
            "format": 1,
            "grid": "ease2_n25",
            "group": [51, 59],
            "version": 1,
            "tiles": {P: {"id": P, "kind": "code", "version": 1}},
            "blocked": {},
        }
    ).encode("utf-8")
    before = store.blobs[blob]
    store.blobs["tiles/v1/index.json"] = json.dumps(
        {
            "format": 1,
            "made_at": "",
            "world": "",
            "groups": {},
            "counts": {"ready": 1, "blocked": {}},
        }
    ).encode("utf-8")
    store.build_jobs["j1"] = _enrich_job(
        tile=P,
        tiles=[P],
        status="checking",
        progress=[
            {
                "id": "enrich_lion",
                "status": "checking",
                "report": "review/j1/enrich_lion/report.html",
            }
        ],
        review={"enrich_lion": {"by": "kai", "at": "t", "result": "shipped"}},
    )
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda: SpeciesSource(client=_SilentClient(), cache_dir=tmp_path / "cache"),
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["versions"] == {}
    assert store.blobs[blob] == before
    (entry,) = store.tile_history
    assert entry["tile"] == P
    assert entry["event"] == "left_out"
    assert entry["reason"] == "place:fi_test"
    assert entry["kind"] == "enrich"
    assert entry["version"] is None


def _region_world(world: Path) -> None:
    regions = world / "regions"
    regions.mkdir(exist_ok=True)
    (regions / "xx_test_reef.json").write_text(
        json.dumps(
            {
                "id": "xx_test_reef",
                "name": {"en": "Test Reef", "fi": "Testiriutta"},
                "center": {"lat": 60.0, "lon": 24.0},
                "grid": {"grid": "ease2_n25", "tiles": [[413, 478]]},
                "animals": {
                    "playable": ["mountain_hare", "vole"],
                    "prey": ["vole"],
                    "predators": [],
                },
                "plants": {"cover": [{"id": "bilberry", "density": 0.1}]},
                "food": [{"kind": "grass"}, {"kind": "berries"}],
            }
        )
    )


def test_enrich_intake_checks_a_region_tile_in_its_region(
    tmp_path: Path,
) -> None:
    """#168.1: a hand-made region's tile checks in the region file."""
    import fake_world

    P = "n_413_478"
    store = FakeStore()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert worker.world is not None
    fake_world.write_converter_tools(worker.world)
    _region_world(worker.world)
    item = {
        "id": "enrich_lion",
        "kind": "animal",
        "what": "lion",
        "from": "fox",
        "estimate_usd": 1.0,
    }
    item_dir = tmp_path / "jobs" / "j1" / "enrich_lion"
    worker._write_enrich_places("j1", item_dir, [P], item)
    region = json.loads((item_dir / "regions" / "xx_test_reef.json").read_text())
    assert region["animals"]["playable"] == ["mountain_hare", "vole", "lion"]
    assert not (item_dir / "regions" / f"tl_{P}.json").exists()
    manifest = json.loads((item_dir / "checks" / "enrich_places.json").read_text())
    assert manifest == {"places": ["xx_test_reef"]}


def test_tile_home_falls_back_to_the_region(tmp_path: Path) -> None:
    """#168.1: a place cell's home is its region file, for the intake."""
    import fake_world

    store = FakeStore()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert worker.world is not None
    fake_world.write_converter_tools(worker.world)
    _region_world(worker.world)
    plan = {"format": 1, "items": []}
    home = worker._tile_home("n_413_478", plan, "j1")
    assert home["id"] == "xx_test_reef"
    assert worker._tile_home("n_413_479", plan, "j1")["habitat"] == "boreal_forest"


def test_enrich_job_on_a_region_tile_reaches_checking(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#168.1: hammerhead-on-the-reef reaches checking with no model call."""
    import fake_world

    import builder.build as build_mod

    P = "n_413_478"
    store = FakeStore()
    store.build_jobs["j1"] = _enrich_job(tile=P, tiles=[P])
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert worker.world is not None
    _named_list(worker.world)
    fake_world.write_converter_tools(worker.world)
    _region_world(worker.world)

    class _Pipe:
        def run(self, ctx: Any) -> dict[str, Any]:
            assert ctx.job_dir.name == "enrich_lion"
            folder = ctx.job_dir / "animals"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "lion.json").write_bytes(b'{"id": "lion"}')
            return {}

    monkeypatch.setattr(build_mod, "item_pipeline", lambda **kwargs: _Pipe())
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "checking"
    assert [(e["id"], e["status"]) for e in job["progress"]] == [
        ("enrich_lion", "checking")
    ]
    assert (
        tmp_path / "jobs" / "j1" / "enrich_lion" / "regions" / "xx_test_reef.json"
    ).is_file()
    assert "review/j1/enrich_lion/report.html" in store.blobs


def test_enrich_failure_names_its_stage_and_the_region(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#168.3: a failed item's progress and report say the stage and why."""
    import fake_world

    P = "n_413_478"
    store = FakeStore()
    store.build_jobs["j1"] = _enrich_job(tile=P, tiles=[P])
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert worker.world is not None
    _named_list(worker.world)
    fake_world.write_converter_tools(worker.world)
    _region_world(worker.world)

    def broken(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
        cast: Any = None,
        enrich_tiles: Any = None,
    ) -> None:
        raise ValueError("tile n_413_478 has no home in this checkout")

    monkeypatch.setattr(worker, "_run_stages", broken)
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "failed"
    (entry,) = job["progress"]
    assert entry["status"] == "failed"
    assert entry["error"] == "intake: tile n_413_478 has no home in this checkout"
    item_dir = tmp_path / "jobs" / "j1" / "enrich_lion"
    text = (item_dir / "report.html").read_text(encoding="utf-8")
    assert "intake: tile n_413_478 has no home in this checkout" in text
    assert "finished every built stage" not in text
    state = json.loads((item_dir / "state.json").read_text(encoding="utf-8"))
    assert state["params"]["area"] == "n_413_478 (Test Reef)"


def test_enrich_review_opens_a_blocked_tile(tmp_path: Path, monkeypatch: Any) -> None:
    """#143.4: the wanted opener publishes its tile at version 1."""
    import json

    from builder.grid import group_path
    from builder.species import Source as SpeciesSource
    from builder.tiles import make_tile

    D = "n_413_478"
    store = FakeStore()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert worker.world is not None
    _named_list(worker.world)
    cache = tmp_path / "cache"
    made, blocked = make_tile(
        D,
        worker._grids(),
        worker.world,
        builder_root=tmp_path,
        species_source=SpeciesSource(
            client=_AnswerClient(
                _forest_survey("lion", name_en="Lion woods", name_fi="Leijonametsä")
            ),
            cache_dir=cache,
        ),
    )
    assert made is None and blocked is not None
    assert blocked["reason"] == "no_animal"
    blob = group_path(D)
    store.blobs[blob] = json.dumps(
        {
            "format": 1,
            "grid": "ease2_n25",
            "group": [51, 59],
            "version": 1,
            "tiles": {},
            "blocked": {D: blocked},
        }
    ).encode("utf-8")
    assert worker.world is not None
    _file_lion(worker.world, ["grass"])
    store.build_jobs["j1"] = _enrich_job(
        tile=D,
        tiles=[D],
        status="checking",
        progress=[
            {
                "id": "enrich_lion",
                "status": "checking",
                "report": "review/j1/enrich_lion/report.html",
            }
        ],
        review={"enrich_lion": {"by": "kai", "at": "t", "result": "shipped"}},
    )
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["versions"] == {D: 1}
    data = json.loads(store.blobs[blob].decode("utf-8"))
    assert "lion" in data["tiles"][D]["species"]["playable"]
    assert data["blocked"] == {}


def test_enrich_review_flags_no_food(tmp_path: Path, monkeypatch: Any) -> None:
    """#143.4: the unfed lion stays out; the report says no_food."""
    import json

    from builder.grid import group_path
    from builder.species import Source as SpeciesSource
    from builder.tiles import make_tile

    A = "n_413_478"
    store = FakeStore()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert worker.world is not None
    _named_list(worker.world)
    cache = tmp_path / "cache"
    made, blocked = make_tile(
        A,
        worker._grids(),
        worker.world,
        builder_root=tmp_path,
        species_source=SpeciesSource(
            client=_AnswerClient(
                _forest_survey("fox", "mountain_hare", "vole", "lion")
            ),
            cache_dir=cache,
        ),
    )
    assert blocked is None and made is not None
    blob = group_path(A)
    store.blobs[blob] = json.dumps(
        {
            "format": 1,
            "grid": "ease2_n25",
            "group": [51, 59],
            "version": 1,
            "tiles": {A: made},
            "blocked": {},
        }
    ).encode("utf-8")
    assert worker.world is not None
    _file_lion(worker.world, ["honey"])
    item_dir = tmp_path / "jobs" / "j1" / "enrich_lion"
    item_dir.mkdir(parents=True)
    (item_dir / "report.html").write_text("<html></html>", encoding="utf-8")
    store.build_jobs["j1"] = _enrich_job(
        tiles=[A],
        status="checking",
        progress=[
            {
                "id": "enrich_lion",
                "status": "checking",
                "report": "review/j1/enrich_lion/report.html",
            }
        ],
        review={"enrich_lion": {"by": "kai", "at": "t", "result": "shipped"}},
    )
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["versions"] == {A: 2}
    data = json.loads(store.blobs[blob].decode("utf-8"))
    assert "lion" not in data["tiles"][A]["species"]["playable"]
    assert "wanted" not in data["tiles"][A]
    report = store.blobs["review/j1/enrich_lion/report.html"].decode("utf-8")
    assert "no_food n_413_478" in report


def _two_tile_lab(
    tmp_path: Path,
) -> tuple[Any, Any, Path, str, str, str]:
    """Two forest tiles listing the lion, filed with grass to eat."""
    import json

    from builder.grid import group_path
    from builder.species import Source as SpeciesSource
    from builder.tiles import make_tile

    A, B = "n_413_478", "n_413_479"
    store = FakeStore()
    block = boreal_3x3(413, 478)
    edge = {f"{c},480": list(block["413,478"]) for c in (412, 413, 414)}
    worker = _flow_worker(tmp_path, store, {**block, **edge})
    assert worker.world is not None
    _named_list(worker.world)
    cache = tmp_path / "cache"
    made = {}
    for tile in (A, B):
        tile_made, blocked = make_tile(
            tile,
            worker._grids(),
            worker.world,
            builder_root=tmp_path,
            species_source=SpeciesSource(
                client=_AnswerClient(
                    _forest_survey("fox", "mountain_hare", "vole", "lion")
                ),
                cache_dir=cache,
            ),
        )
        assert blocked is None and tile_made is not None
        made[tile] = tile_made
    blob = group_path(A)
    assert group_path(B) == blob
    store.blobs[blob] = json.dumps(
        {
            "format": 1,
            "grid": "ease2_n25",
            "group": [51, 59],
            "version": 1,
            "tiles": made,
            "blocked": {},
        }
    ).encode("utf-8")
    store.blobs["tiles/v1/index.json"] = json.dumps(
        {
            "format": 1,
            "made_at": "",
            "world": "",
            "groups": {},
            "counts": {"ready": 2, "blocked": {}},
        }
    ).encode("utf-8")
    assert worker.world is not None
    _file_lion(worker.world, ["grass"])
    return store, worker, cache, blob, A, B


def _ship_job(store: Any, tiles: list[str]) -> None:
    store.build_jobs["j1"] = _enrich_job(
        tiles=tiles,
        status="checking",
        progress=[
            {
                "id": "enrich_lion",
                "status": "checking",
                "report": "review/j1/enrich_lion/report.html",
            }
        ],
        review={"enrich_lion": {"by": "kai", "at": "t", "result": "shipped"}},
    )


def test_enrich_review_ships_only_tiles_that_passed_their_places(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#161.2: the passed tile remakes; the failed one is listed, left out."""
    import json

    from builder.species import Source as SpeciesSource

    store, worker, cache, blob, A, B = _two_tile_lab(tmp_path)
    item_dir = tmp_path / "jobs" / "j1" / "enrich_lion"
    (item_dir / "checks").mkdir(parents=True)
    (item_dir / "report.html").write_text("<html></html>", encoding="utf-8")
    (item_dir / "checks" / "places.json").write_text(
        json.dumps(
            {
                "ok": False,
                "results": [
                    {"check": f"place {A}", "status": "pass", "problems": []},
                    {
                        "check": f"place {B}",
                        "status": "fail",
                        "problems": [
                            f'ERROR: squirrel in tl_{B} eats "mushrooms", '
                            "but the region has no such food"
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    _ship_job(store, [A, B])
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["versions"] == {A: 2}
    data = json.loads(store.blobs[blob].decode("utf-8"))
    assert "lion" in data["tiles"][A]["species"]["playable"]
    assert data["tiles"][B]["version"] == 1
    assert "wanted" in data["tiles"][B]
    report = store.blobs["review/j1/enrich_lion/report.html"].decode("utf-8")
    assert f"place {B}" in report
    assert "mushrooms" in report


def test_enrich_review_fails_place_when_no_tile_passes(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#161.2: with every place refusing, the job fails as place, listed."""
    import json

    from builder.species import Source as SpeciesSource

    store, worker, cache, blob, A, B = _two_tile_lab(tmp_path)
    before = store.blobs[blob]
    item_dir = tmp_path / "jobs" / "j1" / "enrich_lion"
    (item_dir / "checks").mkdir(parents=True)
    (item_dir / "report.html").write_text("<html></html>", encoding="utf-8")
    (item_dir / "checks" / "places.json").write_text(
        json.dumps(
            {
                "ok": False,
                "results": [
                    {
                        "check": f"place {A}",
                        "status": "fail",
                        "problems": ["ERROR: squirrel in tl_n_413_478 eats mushrooms"],
                    },
                    {
                        "check": f"place {B}",
                        "status": "fail",
                        "problems": ["ERROR: squirrel in tl_n_413_479 eats mushrooms"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    _ship_job(store, [A, B])
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "failed"
    assert job["error"] == "place"
    assert store.blobs[blob] == before
    report = store.blobs["review/j1/enrich_lion/report.html"].decode("utf-8")
    assert f"place {A}" in report
    assert f"place {B}" in report


def test_enrich_review_drops_not_accepted(tmp_path: Path) -> None:
    """#143.4: not_accepted fails with nothing_shipped, as for a build."""
    store = FakeStore()
    store.build_jobs["j1"] = _enrich_job(
        status="checking",
        progress=[
            {
                "id": "enrich_lion",
                "status": "checking",
                "report": "review/j1/enrich_lion/report.html",
            }
        ],
        review={"enrich_lion": {"by": "kai", "at": "t", "result": "not_accepted"}},
    )
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "failed"
    assert store.build_jobs["j1"]["error"] == "nothing_shipped"


def test_enrich_review_waits_for_the_world(tmp_path: Path) -> None:
    """#143.4: shipped but unfiled stays checking."""
    store = FakeStore()
    store.build_jobs["j1"] = _enrich_job(
        status="checking",
        progress=[
            {
                "id": "enrich_lion",
                "status": "checking",
                "report": "review/j1/enrich_lion/report.html",
            }
        ],
        review={"enrich_lion": {"by": "kai", "at": "t", "result": "shipped"}},
    )
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "checking"


def test_build_job_refuses_a_blocked_plan(tmp_path: Path) -> None:
    """#141.2: a blocked plan fails with its reason, not no_items."""
    store = FakeStore()
    store.build_jobs["j1"] = {
        "format": 1,
        "tile": "n_401_434",
        "budget_usd": 8.0,
        "kind": "build",
        "status": "queued",
        "items": [],
        "visibility": "public",
        "created_by": "admin",
        "progress": [],
        "cost_usd": 0.0,
    }
    store.plans["n_401_434"] = {"blocked": "open_water", "items": []}
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "failed"
    assert store.build_jobs["j1"]["error"] == "open_water"


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
        cast: Any = None,
        enrich_tiles: Any = None,
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
        cast: Any = None,
        enrich_tiles: Any = None,
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
    assert store.claim_build_job("j1", worker.worker_id) is True
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
    assert worker.poll() == [("build_job", "j1")]
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
        cast: Any = None,
        enrich_tiles: Any = None,
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


def test_ledger_failure_reports_unavailable_and_stops_items(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#159: a down ledger stops the job as unavailable, not as content."""
    from builder.ledger import LedgerError

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
    runs: list[str] = []

    def down(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
        cast: Any = None,
        enrich_tiles: Any = None,
    ) -> None:
        runs.append(item["id"])
        raise LedgerError("cannot reserve spend: locked")

    monkeypatch.setattr(worker, "_run_stages", down)
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["error"] == "ledger"
    assert job["status"] == "failed"
    by_id = {e["id"]: e["status"] for e in job["progress"]}
    assert by_id == {"animal_lion": "failed"}
    assert runs == ["animal_lion"]


def _fake_animal_stages(worker: Worker, monkeypatch: Any, tmp_path: Path) -> None:
    def fake_stages(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
        cast: Any = None,
        enrich_tiles: Any = None,
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
    import json

    data = json.loads(store.blobs["tiles/v1/ease2_n25/51_59.json"].decode("utf-8"))
    assert job["version"] == data["tiles"]["n_413_478"]["version"] == 1


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


def _held_pair(tmp_path: Path):  # type: ignore[no-untyped-def]
    """A combined reservation left outstanding by a timeout (#157)."""
    from builder.ledger import Caps, CombinedLedger, FileLedger

    provider = FileLedger(tmp_path / "meta-pool.json")
    allowance = FileLedger(tmp_path / "allowance.json")
    ledger = CombinedLedger(provider, allowance, Caps(8.0, 20.0, 120.0))
    receipt = ledger.reserve("j1", 0.05, Caps(8.0, 20.0, 120.0))
    return provider, allowance, ledger, receipt


def test_timeout_pauses_item_and_resume_recovers(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#157: the reservation stays held; a later drive retries the item."""
    from types import SimpleNamespace

    from builder.meta import SpendUncertain

    store = FakeStore()
    store.build_jobs["j1"] = _owned_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.worker_id = "wA"
    provider, allowance, ledger, receipt = _held_pair(tmp_path)
    worker.chat = SimpleNamespace(ledger=ledger, model="muse-spark-1.3")

    uncertain = SpendUncertain("chat: sent, spend unknown (timed out)")
    uncertain.receipt = receipt
    uncertain.host = "meta"
    attempts: list[str] = []

    def flaky(*args: Any, **kwargs: Any) -> None:
        attempts.append("stages")
        if len(attempts) == 1:
            raise uncertain

    monkeypatch.setattr(worker, "_run_stages", flaky)
    worker.handle_build_job("j1")
    entry = store.build_jobs["j1"]["progress"][0]
    assert entry["status"] == "paused_spend_uncertain"
    assert entry["uncertain"]["host"] == "meta"
    assert entry["uncertain"]["provider"]["id"] == receipt.provider.id
    assert entry["uncertain"]["allowance"]["id"] == receipt.allowance.id
    assert store.build_jobs["j1"]["status"] == "building"
    assert store.build_jobs["j1"]["error"] == "paused_spend_uncertain"
    assert provider.job_spent("j1") == pytest.approx(0.05)
    assert allowance.job_spent("j1") == pytest.approx(0.05)

    worker.handle_build_job("j1")
    entry = store.build_jobs["j1"]["progress"][0]
    assert entry["status"] == "checking"
    assert store.build_jobs["j1"]["status"] == "checking"
    assert store.build_jobs["j1"].get("error") is None
    assert attempts == ["stages", "stages"]
    # The resume settled the superseded attempt at its estimate.
    for pool, side in ((provider, receipt.provider), (allowance, receipt.allowance)):
        data = json.loads(pool.path.read_text(encoding="utf-8"))
        assert side.id in data["settled"]
        assert pool.job_spent("j1") == pytest.approx(0.05)


def test_reenable_recovers_queued_job_and_request_once(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#158: consumed-while-disabled work proceeds exactly once on re-enable."""
    from builder.worker import Listener

    store = FakeStore()
    store.build_jobs["j1"] = _enrich_job(status="queued")
    store.requests["n_413_478"] = {"tile": "n_413_478"}
    worker = _planned_worker(tmp_path, store, monkeypatch)
    worker.worker_id = "wA"
    listener = Listener(worker=worker, store=store)
    runs: list[str] = []

    def fake_stages(*args: Any, **kwargs: Any) -> None:
        runs.append("stages")

    monkeypatch.setattr(worker, "_run_stages", fake_stages)
    monkeypatch.setattr(Worker, "pull_world", lambda self: False)
    planned: list[str] = []
    orig_set_plan = store.set_plan

    def counting_set_plan(tile: str, plan: dict[str, Any]) -> None:
        planned.append(tile)
        orig_set_plan(tile, plan)

    monkeypatch.setattr(store, "set_plan", counting_set_plan)

    store.enabled = False
    listener._dispatch("build_job", "j1")
    listener._dispatch("request", "n_413_478")
    assert worker.poll() == []
    assert store.build_jobs["j1"]["status"] == "queued"
    assert "n_413_478" not in store.plans
    assert runs == []
    assert planned == []

    store.enabled = True
    found = worker.poll()
    assert ("build_job", "j1") in found
    assert ("request", "n_413_478") in found
    assert worker.poll() == []
    for kind, doc_id in found:
        listener._dispatch(kind, doc_id)
    assert store.build_jobs["j1"]["status"] == "checking"
    assert "n_413_478" in store.plans
    assert runs == ["stages"]
    assert planned == ["n_413_478"]


def test_reenable_protects_owned_terminal_and_planned(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#158: live-owned, terminal and planned work survives the reconcile."""
    from builder.worker import Listener

    store = FakeStore()
    store.build_jobs["live"] = _owned_job(owner="wB")
    store.build_jobs["pub"] = _enrich_job(status="published")
    store.build_jobs["fail"] = _enrich_job(status="failed")
    store.requests["n_413_478"] = {"tile": "n_413_478"}
    store.plans["n_413_478"] = {"tile": "n_413_478"}
    worker = _planned_worker(tmp_path, store, monkeypatch)
    worker.worker_id = "wA"
    listener = Listener(worker=worker, store=store)
    runs: list[str] = []

    def fake_stages(*args: Any, **kwargs: Any) -> None:
        runs.append("stages")

    monkeypatch.setattr(worker, "_run_stages", fake_stages)
    monkeypatch.setattr(Worker, "pull_world", lambda self: False)

    store.enabled = False
    assert worker.poll() == []
    store.enabled = True
    found = worker.poll()
    ids = [doc_id for kind, doc_id in found if kind == "build_job"]
    assert "live" in ids
    assert "pub" not in ids
    assert "fail" not in ids
    assert ("request", "n_413_478") not in found
    for kind, doc_id in found:
        listener._dispatch(kind, doc_id)
    assert runs == []
    assert store.build_jobs["live"]["worker_id"] == "wB"
    assert store.build_jobs["live"]["status"] == "building"


def _paused_job(store: FakeStore, action: str, receipt) -> None:  # type: ignore[no-untyped-def]
    """A paused enrich job with a held reservation, awaiting the operator."""
    from builder.ledger import receipt_refs

    job = _enrich_job(status="building")
    job["error"] = "paused_spend_uncertain"
    job["operator_action"] = action
    job["progress"] = [
        {
            "id": "enrich_lion",
            "status": "paused_spend_uncertain",
            "uncertain": {"host": "meta", **receipt_refs(receipt)},
        }
    ]
    store.build_jobs["j1"] = job


def test_operator_resume_goes_on(tmp_path: Path, monkeypatch: Any) -> None:
    """#166.3: resume settles the held spend, then the item runs."""
    from types import SimpleNamespace

    store = FakeStore()
    provider, allowance, ledger, receipt = _held_pair(tmp_path)
    _paused_job(store, "resume", receipt)
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.worker_id = "wA"
    worker.chat = SimpleNamespace(ledger=ledger, model="muse-spark-1.3")

    def fake_stages(*args: Any, **kwargs: Any) -> None:
        (args[5] / "animals").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worker, "_run_stages", fake_stages)
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "checking"
    assert "operator_action" not in job
    assert job.get("error") is None
    assert job["progress"][-1]["status"] == "checking"
    for pool, side in ((provider, receipt.provider), (allowance, receipt.allowance)):
        data = json.loads(pool.path.read_text(encoding="utf-8"))
        assert side.id in data["settled"]
        assert pool.job_spent("j1") == pytest.approx(0.05)


def test_operator_give_up_fails_and_records(tmp_path: Path, monkeypatch: Any) -> None:
    """#166.3: give-up settles the held spend, records it, fails the job."""
    from types import SimpleNamespace

    store = FakeStore()
    provider, allowance, ledger, receipt = _held_pair(tmp_path)
    _paused_job(store, "give_up", receipt)
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.worker_id = "wA"
    worker.chat = SimpleNamespace(ledger=ledger, model="muse-spark-1.3")

    def no_stages(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("no stages run on give-up")

    monkeypatch.setattr(worker, "_run_stages", no_stages)
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "failed"
    assert job["error"] == "gave_up"
    assert "operator_action" not in job
    assert job["progress"][0]["status"] == "failed"
    assert job["uncertain_usd"] == pytest.approx(0.05)
    for pool, side in ((provider, receipt.provider), (allowance, receipt.allowance)):
        data = json.loads(pool.path.read_text(encoding="utf-8"))
        assert side.id in data["settled"]


def test_stale_operator_action_clears_and_builds(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#166.3: a command with nothing paused clears; the job builds on."""
    store = FakeStore()
    job = _enrich_job(status="building")
    job["operator_action"] = "resume"
    store.build_jobs["j1"] = job
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.worker_id = "wA"

    def fake_stages(*args: Any, **kwargs: Any) -> None:
        (args[5] / "animals").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worker, "_run_stages", fake_stages)
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert "operator_action" not in job
    assert job["status"] == "checking"


def test_resume_settles_superseded_timeout_at_estimate(tmp_path: Path) -> None:
    """#157: a second attempt settles the first one's record at estimate."""
    from types import SimpleNamespace

    from builder.ledger import receipt_refs

    store = FakeStore()
    store.build_jobs["j1"] = _owned_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    provider, allowance, ledger, receipt = _held_pair(tmp_path)
    store.build_jobs["j1"]["progress"] = [
        {
            "id": "enrich_lion",
            "status": "paused_spend_uncertain",
            "uncertain": {"host": "meta", **receipt_refs(receipt)},
        }
    ]
    ctx = SimpleNamespace(client=SimpleNamespace(ledger=ledger), open_client=None)
    worker._settle_superseded("j1", {"id": "enrich_lion"}, ctx)
    worker._settle_superseded("j1", {"id": "enrich_lion"}, ctx)
    for pool, side in ((provider, receipt.provider), (allowance, receipt.allowance)):
        data = json.loads(pool.path.read_text(encoding="utf-8"))
        assert side.id in data["settled"]
        assert pool.job_spent("j1") == pytest.approx(0.05)
