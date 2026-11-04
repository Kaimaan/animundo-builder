"""The ring ahead: a published tile plans its eight neighbours (#283)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fake_store import FakeStore
from fake_world import make_world, write_grids

from builder.config import Config
from builder.grid import group_path, neighbours
from builder.ledger import Caps, FileLedger, today_iso
from builder.worker import Worker

CENTER = "z10_0581_0294"


def _ring_worker(
    tmp_path: Path,
    store: FakeStore,
    monkeypatch: Any,
    cells: dict[str, list[Any]] | None = None,
    **config: Any,
) -> Worker:
    def fake_fetch(tile: str, candidates: list[dict[str, Any]]) -> dict[str, float]:
        return {"fox": 5.0, "vole": 20.0}

    monkeypatch.setattr("builder.plan.fetch_smoothed", fake_fetch)
    world = make_world(tmp_path)
    if cells is None:
        cell = [717, 1.0, None, 0.0, 6, 1.0, 0.0, 80.0]
        cells = {
            f"{c},{r}": list(cell) for r in range(290, 299) for c in range(577, 586)
        }
    grid_dir = write_grids(tmp_path / "grids", cells)
    worker = Worker(
        store=store,
        jobs_dir=tmp_path / "jobs",
        world=world,
        config=Config(ledger_path=str(tmp_path / "ledger.json"), **config),
        grid_dir=grid_dir,
    )
    worker.world_commit = "commit-a"
    worker.species = {
        "place": "Boreal forest in southern Finland",
        "parts": [{"kind": "forest", "species": ["fox", "vole"]}],
        "plants": [],
        "trees": [],
        "outside_animals": [],
        "outside_plants": [],
        "name_en": "Nuuksio forest",
        "name_fi": "Nuuksion metsä",
    }
    return worker


def _ring_tiles() -> list[str]:
    return sorted(t for t in neighbours(CENTER).values() if t is not None)


def _publish(worker: Worker, store: FakeStore, job_id: str = "job-1") -> None:
    store.build_jobs[job_id] = {
        "kind": "build",
        "status": "building",
        "worker_id": worker.worker_id,
        "tile": CENTER,
        "tiles": [CENTER],
        "items": [],
    }
    worker._set_published(job_id, store.build_jobs[job_id], CENTER, {"version": "v3"})


def test_ring_plans_eight_neighbours_on_publish(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#283.1: publishing a tile plans each of its eight neighbours."""
    store = FakeStore()
    worker = _ring_worker(tmp_path, store, monkeypatch)
    _publish(worker, store)
    for tile in _ring_tiles():
        plan = store.plans[tile]
        assert plan["ahead"] is True
        assert plan["world"] == "commit-a"
        doc = store.build_jobs[f"ahead_{tile}"]
        assert doc["kind"] == "request"
        assert doc["status"] == "planned"


