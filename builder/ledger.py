"""Spending ledgers: reserve before every model call, settle after.

Two jobs at once cannot both slip under a cap: every reservation and
settlement happens inside one locked read-modify-write step (a file
lock for the local backend, a Firestore transaction for the hosted
one). If the ledger cannot be read, the call is refused: unknown spend
counts as over the cap.
"""

from __future__ import annotations

import fcntl
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol


class CapExceeded(Exception):
    """A spending cap refused a model call. The job stops cleanly."""


class LedgerError(Exception):
    """The ledger could not be read or written. The call is refused."""


@dataclass(frozen=True)
class Caps:
    """The money limits one reservation is checked against."""

    job_usd: float
    day_usd: float
    total_usd: float


@dataclass(frozen=True)
class Receipt:
    """Proof of a reservation, handed back at settle time."""

    job_id: str
    day: str
    reserved_usd: float


class Ledger(Protocol):
    """What `MetaClient` needs from a spending backend."""

    def reserve(self, job_id: str, amount_usd: float, caps: Caps) -> Receipt: ...
    def settle(self, receipt: Receipt, actual_usd: float) -> None: ...
    def total_spent(self) -> float: ...


def today_iso() -> str:
    return datetime.now(UTC).date().isoformat()


class FileLedger:
    """A ledger in one JSON file, guarded by an exclusive file lock.

    For contributors on the command line. Two runs on one machine take
    the lock in turn, so two jobs racing for the last dollar cannot
    both spend it.
    """

    KEEP_JOB_DAYS = 7

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def reserve(self, job_id: str, amount_usd: float, caps: Caps) -> Receipt:
        day = today_iso()
        with _LockedFile(self.path) as locked:
            data = locked.data
            spent = self._spent(data, job_id, day)
            for name, limit, used in (
                ("job", caps.job_usd, spent[0]),
                ("day", caps.day_usd, spent[1]),
                ("total", caps.total_usd, spent[2]),
            ):
                if used + amount_usd > limit:
                    raise CapExceeded(
                        f"{name} cap ${limit:.2f} refuses "
                        f"${amount_usd:.2f} (${used:.2f} spent)"
                    )
            data["jobs"][job_id] = {
                "spent": spent[0] + amount_usd,
                "updated": day,
            }
            data["days"][day] = data["days"].get(day, 0.0) + amount_usd
            data["total"] = data.get("total", 0.0) + amount_usd
            self._prune(data, day)
            locked.write(data)
        return Receipt(job_id=job_id, day=day, reserved_usd=amount_usd)

    def settle(self, receipt: Receipt, actual_usd: float) -> None:
        with _LockedFile(self.path) as locked:
            data = locked.data
            release = receipt.reserved_usd - actual_usd
            job = data["jobs"].get(receipt.job_id, {"spent": 0.0})
            job["spent"] = max(0.0, job.get("spent", 0.0) - release)
            job["updated"] = today_iso()
            data["jobs"][receipt.job_id] = job
            data["days"][receipt.day] = max(
                0.0, data["days"].get(receipt.day, 0.0) - release
            )
            data["total"] = max(0.0, data.get("total", 0.0) - release)
            locked.write(data)

    def total_spent(self) -> float:
        """The whole pool's spend, for the low-credit warning."""
        with _LockedFile(self.path) as locked:
            return float(locked.data.get("total", 0.0))

    def day_spent(self, day: str | None = None) -> float:
        """Today's (or one day's) spend, for the worker's daily limit."""
        with _LockedFile(self.path) as locked:
            return float(locked.data.get("days", {}).get(day or today_iso(), 0.0))

    def _spent(
        self, data: dict[str, Any], job_id: str, day: str
    ) -> tuple[float, float, float]:
        return (
            float(data["jobs"].get(job_id, {}).get("spent", 0.0)),
            float(data["days"].get(day, 0.0)),
            float(data.get("total", 0.0)),
        )

    def _prune(self, data: dict[str, Any], day: str) -> None:
        kept = {}
        for job, entry in data["jobs"].items():
            try:
                age = (
                    datetime.fromisoformat(day)
                    - datetime.fromisoformat(entry.get("updated", day))
                ).days
            except ValueError:
                continue
            if age < self.KEEP_JOB_DAYS:
                kept[job] = entry
        data["jobs"] = kept


