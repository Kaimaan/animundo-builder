"""Tests for both spending ledger backends. No network, no key."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from builder.ledger import (
    CapExceeded,
    Caps,
    CombinedLedger,
    FileLedger,
    FirestoreLedger,
    LedgerError,
)

CAPS = Caps(job_usd=8.0, day_usd=20.0, total_usd=120.0)


def _spend(ledger: FileLedger) -> dict[str, Any]:
    return json.loads(ledger.path.read_text())


def test_reserve_then_settle_maths(tmp_path: Path) -> None:
    ledger = FileLedger(tmp_path / "spend.json")
    receipt = ledger.reserve("job-1", 1.00, CAPS)
    day = receipt.day
    assert _spend(ledger)["total"] == pytest.approx(1.00)
    ledger.settle(receipt, 0.25)
    data = _spend(ledger)
    assert data["total"] == pytest.approx(0.25)
    assert data["days"][day] == pytest.approx(0.25)
    assert data["jobs"]["job-1"]["spent"] == pytest.approx(0.25)


def test_each_cap_refuses(tmp_path: Path) -> None:
    ledger = FileLedger(tmp_path / "spend.json")
    with pytest.raises(CapExceeded, match="job cap"):
        ledger.reserve("job-1", 8.01, CAPS)
    with pytest.raises(CapExceeded, match="day cap"):
        ledger.reserve("job-1", 20.01, Caps(100.0, 20.0, 1000.0))
    with pytest.raises(CapExceeded, match="total cap"):
        ledger.reserve("job-1", 120.01, Caps(1000.0, 1000.0, 120.0))
    assert ledger.path.read_text().strip() == ""


def test_caps_count_settled_spend(tmp_path: Path) -> None:
    ledger = FileLedger(tmp_path / "spend.json")
    receipt = ledger.reserve("job-1", 7.00, CAPS)
    ledger.settle(receipt, 7.00)
    with pytest.raises(CapExceeded, match="job cap"):
        ledger.reserve("job-1", 1.01, CAPS)
    other = ledger.reserve("job-2", 1.00, CAPS)
    ledger.settle(other, 1.00)
    assert _spend(ledger)["total"] == pytest.approx(8.00)


def test_corrupt_ledger_refuses(tmp_path: Path) -> None:
    path = tmp_path / "spend.json"
    path.write_text("not json{", encoding="utf-8")
    ledger = FileLedger(path)
    with pytest.raises(LedgerError, match="cannot read"):
        ledger.reserve("job-1", 0.01, CAPS)


def test_total_spent_reads_the_pool(tmp_path: Path) -> None:
    ledger = FileLedger(tmp_path / "spend.json")
    assert ledger.total_spent() == 0.0
    receipt = ledger.reserve("job-1", 1.00, CAPS)
    ledger.settle(receipt, 0.25)
    assert ledger.total_spent() == pytest.approx(0.25)


def test_total_spent_refuses_a_broken_ledger(tmp_path: Path) -> None:
    path = tmp_path / "spend.json"
    path.write_text("not json{", encoding="utf-8")
    with pytest.raises(LedgerError, match="cannot read"):
        FileLedger(path).total_spent()


def test_job_spent_reads_one_job(tmp_path: Path) -> None:
    ledger = FileLedger(tmp_path / "spend.json")
    assert ledger.job_spent("job-1") == 0.0
    receipt = ledger.reserve("job-1", 1.00, CAPS)
    ledger.settle(receipt, 0.25)
    ledger.settle(ledger.reserve("job-2", 2.00, CAPS), 2.00)
    assert ledger.job_spent("job-1") == pytest.approx(0.25)
    assert ledger.job_spent("job-2") == pytest.approx(2.00)
    assert ledger.job_spent("job-3") == 0.0


def test_day_spent_reads_one_day(tmp_path: Path) -> None:
    ledger = FileLedger(tmp_path / "spend.json")
    assert ledger.day_spent("2026-10-27") == 0.0
    receipt = ledger.reserve("job-1", 1.00, CAPS)
    ledger.settle(receipt, 0.25)
    assert ledger.day_spent() == pytest.approx(0.25)
    assert ledger.day_spent("2026-01-01") == 0.0


def test_racing_jobs_cannot_both_spend(tmp_path: Path) -> None:
    ledger = FileLedger(tmp_path / "spend.json")
    caps = Caps(job_usd=10.0, day_usd=1.0, total_usd=10.0)
    wins: list[str] = []
    lock = threading.Lock()

    def race(job: str) -> None:
        try:
            receipt = ledger.reserve(job, 0.60, caps)
        except CapExceeded:
            return
        with lock:
            wins.append(job)
        ledger.settle(receipt, 0.60)

    threads = [threading.Thread(target=race, args=(f"job-{n}",)) for n in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(wins) == 1
    (day_spent,) = _spend(ledger)["days"].values()
    assert day_spent == pytest.approx(0.60)


class _FakeSnapshot:
    def __init__(self, data: dict[str, Any] | None) -> None:
        self._data = data

    def to_dict(self) -> dict[str, Any] | None:
        return self._data


class _FakeDb:
    """A Firestore double: `run_transaction` runs the function at once."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.down = False

    def collection(self, name: str) -> _FakeDb:
        assert name == "spend"
        return self

    def document(self, doc_id: str) -> str:
        return doc_id

    def run_transaction(self, fn: Any) -> Any:
        if self.down:
            raise RuntimeError("firestore is down")
        return fn(self)

    def get(self, ref: str) -> _FakeSnapshot:
        return _FakeSnapshot(self.docs.get(ref))

    def set(self, ref: str, data: dict[str, Any]) -> None:
        self.docs[ref] = data