def test_ring_skips_published_planned_blocked_excluded(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#283.3: no plan over published, planned, blocked or excluded cells."""
    store = FakeStore()
    worker = _ring_worker(tmp_path, store, monkeypatch)
    ring = _ring_tiles()
    published, planned, blocked, excluded = ring[0], ring[1], ring[2], ring[3]
    group = group_path(published)
    store.write_blob(group, json.dumps({"tiles": {published: {"v": 3}}}).encode())
    store.plans[planned] = {"world": "commit-a", "code_only": True, "items": []}
    store.plans[blocked] = {"world": "commit-a", "blocked": "no_home", "items": []}
    (tmp_path / "exclusions.json").write_text(json.dumps({"tiles": [excluded]}))
    worker.builder_root = tmp_path
    _publish(worker, store)
    for tile in (published, planned, blocked, excluded):
        assert f"ahead_{tile}" not in store.build_jobs, tile
    assert store.plans[planned].get("ahead") is None
    assert store.plans[blocked]["blocked"] == "no_home"
    for tile in ring[4:]:
        assert store.plans[tile]["ahead"] is True


def test_ring_201st_waits_in_requests(tmp_path: Path, monkeypatch: Any) -> None:
    """#283.4: past ahead_per_day the neighbours wait as requests."""
    store = FakeStore()
    worker = _ring_worker(tmp_path, store, monkeypatch)
    today = today_iso()
    for n in range(200):
        store.plans[f"z10_0000_{n:04d}"] = {
            "ahead": True,
            "ahead_day": today,
            "world": "commit-a",
        }
    _publish(worker, store)
    for tile in _ring_tiles():
        assert tile not in store.plans
        doc = store.build_jobs[f"ahead_{tile}"]
        assert doc["kind"] == "request"
        assert doc["status"] == "pending"


def test_ring_zero_stops_it(tmp_path: Path, monkeypatch: Any) -> None:
    """#283.4: ahead_per_day 0 plans nothing and queues nothing."""
    store = FakeStore()
    worker = _ring_worker(tmp_path, store, monkeypatch, ahead_per_day=0)
    _publish(worker, store)
    assert store.plans == {}
    assert [j for j in store.build_jobs if j != "job-1"] == []


def test_ring_stops_on_a_spent_budget(tmp_path: Path, monkeypatch: Any) -> None:
    """#283.4: no money left under auto_daily_usd, the ring only queues."""
    store = FakeStore()
    worker = _ring_worker(tmp_path, store, monkeypatch, auto_daily_usd=20.0)
    ledger = FileLedger(tmp_path / "ledger.json")
    receipt = ledger.reserve("seed", 20.0, Caps(10**9, 10**9, 10**9))
    ledger.settle(receipt, 20.0)
    _publish(worker, store)
    for tile in _ring_tiles():
        assert tile not in store.plans
        assert store.build_jobs[f"ahead_{tile}"]["status"] == "pending"


def test_ring_open_sea_plans_without_a_survey(tmp_path: Path, monkeypatch: Any) -> None:
    """#283.2: an open-sea neighbour plans $0, past a spent budget."""
    store = FakeStore()
    cell = [717, 1.0, None, 0.0, 6, 1.0, 0.0, 80.0]
    cells = {f"{c},{r}": list(cell) for r in range(290, 299) for c in range(577, 586)}
    cells["582,293"] = [None, 1.0, None, 0.0, None, 0.0, 1.0, 0.0]
    worker = _ring_worker(
        tmp_path, store, monkeypatch, cells=cells, auto_daily_usd=20.0
    )
    ledger = FileLedger(tmp_path / "ledger.json")
    receipt = ledger.reserve("seed", 20.0, Caps(10**9, 10**9, 10**9))
    ledger.settle(receipt, 20.0)
    _publish(worker, store)
    sea = "z10_0582_0293"
    assert store.plans[sea]["ahead"] is True
    assert store.build_jobs[f"ahead_{sea}"]["status"] == "planned"
    land = "z10_0580_0293"
    assert land not in store.plans
    assert store.build_jobs[f"ahead_{land}"]["status"] == "pending"


def test_ring_plans_no_further_rings(tmp_path: Path, monkeypatch: Any) -> None:
    """#283.7: ring plans queue no further rings, one ring only."""
    store = FakeStore()
    worker = _ring_worker(tmp_path, store, monkeypatch)
    _publish(worker, store)
    assert "ahead_z10_0579_0294" not in store.build_jobs
    assert "ahead_z10_0583_0294" not in store.build_jobs


def test_note_on_a_ring_planned_tile_plans_nothing_again(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    """#283.6: a note on the same world commit reuses the ring's plan."""
    store = FakeStore()
    worker = _ring_worker(tmp_path, store, monkeypatch)
    tile = "z10_0581_0294"
    store.requests[tile] = {"tile": tile, "notes": 1}
    store.plans[tile] = {
        "ahead": True,
        "world": "commit-a",
        "code_only": True,
        "items": [],
        "sentinel": True,
    }
    worker.handle_request(tile)
    assert store.plans[tile]["sentinel"] is True
    assert "reusing the ring" in capsys.readouterr().out


def test_note_replans_a_ring_plan_from_an_old_commit(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#283.6: the reuse holds only for the plan's own world commit."""
    store = FakeStore()
    worker = _ring_worker(tmp_path, store, monkeypatch)
    tile = "z10_0581_0294"
    store.requests[tile] = {"tile": tile, "notes": 1}
    store.plans[tile] = {"ahead": True, "world": "commit-old", "items": []}
    worker.handle_request(tile)
    assert store.plans[tile]["world"] == "commit-a"


def test_listener_ignores_ring_requests(tmp_path: Path) -> None:
    """#283.6: a ring request never enters the build queue."""
    store = FakeStore()
    worker = Worker(
        store=store,
        jobs_dir=tmp_path / "jobs",
        world=tmp_path / "world",
        config=Config(ledger_path=str(tmp_path / "ledger.json")),
    )
    doc = {"kind": "request", "tile": CENTER, "status": "pending"}
    assert worker.note_build_job("ahead_x", doc) is False