class _LockedFile:
    """The ledger file, held under an exclusive lock. Unreadable is fatal."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any = None
        self.data: dict[str, Any] = {}

    def __enter__(self) -> _LockedFile:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.handle = self.path.open("a+", encoding="utf-8")
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
            self.handle.seek(0)
            text = self.handle.read()
        except OSError as error:
            raise LedgerError(f"cannot lock the ledger: {error}") from error
        if not text.strip():
            self.data = {"jobs": {}, "days": {}, "total": 0.0}
            return self
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError as error:
            raise LedgerError(f"cannot read the ledger: {error}") from error
        if not isinstance(loaded, dict):
            raise LedgerError("cannot read the ledger: not an object")
        loaded.setdefault("jobs", {})
        loaded.setdefault("days", {})
        loaded.setdefault("total", 0.0)
        self.data = loaded
        return self

    def write(self, data: dict[str, Any]) -> None:
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(json.dumps(data, indent=2))
        self.handle.flush()

    def __exit__(self, *exc: Any) -> None:
        try:
            if self.handle is not None:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
                self.handle.close()
        finally:
            self.handle = None


class FirestoreLedger:
    """The hosted ledger: one transaction over the day and total spend.

    `db` is a Firestore client (or anything with `run_transaction` and
    `collection`, which is what the tests fake). Unknown spend counts
    as over the cap: any transaction failure refuses the call.
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    def reserve(self, job_id: str, amount_usd: float, caps: Caps) -> Receipt:
        day = today_iso()
        receipt: dict[str, Any] = {}

        def txn(transaction: Any) -> None:
            spend = self._db.collection("spend")
            day_ref = spend.document(day)
            total_ref = spend.document("total")
            job_ref = spend.document(f"job-{job_id}")
            used = (
                _doc_spent(transaction.get(day_ref)),
                _doc_spent(transaction.get(total_ref)),
                _doc_spent(transaction.get(job_ref)),
            )
            for name, limit, already in (
                ("day", caps.day_usd, used[0]),
                ("total", caps.total_usd, used[1]),
                ("job", caps.job_usd, used[2]),
            ):
                if already + amount_usd > limit:
                    raise CapExceeded(
                        f"{name} cap ${limit:.2f} refuses "
                        f"${amount_usd:.2f} (${already:.2f} spent)"
                    )
            transaction.set(day_ref, {"spent": used[0] + amount_usd})
            transaction.set(total_ref, {"spent": used[1] + amount_usd})
            transaction.set(job_ref, {"spent": used[2] + amount_usd})
            receipt["receipt"] = Receipt(
                job_id=job_id, day=day, reserved_usd=amount_usd
            )

        try:
            self._db.run_transaction(txn)
        except CapExceeded:
            raise
        except Exception as error:  # noqa: BLE001 - any failure refuses
            raise LedgerError(f"cannot reserve spend: {error}") from error
        return receipt["receipt"]

    def settle(self, receipt: Receipt, actual_usd: float) -> None:
        release = receipt.reserved_usd - actual_usd

        def txn(transaction: Any) -> None:
            spend = self._db.collection("spend")
            for doc_id in (receipt.day, "total", f"job-{receipt.job_id}"):
                ref = spend.document(doc_id)
                spent = _doc_spent(transaction.get(ref))
                transaction.set(ref, {"spent": max(0.0, spent - release)})

        try:
            self._db.run_transaction(txn)
        except Exception as error:  # noqa: BLE001 - settle must not hide
            raise LedgerError(f"cannot settle spend: {error}") from error

    def total_spent(self) -> float:
        """The whole pool's spend, read in one transaction."""
        spent: dict[str, float] = {}

        def txn(transaction: Any) -> None:
            ref = self._db.collection("spend").document("total")
            spent["total"] = _doc_spent(transaction.get(ref))

        try:
            self._db.run_transaction(txn)
        except Exception as error:  # noqa: BLE001 - any failure refuses
            raise LedgerError(f"cannot read spend: {error}") from error
        return spent["total"]


def _doc_spent(snapshot: Any) -> float:
    data = snapshot.to_dict() or {}
    return float(data.get("spent", 0.0))
