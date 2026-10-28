"""`builder worker --listen`: grow the world from Firestore.

Phase 0 keeps the listener and the atomic claim; the draft flows
are gone and the border flows (intake, planning, building) land
in phase 2. This worker watches `border_requests`,
`judge_builds` and `admin_actions` and logs what phase 2 will
build. Nothing is built yet.
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
from .schemas import validate

#: A document update value that deletes the field.
DELETE = object()

#: Errors worth one more attempt (two retries, then a person looks).
TRANSIENT = (ConnectionError, TimeoutError)


class RoundFailed(Exception):
    """One round failed with a short error code for the area."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


class Store(Protocol):
    """The Firestore surface the worker needs."""

    def get_request(self, tile: str) -> dict[str, Any] | None: ...
    def get_area(self, tile: str) -> dict[str, Any] | None: ...
    def update_area(self, tile: str, fields: dict[str, Any]) -> None: ...
    def claim_area(self, tile: str, worker_id: str) -> bool: ...
    def get_judge_build(self, build_id: str) -> dict[str, Any] | None: ...
    def update_judge_build(self, build_id: str, fields: dict[str, Any]) -> None: ...
    def get_action(self, action_id: str) -> dict[str, Any] | None: ...
    def update_action(self, action_id: str, fields: dict[str, Any]) -> None: ...
    def pending_actions(self) -> list[tuple[str, dict[str, Any]]]: ...
    def add_inbox(self, item: dict[str, Any]) -> str: ...
    def get_job(self, job_id: str) -> dict[str, Any] | None: ...
    def set_job(self, job_id: str, doc: dict[str, Any]) -> None: ...
    def update_job(self, job_id: str, fields: dict[str, Any]) -> None: ...


def default_worker_id() -> str:
    """This worker, for the area it claims: host and pid."""
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

    def note_judge_build(self, build_id: str, data: dict[str, Any]) -> bool:
        """Queue a judge build unless it is already queued."""
        return ("judge_build", build_id) not in self.in_flight

    def note_action(self, action_id: str, data: dict[str, Any]) -> bool:
        """Queue an admin action unless it is done or already queued."""
        if data.get("done_at") or ("action", action_id) in self.in_flight:
            return False
        return True

    # -- top-level flows (phase 2 builds these) ---------------------

    def handle_request(self, tile: str) -> None:
        print(f"request {tile}: phase 2 plans and routes this")

    def handle_judge_build(self, build_id: str) -> None:
        print(f"judge build {build_id}: phase 2 builds this")

    def handle_action(self, action_id: str) -> None:
        print(f"action {action_id}: phase 2 carries this out")

    # -- helpers ------------------------------------------------------

    def inbox(self, item: dict[str, Any]) -> str:
        full = {"handled": False, "at": now_iso(), **item}
        validate("admin-inbox-item", full)
        return self.store.add_inbox(full)

    def _finish_action(self, action_id: str, error: str | None) -> None:
        fields: dict[str, Any] = {"done_at": now_iso()}
        if error is not None:
            fields["error"] = error
        self.store.update_action(action_id, fields)


@dataclass
class Listener:
    """The `--listen` loop: Firestore snapshots in, one queue, FIFO out."""

    worker: Worker
    store: Any  # FirebaseStore: watch_requests/watch_judge_builds/watch_actions
    events: queue.Queue = field(default_factory=queue.Queue)
    stop: threading.Event = field(default_factory=threading.Event)

    def start(self) -> None:
        for action_id, doc in self.store.pending_actions():
            if self.worker.note_action(action_id, doc):
                self.events.put(("action", action_id))
        unsub_requests = self.store.watch_requests(self._on_requests)
        unsub_judge_builds = self.store.watch_judge_builds(self._on_judge_builds)
        unsub_actions = self.store.watch_actions(self._on_actions)
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
            unsub_judge_builds()
            unsub_actions()

    def _on_requests(self, snapshots: Any, changes: Any, _read_time: Any) -> None:
        for change in changes:
            if change.type.name == "REMOVED":
                continue
            data = change.document.to_dict() or {}
            if self.worker.note_request(change.document.id, data):
                self.events.put(("request", change.document.id))

    def _on_judge_builds(self, snapshots: Any, changes: Any, _read_time: Any) -> None:
        for change in changes:
            if change.type.name == "REMOVED":
                continue
            data = change.document.to_dict() or {}
            if self.worker.note_judge_build(change.document.id, data):
                self.events.put(("judge_build", change.document.id))

    def _on_actions(self, snapshots: Any, changes: Any, _read_time: Any) -> None:
        for change in changes:
            if change.type.name == "REMOVED":
                continue
            data = change.document.to_dict() or {}
            if self.worker.note_action(change.document.id, data):
                self.events.put(("action", change.document.id))

    def _dispatch(self, kind: str, doc_id: str) -> None:
        key = (kind, doc_id)
        if key in self.worker.in_flight:
            return
        self.worker.in_flight.add(key)
        try:
            print(f"worker: {kind} {doc_id}")
            if kind == "request":
                self.worker.handle_request(doc_id)
            elif kind == "judge_build":
                self.worker.handle_judge_build(doc_id)
            elif kind == "action":
                self.worker.handle_action(doc_id)
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
