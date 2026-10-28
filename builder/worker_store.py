"""The worker's Firestore backend, via the Admin SDK.

Connects with Application Default Credentials (`gcloud auth
application-default login` on Kai's Mac, the service account on
Modal later), so no key file. Honors the emulator
environment variables when they are set.
"""

from __future__ import annotations

from typing import Any

import firebase_admin
from firebase_admin import firestore, storage


class StoreError(Exception):
    """The worker cannot reach Firebase. A person looks."""


class FirebaseStore:
    """`Worker`'s store on the real Firestore and Storage."""

    def __init__(self, project: str | None, bucket_name: str | None = None) -> None:
        try:
            try:
                app = firebase_admin.get_app()
            except ValueError:
                options: dict[str, Any] = {}
                if project is not None:
                    options["projectId"] = project
                if bucket_name is not None:
                    options["storageBucket"] = bucket_name
                app = firebase_admin.initialize_app(options=options or None)
        except Exception as error:
            raise StoreError(
                f"cannot reach Firebase with Application Default Credentials: {error}"
            ) from error
        self._db = firestore.client(app)
        try:
            self._bucket = storage.bucket(app=app)
        except Exception as error:
            raise StoreError(f"cannot open the Storage bucket: {error}") from error

    # -- requests and plans --------------------------------------------

    def get_request(self, tile: str) -> dict[str, Any] | None:
        snap = self._db.collection("border_requests").document(tile).get()
        return snap.to_dict() if snap.exists else None

    def get_plan(self, tile: str) -> dict[str, Any] | None:
        snap = self._db.collection("plans").document(tile).get()
        return snap.to_dict() if snap.exists else None

    def set_plan(self, tile: str, doc: dict[str, Any]) -> None:
        self._db.collection("plans").document(tile).set(doc)

    # -- build jobs ------------------------------------------------------

    def get_build_job(self, job_id: str) -> dict[str, Any] | None:
        snap = self._db.collection("build_jobs").document(job_id).get()
        return snap.to_dict() if snap.exists else None

    def update_build_job(self, job_id: str, fields: dict[str, Any]) -> None:
        from .worker import DELETE

        self._db.collection("build_jobs").document(job_id).update(
            {
                key: firestore.DELETE_FIELD if value is DELETE else value
                for key, value in fields.items()
            }
        )

    def claim_build_job(self, job_id: str, worker_id: str) -> bool:
        """Claim a queued job atomically; the loser gets False."""
        from .worker import now_iso

        ref = self._db.collection("build_jobs").document(job_id)
        return _claim_queued(self._db.transaction(), ref, worker_id, now_iso())

    def pending_build_jobs(self) -> list[tuple[str, dict[str, Any]]]:
        return [
            (snap.id, snap.to_dict() or {})
            for snap in self._db.collection("build_jobs").stream()
            if (snap.to_dict() or {}).get("status") not in ("published", "failed")
        ]

    # -- alerts and the worker's own jobs ----------------------------------

    def add_alert(self, item: dict[str, Any]) -> str:
        _, ref = self._db.collection("builder_alerts").add(item)
        return ref.id

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        snap = self._db.collection("jobs").document(job_id).get()
        return snap.to_dict() if snap.exists else None

    def set_job(self, job_id: str, doc: dict[str, Any]) -> None:
        self._db.collection("jobs").document(job_id).set(doc)

    def update_job(self, job_id: str, fields: dict[str, Any]) -> None:
        self._db.collection("jobs").document(job_id).update(fields)

    # -- watch -------------------------------------------------------------

    def watch_requests(self, callback: Any) -> Any:
        return self._db.collection("border_requests").on_snapshot(callback)

    def watch_build_jobs(self, callback: Any) -> Any:
        return self._db.collection("build_jobs").on_snapshot(callback)


#: Every store method the worker calls. Both the real `FirebaseStore`
#: and the tests' fake must have them all.
STORE_METHODS = (
    "add_alert",
    "claim_build_job",
    "get_build_job",
    "get_job",
    "get_plan",
    "get_request",
    "pending_build_jobs",
    "set_job",
    "set_plan",
    "update_build_job",
    "update_job",
    "watch_build_jobs",
    "watch_requests",
)


@firestore.transactional
def _claim_queued(transaction: Any, ref: Any, worker_id: str, at: str) -> bool:
    """One transaction: still queued and unclaimed, it is ours."""
    snap = ref.get(transaction=transaction)
    if not snap.exists:
        return False
    data = snap.to_dict() or {}
    if data.get("status") != "queued" or data.get("worker_id"):
        return False
    transaction.update(ref, {"worker_id": worker_id, "updated_at": at})
    return True
