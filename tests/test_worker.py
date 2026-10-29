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
    monkeypatch.setattr("builder.tiles.fetch_smoothed", fake_fetch)
    world = make_world(tmp_path)
    grid_dir = write_grids(tmp_path / "grids", boreal_3x3())
    worker = _worker(tmp_path, store)
    worker.world = world
    worker.grid_dir = grid_dir
    worker.world_commit = "abc123"
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
