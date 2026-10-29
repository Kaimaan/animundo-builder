"""Shared fake: an in-memory Firestore plus Storage blobs."""

from __future__ import annotations

import threading
from typing import Any

from builder.worker import DELETE


class FakeStore:
    """An in-memory Firestore."""

    def __init__(self) -> None:
        self.requests: dict[str, dict[str, Any]] = {}
        self.plans: dict[str, dict[str, Any]] = {}
        self.build_jobs: dict[str, dict[str, Any]] = {}
        self.alerts: list[dict[str, Any]] = []
        self.jobs: dict[str, dict[str, Any]] = {}
        self.watchers: list[tuple[str, Any]] = []
        self.blobs: dict[str, bytes] = {}
        self.enabled = True
        self._lock = threading.Lock()

    def get_request(self, tile: str) -> dict[str, Any] | None:
        return self.requests.get(tile)

    def list_requests(self) -> list[tuple[str, dict[str, Any]]]:
        return list(self.requests.items())

    def list_plans(self) -> list[tuple[str, dict[str, Any]]]:
        return list(self.plans.items())

    def get_enabled(self) -> bool:
        return self.enabled

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

    def read_blob(self, path: str) -> bytes | None:
        return self.blobs.get(path)

    def write_blob(
        self, path: str, data: bytes, content_type: str = "application/json"
    ) -> None:
        self.blobs[path] = data

    def list_blobs(self, prefix: str) -> list[str]:
        return sorted(p for p in self.blobs if p.startswith(prefix))
