"""The phase-0 worker: it listens, claims and logs. No Firebase."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from builder.config import Config
from builder.worker import DELETE, Listener, Worker
from builder.worker_store import STORE_METHODS, FirebaseStore


class FakeStore:
    """An in-memory Firestore."""

    def __init__(self) -> None:
        self.requests: dict[str, dict[str, Any]] = {}
        self.plans: dict[str, dict[str, Any]] = {}
        self.build_jobs: dict[str, dict[str, Any]] = {}
        self.alerts: list[dict[str, Any]] = []
        self.jobs: dict[str, dict[str, Any]] = {}
        self.watchers: list[tuple[str, Any]] = []
        self._lock = threading.Lock()

    def get_request(self, tile: str) -> dict[str, Any] | None:
        return self.requests.get(tile)

    def get_plan(self, tile: str) -> dict[str, Any] | None:
        return self.plans.get(tile)

    def set_plan(self, tile: str, doc: dict[str, Any]) -> None:
        self.plans[tile] = doc

    def get_build_job(self, job_id: str) -> dict[str, Any] | None:
        return self.build_jobs.get(job_id)

    def update_build_job(self, job_id: str, fields: dict[str, Any]) -> None:
        doc = self.build_jobs[job_id]
        for key, value in fields.items():
            if value is DELETE:
                doc.pop(key, None)
            else:
                doc[key] = value

    def claim_build_job(self, job_id: str, worker_id: str) -> bool:
        with self._lock:
            doc = self.build_jobs.get(job_id)
            if doc is None or doc.get("status") != "queued" or doc.get("worker_id"):
                return False
            doc["worker_id"] = worker_id
            return True

    def pending_build_jobs(self) -> list[tuple[str, dict[str, Any]]]:
        return [
            (job_id, doc)
            for job_id, doc in self.build_jobs.items()
            if doc.get("status") not in ("published", "failed")
        ]

    def add_alert(self, item: dict[str, Any]) -> str:
        self.alerts.append(item)
        return f"alert-{len(self.alerts)}"

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self.jobs.get(job_id)

    def set_job(self, job_id: str, doc: dict[str, Any]) -> None:
        self.jobs[job_id] = doc

    def update_job(self, job_id: str, fields: dict[str, Any]) -> None:
        self.jobs[job_id].update(fields)

    def watch_requests(self, callback: Any) -> Any:
        self.watchers.append(("requests", callback))
        return lambda: None

    def watch_build_jobs(self, callback: Any) -> Any:
        self.watchers.append(("build_jobs", callback))
        return lambda: None


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


def test_handle_request_logs_and_plans_nothing(tmp_path: Path, capsys: Any) -> None:
    store = FakeStore()
    store.requests["n_413_296"] = {"tile": "n_413_296"}
    worker = _worker(tmp_path, store)
    worker.handle_request("n_413_296")
    assert "phase 2" in capsys.readouterr().out
    assert "n_413_296" not in store.plans


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
