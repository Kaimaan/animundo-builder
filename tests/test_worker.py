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
        self.areas: dict[str, dict[str, Any]] = {}
        self.judge_builds: dict[str, dict[str, Any]] = {}
        self.actions: dict[str, dict[str, Any]] = {}
        self.inbox: list[dict[str, Any]] = []
        self.jobs: dict[str, dict[str, Any]] = {}
        self.watchers: list[tuple[str, Any]] = []
        self._lock = threading.Lock()

    def get_request(self, tile: str) -> dict[str, Any] | None:
        return self.requests.get(tile)

    def get_area(self, tile: str) -> dict[str, Any] | None:
        return self.areas.get(tile)

    def update_area(self, tile: str, fields: dict[str, Any]) -> None:
        doc = self.areas[tile]
        for key, value in fields.items():
            if value is DELETE:
                doc.pop(key, None)
            else:
                doc[key] = value

    def claim_area(self, tile: str, worker_id: str) -> bool:
        with self._lock:
            doc = self.areas.get(tile)
            if doc is None or doc.get("status") != "waiting" or doc.get("worker_id"):
                return False
            doc["worker_id"] = worker_id
            return True

    def get_judge_build(self, build_id: str) -> dict[str, Any] | None:
        return self.judge_builds.get(build_id)

    def update_judge_build(self, build_id: str, fields: dict[str, Any]) -> None:
        doc = self.judge_builds[build_id]
        for key, value in fields.items():
            if value is DELETE:
                doc.pop(key, None)
            else:
                doc[key] = value

    def get_action(self, action_id: str) -> dict[str, Any] | None:
        return self.actions.get(action_id)

    def update_action(self, action_id: str, fields: dict[str, Any]) -> None:
        self.actions[action_id].update(fields)

    def pending_actions(self) -> list[tuple[str, dict[str, Any]]]:
        return [
            (action_id, doc)
            for action_id, doc in self.actions.items()
            if not doc.get("done_at")
        ]

    def add_inbox(self, item: dict[str, Any]) -> str:
        self.inbox.append(item)
        return f"inbox-{len(self.inbox)}"

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self.jobs.get(job_id)

    def set_job(self, job_id: str, doc: dict[str, Any]) -> None:
        self.jobs[job_id] = doc

    def update_job(self, job_id: str, fields: dict[str, Any]) -> None:
        self.jobs[job_id].update(fields)

    def watch_requests(self, callback: Any) -> Any:
        self.watchers.append(("requests", callback))
        return lambda: None

    def watch_judge_builds(self, callback: Any) -> Any:
        self.watchers.append(("judge_builds", callback))
        return lambda: None

    def watch_actions(self, callback: Any) -> Any:
        self.watchers.append(("actions", callback))
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


def test_note_judge_build_queues_once(tmp_path: Path) -> None:
    worker = _worker(tmp_path, FakeStore())
    assert worker.note_judge_build("b1", {}) is True
    worker.in_flight.add(("judge_build", "b1"))
    assert worker.note_judge_build("b1", {}) is False


def test_note_action_skips_done_and_in_flight(tmp_path: Path) -> None:
    worker = _worker(tmp_path, FakeStore())
    assert worker.note_action("a1", {}) is True
    assert worker.note_action("a2", {"done_at": "then"}) is False
    worker.in_flight.add(("action", "a1"))
    assert worker.note_action("a1", {}) is False


def test_claim_area_leaves_one_winner() -> None:
    store = FakeStore()
    store.areas["n_413_296"] = {"status": "waiting"}
    assert store.claim_area("n_413_296", "w1") is True
    assert store.claim_area("n_413_296", "w2") is False
    assert store.areas["n_413_296"]["worker_id"] == "w1"


def test_claim_area_needs_waiting() -> None:
    store = FakeStore()
    assert store.claim_area("n_000_000", "w1") is False
    store.areas["n_413_296"] = {"status": "ready"}
    assert store.claim_area("n_413_296", "w1") is False


def test_two_workers_on_one_area_only_one_claims(tmp_path: Path) -> None:
    store = FakeStore()
    store.areas["n_413_296"] = {"status": "waiting"}
    first = _worker(tmp_path, store)
    second = _worker(tmp_path, store)
    assert store.claim_area("n_413_296", first.worker_id) is True
    assert store.claim_area("n_413_296", second.worker_id) is False


def test_handle_request_logs_and_builds_nothing(tmp_path: Path, capsys: Any) -> None:
    store = FakeStore()
    store.requests["n_413_296"] = {"tile": "n_413_296"}
    worker = _worker(tmp_path, store)
    worker.handle_request("n_413_296")
    assert "phase 2" in capsys.readouterr().out
    assert "n_413_296" not in store.areas


def test_handle_action_leaves_it_pending(tmp_path: Path) -> None:
    store = FakeStore()
    store.actions["a1"] = {"action": "approve_request", "tile": "n_413_296"}
    worker = _worker(tmp_path, store)
    worker.handle_action("a1")
    assert "done_at" not in store.actions["a1"]
    assert len(store.pending_actions()) == 1


def test_dispatch_runs_and_releases(tmp_path: Path) -> None:
    worker = _worker(tmp_path, FakeStore())
    listener = Listener(worker=worker, store=FakeStore())
    listener._dispatch("request", "n_413_296")
    assert ("request", "n_413_296") not in worker.in_flight
    listener._dispatch("judge_build", "b1")
    assert ("judge_build", "b1") not in worker.in_flight
    listener._dispatch("action", "a1")
    assert ("action", "a1") not in worker.in_flight


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