def test_firestore_reserve_then_settle() -> None:
    db = _FakeDb()
    ledger = FirestoreLedger(db)
    receipt = ledger.reserve("job-1", 1.00, CAPS)
    assert db.docs["total"] == {"spent": 1.00}
    assert db.docs["job-job-1"] == {"spent": 1.00}
    ledger.settle(receipt, 0.25)
    assert db.docs["total"] == {"spent": 0.25}
    assert db.docs[receipt.day] == {"spent": 0.25}


def test_firestore_each_cap_refuses() -> None:
    ledger = FirestoreLedger(_FakeDb())
    with pytest.raises(CapExceeded, match="job cap"):
        ledger.reserve("job-1", 8.01, CAPS)
    with pytest.raises(CapExceeded, match="day cap"):
        ledger.reserve("job-1", 20.01, Caps(100.0, 20.0, 1000.0))
    with pytest.raises(CapExceeded, match="total cap"):
        ledger.reserve("job-1", 120.01, Caps(1000.0, 1000.0, 120.0))


def test_firestore_outage_refuses() -> None:
    db = _FakeDb()
    db.down = True
    ledger = FirestoreLedger(db)
    with pytest.raises(LedgerError, match="cannot reserve"):
        ledger.reserve("job-1", 0.01, CAPS)


def test_firestore_total_spent() -> None:
    ledger = FirestoreLedger(_FakeDb())
    assert ledger.total_spent() == 0.0
    receipt = ledger.reserve("job-1", 1.00, CAPS)
    ledger.settle(receipt, 0.25)
    assert ledger.total_spent() == pytest.approx(0.25)


def test_firestore_job_spent() -> None:
    ledger = FirestoreLedger(_FakeDb())
    assert ledger.job_spent("job-1") == 0.0
    receipt = ledger.reserve("job-1", 1.00, CAPS)
    ledger.settle(receipt, 0.25)
    assert ledger.job_spent("job-1") == pytest.approx(0.25)
    assert ledger.job_spent("job-2") == 0.0


def _combined(
    tmp_path: Path, name: str, pool_caps: Caps, job_usd: float
) -> CombinedLedger:
    allowance = FileLedger(tmp_path / "allowance.json")
    pool = FileLedger(tmp_path / f"{name}.json")
    return CombinedLedger(
        pool,
        allowance,
        Caps(job_usd=job_usd, day_usd=float("inf"), total_usd=float("inf")),
    )


def test_combined_allowance_binds_both_providers(tmp_path: Path) -> None:
    """#156: Meta and Baseten share one $8 job allowance, not $8 each."""
    pool_caps = Caps(job_usd=80.0, day_usd=200.0, total_usd=800.0)
    meta = _combined(tmp_path, "meta", pool_caps, 8.0)
    open_weights = _combined(tmp_path, "baseten", pool_caps, 8.0)
    first = meta.reserve("job-1", 5.00, pool_caps)
    with pytest.raises(CapExceeded, match="job cap"):
        open_weights.reserve("job-1", 5.00, pool_caps)
    meta.settle(first, 4.00)
    second = open_weights.reserve("job-1", 4.00, pool_caps)
    open_weights.settle(second, 3.00)
    assert meta.allowance.job_spent("job-1") == pytest.approx(7.00)


