"""Tests for measured job costs and the cost log (#259.2-6). No key."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fake_store import FakeStore

from builder.config import Config
from builder.costs import median_estimates, rollup_parts
from builder.ledger import Caps, FileLedger
from builder.worker import Worker

MODEL = "muse-spark-1.3"
TILE = "z10_0581_0294"


def _worker(tmp_path: Path, store: FakeStore) -> Worker:
    return Worker(
        store=store,
        jobs_dir=tmp_path / "jobs",
        world=None,
        config=Config(ledger_path=str(tmp_path / "ledger.json")),
    )


def _line(part: str, usd: float, result: str = "ok", **over: Any) -> dict[str, Any]:
    line: dict[str, Any] = {
        "model": MODEL,
        "part": part,
        "cost_usd": usd,
        "input_tokens": 100,
        "output_tokens": 50,
        "result": result,
    }
    line.update(over)
    return line


def _write(path: Path, lines: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")


def _settle(ledger_path: str, namespace: str, usd: float) -> None:
    ledger = FileLedger(ledger_path)
    receipt = ledger.reserve(namespace, usd, Caps(100.0, 1000.0, 10000.0))
    ledger.settle(receipt, usd)


def _parts_total(parts: dict[str, Any]) -> float:
    total = 0.0
    for key in ("research", "tiles", "review", "retries", "search", "images", "other"):
        total += parts[key]["usd"]
    for group in ("animals", "plants", "homes"):
        total += sum(entry["usd"] for entry in parts[group].values())
    return round(total, 6)


def _place_job() -> dict[str, Any]:
    return {
        "kind": "place",
        "status": "building",
        "tiles": [TILE],
        "started_at": "2026-11-03T00:00:00+00:00",
        "research": {
            "tiles": [TILE],
            "biome": "rainforest",
            "animals": [{"id": "lion", "source": "s"}],
            "plants": [{"name": "Açaí", "source": "s"}],
            "price_parts": {
                "total": 1.42,
                "tiles": {"n": 1, "usd": 0.50, "from": "fixed"},
                "animals": [{"what": "lion", "usd": 0.42, "from": "fixed"}],
                "plants": [],
            },
        },
    }


def test_published_replay_writes_cost_and_one_log_row(tmp_path: Path) -> None:
    """#259.6: an ended job carries cost; the log row's parts sum to it."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    job_dir = tmp_path / "jobs" / "j1"
    _write(
        job_dir / "log.jsonl",
        [_line("research", 0.10), _line("review", 0.02)],
    )
    _write(
        job_dir / "x" / "log.jsonl",
        [_line("animal:lion", 0.30), _line("animal:lion", 0.05, result="error")],
    )
    _settle(str(tmp_path / "ledger.json"), "j1", 0.12)
    _settle(str(tmp_path / "ledger.json"), "j1/x", 0.35)
    store.build_jobs["j1"] = _place_job()
    assert worker._claim("j1")
    worker._set_status("j1", "published")
    job = store.build_jobs["j1"]
    cost = job["cost"]
    assert cost["estimate"] == {
        "total": 1.42,
        "parts": job["research"]["price_parts"],
        "from": "research.price_parts",
    }
    assert cost["actual"] == 0.47
    assert cost["parts"]["animals"]["lion"]["usd"] == 0.30
    assert cost["parts"]["animals"]["lion"]["calls"] == 1
    assert cost["parts"]["retries"]["usd"] == 0.05
    assert cost["parts"]["research"]["usd"] == 0.10
    assert cost["parts"]["review"]["usd"] == 0.02
    assert cost["parts"]["research"]["models"] == [MODEL]
    assert _parts_total(cost["parts"]) == cost["actual"]
    assert cost["wall_s"] > 0
    rows = store.list_cost_log()
    assert [doc_id for doc_id, _ in rows] == ["j1"]
    row = rows[0][1]
    assert row["job"] == "j1"
    assert row["kind"] == "place"
    assert row["tiles"] == [TILE]
    assert row["species"] == ["Açaí", "lion"]
    assert row["cost"] == cost
    assert row["builder_commit"] and row["date"]


def test_failed_job_writes_cost_and_one_log_row(tmp_path: Path) -> None:
    """#259.6: a failed job carries cost too, with its error kept."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    job_dir = tmp_path / "jobs" / "j2"
    _write(job_dir / "log.jsonl", [_line("research", 0.10)])
    _settle(str(tmp_path / "ledger.json"), "j2", 0.10)
    store.build_jobs["j2"] = _place_job()
    assert worker._claim("j2")
    worker._fail("j2", "bad_pack")
    job = store.build_jobs["j2"]
    assert job["status"] == "failed"
    assert job["error"] == "bad_pack"
    assert job["cost"]["actual"] == 0.10
    assert _parts_total(job["cost"]["parts"]) == 0.10
    rows = store.list_cost_log()
    assert [doc_id for doc_id, _ in rows] == ["j2"]


def test_rerun_never_overwrites_its_log_row(tmp_path: Path) -> None:
    """#259.3: a second ending of one job appends `_2`."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    store.build_jobs["j3"] = _place_job()
    assert worker._claim("j3")
    store.write_cost_log("j3", {"old": True})
    worker._record_cost("j3")
    assert store.get_cost_log("j3") == {"old": True}
    row = store.get_cost_log("j3_2")
    assert row is not None and row["job"] == "j3"
    worker._record_cost("j3")
    assert store.get_cost_log("j3_3") is not None


def test_median_estimates_read_the_log_after_three_rows(tmp_path: Path) -> None:
    """#259.4: three rows switch a part from fixed to the log median."""
    store = FakeStore()
    assert median_estimates(store)["animal"] == (0.42, "fixed")
    for n, usd in enumerate((0.40, 0.50, 0.60)):
        store.write_cost_log(
            f"a{n}",
            {
                "tiles": [TILE],
                "cost": {"parts": {"animals": {"x": {"usd": usd}}}},
            },
        )
    assert median_estimates(store)["animal"] == (0.50, "log, 3 rows")
    assert median_estimates(store)["plant"] == (0.13, "fixed")
    for n, usd in enumerate((0.03, 0.04, 0.05)):
        store.write_cost_log(
            f"s{n}",
            {
                "tiles": [TILE],
                "cost": {"parts": {"tiles": {"usd": usd}}},
            },
        )
    assert median_estimates(store)["survey"] == (0.04, "log, 3 rows")
    assert median_estimates(store)["tile"] == (0.04, "log, 3 rows")


def test_search_and_image_dollars_split_to_own_parts(tmp_path: Path) -> None:
    """#259.5: search and image spend leaves the semantic part."""
    job_dir = tmp_path / "jobs" / "j4"
    _write(
        job_dir / "log.jsonl",
        [_line("research", 0.10, searches=4, images=2, generated=1)],
    )
    parts = rollup_parts(job_dir)
    assert parts["search"]["usd"] == 0.01
    assert parts["images"]["usd"] == 0.015
    assert parts["research"]["usd"] == 0.075
    assert parts["research"]["input_tokens"] == 100
    assert parts["search"]["input_tokens"] == 0
    assert _parts_total(parts) == 0.10
