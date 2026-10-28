"""The worker's Firestore + Storage backend, via the Admin SDK.

Connects with Application Default Credentials (`gcloud auth
application-default login` on Kai's Mac, the service account on
Cloud Run later), so no key file. Honors the emulator
environment variables when they are set.
"""

from __future__ import annotations

from pathlib import Path
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

    # -- drafts --------------------------------------------------------

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        snap = self._db.collection("drafts").document(draft_id).get()
        return snap.to_dict() if snap.exists else None

    def update_draft(self, draft_id: str, fields: dict[str, Any]) -> None:
        from .worker import DELETE

        self._db.collection("drafts").document(draft_id).update(
            {
                key: firestore.DELETE_FIELD if value is DELETE else value
                for key, value in fields.items()
            }
        )

    def claim_draft(self, draft_id: str, worker_id: str) -> bool:
        """Move `requested` to `queued` atomically; the loser gets False."""
        from .worker import now_iso

        ref = self._db.collection("drafts").document(draft_id)
        return _claim_requested(self._db.transaction(), ref, worker_id, now_iso())


@firestore.transactional
def _claim_requested(transaction: Any, ref: Any, worker_id: str, at: str) -> bool:
    """One transaction: still requested, it is ours (#81.1)."""
    snap = ref.get(transaction=transaction)
    if not snap.exists or (snap.to_dict() or {}).get("status") != "requested":
        return False
    transaction.update(
        ref, {"status": "queued", "worker_id": worker_id, "updated_at": at}
    )
    return True

    def drafts_by_owner(self, owner_uid: str) -> list[tuple[str, dict[str, Any]]]:
        return [
            (snap.id, snap.to_dict() or {})
            for snap in self._db.collection("drafts")
            .where("owner_uid", "==", owner_uid)
            .stream()
        ]

    def requested_drafts(self) -> list[tuple[str, dict[str, Any]]]:
        return [
            (snap.id, snap.to_dict() or {})
            for snap in self._db.collection("drafts")
            .where("status", "==", "requested")
            .stream()
        ]

    def drafts_in_statuses(
        self, statuses: list[str]
    ) -> list[tuple[str, dict[str, Any]]]:
        return [
            (snap.id, snap.to_dict() or {})
            for snap in self._db.collection("drafts")
            .where("status", "in", statuses)
            .stream()
        ]

    # -- feed, inbox, jobs ----------------------------------------------

    def add_feed(self, draft_id: str, entry: dict[str, Any]) -> None:
        self._db.collection("drafts").document(draft_id).collection("feed").document(
            str(entry["n"])
        ).set(entry)

    def feed_count(self, draft_id: str) -> int:
        return len(
            list(
                self._db.collection("drafts")
                .document(draft_id)
                .collection("feed")
                .stream()
            )
        )

    def add_inbox(self, item: dict[str, Any]) -> str:
        _, ref = self._db.collection("admin_inbox").add(item)
        return ref.id

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        snap = self._db.collection("jobs").document(job_id).get()
        return snap.to_dict() if snap.exists else None

    def set_job(self, job_id: str, doc: dict[str, Any]) -> None:
        self._db.collection("jobs").document(job_id).set(doc)

    def update_job(self, job_id: str, fields: dict[str, Any]) -> None:
        self._db.collection("jobs").document(job_id).update(fields)

    def set_public(self, area_id: str, doc: dict[str, Any]) -> None:
        self._db.collection("places_public").document(area_id).set(doc)

    # -- admin actions ---------------------------------------------------

    def get_action(self, action_id: str) -> dict[str, Any] | None:
        snap = self._db.collection("admin_actions").document(action_id).get()
        return snap.to_dict() if snap.exists else None

    def update_action(self, action_id: str, fields: dict[str, Any]) -> None:
        self._db.collection("admin_actions").document(action_id).update(fields)

    def pending_actions(self) -> list[tuple[str, dict[str, Any]]]:
        return [
            (snap.id, snap.to_dict() or {})
            for snap in self._db.collection("admin_actions").stream()
            if not (snap.to_dict() or {}).get("done_at")
        ]

    # -- storage ----------------------------------------------------------

    def upload_pack(self, draft_id: str, version: int, pack_dir: Path) -> None:
        for path in sorted(pack_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(pack_dir)
            self._bucket.blob(f"packs/{draft_id}/{version}/{rel}").upload_from_filename(
                str(path)
            )

    def upload_report(self, draft_id: str, round_no: int, report: Path) -> None:
        self._bucket.blob(
            f"reports/{draft_id}/{round_no}/report.html"
        ).upload_from_filename(str(report))

    def delete_pack(self, draft_id: str) -> None:
        for blob in self._bucket.list_blobs(prefix=f"packs/{draft_id}/"):
            blob.delete()

    # -- watch -------------------------------------------------------------

    def watch_drafts(self, callback: Any) -> Any:
        return self._db.collection("drafts").on_snapshot(callback)

    def watch_actions(self, callback: Any) -> Any:
        return self._db.collection("admin_actions").on_snapshot(callback)
