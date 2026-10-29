"""`builder worker --listen`: plan border requests, build jobs.

The builder is a factory (D17): the game runs the world, the worker
plans every new or changed border request and builds only the build
jobs the game creates. Planning (T74) is live; building (T76)
lands next.
"""

from __future__ import annotations

import os
import queue
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .config import Config
from .plan import plan_tile

#: A document update value that deletes the field.
DELETE = object()


class Store(Protocol):
    """The Firestore surface the worker needs."""

    def get_request(self, tile: str) -> dict[str, Any] | None: ...
    def list_requests(self) -> list[tuple[str, dict[str, Any]]]: ...
    def list_plans(self) -> list[tuple[str, dict[str, Any]]]: ...
    def get_enabled(self) -> bool: ...
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


def git_commit(world: Path | None) -> str:
    """The world checkout's short commit, or "unknown" without git."""
    if world is None:
        return "unknown"
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=world,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    except OSError:
        return "unknown"
    return out or "unknown"


@dataclass
class Worker:
    """Plans border requests; build jobs land with T76."""

    store: Store
    jobs_dir: Path
    world: Path | None
    config: Config
    worker_id: str = field(default_factory=default_worker_id)
    author: str = "Animundo Builder"
    in_flight: set[tuple[str, str]] = field(default_factory=set)
    grid_dir: Path | None = None
    world_commit: str | None = None
    _grid_cache: dict[str, Any] | None = field(default=None, repr=False)

    # -- events ----------------------------------------------------

    def note_request(self, tile: str, data: dict[str, Any]) -> bool:
        """Queue a border request unless it is already queued."""
        return ("request", tile) not in self.in_flight

    def note_build_job(self, job_id: str, data: dict[str, Any]) -> bool:
        """Queue a build job unless it is done or already queued."""
        if data.get("status") in ("published", "failed"):
            return False
        return ("build_job", job_id) not in self.in_flight

    # -- top-level flows ----------------------------------------------

    def handle_request(self, tile: str) -> None:
        """Plan one border request into `plans/{tile}` (T74)."""
        if not self.store.get_enabled():
            print(f"request {tile}: the off switch is off, skipping")
            return
        if self.world is None or self.grid_dir is None:
            print(
                f"request {tile}: planning needs --world and --habitat-grid",
                file=sys.stderr,
            )
            return
        self.sync_world()
        request = self.store.get_request(tile)
        if request is None:
            return
        try:
            plan = plan_tile(
                tile,
                request,
                self._grids(),
                self.world,
                self.world_commit or "unknown",
            )
        except (ValueError, RuntimeError, FileNotFoundError, ConnectionError) as error:
            print(f"request {tile}: cannot plan: {error}", file=sys.stderr)
            self.alert({"kind": "plan_failed", "tile": tile, "error": str(error)[:200]})
            return
        self.store.set_plan(tile, plan)
        print(f"request {tile}: planned code_only={plan['code_only']}")

    def handle_build_job(self, job_id: str) -> None:
        print(f"build job {job_id}: phase 2 builds this")

    def sync_world(self, commit: str | None = None) -> None:
        """Re-plan every request when the world commit moved (T74).

        A new home can turn an AI plan into `code_only`. (The tile
        run on a new commit is phase 3's; the planner only re-plans.)
        """
        commit = commit if commit is not None else git_commit(self.world)
        if self.world_commit is None:
            self.world_commit = commit
            return
        if commit == self.world_commit or commit == "unknown":
            return
        print(f"world moved {self.world_commit}..{commit}: re-planning")
        self.world_commit = commit
        for tile, _doc in self.store.list_requests():
            self.handle_request(tile)

    # -- helpers ------------------------------------------------------

    def alert(self, item: dict[str, Any]) -> str:
        full = {"at": now_iso(), **item}
        return self.store.add_alert(full)

    def _grids(self) -> dict[str, Any]:
        """Both baked habitat grids, loaded once."""
        if self._grid_cache is None:
            from .habitats import HabitatGrid

            assert self.grid_dir is not None
            self._grid_cache = {
                name: HabitatGrid.load(self.grid_dir / f"{name}.json.gz", name)
                for name in ("ease2_n25", "ease2_s25")
            }
        return self._grid_cache


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
    grid_dir: Path | None = None,
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
        grid_dir=grid_dir,
    )
    Listener(worker=worker, store=store).start()
    return 0