def test_combined_releases_the_other_side_on_refusal(tmp_path: Path) -> None:
    """#156: a refused provider frees the allowance for the fallback."""
    pool_caps = Caps(job_usd=8.0, day_usd=20.0, total_usd=120.0)
    meta = _combined(tmp_path, "meta", pool_caps, 8.0)
    open_weights = _combined(tmp_path, "baseten", pool_caps, 8.0)
    fell = open_weights.provider.reserve("job-1", 7.00, pool_caps)
    open_weights.provider.settle(fell, 7.00)
    with pytest.raises(CapExceeded):
        open_weights.reserve("job-1", 2.00, pool_caps)
    assert meta.allowance.job_spent("job-1") == pytest.approx(0.0)
    good = meta.reserve("job-1", 2.00, pool_caps)
    meta.settle(good, 1.50)
    assert meta.allowance.job_spent("job-1") == pytest.approx(1.50)


def test_combined_competing_reservations_leave_one_winner(tmp_path: Path) -> None:
    """#156: two racers for the last dollars cannot both spend them."""
    pool_caps = Caps(job_usd=80.0, day_usd=200.0, total_usd=800.0)
    winners: list[str] = []

    def racer(name: str) -> None:
        ledger = _combined(tmp_path, name, pool_caps, 8.0)
        try:
            receipt = ledger.reserve("job-1", 5.00, pool_caps)
        except CapExceeded:
            return
        winners.append(name)
        ledger.settle(receipt, 5.00)

    threads = [threading.Thread(target=racer, args=(n,)) for n in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(winners) == ["a"] or sorted(winners) == ["b"]
    assert FileLedger(tmp_path / "allowance.json").job_spent("job-1") == pytest.approx(
        5.00
    )


def test_combined_restart_keeps_outstanding_spend(tmp_path: Path) -> None:
    """#156: a reservation nobody settled still counts after a restart."""
    pool_caps = Caps(job_usd=80.0, day_usd=200.0, total_usd=800.0)
    meta = _combined(tmp_path, "meta", pool_caps, 8.0)
    meta.reserve("job-1", 6.00, pool_caps)
    del meta
    resumed = _combined(tmp_path, "meta", pool_caps, 8.0)
    assert resumed.allowance.job_spent("job-1") == pytest.approx(6.00)
    with pytest.raises(CapExceeded, match="job cap"):
        resumed.reserve("job-1", 3.00, pool_caps)


def test_settle_releases_only_once(tmp_path: Path) -> None:
    """#156: settling the same receipt twice releases once."""
    ledger = FileLedger(tmp_path / "spend.json")
    receipt = ledger.reserve("job-1", 1.00, CAPS)
    ledger.settle(receipt, 0.25)
    ledger.settle(receipt, 0.25)
    assert _spend(ledger)["total"] == pytest.approx(0.25)


def test_firestore_settle_releases_only_once() -> None:
    """#156: settling the same receipt twice releases once, hosted too."""
    ledger = FirestoreLedger(_FakeDb())
    receipt = ledger.reserve("job-1", 1.00, CAPS)
    ledger.settle(receipt, 0.25)
    ledger.settle(receipt, 0.25)
    assert ledger.total_spent() == pytest.approx(0.25)


def test_make_context_shares_one_allowance(tmp_path: Path) -> None:
    """#156: both clients in a context reserve against one allowance."""
    from builder.config import Config
    from builder.jobs import make_context

    config = Config(
        ledger_path=str(tmp_path / "spend.json"),
        job_budget_usd=8.0,
        model_api_key="k",
        open_model_host="baseten",
        baseten_api_key="b",
    )
    ctx = make_context(config, "job-1", tmp_path / "job", None)
    assert ctx.client is not None
    assert ctx.open_client is not None
    assert isinstance(ctx.client.ledger, CombinedLedger)
    assert isinstance(ctx.open_client.ledger, CombinedLedger)
    assert ctx.open_client.ledger.allowance is ctx.client.ledger.allowance
    assert ctx.client.ledger.allowance_caps.job_usd == pytest.approx(8.0)
    assert (tmp_path / "spend-allowance.json").is_file() is False
    receipt = ctx.client.ledger.reserve(
        "job-1", 3.00, Caps(job_usd=80.0, day_usd=200.0, total_usd=800.0)
    )
    ctx.client.ledger.settle(receipt, 2.00)
    assert ctx.client.ledger.allowance.job_spent("job-1") == pytest.approx(2.00)
    assert ctx.open_client.ledger.allowance.job_spent("job-1") == pytest.approx(2.00)
