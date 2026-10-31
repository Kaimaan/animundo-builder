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

    def list_requests(self) -> list[tuple[str, dict[str, Any]]]:
        return [
            (snap.id, snap.to_dict() or {})
            for snap in self._db.collection("border_requests").stream()
        ]

    def get_enabled(self) -> bool:
        """The off switch: `config/builder.enabled`, on when missing."""
        snap = self._db.collection("config").document("builder").get()
        data = snap.to_dict() if snap.exists else None
        return bool((data or {}).get("enabled", True))

    def get_plan(self, tile: str) -> dict[str, Any] | None:
        snap = self._db.collection("plans").document(tile).get()
        return snap.to_dict() if snap.exists else None

    def set_plan(self, tile: str, doc: dict[str, Any]) -> None:
        self._db.collection("plans").document(tile).set(doc)

    def list_plans(self) -> list[tuple[str, dict[str, Any]]]:
        return [
            (snap.id, snap.to_dict() or {})
            for snap in self._db.collection("plans").stream()
        ]

    # -- build jobs ------------------------------------------------------

    def get_build_job(self, job_id: str) -> dict[str, Any] | None:
        snap = self._db.collection("build_jobs").document(job_id).get()
        return snap.to_dict() if snap.exists else None

    def update_build_job(self, job_id: str, fields: dict[str, Any]) -> None:
        self._db.collection("build_jobs").document(job_id).update(_translated(fields))

    def claim_build_job(self, job_id: str, worker_id: str) -> bool:
        """Claim a job atomically: queued, own, or an expired lease (#155)."""
        from .worker import lease_fresh_iso, now_iso

        ref = self._db.collection("build_jobs").document(job_id)
        return _claim_lease(
            self._db.transaction(), ref, worker_id, now_iso(), lease_fresh_iso()
        )

    def renew_build_job(self, job_id: str, worker_id: str) -> bool:
        """Extend our own lease atomically; False when it is not ours."""
        from .worker import lease_fresh_iso, now_iso

        ref = self._db.collection("build_jobs").document(job_id)
        return _renew_lease(
            self._db.transaction(), ref, worker_id, now_iso(), lease_fresh_iso()
        )

    def update_build_job_if_owner(
        self, job_id: str, worker_id: str, fields: dict[str, Any]
    ) -> bool:
        """Write only while owning the job, atomically (#155)."""
        ref = self._db.collection("build_jobs").document(job_id)
        return _update_if_owner(self._db.transaction(), ref, worker_id, dict(fields))

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

    def add_tile_history(self, item: dict[str, Any]) -> str:
        _, ref = self._db.collection("tile_history").add(item)
        return ref.id

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        snap = self._db.collection("jobs").document(job_id).get()
        return snap.to_dict() if snap.exists else None

    def set_job(self, job_id: str, doc: dict[str, Any]) -> None:
        self._db.collection("jobs").document(job_id).set(doc)

    def update_job(self, job_id: str, fields: dict[str, Any]) -> None:
        self._db.collection("jobs").document(job_id).update(_translated(fields))

    # -- Storage blobs: tiles, review bundles, previews ----------------------

    def read_blob(self, path: str) -> bytes | None:
        blob = self._bucket.blob(path)
        return blob.download_as_bytes() if blob.exists() else None

    def write_blob(
        self, path: str, data: bytes, content_type: str = "application/json"
    ) -> None:
        self._bucket.blob(path).upload_from_string(data, content_type=content_type)

    def list_blobs(self, prefix: str) -> list[str]:
        return [blob.name for blob in self._bucket.list_blobs(prefix=prefix)]

    # -- watch -------------------------------------------------------------

    def watch_requests(self, callback: Any) -> Any:
        return self._db.collection("border_requests").on_snapshot(callback)

    def watch_build_jobs(self, callback: Any) -> Any:
        return self._db.collection("build_jobs").on_snapshot(callback)


#: Every store method the worker calls. Both the real `FirebaseStore`
#: and the tests' fake must have them all.
STORE_METHODS = (
    "add_alert",
    "add_tile_history",
    "claim_build_job",
    "get_build_job",
    "get_enabled",
    "get_job",
    "get_plan",
    "get_request",
    "list_blobs",
    "list_plans",
    "list_requests",
    "pending_build_jobs",
    "read_blob",
    "renew_build_job",
    "set_job",
    "set_plan",
    "update_build_job",
    "update_build_job_if_owner",
    "update_job",
    "watch_build_jobs",
    "watch_requests",
    "write_blob",
)


@firestore.transactional
def _claim_lease(
    transaction: Any, ref: Any, worker_id: str, at: str, fresh: str
) -> bool:
    """One transaction: queued, ours already, or an expired lease (#155)."""
    from .worker import lease_expired

    snap = ref.get(transaction=transaction)
    if not snap.exists:
        return False
    data = snap.to_dict() or {}
    status = data.get("status") or "queued"
    if status not in ("queued", "building", "checking"):
        return False
    if status != "queued":
        if data.get("worker_id") != worker_id and not lease_expired(
            data.get("lease_until")
        ):
            return False
    update: dict[str, Any] = {
        "worker_id": worker_id,
        "lease_until": fresh,
        "updated_at": at,
    }
    if status == "queued":
        update["status"] = "building"
    transaction.update(ref, update)
    return True


@firestore.transactional
def _renew_lease(
    transaction: Any, ref: Any, worker_id: str, at: str, fresh: str
) -> bool:
    """One transaction: still ours, the lease extends."""
    snap = ref.get(transaction=transaction)
    if not snap.exists:
        return False
    data = snap.to_dict() or {}
    if data.get("worker_id") != worker_id:
        return False
    transaction.update(ref, {"lease_until": fresh, "updated_at": at})
    return True


def _translated(fields: dict[str, Any]) -> dict[str, Any]:
    """Caller fields as Firestore writes: `DELETE` deletes (#172.1).

    Every update path translates; `set` and `add` take whole new
    documents, where a delete marker is meaningless, so the worker
    never sends one there.
    """
    from .worker import DELETE

    return {
        key: firestore.DELETE_FIELD if value is DELETE else value
        for key, value in fields.items()
    }


@firestore.transactional
def _update_if_owner(
    transaction: Any, ref: Any, worker_id: str, fields: dict[str, Any]
) -> bool:
    """One transaction: still ours, the fields land."""
    snap = ref.get(transaction=transaction)
    if not snap.exists:
        return False
    data = snap.to_dict() or {}
    if data.get("worker_id") != worker_id:
        return False
    transaction.update(ref, _translated(fields))
    return True
