"""`builder worker --listen`: plan border requests, build jobs.

The builder is a factory (D17): the game runs the world, the worker
plans every new or changed border request and builds only the build
jobs the game creates. Phase 0 keeps the listener and the atomic
claim; planning (T74) and building (T76) land in phase 2. This
worker watches `border_requests` and `build_jobs` and logs what
phase 2 will do. Nothing is built yet.
"""

from __future__ import annotations

import os
import queue
import socket
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .config import Config

#: A document update value that deletes the field.
DELETE = object()

#: Errors worth one more attempt (two retries, then a person looks).
TRANSIENT = (ConnectionError, TimeoutError)


class RoundFailed(Exception):
    """One round failed with a short error code for the job."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


class Store(Protocol):
    """The Firestore surface the worker needs."""

    def get_request(self, tile: str) -> dict[str, Any] | None: ...
    def get_plan(self, tile: str) -> dict[str, Any] | None: ...
    def set_plan(self, tile: str, doc: dict[str, Any]) -> None: ...
    def get_build_job(self, job_id: str) -> dict[str, Any] | None: ...
    def update_build_job(self, job_id: str, fields: dict[str, Any]) -> None: ...
    def claim_build_job(self, job_id: str, worker_id: str) -> bool: ...
    def pending_build_jobs(self) -> list[tuple[str, dict[str, Any]]]: ...
    def add_alert(self, item: dict[str, Any]) -> str: ...
    def get_job(self, job_id: str) -> dict[str, Any] | None: ...
    def set_job(self, job_id: str, doc: dict[str, Any]) -> None: ...
    def update_job(self, job_id: str, fields: dict[str, Any]) -> None: ...


def default_worker_id() -> str:
    """This worker, for the job it claims: host and pid."""
    return f"{socket.gethostname()}:{os.getpid()}"


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Worker:
    """The phase-0 worker: it listens, claims and logs. Phase 2 builds."""

    store: Store
    jobs_dir: Path
    world: Path | None
    config: Config
    worker_id: str = field(default_factory=default_worker_id)
    author: str = "Animundo Builder"
    in_flight: set[tuple[str, str]] = field(default_factory=set)
    runner: Callable[..., dict[str, Any]] | None = None

    # -- events ----------------------------------------------------

    def note_request(self, tile: str, data: dict[str, Any]) -> bool:
        """Queue a border request unless it is already queued."""
        return ("request", tile) not in self.in_flight

    def note_build_job(self, job_id: str, data: dict[str, Any]) -> bool:
        """Queue a build job unless it is done or already queued."""
        if data.get("status") in ("published", "failed"):
            return False
        return ("build_job", job_id) not in self.in_flight

    # -- top-level flows (phase 2 builds these) ---------------------

    def handle_request(self, tile: str) -> None:
        print(f"request {tile}: phase 2 plans this")

    def handle_build_job(self, job_id: str) -> None:
        print(f"build job {job_id}: phase 2 builds this")

    # -- helpers ------------------------------------------------------

    def alert(self, item: dict[str, Any]) -> str:
        full = {"at": now_iso(), **item}
        return self.store.add_alert(full)


@dataclass
class Listener:
    """The `--listen` loop: Firestore snapshots in, one queue, FIFO out."""

    worker: Worker
    store: Any  # FirebaseStore: watch_requests/watch_build_jobs
    events: queue.Queue = field(default_factory=queue.Queue)
    stop: threading.Event = field(default_factory=threading.Event)

    def start(self) -> None:
        for job_id, doc in self.store.pending_build_jobs():
            if self.worker.note_build_job(job_id, doc):
                self.events.put(("build_job", job_id))
        unsub_requests = self.store.watch_requests(self._on_requests)
        unsub_jobs = self.store.watch_build_jobs(self._on_build_jobs)
        print("worker listening (Ctrl-C stops it)")
        try:
            while not self.stop.is_set():
                try:
                    kind, doc_id = self.events.get(timeout=1.0)
                except queue.Empty:
                    continue
                self._dispatch(kind, doc_id)
                self.events.task_done()
        except KeyboardInterrupt:
            pass
        finally:
            unsub_requests()
            unsub_jobs()

    def _on_requests(self, snapshots: Any, changes: Any, _read_time: Any) -> None:
        for change in changes:
            if change.type.name == "REMOVED":
                continue
            data = change.document.to_dict() or {}
            if self.worker.note_request(change.document.id, data):
                self.events.put(("request", change.document.id))

    def _on_build_jobs(self, snapshots: Any, changes: Any, _read_time: Any) -> None:
        for change in changes:
            if change.type.name == "REMOVED":
                continue
            data = change.document.to_dict() or {}
            if self.worker.note_build_job(change.document.id, data):
                self.events.put(("build_job", change.document.id))

    def _dispatch(self, kind: str, doc_id: str) -> None:
        key = (kind, doc_id)
        if key in self.worker.in_flight:
            return
        self.worker.in_flight.add(key)
        try:
            print(f"worker: {kind} {doc_id}")
            if kind == "request":
                self.worker.handle_request(doc_id)
            elif kind == "build_job":
                self.worker.handle_build_job(doc_id)
            else:
                print(f"worker: unknown kind {kind}", file=sys.stderr)
        except RoundFailed as error:
            print(f"worker: {kind} {doc_id} failed: {error}", file=sys.stderr)
        except Exception as error:  # noqa: BLE001 - the loop must live
            print(f"worker: {kind} {doc_id} crashed: {error!r}", file=sys.stderr)
        finally:
            self.worker.in_flight.discard(key)


def listen(
    config: Config,
    jobs_dir: Path,
    world: Path | None,
    project: str | None,
    bucket: str | None = None,
) -> int:
    """Connect with Application Default Credentials and listen forever."""
    from .worker_store import FirebaseStore, StoreError

    try:
        store = FirebaseStore(project, bucket)
    except StoreError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    worker = Worker(
        store=store,
        jobs_dir=jobs_dir,
        world=world,
        config=config,
    )
    Listener(worker=worker, store=store).start()
    return 0
