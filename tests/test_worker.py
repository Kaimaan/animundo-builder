"""The worker: it plans requests, queues jobs, alerts loudly. No Firebase."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fake_store import FakeStore
from fake_world import boreal_3x3, make_world, write_grids

from builder.config import Config
from builder.worker import Listener, Worker
from builder.worker_store import STORE_METHODS, FirebaseStore


def _worker(tmp_path: Path, store: FakeStore) -> Worker:
    return Worker(
        store=store,
        jobs_dir=tmp_path / "jobs",
        world=tmp_path / "world",
        config=Config(ledger_path=str(tmp_path / "ledger.json")),
    )


def test_stores_expose_every_worker_method() -> None:
    assert len(STORE_METHODS) == len(set(STORE_METHODS))
    for name in STORE_METHODS:
        assert callable(getattr(FakeStore, name, None)), f"fake lacks {name}"
        assert callable(getattr(FirebaseStore, name, None)), f"real lacks {name}"


def test_owner_update_encodes_delete_for_firestore() -> None:
    """#172.1: the give-up patch survives the real client's encoder."""
    import pytest
    from google.cloud.firestore_v1._helpers import pbs_for_update

    from builder.worker import DELETE
    from builder.worker_store import _translated, _update_if_owner

    class _Snap:
        exists = True

        def to_dict(self) -> dict[str, str]:
            return {"worker_id": "wA"}

    class _Ref:
        def get(self, transaction: Any = None) -> _Snap:
            return _Snap()

    class _Tx:
        def __init__(self) -> None:
            self.fields: dict[str, Any] | None = None

        def update(self, ref: Any, fields: dict[str, Any]) -> None:
            self.fields = fields

    path = "projects/x/databases/(default)/documents/build_jobs/j1"
    patch = {"progress": [], "operator_action": DELETE, "error": "gave_up"}
    assert _translated(patch)["operator_action"] is not DELETE
    tx = _Tx()
    body = getattr(_update_if_owner, "to_wrap", _update_if_owner)
    assert body(tx, _Ref(), "wA", patch) is True
    assert tx.fields is not None
    pbs_for_update(path, tx.fields, None)
    with pytest.raises(TypeError):
        pbs_for_update(path, patch, None)


def test_note_request_queues_once(tmp_path: Path) -> None:
    worker = _worker(tmp_path, FakeStore())
    assert worker.note_request("z10_0909_0217", {}) is True
    worker.in_flight.add(("request", "z10_0909_0217"))
    assert worker.note_request("z10_0909_0217", {}) is False


def test_note_build_job_skips_done_and_in_flight(tmp_path: Path) -> None:
    worker = _worker(tmp_path, FakeStore())
    assert worker.note_build_job("j1", {"status": "queued"}) is True
    assert worker.note_build_job("j2", {"status": "published"}) is False
    assert worker.note_build_job("j3", {"status": "failed"}) is False
    worker.in_flight.add(("build_job", "j1"))
    assert worker.note_build_job("j1", {"status": "queued"}) is False


def test_claim_build_job_leaves_one_winner() -> None:
    store = FakeStore()
    store.build_jobs["j1"] = {"status": "queued"}
    assert store.claim_build_job("j1", "w1") is True
    assert store.claim_build_job("j1", "w2") is False
    assert store.build_jobs["j1"]["worker_id"] == "w1"


def test_job_without_status_counts_as_queued(tmp_path: Path, monkeypatch: Any) -> None:
    """#164: a status-less job is claimed, built, and reconciled."""
    store = FakeStore()
    job = _enrich_job()
    del job["status"]
    store.build_jobs["j1"] = job
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.worker_id = "wA"

    def fake_stages(*args: Any, **kwargs: Any) -> None:
        (args[5] / "animals").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worker, "_run_stages", fake_stages)
    monkeypatch.setattr(Worker, "pull_world", lambda self: False)
    store.enabled = False
    assert worker.poll() == []
    store.enabled = True
    assert ("build_job", "j1") in worker.poll()
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "checking"
    assert store.build_jobs["j1"]["progress"][0]["id"] == "enrich_lion"


def _owned_job(status: str = "building", owner: str = "wA") -> dict[str, Any]:
    job = _enrich_job(status=status)
    job["worker_id"] = owner
    job["lease_until"] = "2999-01-01T00:00:00+00:00"
    return job


def test_second_worker_runs_no_stages_for_a_live_job(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#155: a job owned live is untouched by another worker."""
    store = FakeStore()
    store.build_jobs["j1"] = _owned_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.worker_id = "wB"
    ran: list[str] = []

    def fake_stages(*args: Any, **kwargs: Any) -> None:
        ran.append("stages")

    monkeypatch.setattr(worker, "_run_stages", fake_stages)
    worker.handle_build_job("j1")
    assert ran == []
    assert store.build_jobs["j1"]["worker_id"] == "wA"
    assert store.build_jobs["j1"]["status"] == "building"


def test_expired_lease_can_be_recovered(tmp_path: Path, monkeypatch: Any) -> None:
    """#155: a dead owner's job builds on under the new owner."""
    store = FakeStore()
    job = _owned_job()
    job["lease_until"] = "2020-01-01T00:00:00+00:00"
    store.build_jobs["j1"] = job
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.worker_id = "wB"

    def fake_stages(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
        cast: Any = None,
        enrich_tiles: Any = None,
    ) -> None:
        (item_dir / "animals").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worker, "_run_stages", fake_stages)
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["worker_id"] == "wB"
    assert store.build_jobs["j1"]["status"] == "checking"
    assert store.build_jobs["j1"]["progress"][0]["id"] == "enrich_lion"


def test_stale_owner_writes_nothing(tmp_path: Path) -> None:
    """#155: after takeover the old owner neither writes nor publishes."""
    import pytest

    from builder.pipeline import StaleOwner

    store = FakeStore()
    store.build_jobs["j1"] = _owned_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.worker_id = "wA"
    assert worker._claim("j1") is True
    store.build_jobs["j1"]["lease_until"] = "2020-01-01T00:00:00+00:00"
    assert store.claim_build_job("j1", "wB") is True
    store.update_build_job("j1", {"progress": [{"id": "enrich_lion"}]})
    before = dict(store.build_jobs["j1"])
    blobs = dict(store.blobs)
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"] == before
    assert store.blobs == blobs
    with pytest.raises(StaleOwner):
        worker._set_status("j1", "failed")


def test_dead_same_host_owner_is_followed_at_once(tmp_path: Path) -> None:
    """#155: a restart's new pid takes over without waiting out the lease."""
    import os
    import socket
    import subprocess

    proc = subprocess.Popen(["true"])
    proc.wait()
    dead = f"{socket.gethostname()}:{proc.pid}"
    assert dead != f"{socket.gethostname()}:{os.getpid()}"
    store = FakeStore()
    store.build_jobs["j1"] = _owned_job(owner=dead)
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert worker._claim("j1") is True
    assert store.build_jobs["j1"]["worker_id"] == worker.worker_id


def test_restart_resumes_without_repeating_finished_items(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#155: the new owner skips finished items; their calls stay spent."""
    import pytest

    store = FakeStore()
    store.build_jobs["j1"] = {
        "format": 1,
        "kind": "build",
        "tile": "z10_0581_0294",
        "status": "queued",
        "budget_usd": 10.0,
        "cost_usd": 0.0,
        "progress": [],
        "items": ["animal_fox", "animal_hare"],
    }
    store.plans["z10_0581_0294"] = {
        "tile": "z10_0581_0294",
        "items": [
            {"id": "animal_fox", "kind": "animal", "what": "fox", "estimate_usd": 1.0},
            {
                "id": "animal_hare",
                "kind": "animal",
                "what": "mountain_hare",
                "estimate_usd": 1.0,
            },
        ],
        "improve": [],
        "world": "abc123",
    }
    calls: list[str] = []
    seen: set[str] = set()

    def fake_stages(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
        cast: Any = None,
        enrich_tiles: Any = None,
    ) -> None:
        calls.append(item["id"])
        (item_dir / "animals").mkdir(parents=True, exist_ok=True)
        if item["id"] == "animal_hare" and "crashed" not in seen:
            seen.add("crashed")
            raise KeyboardInterrupt("the worker dies here")

    import dataclasses

    first = _flow_worker(tmp_path, store, boreal_3x3())
    first.worker_id = "wA"
    monkeypatch.setattr(first, "_run_stages", fake_stages)
    with pytest.raises(KeyboardInterrupt):
        first.handle_build_job("j1")
    assert calls == ["animal_fox", "animal_hare"]
    assert [e["id"] for e in store.build_jobs["j1"]["progress"]] == ["animal_fox"]
    store.build_jobs["j1"]["lease_until"] = "2020-01-01T00:00:00+00:00"
    second = dataclasses.replace(first, worker_id="wB")
    monkeypatch.setattr(second, "_run_stages", fake_stages)
    second.handle_build_job("j1")
    assert store.build_jobs["j1"]["worker_id"] == "wB"
    assert calls == ["animal_fox", "animal_hare", "animal_hare"]
    assert [e["id"] for e in store.build_jobs["j1"]["progress"]] == [
        "animal_fox",
        "animal_hare",
    ]


def test_claim_build_job_needs_queued_or_an_expired_lease() -> None:
    """#155: a live lease refuses; an expired one can be taken."""
    store = FakeStore()
    assert store.claim_build_job("missing", "w1") is False
    store.build_jobs["j1"] = {"status": "building"}
    assert store.claim_build_job("j1", "w1") is True
    assert store.build_jobs["j1"]["worker_id"] == "w1"
    assert store.claim_build_job("j1", "w2") is False
    assert store.claim_build_job("j1", "w1") is True
    store.build_jobs["j1"]["lease_until"] = "2020-01-01T00:00:00+00:00"
    assert store.claim_build_job("j1", "w2") is True
    assert store.build_jobs["j1"]["worker_id"] == "w2"
    store.build_jobs["j1"]["status"] = "published"
    assert store.claim_build_job("j1", "w1") is False


def _planned_worker(tmp_path: Path, store: FakeStore, monkeypatch: Any) -> Worker:
    def fake_fetch(tile: str, candidates: list[dict[str, Any]]) -> dict[str, float]:
        return {"fox": 5.0, "vole": 20.0}

    monkeypatch.setattr("builder.plan.fetch_smoothed", fake_fetch)
    world = make_world(tmp_path)
    grid_dir = write_grids(tmp_path / "grids", boreal_3x3())
    worker = _worker(tmp_path, store)
    worker.world = world
    worker.grid_dir = grid_dir
    worker.world_commit = "abc123"
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


def test_handle_request_plans_into_store(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    store = FakeStore()
    store.requests["z10_0581_0294"] = {"tile": "z10_0581_0294"}
    worker = _planned_worker(tmp_path, store, monkeypatch)
    worker.handle_request("z10_0581_0294")
    plan = store.plans["z10_0581_0294"]
    assert plan["code_only"] is True
    assert plan["world"] == "abc123"
    assert "planned code_only=True" in capsys.readouterr().out


def test_handle_request_skips_when_off(tmp_path: Path, monkeypatch: Any) -> None:
    store = FakeStore()
    store.enabled = False
    store.requests["z10_0581_0294"] = {"tile": "z10_0581_0294"}
    worker = _planned_worker(tmp_path, store, monkeypatch)
    worker.handle_request("z10_0581_0294")
    assert "z10_0581_0294" not in store.plans


def test_handle_request_alerts_when_unplannable(
    tmp_path: Path, monkeypatch: Any
) -> None:
    store = FakeStore()
    store.requests["xx"] = {"tile": "xx"}
    worker = _planned_worker(tmp_path, store, monkeypatch)
    worker.handle_request("xx")
    assert "xx" not in store.plans
    assert store.alerts[-1]["kind"] == "plan_failed"


def test_handle_request_skips_old_grid_quietly(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    """#195.5: retired EASE ids log nothing and raise no alert."""
    store = FakeStore()
    store.requests["n_414_478"] = {"tile": "n_414_478"}
    worker = _planned_worker(tmp_path, store, monkeypatch)
    worker.handle_request("n_414_478")
    assert "n_414_478" not in store.plans
    assert store.alerts == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_unknown_kind_stays_queued(tmp_path: Path) -> None:
    """#199.3: a kind the worker doesn't know is never failed."""
    store = FakeStore()
    store.build_jobs["j1"] = {
        "kind": "terraform",
        "status": "queued",
        "tile": "z10_0581_0294",
        "tiles": ["z10_0581_0294"],
        "items": [],
    }
    worker = _worker(tmp_path, store)
    worker.world = make_world(tmp_path)
    worker.grid_dir = tmp_path
    worker.build_job("j1", store.build_jobs["j1"])
    assert store.build_jobs["j1"]["status"] == "queued"
    assert "owner" not in store.build_jobs["j1"]


def test_handle_request_writes_a_blocked_plan(tmp_path: Path, monkeypatch: Any) -> None:
    """#198.1: an unplannable request ends blocked, never waiting."""
    import builder.plan as plan_mod

    store = FakeStore()
    store.requests["z10_0581_0294"] = {"tile": "z10_0581_0294"}
    worker = _planned_worker(tmp_path, store, monkeypatch)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(
            "tile z10_0581_0294 failed the world's check: animals: no home"
        )

    monkeypatch.setattr(plan_mod, "make_tile", boom)
    worker.handle_request("z10_0581_0294")
    plan = store.plans["z10_0581_0294"]
    assert plan["blocked"] == "world_check: animals: no home"
    assert plan["items"] == []


def test_sync_world_replans_on_move(tmp_path: Path, monkeypatch: Any) -> None:
    store = FakeStore()
    store.requests["z10_0581_0294"] = {"tile": "z10_0581_0294"}
    worker = _planned_worker(tmp_path, store, monkeypatch)
    worker.handle_request("z10_0581_0294")
    assert store.plans["z10_0581_0294"]["world"] == "abc123"
    worker.sync_world("def456")
    assert worker.world_commit == "def456"
    assert store.plans["z10_0581_0294"]["world"] == "def456"


def test_sync_world_ignores_same_and_unknown(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    store = FakeStore()
    worker = _planned_worker(tmp_path, store, monkeypatch)
    worker.sync_world("abc123")
    worker.sync_world("unknown")
    assert worker.world_commit == "abc123"
    assert "re-planning" not in capsys.readouterr().out


def test_dispatch_alerts_on_crash(tmp_path: Path) -> None:
    store = FakeStore()
    worker = _worker(tmp_path, store)

    def boom(tile: str) -> None:
        raise RuntimeError("kaboom")

    worker.handle_request = boom  # type: ignore[method-assign]
    listener = Listener(worker=worker, store=store)
    listener._dispatch("request", "z10_0909_0217")
    assert ("request", "z10_0909_0217") not in worker.in_flight
    assert store.alerts[-1]["kind"] == "request_failed"


def test_handle_build_job_leaves_it_queued(tmp_path: Path) -> None:
    store = FakeStore()
    store.build_jobs["j1"] = {"status": "queued", "tile": "z10_0909_0217"}
    worker = _worker(tmp_path, store)
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "queued"
    assert len(store.pending_build_jobs()) == 1


def test_dispatch_runs_and_releases(tmp_path: Path) -> None:
    worker = _worker(tmp_path, FakeStore())
    listener = Listener(worker=worker, store=FakeStore())
    listener._dispatch("request", "z10_0909_0217")
    assert ("request", "z10_0909_0217") not in worker.in_flight
    listener._dispatch("build_job", "j1")
    assert ("build_job", "j1") not in worker.in_flight


def test_listener_stop_unsubscribes_both_watches(tmp_path: Path) -> None:
    """#150.3: stopping the listener unsubscribes, never crashes."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    listener = Listener(worker=worker, store=store)
    listener.stop.set()
    listener.start()
    assert store.unsubscribed == ["requests", "build_jobs"]


def test_dispatch_survives_unknown_kinds(tmp_path: Path, capsys: Any) -> None:
    worker = _worker(tmp_path, FakeStore())
    listener = Listener(worker=worker, store=FakeStore())
    listener._dispatch("dragon", "d1")
    assert "unknown kind" in capsys.readouterr().err
    assert ("dragon", "d1") not in worker.in_flight


def test_listen_reports_a_dead_backend(tmp_path: Path, monkeypatch, capsys) -> None:
    from builder import worker_store
    from builder.worker import listen

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise worker_store.StoreError("no credentials")

    monkeypatch.setattr(worker_store, "FirebaseStore", boom)
    code = listen(
        Config(ledger_path=str(tmp_path / "ledger.json")),
        tmp_path / "jobs",
        tmp_path / "world",
        "demo",
    )
    assert code == 1
    assert "no credentials" in capsys.readouterr().err


def test_listen_refuses_a_grid_without_its_files(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    """#247.2: the worker never starts on a file-less grid."""
    import gzip

    from builder import worker_store
    from builder.worker import listen

    class _Store:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    monkeypatch.setattr(worker_store, "FirebaseStore", _Store)
    grid_dir = tmp_path / "grids"
    grid_dir.mkdir()
    with gzip.open(grid_dir / "z10.json.gz", "wt", encoding="utf-8") as handle:
        json.dump({"grid": "z10", "cells": {}}, handle)
    code = listen(
        Config(ledger_path=str(tmp_path / "ledger.json")),
        tmp_path / "jobs",
        tmp_path / "world",
        "demo",
        None,
        grid_dir,
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "reef.json.gz" in err
    assert "landcover.json.gz" in err


def _flow_worker(
    tmp_path: Path, store: FakeStore, tile_cells: dict[str, Any]
) -> Worker:
    from fake_world import make_world, write_grids

    worker = _worker(tmp_path, store)
    worker.world = make_world(tmp_path)
    worker.grid_dir = write_grids(tmp_path / "grids", tile_cells)
    worker.world_commit = "abc123"
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


def _code_plan(tile: str) -> dict[str, Any]:
    return {
        "format": 1,
        "tile": tile,
        "code_only": True,
        "items": [],
        "improve": [
            {
                "id": "improve_lion",
                "kind": "animal",
                "what": "lion",
                "from": "fox",
                "estimate_usd": 0.15,
            }
        ],
        "estimate_usd": 0.15,
        "name": {"en": "Boreal forest", "fi": "Havumetsä"},
        "coming": ["improve_lion"],
        "world": "abc123",
        "planned_at": "2026-10-28T00:00:00+00:00",
    }


def _code_job() -> dict[str, Any]:
    return {
        "format": 1,
        "tile": "z10_0581_0294",
        "budget_usd": 0.0,
        "kind": "code",
        "status": "queued",
        "items": ["improve_lion"],
        "visibility": "public",
        "created_by": "policy",
        "progress": [],
        "cost_usd": 0.0,
    }


def test_code_job_publishes_with_no_model_call(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#118.10: a code request becomes ready with no live call."""
    import builder.species

    def no_model(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a recorded answer makes no live call")

    monkeypatch.setattr(builder.species, "ask", no_model)
    store = FakeStore()
    store.plans["z10_0581_0294"] = _code_plan("z10_0581_0294")
    store.build_jobs["j1"] = _code_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["cost_usd"] == 0.0
    assert job["worker_id"] == worker.worker_id
    assert "tiles/v2/z10/072_036.json" in store.blobs
    assert "tiles/v2/index.json" in store.blobs


def test_border_code_job_carries_the_reef_mask(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#256: a mist-made reef tile carries the map's mask; a far one none."""
    import json

    from builder.habitats import HabitatGrid
    from builder.schemas import ROOT

    world = ROOT.parent / "animundo-world"
    grid_dir = ROOT / "data" / "habitat-grid"
    if not (world / "animal-list.json").is_file():
        pytest.skip("needs the world checkout next to the builder")
    if not (grid_dir / "reef.json.gz").is_file():
        pytest.skip("needs the baked reef map")
    monkeypatch.setattr(
        "builder.tiles.fetch_smoothed", lambda tile, c: {"common_tern": 9.0}
    )
    monkeypatch.setattr(Worker, "pull_world", lambda self: False)
    store = FakeStore()
    store.build_jobs["j1"] = {**_code_job(), "tile": "z10_0927_0559"}
    store.requests["z10_0927_0560"] = {"notes": 2}
    worker = _worker(tmp_path, store)
    worker.world = world
    worker.grid_dir = grid_dir
    worker.world_commit = "testworld"
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "published"
    data = json.loads(store.read_blob("tiles/v2/z10/115_069.json")[0].decode("utf-8"))
    tile = data["tiles"]["z10_0927_0559"]
    grid = HabitatGrid.load(grid_dir / "z10.json.gz")
    assert tile["terrain"]["reef_mask"] == grid.reef_mask(927, 559)
    assert tile["terrain"]["reef"] == round(sum(tile["terrain"]["reef_mask"]) / 256, 3)
    store.build_jobs["j2"] = {**_code_job(), "tile": "z10_0581_0294"}
    worker.species = _forest_survey("fox", "vole")
    worker.handle_build_job("j2")
    assert store.build_jobs["j2"]["status"] == "published"
    data = json.loads(store.read_blob("tiles/v2/z10/072_036.json")[0].decode("utf-8"))
    assert "terrain" not in data["tiles"]["z10_0581_0294"]


def test_second_publish_carries_the_newer_version(tmp_path: Path) -> None:
    """#143.5: a tile published twice stamps 1, then 2 with new content."""
    import json

    store = FakeStore()
    store.build_jobs["j1"] = _code_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["version"] == 1
    assert worker.world is not None
    _file_lion(worker.world, ["grass"])
    worker.species = _forest_survey("fox", "mountain_hare", "vole", "lion")
    store.build_jobs["j2"] = _code_job()
    worker.handle_build_job("j2")
    assert store.build_jobs["j2"]["version"] == 2
    data = json.loads(store.read_blob("tiles/v2/z10/072_036.json")[0].decode("utf-8"))
    assert data["tiles"]["z10_0581_0294"]["version"] == 2
    assert "lion" in data["tiles"]["z10_0581_0294"]["species"]["playable"]


def _remake_job() -> dict[str, Any]:
    return {
        "format": 1,
        "tiles": ["z10_0581_0294"],
        "budget_usd": 0.0,
        "kind": "remake",
        "status": "queued",
        "visibility": "public",
        "created_by": "admin",
        "progress": [],
        "cost_usd": 0.0,
    }


def _seed_ready_tile(store: FakeStore, version: int = 1) -> None:
    store.blobs["tiles/v2/z10/072_036.json"] = json.dumps(
        {
            "format": 1,
            "grid": "z10",
            "group": [72, 36],
            "version": version,
            "tiles": {
                "z10_0581_0294": {
                    "id": "z10_0581_0294",
                    "version": version,
                    "biome": "boreal_taiga",
                }
            },
            "blocked": {},
        }
    ).encode("utf-8")


def _empty_cache(worker: Worker, tmp_path: Path) -> Worker:
    import dataclasses

    cache = tmp_path / "species"
    cache.mkdir(exist_ok=True)
    worker.config = dataclasses.replace(worker.config, species_cache_dir=str(cache))
    return worker


def _group_version(store: FakeStore) -> int:
    data = json.loads(store.read_blob("tiles/v2/z10/072_036.json")[0].decode("utf-8"))
    return int(data["tiles"]["z10_0581_0294"]["version"])


def test_remake_that_would_spend_stops_for_a_price(tmp_path: Path) -> None:
    """#243.3: a remake with no cached survey waits priced, living on v1."""
    from builder.prices import SURVEY_ESTIMATE_USD

    store = FakeStore()
    store.build_jobs["j1"] = _remake_job()
    _seed_ready_tile(store)
    worker = _empty_cache(_flow_worker(tmp_path, store, boreal_3x3()), tmp_path)
    worker.species = None
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "waiting_yes"
    assert job["price_usd"] == SURVEY_ESTIMATE_USD
    assert _group_version(store) == 1
    assert store.plans == {}


def test_remake_keeps_old_live_then_publishes_next_version(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#243.3: v1 readable through the waits, v2 with tiles+versions."""
    monkeypatch.setattr(
        "builder.plan.fetch_smoothed", lambda tile, candidates: {"fox": 5.0}
    )
    store = FakeStore()
    store.build_jobs["j1"] = _remake_job()
    _seed_ready_tile(store)
    worker = _empty_cache(_flow_worker(tmp_path, store, boreal_3x3()), tmp_path)
    worker.species = None
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "waiting_yes"
    assert _group_version(store) == 1
    store.build_jobs["j1"]["remake_yes"] = {"cap": 1.0}
    worker.species = _forest_survey("fox", "vole")
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "building"
    assert _group_version(store) == 1
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert _group_version(store) == 2
    assert job["tiles"] == ["z10_0581_0294"]
    assert job["versions"] == {"z10_0581_0294": 2}


def test_remake_refuses_a_tile_that_is_not_ready(tmp_path: Path) -> None:
    """#243: only a ready tile can be asked for again."""
    store = FakeStore()
    store.build_jobs["j1"] = _remake_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "failed"
    assert store.build_jobs["j1"]["error"] == "not_ready"


def test_remake_refuses_bad_tiles(tmp_path: Path) -> None:
    """#243: a remake names exactly one tile."""
    store = FakeStore()
    first = _remake_job()
    del first["tiles"]
    store.build_jobs["j1"] = first
    second = _remake_job()
    second["tiles"] = ["z10_0581_0294", "z10_0582_0294"]
    store.build_jobs["j2"] = second
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_build_job("j1")
    worker.handle_build_job("j2")
    assert store.build_jobs["j1"]["error"] == "bad_remake"
    assert store.build_jobs["j2"]["error"] == "bad_remake"


def test_remake_no_turns_the_job_down(tmp_path: Path) -> None:
    """#243.2: a no ends the priced remake."""
    store = FakeStore()
    job = _remake_job()
    job["status"] = "waiting_yes"
    job["remake_no"] = True
    store.build_jobs["j1"] = job
    _seed_ready_tile(store)
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "failed"
    assert store.build_jobs["j1"]["error"] == "turned_down"
    assert _group_version(store) == 1


def test_set_published_carries_tiles_for_a_remake(tmp_path: Path) -> None:
    """#243: the policy moves a remake's areas from tiles+versions."""
    store = FakeStore()
    store.build_jobs["j1"] = {"kind": "remake", "status": "building"}
    store.build_jobs["j2"] = {"kind": "code", "status": "building"}
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert store.claim_build_job("j1", worker.worker_id) is True
    assert store.claim_build_job("j2", worker.worker_id) is True
    worker._set_published("j1", {"kind": "remake"}, "z10_0581_0294", {"version": 4})
    assert store.build_jobs["j1"]["status"] == "published"
    assert store.build_jobs["j1"]["tiles"] == ["z10_0581_0294"]
    assert store.build_jobs["j1"]["versions"] == {"z10_0581_0294": 4}
    worker._set_published("j2", {"kind": "code"}, "z10_0581_0294", {"version": 5})
    assert store.build_jobs["j2"]["version"] == 5
    assert "tiles" not in store.build_jobs["j2"]


def test_set_published_carries_plants_over_cap(tmp_path: Path) -> None:
    """#253: survey extras past the caps ride the job, sorted."""
    store = FakeStore()
    store.build_jobs["j1"] = {"kind": "code", "status": "building"}
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert store.claim_build_job("j1", worker.worker_id) is True
    worker._set_published(
        "j1", {"kind": "code"}, "z10_0581_0294", {"version": 2}, ["b", "a", "b"]
    )
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["plants_over_cap"] == ["a", "b"]


def test_remake_with_gaps_publishes_by_code(tmp_path: Path, monkeypatch: Any) -> None:
    """#246.3: a gappy remake needs no yes and publishes the next version."""
    monkeypatch.setattr(
        "builder.plan.fetch_smoothed", lambda tile, candidates: {"fox": 5.0}
    )
    store = FakeStore()
    store.build_jobs["j1"] = _remake_job()
    _seed_ready_tile(store)
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert worker.world is not None
    _link_food_rule(worker.world)
    (worker.world / "homes" / "boreal_forest.json").write_text(
        json.dumps({"habitat": "boreal_forest", "ecoregions": [717], "food": []})
    )
    worker.species = _forest_survey("fox", "mountain_hare", "vole")
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert _group_version(store) == 2
    assert job["versions"] == {"z10_0581_0294": 2}


def test_remake_ignores_an_older_survey_on_a_cached_run(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#257.1: a cached survey records 0, never the ledger's past."""
    monkeypatch.setattr(
        "builder.plan.fetch_smoothed", lambda tile, candidates: {"fox": 5.0}
    )
    store = FakeStore()
    store.build_jobs["j1"] = _remake_job()
    _seed_ready_tile(store)
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    survey = {"spent": 0.05, "updated": "2026-11-01"}
    (tmp_path / "ledger.json").write_text(
        json.dumps(
            {
                "jobs": {"survey:z10_0581_0294": survey},
                "days": {},
                "total": 0.05,
            }
        )
    )
    worker.species = _forest_survey("fox", "vole")
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["cost_usd"] == 0.0
    assert job["survey_cached"] is True
    assert "survey_usd" not in job
    assert store.tile_docs["z10_0581_0294"]["cost_usd"] == 0.0


def test_remake_records_only_its_own_survey_call(tmp_path: Path) -> None:
    """#257.1: a paid survey records its own call, not the past."""
    store = FakeStore()
    store.build_jobs["j1"] = _remake_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert store.claim_build_job("j1", worker.worker_id) is True
    survey = {"spent": 0.08, "updated": "2026-11-03"}
    (tmp_path / "ledger.json").write_text(
        json.dumps(
            {
                "jobs": {"survey:z10_0581_0294": survey},
                "days": {},
                "total": 0.08,
            }
        )
    )
    worker._record_remake_survey_spend("j1", "z10_0581_0294", 0.05)
    job = store.build_jobs["j1"]
    assert job["cost_usd"] == 0.03
    assert job["survey_usd"] == 0.03
    worker._record_remake_survey_spend("j1", "z10_0581_0294", 0.05)
    assert store.build_jobs["j1"]["cost_usd"] == 0.03


def test_remake_marks_a_cached_survey_on_the_job(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#252.2: a free survey says cached on the job."""
    monkeypatch.setattr(
        "builder.plan.fetch_smoothed", lambda tile, candidates: {"fox": 5.0}
    )
    store = FakeStore()
    store.build_jobs["j1"] = _remake_job()
    _seed_ready_tile(store)
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.species = _forest_survey("fox", "vole")
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["cost_usd"] == 0.0
    assert job["survey_cached"] is True


def test_remake_build_path_prices_its_items(tmp_path: Path) -> None:
    """#243.3: a remake that needs making waits with the build price."""
    store = FakeStore()
    store.build_jobs["j1"] = _remake_job()
    _seed_ready_tile(store)
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert store.claim_build_job("j1", worker.worker_id) is True
    plan = {
        "tile": "z10_0581_0294",
        "code_only": False,
        "items": [
            {
                "id": "animal_lion",
                "kind": "animal",
                "what": "lion",
                "from": "fox",
                "estimate_usd": 0.4,
            }
        ],
        "improve": [],
    }
    worker._remake_build("j1", store.build_jobs["j1"], "z10_0581_0294", plan)
    job = store.build_jobs["j1"]
    assert job["status"] == "waiting_yes"
    assert job["price_usd"] == 0.4
    assert job["items"] == ["animal_lion"]
    assert _group_version(store) == 1


def test_code_job_fails_when_tile_blocks(tmp_path: Path, monkeypatch: Any) -> None:
    store = FakeStore()
    store.plans["z10_0581_0294"] = _code_plan("z10_0581_0294")
    store.build_jobs["j1"] = _code_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.species = {
        "place": "Boreal forest in southern Finland",
        "parts": [{"kind": "forest", "species": []}],
        "plants": [],
        "trees": [],
        "outside_animals": [],
        "outside_plants": [],
        "name_en": "Nuuksio forest",
        "name_fi": "Nuuksion metsä",
    }
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "failed"
    assert job["error"] == "no_animal"
    assert store.alerts[-1]["kind"] == "job_failed"


def test_code_job_records_its_tile_history(tmp_path: Path) -> None:
    """#165.3: a publish lands one made row that stays put."""
    store = FakeStore()
    store.build_jobs["j1"] = _code_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_build_job("j1")
    (entry,) = store.tile_history
    assert entry["tile"] == "z10_0581_0294"
    assert entry["event"] == "made"
    assert entry["job"] == "j1"
    assert entry["kind"] == "code"
    assert entry["world"] == "abc123"
    assert entry["version"] == 1
    assert entry["maker"]
    assert entry["at"]
    assert "reason" not in entry


def test_second_publish_appends_a_remake_row(tmp_path: Path) -> None:
    """#165.3: a remake bumps the version; both rows remain."""
    store = FakeStore()
    store.build_jobs["j1"] = _code_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_build_job("j1")
    assert worker.world is not None
    _file_lion(worker.world, ["grass"])
    worker.species = _forest_survey("fox", "mountain_hare", "vole", "lion")
    store.build_jobs["j2"] = _code_job()
    worker.handle_build_job("j2")
    assert [e["event"] for e in store.tile_history] == ["made", "remade"]
    assert [e["version"] for e in store.tile_history] == [1, 2]
    assert [e["job"] for e in store.tile_history] == ["j1", "j2"]


def test_blocked_code_job_records_its_reason(tmp_path: Path) -> None:
    """#165.3: a blocked tile lands a row with its reason, no version."""
    store = FakeStore()
    store.build_jobs["j1"] = _code_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.species = _forest_survey()
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "failed"
    (entry,) = store.tile_history
    assert entry["event"] == "blocked"
    assert entry["reason"] == "no_animal"
    assert entry["version"] is None


def _open_sea_cells() -> dict[str, Any]:
    cell: list[Any] = [None, 1.0, None, 0.0, None, 0.0, 1.0, -50.0]
    return {f"{c},{r}": list(cell) for r in (293, 294, 295) for c in (580, 581, 582)}


def test_code_job_refuses_open_sea_far_from_everything(tmp_path: Path) -> None:
    """#177.6: a lone open-sea tile fails before the maker runs."""
    store = FakeStore()
    store.build_jobs["j1"] = _code_job()
    worker = _flow_worker(tmp_path, store, _open_sea_cells())
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "failed"
    assert job["error"] == "too_far"
    (entry,) = store.tile_history
    assert entry["event"] == "blocked"
    assert entry["reason"] == "too_far"


def test_code_job_makes_open_sea_near_a_made_tile(tmp_path: Path) -> None:
    """#177.6: a made neighbour lets the open-sea tile through to making."""
    store = FakeStore()
    store.build_jobs["j1"] = _code_job()
    store.blobs["tiles/v2/z10/072_036.json"] = json.dumps(
        {"tiles": {"z10_0582_0294": {"id": "z10_0582_0294"}}}
    ).encode("utf-8")
    worker = _flow_worker(tmp_path, store, _open_sea_cells())
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["error"] == "no_home"


def test_code_job_makes_open_sea_near_a_noted_tile(tmp_path: Path) -> None:
    """#177.6: notes 3 cells off let the open-sea tile through to making."""
    store = FakeStore()
    store.build_jobs["j1"] = _code_job()
    store.requests["z10_0584_0295"] = {"tile": "z10_0584_0295", "notes": 3}
    store.requests["z10_0585_0296"] = {"tile": "z10_0585_0296", "notes": 0}
    worker = _flow_worker(tmp_path, store, _open_sea_cells())
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["error"] == "no_home"


def test_broken_history_never_stops_the_tile(tmp_path: Path, monkeypatch: Any) -> None:
    """#165.3: a failing history write prints; the tile still ships."""

    def broken(item: dict[str, Any]) -> str:
        raise ValueError("firestore is down")

    store = FakeStore()
    store.build_jobs["j1"] = _code_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    monkeypatch.setattr(store, "add_tile_history", broken)
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "published"
    assert store.tile_history == []


def test_pending_request_builds_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    """#118.10: a pending or turned-down request builds nothing."""
    store = FakeStore()
    store.requests["z10_0581_0294"] = {"tile": "z10_0581_0294", "turned_down": "far"}
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    monkeypatch.setattr("builder.plan.fetch_smoothed", lambda tile, c: {})
    worker.handle_request("z10_0581_0294")
    assert "z10_0581_0294" in store.plans
    assert store.build_jobs == {}
    assert store.blobs == {}


def test_request_alerts_plan_failed_when_gbif_is_down(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A GBIF outage is a plan failure, loud and unretried."""
    store = FakeStore()
    store.requests["z10_0581_0294"] = {"tile": "z10_0581_0294"}

    def down(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("GBIF timed out")

    monkeypatch.setattr("builder.worker.plan_tile", down)
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_request("z10_0581_0294")
    assert "z10_0581_0294" not in store.plans
    assert store.alerts[-1]["kind"] == "plan_failed"


def test_plant_item_builds_through_stages(tmp_path: Path, monkeypatch: Any) -> None:
    """T76 finished: a plant item briefs, scaffolds and checks."""
    import json

    from builder import build as build_mod

    store = FakeStore()
    plan = _code_plan("z10_0581_0294")
    plan["code_only"] = False
    plan["items"] = [
        {
            "id": "plant_cloudberry",
            "kind": "plant",
            "what": "cloudberry",
            "from": "bilberry",
            "estimate_usd": 0.02,
        }
    ]
    plan["improve"] = []
    store.plans["z10_0581_0294"] = plan
    store.build_jobs["j1"] = {
        "format": 1,
        "tile": "z10_0581_0294",
        "budget_usd": 8.0,
        "kind": "build",
        "status": "queued",
        "items": ["plant_cloudberry"],
        "visibility": "public",
        "created_by": "admin",
        "progress": [],
        "cost_usd": 0.0,
    }
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    home_path = worker.world / "homes" / "boreal_forest.json"
    home = json.loads(home_path.read_text())
    home["food"] = [{"kind": "berries", "names": ["cloudberry"]}]
    home_path.write_text(json.dumps(home))

    made: list[str] = []

    class Pipe:
        def run(self, ctx: Any) -> dict[str, Any]:
            item_dir = tmp_path / "jobs" / "j1" / "plant_cloudberry"
            models = item_dir / "models" / "plants"
            models.mkdir(parents=True)
            (models / "cloudberry.json").write_text(json.dumps({"id": "cloudberry"}))
            made.append("cloudberry")
            return {}

    monkeypatch.setattr(build_mod, "item_pipeline", lambda **k: Pipe())
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert made == ["cloudberry"]
    assert job["status"] == "checking"
    assert job["progress"] == [
        {
            "id": "plant_cloudberry",
            "status": "checking",
            "report": job["progress"][0]["report"],
            "cost_usd": 0.0,
        }
    ]
    item_dir = tmp_path / "jobs" / "j1" / "plant_cloudberry"
    brief = json.loads((item_dir / "home.json").read_text())
    assert brief["plant"] == "cloudberry"
    assert brief["names"] == ["cloudberry"]
    region = json.loads((item_dir / "regions" / "zz_work.json").read_text())
    assert region["ground_cover"]["plants"] == [
        {"model": "cloudberry", "names": ["cloudberry"]}
    ]
    survey = json.loads((item_dir / "survey.json").read_text())
    assert survey["facts"] == [{"topic": "plant", "text": "cloudberry grows here"}]
    assert (
        store.blobs["review/j1/plant_cloudberry/models/plants/cloudberry.json"]
        == b'{"id": "cloudberry"}'
    )


def test_private_job_writes_judge_preview(tmp_path: Path, monkeypatch: Any) -> None:
    """T84: a private job's checked items become its judge's pack."""
    store = FakeStore()
    plan = _code_plan("z10_0581_0294")
    plan["code_only"] = False
    plan["items"] = [
        {
            "id": "animal_lion",
            "kind": "animal",
            "what": "lion",
            "from": "fox",
            "estimate_usd": 0.15,
        }
    ]
    plan["improve"] = []
    store.plans["z10_0581_0294"] = plan
    store.build_jobs["j1"] = {
        "format": 1,
        "tile": "z10_0581_0294",
        "budget_usd": 8.0,
        "kind": "build",
        "status": "queued",
        "items": ["animal_lion"],
        "visibility": "private",
        "created_by": "policy",
        "progress": [],
        "cost_usd": 0.0,
        "private_to": "judge1",
    }
    worker = _flow_worker(tmp_path, store, boreal_3x3())

    def fake_stages(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
        cast: Any = None,
        enrich_tiles: Any = None,
    ) -> None:
        folder = item_dir / "animals"
        folder.mkdir(parents=True)
        (folder / "lion.json").write_bytes(b'{"id": "lion"}')

    monkeypatch.setattr(worker, "_run_stages", fake_stages)
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "checking"
    assert job["preview"] == {"pack": "previews/j1/1/"}
    assert "previews/j1/1/manifest.json" in store.blobs
    assert "previews/j1/1/animals/lion.json" in store.blobs


def test_build_job_fails_without_its_tile(tmp_path: Path) -> None:
    store = FakeStore()
    store.plans["z10_0581_0294"] = _code_plan("z10_0581_0294")
    job = _code_job()
    del job["tile"]
    store.build_jobs["j1"] = job
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "failed"
    assert store.build_jobs["j1"]["error"] == "no_tile"


def test_build_job_fails_without_its_plan(tmp_path: Path) -> None:
    job = _code_job()
    job["kind"] = "build"
    store = FakeStore()
    store.build_jobs["j1"] = job
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "failed"
    assert store.build_jobs["j1"]["error"] == "no_plan"


def test_code_job_needs_no_plan(tmp_path: Path) -> None:
    """#143.3: a code job makes its tile straight from the world."""
    store = FakeStore()
    store.build_jobs["j1"] = _code_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "published"


def _enrich_job(**over: Any) -> dict[str, Any]:
    job: dict[str, Any] = {
        "format": 1,
        "kind": "enrich",
        "tile": "z10_0581_0294",
        "tiles": ["z10_0581_0294", "z10_0580_0295"],
        "items": ["enrich_lion"],
        "enrich": {"kind": "animal", "what": "lion", "from": "fox"},
        "budget_usd": 1.0,
        "visibility": "public",
        "created_by": "enrich",
        "status": "queued",
        "progress": [],
        "cost_usd": 0.0,
    }
    job.update(over)
    return job


def test_enrich_job_builds_its_item_to_checking(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#143.3: no plan document; the item comes from the job."""
    import json

    store = FakeStore()
    store.build_jobs["j1"] = _enrich_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())

    def fake_stages(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
        cast: Any = None,
        enrich_tiles: Any = None,
    ) -> None:
        assert tile == "z10_0581_0294"
        assert item["id"] == "enrich_lion"
        folder = item_dir / "animals"
        folder.mkdir(parents=True)
        (folder / "lion.json").write_bytes(b'{"id": "lion"}')

    monkeypatch.setattr(worker, "_run_stages", fake_stages)
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "checking"
    assert [(e["id"], e["status"]) for e in job["progress"]] == [
        ("enrich_lion", "checking")
    ]
    assert "review/j1/enrich_lion/report.html" in store.blobs
    snapshot = json.loads((tmp_path / "jobs" / "j1" / "plan.json").read_text())
    assert snapshot["items"] == [
        {
            "id": "enrich_lion",
            "kind": "animal",
            "what": "lion",
            "from": "fox",
            "estimate_usd": 1.0,
        }
    ]


def test_enrich_item_finds_its_own_from(tmp_path: Path) -> None:
    """#143.3: the list's start_from, or the closest plant."""
    store = FakeStore()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    item = worker._enrich_item(_enrich_job(enrich={"kind": "animal", "what": "lion"}))
    assert item["id"] == "enrich_lion"
    assert item["from"] == "fox"
    item = worker._enrich_item(
        _enrich_job(
            items=["enrich_arctic_willow"],
            enrich={"kind": "plant", "what": "arctic_willow"},
        )
    )
    assert item["id"] == "enrich_arctic_willow"
    assert item["from"] == "bilberry"


def test_enrich_job_fails_cleanly_when_malformed(tmp_path: Path) -> None:
    """#143.3: a bad enrich spec fails the job, never crashes it."""
    store = FakeStore()
    store.build_jobs["j1"] = _enrich_job(enrich={"kind": "animal"})
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "failed"
    assert store.build_jobs["j1"]["error"] == "bad_enrich"
    store.build_jobs["j2"] = _enrich_job(items=["enrich_tiger"])
    worker.handle_build_job("j2")
    assert store.build_jobs["j2"]["error"] == "bad_enrich"


def _forest_survey(
    *species: str, name_en: str = "Fox woods", name_fi: str = "Kettumetsä"
) -> dict[str, Any]:
    return {
        "place": "Boreal forest in southern Finland",
        "parts": [{"kind": "forest", "species": list(species)}],
        "plants": [],
        "trees": [],
        "outside_animals": [],
        "outside_plants": [],
        "name_en": name_en,
        "name_fi": name_fi,
    }


class _AnswerClient:
    """One survey answer, on repeat."""

    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer

    def chat(self, **kwargs: Any) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(self.answer))
                )
            ]
        )


class _SilentClient:
    """A remake never calls, however live the worker."""

    def chat(self, **kwargs: Any) -> Any:
        raise AssertionError("a remake never calls")

    def responses_create(self, **kwargs: Any) -> Any:
        raise AssertionError("a remake never calls")


def _named_list(world: Path) -> None:
    """Names for the fake list's animals: the survey prompt needs them."""
    names = {
        "fox": ("Red fox", "Vulpes vulpes"),
        "mountain_hare": ("Mountain hare", "Lepus timidus"),
        "vole": ("Vole", "Microtus agrestis"),
        "lion": ("Lion", "Panthera leo"),
        "tiger": ("Tiger", "Panthera tigris"),
    }
    listed = json.loads((world / "animal-list.json").read_text())
    for entry in listed["animals"]:
        if entry["id"] in names:
            entry["name"], entry["latin"] = names[entry["id"]]
    (world / "animal-list.json").write_text(json.dumps(listed))


def _file_lion(world: Path, diet: list[str]) -> None:
    import json

    (world / "animals" / "lion.json").write_text(
        json.dumps({"playable": True, "tags": ["big_cat"], "diet": diet})
    )
    listed = json.loads((world / "animal-list.json").read_text())
    next(e for e in listed["animals"] if e["id"] == "lion")["in_library"] = "lion"
    (world / "animal-list.json").write_text(json.dumps(listed))


def _link_food_rule(world: Path) -> None:
    """The checkout's food rule into the fake world, or skip without it (#241.3).

    A symlink, not a copy: the rule always runs from the world
    checkout beside the builder.
    """
    from builder.schemas import ROOT

    real = ROOT.parent / "animundo-world" / "tools" / "food.py"
    if not real.is_file():
        pytest.skip("needs a 0061 world checkout next to the builder")
    tools = world / "tools"
    tools.mkdir(exist_ok=True)
    link = tools / "food.py"
    if not link.exists():
        link.symlink_to(real)
    food_dir = world / "food"
    food_dir.mkdir(exist_ok=True)
    rules = food_dir / "rules.json"
    if not rules.is_file():
        rules.write_text(json.dumps({"kinds": {"grass": {"from": "grass"}}}))
    for home_path in (world / "homes").glob("*.json"):
        try:
            home = json.loads(home_path.read_text())
        except ValueError:
            continue
        if isinstance(home, dict) and "ground_cover" not in home:
            home["ground_cover"] = {"grass": 0.15, "plants": []}
            home_path.write_text(json.dumps(home))


def test_enrich_review_overlays_only_the_tiles_that_list_it(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#220: two tiles list the lion, one doesn't (the lab of #143.4)."""
    import json

    from builder.grid import group_path
    from builder.species import Source as SpeciesSource
    from builder.tiles import make_tile

    A, B, C = "z10_0581_0294", "z10_0580_0295", "z10_0590_0298"
    block = boreal_3x3()
    side = boreal_3x3(590, 298)
    # B's southern and western neighbours: the row below, column 579.
    edge = {f"{c},{r}": list(block["581,294"]) for r in (294, 295, 296) for c in (579,)}
    edge.update({f"{c},296": list(block["581,294"]) for c in (580, 581)})
    cells = {**block, **edge, **side}
    store = FakeStore()
    worker = _flow_worker(tmp_path, store, cells)
    assert worker.world is not None
    _named_list(worker.world)
    cache = tmp_path / "cache"
    FOUR = ("fox", "mountain_hare", "vole", "lion")
    THREE = ("fox", "mountain_hare", "vole")
    made: dict[str, Any] = {}
    for tile, species in ((A, FOUR), (B, FOUR), (C, THREE)):
        tile_made, blocked = make_tile(
            tile,
            worker._habitat_grid(),
            worker.world,
            builder_root=tmp_path,
            species_source=SpeciesSource(
                client=_AnswerClient(_forest_survey(*species)), cache_dir=cache
            ),
        )
        assert blocked is None and tile_made is not None
        made[tile] = tile_made
    assert made[A]["wanted"] == [{"kind": "animal", "what": "lion", "part": "forest"}]
    assert "wanted" not in made[C]
    g1, g2 = group_path(A), group_path(C)
    assert g1 != g2 and group_path(B) == g1
    store.blobs[g1] = json.dumps(
        {
            "format": 1,
            "grid": "z10",
            "group": [72, 36],
            "version": 1,
            "tiles": {A: made[A], B: made[B]},
            "blocked": {},
        }
    ).encode("utf-8")
    store.blobs[g2] = json.dumps(
        {
            "format": 1,
            "grid": "z10",
            "group": [73, 37],
            "version": 1,
            "tiles": {C: made[C]},
            "blocked": {},
        }
    ).encode("utf-8")
    before_c = store.blobs[g2]
    before_g1 = store.blobs[g1]
    store.blobs["tiles/v2/index.json"] = json.dumps(
        {
            "format": 1,
            "made_at": "",
            "world": "",
            "groups": {},
            "counts": {"ready": 3, "blocked": {}},
        }
    ).encode("utf-8")
    assert worker.world is not None
    _file_lion(worker.world, ["grass"])
    store.build_jobs["j1"] = _enrich_job(
        tile=A,
        tiles=[A, B, C],
        status="checking",
        progress=[
            {
                "id": "enrich_lion",
                "status": "checking",
                "report": "review/j1/enrich_lion/report.html",
            }
        ],
        review={"enrich_lion": {"by": "kai", "at": "t", "result": "shipped"}},
    )
    writes: list[str] = []
    real_write = store.write_blob
    monkeypatch.setattr(
        store,
        "write_blob",
        lambda path, data, *a, **k: (
            writes.append(path),
            real_write(path, data, *a, **k),
        ),
    )
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["overlay"] == [A, B]
    assert job["gaps"] == {}
    assert "versions" not in job
    assert job["pack"] == "packs/animal_lion/1/"
    tiles_doc, manifest = _pack_files(store, job["pack"])
    assert tiles_doc == {"format": 1, "playable": ["lion"], "tiles": [A, B]}
    assert manifest["min_game"] == 2
    assert store.blobs[g1] == before_g1
    assert store.blobs[g2] == before_c
    assert writes.count(g1) == 0
    assert writes.count(g2) == 0
    assert writes.count("tiles/v2/index.json") == 0


def test_enrich_review_seats_the_items_own_plant(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#251.2: a plant enrich overlays with its reviewed seating."""
    import json

    from builder.grid import group_path
    from builder.species import Source as SpeciesSource
    from builder.tiles import make_tile

    A = "z10_0581_0294"
    store = FakeStore()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert worker.world is not None
    _named_list(worker.world)
    cache = tmp_path / "cache"
    made, blocked = make_tile(
        A,
        worker._habitat_grid(),
        worker.world,
        builder_root=tmp_path,
        species_source=SpeciesSource(
            client=_AnswerClient(_forest_survey("fox", "vole")), cache_dir=cache
        ),
    )
    assert blocked is None and made is not None
    assert "plants" not in made
    blob = group_path(A)
    store.blobs[blob] = json.dumps(
        {
            "format": 1,
            "grid": "z10",
            "group": [72, 36],
            "version": 1,
            "tiles": {A: made},
            "blocked": {},
        }
    ).encode("utf-8")
    plants = worker.world / "models" / "plants"
    plants.mkdir(parents=True, exist_ok=True)
    (plants / "table_coral.json").write_text(json.dumps({"id": "table_coral"}))
    regions = tmp_path / "jobs" / "j1" / "enrich_table_coral" / "regions"
    regions.mkdir(parents=True)
    (regions / f"tl_{A}.json").write_text(
        json.dumps(
            {
                "id": f"tl_{A}",
                "biome": "boreal_forest",
                "plants": {"water": [{"id": "table_coral", "density": 0.245}]},
                "species": {"playable": ["fox", "vole"]},
            }
        )
    )
    store.build_jobs["j1"] = _enrich_job(
        tile=A,
        tiles=[A],
        items=["enrich_table_coral"],
        enrich={"kind": "plant", "what": "table_coral", "from": "soft_coral"},
        status="checking",
        progress=[{"id": "enrich_table_coral", "status": "checking"}],
        review={"enrich_table_coral": {"by": "kai", "at": "t", "result": "shipped"}},
    )
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["overlay"] == [A]
    assert job["pack"] == "packs/plant_table_coral/1/"
    tiles_doc, _ = _pack_files(store, job["pack"])
    assert tiles_doc["plants"] == {"water": [{"id": "table_coral", "density": 0.245}]}
    assert tiles_doc["tiles"] == [A]


def test_enrich_review_skips_an_animal_where_its_habitat_mismatches(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#258.2: the animal's habitats must hold the tile's biome."""
    import json

    from builder.grid import group_path
    from builder.species import Source as SpeciesSource
    from builder.tiles import make_tile

    A = "z10_0581_0294"

    def run(job_id: str, habitats: list[str]) -> dict[str, Any]:
        base = tmp_path / job_id
        store = FakeStore()
        worker = _flow_worker(base, store, boreal_3x3())
        assert worker.world is not None
        _named_list(worker.world)
        listed = json.loads((worker.world / "animal-list.json").read_text())
        next(e for e in listed["animals"] if e["id"] == "lion")["habitats"] = habitats
        (worker.world / "animal-list.json").write_text(json.dumps(listed))
        cache = base / "cache"
        made, blocked = make_tile(
            A,
            worker._habitat_grid(),
            worker.world,
            builder_root=base,
            species_source=SpeciesSource(
                client=_AnswerClient(_forest_survey("fox", "vole")), cache_dir=cache
            ),
        )
        assert blocked is None and made is not None
        assert made["biome"] == "boreal_forest"
        store.blobs[group_path(A)] = json.dumps(
            {
                "format": 1,
                "grid": "z10",
                "group": [72, 36],
                "version": 1,
                "tiles": {A: made},
                "blocked": {},
            }
        ).encode("utf-8")
        _file_lion(worker.world, ["grass"])
        regions = base / "jobs" / job_id / "enrich_lion" / "regions"
        regions.mkdir(parents=True)
        (regions / f"tl_{A}.json").write_text(
            json.dumps(
                {
                    "id": f"tl_{A}",
                    "biome": "boreal_forest",
                    "species": {"playable": ["fox", "vole", "lion"]},
                }
            )
        )
        store.build_jobs[job_id] = _enrich_job(
            tile=A,
            tiles=[A],
            status="checking",
            progress=[{"id": "enrich_lion", "status": "checking"}],
            review={"enrich_lion": {"by": "kai", "at": "t", "result": "shipped"}},
        )
        with monkeypatch.context() as m:
            m.setattr(
                worker,
                "_species_source",
                lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
            )
            worker.handle_build_job(job_id)
        return store.build_jobs[job_id]

    bad = run("j1", ["coral_reef"])
    assert bad["status"] == "failed"
    assert bad["skipped"] == {A: "habitat_mismatch"}
    good = run("j2", ["boreal_forest"])
    assert good["status"] == "published"
    assert good["overlay"] == [A]


def test_enrich_review_pack_failure_fails_the_item(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#257.3: bad_pack and PackExists fail the item with the reason."""
    import json

    from builder.grid import group_path
    from builder.packs import PackExists
    from builder.species import Source as SpeciesSource
    from builder.tiles import make_tile

    A = "z10_0581_0294"

    def run(job_id: str, blame: str) -> dict[str, Any]:
        base = tmp_path / job_id
        store = FakeStore()
        worker = _flow_worker(base, store, boreal_3x3())
        assert worker.world is not None
        _named_list(worker.world)
        cache = base / "cache"
        made, blocked = make_tile(
            A,
            worker._habitat_grid(),
            worker.world,
            builder_root=base,
            species_source=SpeciesSource(
                client=_AnswerClient(_forest_survey("fox", "vole")), cache_dir=cache
            ),
        )
        assert blocked is None and made is not None
        store.blobs[group_path(A)] = json.dumps(
            {
                "format": 1,
                "grid": "z10",
                "group": [72, 36],
                "version": 1,
                "tiles": {A: made},
                "blocked": {},
            }
        ).encode("utf-8")
        plants = worker.world / "models" / "plants"
        plants.mkdir(parents=True, exist_ok=True)
        (plants / "table_coral.json").write_text(json.dumps({"id": "table_coral"}))
        regions = base / "jobs" / job_id / "enrich_table_coral" / "regions"
        regions.mkdir(parents=True)
        (regions / f"tl_{A}.json").write_text(
            json.dumps(
                {
                    "id": f"tl_{A}",
                    "biome": "boreal_forest",
                    "plants": {"water": [{"id": "table_coral", "density": 0.245}]},
                    "species": {"playable": ["fox", "vole"]},
                }
            )
        )
        store.build_jobs[job_id] = _enrich_job(
            tile=A,
            tiles=[A],
            items=["enrich_table_coral"],
            enrich={"kind": "plant", "what": "table_coral", "from": "soft_coral"},
            status="checking",
            progress=[{"id": "enrich_table_coral", "status": "checking"}],
            review={
                "enrich_table_coral": {"by": "kai", "at": "t", "result": "shipped"}
            },
            overlay=["z10_0000_0000"],
            skipped={"z10_0000_0000": "stale"},
            gaps={"z10_0000_0000": {}},
        )
        with monkeypatch.context() as m:
            m.setattr(
                worker,
                "_species_source",
                lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
            )
            if blame == "bad_pack":
                m.setattr("builder.packs.check_pack", lambda packed: ["boom"])
            else:

                def taken(*args: Any, **kwargs: Any) -> Any:
                    raise PackExists("taken")

                m.setattr("builder.packs.publish_pack", taken)
            worker.handle_build_job(job_id)
        return store.build_jobs[job_id]

    for blame in ("bad_pack", "PackExists"):
        job = run(f"j-{blame}", blame)
        assert job["status"] == "failed"
        assert job["error"] == "bad_pack"
        assert job["progress"] == [{"id": "enrich_table_coral", "status": "failed"}]
        # #261b: this run's overlay, not the previous run's values.
        assert job["overlay"] == [A]
        assert job["skipped"] == {}
        assert job["gaps"] == {}


def test_enrich_review_fails_when_every_tile_skips(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#251.3: an empty overlay fails loud, never published."""
    import json

    from builder.grid import group_path
    from builder.species import Source as SpeciesSource
    from builder.tiles import make_tile

    A = "z10_0581_0294"
    store = FakeStore()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert worker.world is not None
    _named_list(worker.world)
    cache = tmp_path / "cache"
    made, blocked = make_tile(
        A,
        worker._habitat_grid(),
        worker.world,
        builder_root=tmp_path,
        species_source=SpeciesSource(
            client=_AnswerClient(_forest_survey("fox", "vole")), cache_dir=cache
        ),
    )
    assert blocked is None and made is not None
    blob = group_path(A)
    store.blobs[blob] = json.dumps(
        {
            "format": 1,
            "grid": "z10",
            "group": [72, 36],
            "version": 1,
            "tiles": {A: made},
            "blocked": {},
        }
    ).encode("utf-8")
    plants = worker.world / "models" / "plants"
    plants.mkdir(parents=True, exist_ok=True)
    (plants / "table_coral.json").write_text(json.dumps({"id": "table_coral"}))
    store.build_jobs["j1"] = _enrich_job(
        tile=A,
        tiles=[A],
        items=["enrich_table_coral"],
        enrich={"kind": "plant", "what": "table_coral", "from": "soft_coral"},
        status="checking",
        progress=[{"id": "enrich_table_coral", "status": "checking"}],
        review={"enrich_table_coral": {"by": "kai", "at": "t", "result": "shipped"}},
    )
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "failed"
    assert job["error"] == "nothing_overlaid"
    assert job["overlay"] == []
    assert job["skipped"] == {A: "table_coral is not in the remake"}
    assert job["progress"] == [{"id": "enrich_table_coral", "status": "failed"}]
    assert "pack" not in job
    assert not [name for name in store.blobs if name.startswith("packs/")]


def test_enrich_review_overlays_drifted_tiles(tmp_path: Path, monkeypatch: Any) -> None:
    """#227.1: enrich judges the species, never the moved shore or plants."""
    import json

    from builder.grid import group_path
    from builder.species import Source as SpeciesSource
    from builder.tiles import make_tile

    A, B = "z10_0581_0294", "z10_0580_0295"
    block = boreal_3x3()
    edge = {f"{c},{r}": list(block["581,294"]) for r in (294, 295, 296) for c in (579,)}
    edge.update({f"{c},296": list(block["581,294"]) for c in (580, 581)})
    store = FakeStore()
    worker = _flow_worker(tmp_path, store, {**block, **edge})
    assert worker.world is not None
    _named_list(worker.world)
    cache = tmp_path / "cache"
    FOUR = ("fox", "mountain_hare", "vole", "lion")
    made: dict[str, Any] = {}
    for tile in (A, B):
        tile_made, blocked = make_tile(
            tile,
            worker._habitat_grid(),
            worker.world,
            builder_root=tmp_path,
            species_source=SpeciesSource(
                client=_AnswerClient(_forest_survey(*FOUR)), cache_dir=cache
            ),
        )
        assert blocked is None and tile_made is not None
        made[tile] = tile_made
    blob_path = group_path(A)
    store.blobs[blob_path] = json.dumps(
        {
            "format": 1,
            "grid": "z10",
            "group": [72, 36],
            "version": 1,
            "tiles": {
                tile: {
                    **made[tile],
                    "shore": {"span": 999, "reach": 999, "d": "AAAA"},
                    "plants": {"cover": [{"id": "drifted_weed", "share": 0.9}]},
                }
                for tile in (A, B)
            },
            "blocked": {},
        }
    ).encode("utf-8")
    store.blobs["tiles/v2/index.json"] = json.dumps(
        {"format": 1, "made_at": "", "world": "", "groups": {}, "counts": {}}
    ).encode("utf-8")
    _file_lion(worker.world, ["grass"])
    store.build_jobs["j1"] = _enrich_job(
        tile=A,
        tiles=[A, B],
        status="checking",
        progress=[
            {
                "id": "enrich_lion",
                "status": "checking",
                "report": "review/j1/enrich_lion/report.html",
            }
        ],
        review={"enrich_lion": {"by": "kai", "at": "t", "result": "shipped"}},
    )
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["overlay"] == [A, B]
    assert job["gaps"] == {}
    assert job["skipped"] == {}


def test_enrich_intake_writes_converter_places(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#161.1: an enrich item checks against its tiles' places, no scaffold."""
    import json
    from types import SimpleNamespace

    import fake_world

    import builder.build as build_mod
    from builder.grid import group_path
    from builder.stages.maker import AnimalMakerStage

    T = "z10_0581_0294"
    store = FakeStore()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert worker.world is not None
    _named_list(worker.world)
    fake_world.write_converter_tools(worker.world)
    cast = {
        "playable": ["mountain_hare", "vole"],
        "prey": ["vole"],
        "predators": [],
    }
    store.blobs[group_path(T)] = json.dumps(
        {
            "format": 1,
            "grid": "z10",
            "group": [72, 36],
            "version": 1,
            "tiles": {
                T: {
                    "id": T,
                    "kind": "code",
                    "version": 1,
                    "home": "boreal_forest",
                    "species": cast,
                }
            },
            "blocked": {},
        }
    ).encode("utf-8")

    class _Pipe:
        def run(self, ctx: Any) -> dict[str, Any]:
            return {}

    monkeypatch.setattr(build_mod, "item_pipeline", lambda **kwargs: _Pipe())
    item = {
        "id": "enrich_lion",
        "kind": "animal",
        "what": "lion",
        "from": "fox",
        "estimate_usd": 1.0,
    }
    plan = {
        "format": 1,
        "tile": T,
        "code_only": False,
        "items": [item],
        "improve": [],
        "estimate_usd": 1.0,
    }
    item_dir = tmp_path / "jobs" / "j1" / "enrich_lion"
    worker._write_enrich_places("j1", item_dir, [T], item)
    worker._run_stages(SimpleNamespace(), "j1", T, plan, item, item_dir, None, [T])
    assert not (item_dir / "regions" / "zz_work.json").exists()
    region = json.loads((item_dir / "regions" / f"tl_{T}.json").read_text())
    assert region["biome"] == "boreal_forest"
    assert "animals" not in region
    assert region["species"]["playable"] == ["mountain_hare", "vole", "lion"]
    brief = json.loads((item_dir / "home.json").read_text())
    assert brief["enrich"] == {"tiles": [T]}
    research = {
        "main_food": {"kind": "prey", "name": "Mountain hare"},
        "other_foods": [],
    }
    tags = AnimalMakerStage()._code_hunts(
        worker.world, SimpleNamespace(job_dir=item_dir), research, region, "lion"
    )
    assert tags == ["small_mammal"]


def test_enrich_run_item_writes_places_for_its_tiles(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#161.1: an enrich item's intake converts its tiles, no cast."""
    import json

    import fake_world

    from builder.grid import group_path

    T = "z10_0581_0294"
    store = FakeStore()
    store.build_jobs["j1"] = _enrich_job(status="building")
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert worker.world is not None
    fake_world.write_converter_tools(worker.world)
    cast = {"playable": ["fox"], "prey": [], "predators": ["fox"]}
    store.blobs[group_path(T)] = json.dumps(
        {
            "format": 1,
            "grid": "z10",
            "group": [72, 36],
            "version": 1,
            "tiles": {
                T: {
                    "id": T,
                    "kind": "code",
                    "version": 1,
                    "home": "boreal_forest",
                    "species": cast,
                }
            },
            "blocked": {},
        }
    ).encode("utf-8")
    seen: dict[str, Any] = {}

    def fake_stages(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
        cast: Any = None,
        enrich_tiles: Any = None,
    ) -> None:
        seen["cast"] = cast
        seen["tiles"] = enrich_tiles

    monkeypatch.setattr(worker, "_run_stages", fake_stages)
    assert store.claim_build_job("j1", worker.worker_id) is True
    job = store.build_jobs["j1"]
    item = {
        "id": "enrich_lion",
        "kind": "animal",
        "what": "lion",
        "from": "fox",
        "estimate_usd": 1.0,
    }
    worker.run_item("j1", job, T, {"items": [item]}, item, 1.0)
    assert seen["cast"] is None
    assert seen["tiles"] == ["z10_0581_0294", "z10_0580_0295"]
    region = json.loads(
        (
            tmp_path / "jobs" / "j1" / "enrich_lion" / "regions" / f"tl_{T}.json"
        ).read_text()
    )
    assert region["species"]["playable"] == ["fox", "lion"]


def test_enrich_item_report_names_its_tile(tmp_path: Path, monkeypatch: Any) -> None:
    """#150.2: an enrich report shows the tile, its name and the kind."""
    T = "z10_0581_0294"
    store = FakeStore()
    store.build_jobs["j1"] = _enrich_job(status="building")
    worker = _flow_worker(tmp_path, store, boreal_3x3())

    def fake_stages(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
        cast: Any = None,
        enrich_tiles: Any = None,
    ) -> None:
        (item_dir / "animals").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worker, "_run_stages", fake_stages)
    assert store.claim_build_job("j1", worker.worker_id) is True
    job = store.build_jobs["j1"]
    item = {
        "id": "enrich_lion",
        "kind": "animal",
        "what": "lion",
        "from": "fox",
        "estimate_usd": 1.0,
    }
    worker.run_item("j1", job, T, {"items": [item]}, item, 1.0)
    report = (tmp_path / "jobs" / "j1" / "enrich_lion" / "report.html").read_text()
    assert f"<td>{T} (" in report
    assert "Nuuksio" in report
    assert "<td>enrich</td>" in report


def test_build_item_report_keeps_old_labels(tmp_path: Path, monkeypatch: Any) -> None:
    """#150.2: a build item's report is unlabeled, as before."""
    store = FakeStore()
    store.build_jobs["j1"] = {
        "format": 1,
        "kind": "build",
        "tile": "z10_0581_0294",
        "status": "building",
        "cost_usd": 0.0,
        "progress": [],
    }
    worker = _flow_worker(tmp_path, store, boreal_3x3())

    def fake_stages(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
        cast: Any = None,
        enrich_tiles: Any = None,
    ) -> None:
        (item_dir / "animals").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worker, "_run_stages", fake_stages)
    assert store.claim_build_job("j1", worker.worker_id) is True
    job = store.build_jobs["j1"]
    item = {
        "id": "animal_fox",
        "kind": "animal",
        "what": "fox",
        "from": "fox",
        "estimate_usd": 1.0,
    }
    worker.run_item("j1", job, "z10_0581_0294", {"items": [item]}, item, 1.0)
    report = (tmp_path / "jobs" / "j1" / "animal_fox" / "report.html").read_text()
    assert "(unknown area)" in report
    assert "new_place" in report


def test_tile_doc_remakes_an_unpublished_tile(tmp_path: Path, monkeypatch: Any) -> None:
    """#161.1: with no published entry, the doc remakes by code."""
    import builder.tiles as tiles_mod

    T = "z10_0581_0294"
    store = FakeStore()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    doc = {"id": T, "kind": "code", "species": {"playable": ["fox"]}}
    calls: list[str] = []

    def fake_make(tile: str, *args: Any, **kwargs: Any) -> tuple[Any, Any]:
        calls.append(tile)
        return doc, None

    monkeypatch.setattr(tiles_mod, "make_tile", fake_make)
    assert worker._tile_doc(T) == doc
    assert calls == [T]
    monkeypatch.setattr(tiles_mod, "make_tile", lambda *a, **k: (None, {}))
    with pytest.raises(ValueError, match="remakes blocked"):
        worker._tile_doc(T)


def test_enrich_review_overlays_a_region_tile(tmp_path: Path, monkeypatch: Any) -> None:
    """#220: a region grid no longer shields a tile from its overlay (#202.3)."""
    import json

    from builder.grid import group_path
    from builder.species import Source as SpeciesSource
    from builder.tiles import make_tile

    P = "z10_0581_0294"
    store = FakeStore()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert worker.world is not None
    regions = worker.world / "regions"
    regions.mkdir()
    (regions / "fi_test.json").write_text(
        json.dumps({"id": "fi_test", "grid": {"grid": "z10", "tiles": [[581, 294]]}})
    )
    _named_list(worker.world)
    cache = tmp_path / "cache"
    made, blocked = make_tile(
        P,
        worker._habitat_grid(),
        worker.world,
        builder_root=tmp_path,
        species_source=SpeciesSource(
            client=_AnswerClient(
                _forest_survey("fox", "mountain_hare", "vole", "lion")
            ),
            cache_dir=cache,
        ),
    )
    assert blocked is None and made is not None
    _file_lion(worker.world, ["grass"])
    blob = group_path(P)
    store.blobs[blob] = json.dumps(
        {
            "format": 1,
            "grid": "z10",
            "group": [72, 36],
            "version": 1,
            "tiles": {P: made},
            "blocked": {},
        }
    ).encode("utf-8")
    store.blobs["tiles/v2/index.json"] = json.dumps(
        {
            "format": 1,
            "made_at": "",
            "world": "",
            "groups": {},
            "counts": {"ready": 1, "blocked": {}},
        }
    ).encode("utf-8")
    store.build_jobs["j1"] = _enrich_job(
        tile=P,
        tiles=[P],
        status="checking",
        progress=[
            {
                "id": "enrich_lion",
                "status": "checking",
                "report": "review/j1/enrich_lion/report.html",
            }
        ],
        review={"enrich_lion": {"by": "kai", "at": "t", "result": "shipped"}},
    )
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
    )
    before = store.blobs[blob]
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["overlay"] == [P]
    assert "versions" not in job
    tiles_doc, manifest = _pack_files(store, job["pack"])
    assert tiles_doc["playable"] == ["lion"]
    assert tiles_doc["tiles"] == [P]
    assert manifest["min_game"] == 2
    assert store.blobs[blob] == before


def _region_world(world: Path) -> None:
    regions = world / "regions"
    regions.mkdir(exist_ok=True)
    (regions / "xx_test_reef.json").write_text(
        json.dumps(
            {
                "id": "xx_test_reef",
                "name": {"en": "Test Reef", "fi": "Testiriutta"},
                "center": {"lat": 60.0, "lon": 24.0},
                "grid": {"grid": "z10", "tiles": [[581, 294]]},
                "animals": {
                    "playable": ["mountain_hare", "vole"],
                    "prey": ["vole"],
                    "predators": [],
                },
                "plants": {"cover": [{"id": "bilberry", "density": 0.1}]},
                "food": [{"kind": "grass"}, {"kind": "berries"}],
            }
        )
    )


def test_enrich_intake_checks_a_region_tile_as_a_tile(
    tmp_path: Path,
) -> None:
    """#202.3: a region grid no longer diverts the intake; tile converts."""
    import fake_world

    from builder.grid import group_path

    P = "z10_0581_0294"
    store = FakeStore()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert worker.world is not None
    fake_world.write_converter_tools(worker.world)
    _region_world(worker.world)
    store.blobs[group_path(P)] = json.dumps(
        {
            "format": 1,
            "grid": "z10",
            "group": [72, 36],
            "version": 1,
            "tiles": {
                P: {
                    "id": P,
                    "kind": "code",
                    "version": 1,
                    "home": "boreal_forest",
                    "species": {
                        "playable": ["mountain_hare", "vole"],
                        "prey": ["vole"],
                        "predators": [],
                    },
                }
            },
            "blocked": {},
        }
    ).encode("utf-8")
    item = {
        "id": "enrich_lion",
        "kind": "animal",
        "what": "lion",
        "from": "fox",
        "estimate_usd": 1.0,
    }
    item_dir = tmp_path / "jobs" / "j1" / "enrich_lion"
    worker._write_enrich_places("j1", item_dir, [P], item)
    region = json.loads((item_dir / "regions" / f"tl_{P}.json").read_text())
    assert region["species"]["playable"] == ["mountain_hare", "vole", "lion"]
    assert not (item_dir / "regions" / "xx_test_reef.json").exists()
    manifest = json.loads((item_dir / "checks" / "enrich_places.json").read_text())
    assert manifest == {"places": [f"tl_{P}"]}


def test_tile_home_ignores_the_region(tmp_path: Path) -> None:
    """#202.3: a region grid no longer lends its file as the home."""
    import fake_world

    store = FakeStore()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert worker.world is not None
    fake_world.write_converter_tools(worker.world)
    _region_world(worker.world)
    plan = {"format": 1, "items": []}
    assert worker._tile_home("z10_0581_0294", plan, "j1")["habitat"] == "boreal_forest"
    assert worker._tile_home("z10_0580_0295", plan, "j1")["habitat"] == "boreal_forest"


def test_enrich_job_on_a_region_tile_reaches_checking(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#202.3: a region grid no longer diverts the intake; tile left out."""
    import fake_world

    import builder.build as build_mod

    P = "z10_0581_0294"
    store = FakeStore()
    store.build_jobs["j1"] = _enrich_job(tile=P, tiles=[P])
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert worker.world is not None
    _named_list(worker.world)
    fake_world.write_converter_tools(worker.world)
    _region_world(worker.world)

    class _Pipe:
        def run(self, ctx: Any) -> dict[str, Any]:
            assert ctx.job_dir.name == "enrich_lion"
            folder = ctx.job_dir / "animals"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "lion.json").write_bytes(b'{"id": "lion"}')
            return {}

    monkeypatch.setattr(build_mod, "item_pipeline", lambda **kwargs: _Pipe())
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "checking"
    assert [(e["id"], e["status"]) for e in job["progress"]] == [
        ("enrich_lion", "checking")
    ]
    assert not (
        tmp_path / "jobs" / "j1" / "enrich_lion" / "regions" / "xx_test_reef.json"
    ).exists()
    assert "review/j1/enrich_lion/report.html" in store.blobs


def test_checking_item_with_failing_checks_says_so(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#173.2: survived failures land in progress[] as checks_failed."""
    import fake_world

    import builder.build as build_mod

    P = "z10_0581_0294"
    store = FakeStore()
    store.build_jobs["j1"] = _enrich_job(tile=P, tiles=[P])
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert worker.world is not None
    _named_list(worker.world)
    fake_world.write_converter_tools(worker.world)
    _region_world(worker.world)

    class _Pipe:
        def run(self, ctx: Any) -> dict[str, Any]:
            folder = ctx.job_dir / "animals"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "lion.json").write_bytes(b'{"id": "lion"}')
            checks = ctx.job_dir / "checks"
            checks.mkdir(exist_ok=True)
            (checks / "places.json").write_text(
                json.dumps(
                    {
                        "ok": False,
                        "results": [
                            {
                                "check": "place xx_test_reef",
                                "status": "fail",
                                "problems": ["no food"],
                            }
                        ],
                    }
                )
            )
            return {}

    monkeypatch.setattr(build_mod, "item_pipeline", lambda **kwargs: _Pipe())
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "checking"
    (entry,) = job["progress"]
    assert entry["checks_failed"] == ["places"]
    text = (tmp_path / "jobs" / "j1" / "enrich_lion" / "report.html").read_text(
        encoding="utf-8"
    )
    assert "Checks failing: places." in text


def test_enrich_failure_names_its_stage_and_the_tile(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#168.3: a failed item's progress and report say the stage and why."""
    import fake_world

    P = "z10_0581_0294"
    store = FakeStore()
    store.build_jobs["j1"] = _enrich_job(tile=P, tiles=[P])
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert worker.world is not None
    _named_list(worker.world)
    fake_world.write_converter_tools(worker.world)
    _region_world(worker.world)

    def broken(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
        cast: Any = None,
        enrich_tiles: Any = None,
    ) -> None:
        raise ValueError("tile z10_0581_0294 has no home in this checkout")

    monkeypatch.setattr(worker, "_run_stages", broken)
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "failed"
    (entry,) = job["progress"]
    assert entry["status"] == "failed"
    assert entry["error"] == "intake: tile z10_0581_0294 has no home in this checkout"
    item_dir = tmp_path / "jobs" / "j1" / "enrich_lion"
    text = (item_dir / "report.html").read_text(encoding="utf-8")
    assert "intake: tile z10_0581_0294 has no home in this checkout" in text
    assert "finished every built stage" not in text
    state = json.loads((item_dir / "state.json").read_text(encoding="utf-8"))
    assert state["params"]["area"] == "z10_0581_0294 (Boreal forest north of Nuuksio)"


def test_enrich_review_overlays_a_blocked_tile(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#220: the wanted opener joins the overlay; its group stays blocked."""
    import json

    from builder.grid import group_path
    from builder.species import Source as SpeciesSource
    from builder.tiles import make_tile

    D = "z10_0581_0294"
    store = FakeStore()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert worker.world is not None
    _named_list(worker.world)
    cache = tmp_path / "cache"
    made, blocked = make_tile(
        D,
        worker._habitat_grid(),
        worker.world,
        builder_root=tmp_path,
        species_source=SpeciesSource(
            client=_AnswerClient(
                _forest_survey("lion", name_en="Lion woods", name_fi="Leijonametsä")
            ),
            cache_dir=cache,
        ),
    )
    assert made is None and blocked is not None
    assert blocked["reason"] == "no_animal"
    blob = group_path(D)
    store.blobs[blob] = json.dumps(
        {
            "format": 1,
            "grid": "z10",
            "group": [72, 36],
            "version": 1,
            "tiles": {},
            "blocked": {D: blocked},
        }
    ).encode("utf-8")
    assert worker.world is not None
    _file_lion(worker.world, ["grass"])
    store.build_jobs["j1"] = _enrich_job(
        tile=D,
        tiles=[D],
        status="checking",
        progress=[
            {
                "id": "enrich_lion",
                "status": "checking",
                "report": "review/j1/enrich_lion/report.html",
            }
        ],
        review={"enrich_lion": {"by": "kai", "at": "t", "result": "shipped"}},
    )
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
    )
    before = store.blobs[blob]
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["overlay"] == [D]
    assert "versions" not in job
    tiles_doc, _manifest = _pack_files(store, job["pack"])
    assert tiles_doc["playable"] == ["lion"]
    assert tiles_doc["tiles"] == [D]
    assert store.blobs[blob] == before


def test_enrich_review_flags_gaps(tmp_path: Path, monkeypatch: Any) -> None:
    """#241.2: the half-fed lion stays, with gaps in the report."""
    import json

    from builder.grid import group_path
    from builder.species import Source as SpeciesSource
    from builder.tiles import make_tile

    A = "z10_0581_0294"
    store = FakeStore()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert worker.world is not None
    _named_list(worker.world)
    cache = tmp_path / "cache"
    made, blocked = make_tile(
        A,
        worker._habitat_grid(),
        worker.world,
        builder_root=tmp_path,
        species_source=SpeciesSource(
            client=_AnswerClient(
                _forest_survey("fox", "mountain_hare", "vole", "lion")
            ),
            cache_dir=cache,
        ),
    )
    assert blocked is None and made is not None
    blob = group_path(A)
    store.blobs[blob] = json.dumps(
        {
            "format": 1,
            "grid": "z10",
            "group": [72, 36],
            "version": 1,
            "tiles": {A: made},
            "blocked": {},
        }
    ).encode("utf-8")
    assert worker.world is not None
    _file_lion(worker.world, ["grass", "honey"])
    _link_food_rule(worker.world)
    item_dir = tmp_path / "jobs" / "j1" / "enrich_lion"
    item_dir.mkdir(parents=True)
    (item_dir / "report.html").write_text("<html></html>", encoding="utf-8")
    store.build_jobs["j1"] = _enrich_job(
        tiles=[A],
        status="checking",
        progress=[
            {
                "id": "enrich_lion",
                "status": "checking",
                "report": "review/j1/enrich_lion/report.html",
            }
        ],
        review={"enrich_lion": {"by": "kai", "at": "t", "result": "shipped"}},
    )
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
    )
    before = store.blobs[blob]
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["overlay"] == [A]
    assert job["gaps"] == {A: {"diet": ["honey"], "hunts": []}}
    assert job["pack"] == "packs/animal_lion/1/"
    assert "versions" not in job
    assert store.blobs[blob] == before
    report = store.blobs["review/j1/enrich_lion/report.html"].decode("utf-8")
    assert f"gaps {A}" in report
    assert "honey" in report


def _two_tile_lab(
    tmp_path: Path,
) -> tuple[Any, Any, Path, str, str, str]:
    """Two forest tiles listing the lion, filed with grass to eat."""
    import json

    from builder.grid import group_path
    from builder.species import Source as SpeciesSource
    from builder.tiles import make_tile

    A, B = "z10_0581_0294", "z10_0580_0295"
    store = FakeStore()
    block = boreal_3x3()
    edge = {f"{c},{r}": list(block["581,294"]) for r in (294, 295, 296) for c in (579,)}
    edge.update({f"{c},296": list(block["581,294"]) for c in (580, 581)})
    worker = _flow_worker(tmp_path, store, {**block, **edge})
    assert worker.world is not None
    _named_list(worker.world)
    cache = tmp_path / "cache"
    made = {}
    for tile in (A, B):
        tile_made, blocked = make_tile(
            tile,
            worker._habitat_grid(),
            worker.world,
            builder_root=tmp_path,
            species_source=SpeciesSource(
                client=_AnswerClient(
                    _forest_survey("fox", "mountain_hare", "vole", "lion")
                ),
                cache_dir=cache,
            ),
        )
        assert blocked is None and tile_made is not None
        made[tile] = tile_made
    blob = group_path(A)
    assert group_path(B) == blob
    store.blobs[blob] = json.dumps(
        {
            "format": 1,
            "grid": "z10",
            "group": [72, 36],
            "version": 1,
            "tiles": made,
            "blocked": {},
        }
    ).encode("utf-8")
    store.blobs["tiles/v2/index.json"] = json.dumps(
        {
            "format": 1,
            "made_at": "",
            "world": "",
            "groups": {},
            "counts": {"ready": 2, "blocked": {}},
        }
    ).encode("utf-8")
    assert worker.world is not None
    _file_lion(worker.world, ["grass"])
    return store, worker, cache, blob, A, B


def _ship_job(store: Any, tiles: list[str]) -> None:
    store.build_jobs["j1"] = _enrich_job(
        tiles=tiles,
        status="checking",
        progress=[
            {
                "id": "enrich_lion",
                "status": "checking",
                "report": "review/j1/enrich_lion/report.html",
            }
        ],
        review={"enrich_lion": {"by": "kai", "at": "t", "result": "shipped"}},
    )


def test_enrich_review_ships_only_tiles_that_passed_their_places(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#220: the passed tile overlays; the failed one is listed, left out."""
    import json

    from builder.species import Source as SpeciesSource

    store, worker, cache, blob, A, B = _two_tile_lab(tmp_path)
    item_dir = tmp_path / "jobs" / "j1" / "enrich_lion"
    (item_dir / "checks").mkdir(parents=True)
    (item_dir / "report.html").write_text("<html></html>", encoding="utf-8")
    (item_dir / "checks" / "places.json").write_text(
        json.dumps(
            {
                "ok": False,
                "results": [
                    {"check": f"place {A}", "status": "pass", "problems": []},
                    {
                        "check": f"place {B}",
                        "status": "fail",
                        "problems": [
                            f'ERROR: squirrel in tl_{B} eats "mushrooms", '
                            "but the region has no such food"
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    _ship_job(store, [A, B])
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
    )
    before = store.blobs[blob]
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["overlay"] == [A]
    assert job["gaps"] == {}
    assert "versions" not in job
    tiles_doc, _manifest = _pack_files(store, job["pack"])
    assert tiles_doc["tiles"] == [A]
    assert store.blobs[blob] == before
    report = store.blobs["review/j1/enrich_lion/report.html"].decode("utf-8")
    assert f"place {B}" in report
    assert "mushrooms" in report


def test_enrich_review_fails_place_when_no_tile_passes(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#161.2: with every place refusing, the job fails as place, listed."""
    import json

    from builder.species import Source as SpeciesSource

    store, worker, cache, blob, A, B = _two_tile_lab(tmp_path)
    before = store.blobs[blob]
    item_dir = tmp_path / "jobs" / "j1" / "enrich_lion"
    (item_dir / "checks").mkdir(parents=True)
    (item_dir / "report.html").write_text("<html></html>", encoding="utf-8")
    (item_dir / "checks" / "places.json").write_text(
        json.dumps(
            {
                "ok": False,
                "results": [
                    {
                        "check": f"place {A}",
                        "status": "fail",
                        "problems": [
                            "ERROR: squirrel in tl_z10_0581_0294 eats mushrooms"
                        ],
                    },
                    {
                        "check": f"place {B}",
                        "status": "fail",
                        "problems": [
                            "ERROR: squirrel in tl_z10_0580_0295 eats mushrooms"
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    _ship_job(store, [A, B])
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "failed"
    assert job["error"] == "place"
    assert store.blobs[blob] == before
    report = store.blobs["review/j1/enrich_lion/report.html"].decode("utf-8")
    assert f"place {A}" in report
    assert f"place {B}" in report


def test_enrich_review_drops_not_accepted(tmp_path: Path) -> None:
    """#143.4: not_accepted fails with nothing_shipped, as for a build."""
    store = FakeStore()
    store.build_jobs["j1"] = _enrich_job(
        status="checking",
        progress=[
            {
                "id": "enrich_lion",
                "status": "checking",
                "report": "review/j1/enrich_lion/report.html",
            }
        ],
        review={"enrich_lion": {"by": "kai", "at": "t", "result": "not_accepted"}},
    )
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "failed"
    assert store.build_jobs["j1"]["error"] == "nothing_shipped"


def test_enrich_review_waits_for_the_world(tmp_path: Path) -> None:
    """#143.4: shipped but unfiled stays checking."""
    store = FakeStore()
    store.build_jobs["j1"] = _enrich_job(
        status="checking",
        progress=[
            {
                "id": "enrich_lion",
                "status": "checking",
                "report": "review/j1/enrich_lion/report.html",
            }
        ],
        review={"enrich_lion": {"by": "kai", "at": "t", "result": "shipped"}},
    )
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "checking"


def test_build_job_refuses_a_blocked_plan(tmp_path: Path) -> None:
    """#141.2: a blocked plan fails with its reason, not no_items."""
    store = FakeStore()
    store.build_jobs["j1"] = {
        "format": 1,
        "tile": "z10_0594_0222",
        "budget_usd": 8.0,
        "kind": "build",
        "status": "queued",
        "items": [],
        "visibility": "public",
        "created_by": "admin",
        "progress": [],
        "cost_usd": 0.0,
    }
    store.plans["z10_0594_0222"] = {"blocked": "open_water", "items": []}
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "failed"
    assert store.build_jobs["j1"]["error"] == "open_water"


def test_build_job_stops_at_zero_budget(tmp_path: Path) -> None:
    """#118.10: a judge build stops at its cap."""
    store = FakeStore()
    plan = _code_plan("z10_0594_0222")
    plan["code_only"] = False
    plan["items"] = [
        {
            "id": "home_774",
            "kind": "home",
            "what": "774",
            "from": "tundra",
            "estimate_usd": 0.02,
        }
    ]
    plan["improve"] = []
    store.plans["z10_0594_0222"] = plan
    store.build_jobs["j1"] = {
        "format": 1,
        "tile": "z10_0594_0222",
        "budget_usd": 0.0,
        "kind": "build",
        "status": "queued",
        "items": ["home_774"],
        "visibility": "private",
        "created_by": "policy",
        "progress": [],
        "cost_usd": 0.0,
        "private_to": "judge1",
    }
    worker = _flow_worker(
        tmp_path, store, {"594,222": [774, 1.0, None, 0.0, 11, 1.0, 0.0, 200.0]}
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "failed"
    assert job["error"] == "budget"
    assert job["progress"] == []
    assert store.blobs == {}
    assert "preview" not in job


def test_failed_item_does_not_stop_others(tmp_path: Path, monkeypatch: Any) -> None:
    """#118.10: a failed item doesn't stop the others."""
    store = FakeStore()
    plan = _code_plan("z10_0594_0222")
    plan["code_only"] = False
    plan["items"] = [
        {
            "id": "home_774",
            "kind": "home",
            "what": "774",
            "from": "tundra",
            "estimate_usd": 0.02,
        },
        {
            "id": "animal_lion",
            "kind": "animal",
            "what": "lion",
            "from": "fox",
            "estimate_usd": 0.15,
        },
    ]
    plan["improve"] = []
    store.plans["z10_0594_0222"] = plan
    store.build_jobs["j1"] = {
        "format": 1,
        "tile": "z10_0594_0222",
        "budget_usd": 8.0,
        "kind": "build",
        "status": "queued",
        "items": ["home_774", "animal_lion"],
        "visibility": "public",
        "created_by": "admin",
        "progress": [],
        "cost_usd": 0.0,
    }
    calls: list[str] = []
    worker = _flow_worker(
        tmp_path, store, {"594,222": [774, 1.0, None, 0.0, 11, 1.0, 0.0, 200.0]}
    )

    def flaky(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
        cast: Any = None,
        enrich_tiles: Any = None,
    ) -> None:
        calls.append(item["id"])
        if item["id"] == "home_774":
            raise RuntimeError("boom")

    monkeypatch.setattr(worker, "_run_stages", flaky)
    worker.handle_build_job("j1")
    assert calls == ["home_774", "animal_lion"]
    assert store.build_jobs["j1"]["status"] == "checking"
    by_id = {e["id"]: e["status"] for e in store.build_jobs["j1"]["progress"]}
    assert by_id == {"home_774": "failed", "animal_lion": "checking"}


def test_item_needing_a_failed_home_is_skipped(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#135.3: no home, no start: skipped with needs, job names the home."""
    store = FakeStore()
    plan = _code_plan("z10_0594_0222")
    plan["code_only"] = False
    plan["items"] = [
        {
            "id": "home_774",
            "kind": "home",
            "what": "774",
            "from": "tundra",
            "estimate_usd": 0.02,
        },
        {
            "id": "animal_fox",
            "kind": "animal",
            "what": "fox",
            "from": "fox",
            "estimate_usd": 0.15,
        },
    ]
    plan["improve"] = []
    store.plans["z10_0594_0222"] = plan
    store.build_jobs["j1"] = {
        "format": 1,
        "tile": "z10_0594_0222",
        "budget_usd": 8.0,
        "kind": "build",
        "status": "queued",
        "items": ["home_774", "animal_fox"],
        "visibility": "public",
        "created_by": "admin",
        "progress": [],
        "cost_usd": 0.0,
    }
    worker = _flow_worker(
        tmp_path, store, {"594,222": [774, 1.0, None, 0.0, 11, 1.0, 0.0, 200.0]}
    )
    real_run_stages = worker._run_stages

    def flaky(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
        cast: Any = None,
        enrich_tiles: Any = None,
    ) -> None:
        if item["id"] == "home_774":
            raise RuntimeError("boom")
        return real_run_stages(ctx, job_id, tile, plan, item, item_dir)

    monkeypatch.setattr(worker, "_run_stages", flaky)
    worker.handle_build_job("j1")
    by_id = {e["id"]: e["status"] for e in store.build_jobs["j1"]["progress"]}
    assert by_id == {"home_774": "failed", "animal_fox": "skipped"}
    report = (tmp_path / "jobs" / "j1" / "animal_fox" / "report.html").read_text()
    assert "needs home_774" in report
    assert store.build_jobs["j1"]["status"] == "failed"
    assert store.build_jobs["j1"]["error"] == "home_774"


def test_close_item_alerts_on_spending_crossings(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from builder.pipeline import BudgetTracker

    store = FakeStore()
    store.build_jobs["j1"] = {
        "area": "a",
        "budget_usd": 10.0,
        "kind": "build",
        "status": "building",
        "items": ["animal_lion"],
        "progress": [],
        "cost_usd": 4.0,
    }
    worker = _worker(tmp_path, store)
    assert store.claim_build_job("j1", worker.worker_id) is True
    item_dir = tmp_path / "jobs" / "j1" / "animal_lion"
    item_dir.mkdir(parents=True)
    tracker = BudgetTracker(budget_usd=10.0)
    tracker.spent_usd = 9.0
    ctx = SimpleNamespace(tracker=tracker)
    worker._close_item("j1", {"id": "animal_lion"}, item_dir, ctx, 0.0, 4.0, 10.0, None)
    job = store.build_jobs["j1"]
    assert job["cost_usd"] == 13.0
    assert [a["pct"] for a in store.alerts] == [50, 80, 100]


def test_close_item_keeps_spent_and_cost_together(tmp_path: Path) -> None:
    """#269.2: closing an item moves spent_usd with cost_usd."""
    from types import SimpleNamespace

    from builder.pipeline import BudgetTracker

    store = FakeStore()
    store.build_jobs["j1"] = {
        "area": "a",
        "budget_usd": 10.0,
        "kind": "build",
        "status": "building",
        "items": ["animal_lion"],
        "progress": [],
        "cost_usd": 4.0,
        "spent_usd": 4.0,
    }
    worker = _worker(tmp_path, store)
    assert store.claim_build_job("j1", worker.worker_id) is True
    item_dir = tmp_path / "jobs" / "j1" / "animal_lion"
    item_dir.mkdir(parents=True)
    tracker = BudgetTracker(budget_usd=10.0)
    tracker.spent_usd = 9.0
    ctx = SimpleNamespace(tracker=tracker)
    worker._close_item("j1", {"id": "animal_lion"}, item_dir, ctx, 0.0, 4.0, 10.0, None)
    job = store.build_jobs["j1"]
    assert job["cost_usd"] == 13.0
    assert job["spent_usd"] == 13.0


def test_poll_resumes_builds_on_reenable(tmp_path: Path, monkeypatch: Any) -> None:
    store = FakeStore()
    store.build_jobs["j1"] = {
        "area": "a",
        "budget_usd": 8.0,
        "kind": "build",
        "status": "building",
        "items": [],
        "progress": [],
        "cost_usd": 0.0,
    }
    worker = _worker(tmp_path, store)
    monkeypatch.setattr(Worker, "pull_world", lambda self: False)
    store.enabled = False
    assert worker.poll() == []
    store.enabled = True
    assert worker.poll() == [("build_job", "j1")]
    assert worker.poll() == []


def _paused_lynx() -> dict[str, Any]:
    """The lynx as #171 found it: building, paused, lease gone."""
    job = _enrich_job(status="building")
    job["error"] = "paused_spend_uncertain"
    job["progress"] = [
        {
            "id": "enrich_lion",
            "status": "paused_spend_uncertain",
            "report": "review/j1/enrich_lion/report.html",
            "cost_usd": 0.26,
        }
    ]
    return job


def test_restart_leaves_a_paused_job_for_the_operator(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    """#171.1: a restart never runs a paused job on its own."""
    store = FakeStore()
    store.build_jobs["j1"] = _paused_lynx()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.worker_id = "wB"

    def no_stages(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("no stages run without a resume")

    monkeypatch.setattr(worker, "_run_stages", no_stages)
    assert worker.note_build_job("j1", store.build_jobs["j1"]) is False
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "building"
    assert job["error"] == "paused_spend_uncertain"
    assert job["progress"][0]["status"] == "paused_spend_uncertain"
    assert job.get("worker_id") != "wB"
    assert "waiting for an operator" in capsys.readouterr().out


def test_reconcile_skips_a_paused_job(tmp_path: Path, monkeypatch: Any) -> None:
    """#171.1: re-enable re-queues everything but a paused job."""
    store = FakeStore()
    store.build_jobs["j1"] = _paused_lynx()
    store.build_jobs["j2"] = {
        "area": "a",
        "budget_usd": 8.0,
        "kind": "build",
        "status": "building",
        "items": [],
        "progress": [],
        "cost_usd": 0.0,
    }
    worker = _worker(tmp_path, store)
    monkeypatch.setattr(Worker, "pull_world", lambda self: False)
    store.enabled = False
    assert worker.poll() == []
    store.enabled = True
    assert worker.poll() == [("build_job", "j2")]


def test_paused_job_with_resume_queues_and_builds(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#171.1: the operator's resume is obeyed, not skipped."""
    store = FakeStore()
    job = _paused_lynx()
    job["operator_action"] = "resume"
    store.build_jobs["j1"] = job
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.worker_id = "wA"
    assert worker.note_build_job("j1", store.build_jobs["j1"]) is True

    def fake_stages(*args: Any, **kwargs: Any) -> None:
        (args[5] / "animals").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worker, "_run_stages", fake_stages)
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "checking"
    assert job.get("error") is None
    assert "operator_action" not in job


def test_code_hunts_keep_small_fish_not_fish(tmp_path: Path) -> None:
    """#173.4: a reef hunter hunts small fish, never anything with fins."""
    from types import SimpleNamespace

    import fake_world

    from builder.stages.maker import AnimalMakerStage

    world = fake_world.make_world(tmp_path)
    listed = json.loads((world / "animal-list.json").read_text())
    listed["animals"].append(
        {"id": "clownfish", "name": "Clownfish", "habitats": ["sea"]}
    )
    listed["animals"].append(
        {"id": "lionfish", "name": "Lionfish", "habitats": ["sea"]}
    )
    (world / "animal-list.json").write_text(json.dumps(listed))
    (world / "animals" / "clownfish.json").write_text(
        json.dumps({"tags": ["fish", "small_fish"]})
    )
    (world / "animals" / "lionfish.json").write_text(json.dumps({"tags": ["fish"]}))
    region = {"animals": {"playable": ["clownfish", "lionfish"]}, "fish": []}
    ctx = SimpleNamespace(job_dir=tmp_path / "jobs" / "j1")
    small = AnimalMakerStage()._code_hunts(
        world,
        ctx,
        {"main_food": {"kind": "prey", "name": "Clownfish"}, "other_foods": []},
        region,
        "hammerhead",
    )
    assert small == ["small_fish"]
    only = AnimalMakerStage()._code_hunts(
        world,
        ctx,
        {"main_food": {"kind": "prey", "name": "Lionfish"}, "other_foods": []},
        region,
        "grouper",
    )
    assert only == ["fish"]


def test_resume_settles_first_and_marks_items_building(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#171.1: resume settles the held spend, then the job may run."""
    from types import SimpleNamespace

    store = FakeStore()
    provider, allowance, ledger, receipt = _held_pair(tmp_path)
    _paused_job(store, "resume", receipt)
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.worker_id = "wA"
    worker.chat = SimpleNamespace(ledger=ledger, model="muse-spark-1.3")
    assert worker._operator_action("j1", store.build_jobs["j1"]) is True
    job = store.build_jobs["j1"]
    assert job["progress"][0]["status"] == "building"
    assert worker._job_paused(job) is False
    for pool, side in ((provider, receipt.provider), (allowance, receipt.allowance)):
        data = json.loads(pool.path.read_text(encoding="utf-8"))
        assert side.id in data["settled"]


def test_guard_stops_pipeline_at_next_stage(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from builder.pipeline import OffSwitch, Pipeline

    ran: list[str] = []

    class Step:
        def __init__(self, name: str) -> None:
            self.name = name

        def run(self, ctx: Any) -> dict[str, Any]:
            ran.append(self.name)
            return {}

    ctx = SimpleNamespace(
        job_dir=tmp_path, client=None, open_client=None, check_budget=lambda: None
    )

    def guard(stage: str) -> None:
        if stage == "b":
            raise OffSwitch("off")

    pipeline = Pipeline(
        [Step("a"), Step("b")],  # type: ignore[list-item]
        state_path=tmp_path / "state.json",
        guard=guard,
    )
    try:
        pipeline.run(ctx)
    except OffSwitch:
        pass
    assert ran == ["a"]


def _build_job(tile: str, items: list[str], **over: Any) -> dict[str, Any]:
    job = {
        "format": 1,
        "tile": tile,
        "budget_usd": 8.0,
        "kind": "build",
        "status": "queued",
        "items": items,
        "visibility": "public",
        "created_by": "admin",
        "progress": [],
        "cost_usd": 0.0,
    }
    job.update(over)
    return job


def _file_item(item_id: str, kind: str, what: str, ref: str) -> dict[str, Any]:
    return {
        "id": item_id,
        "kind": kind,
        "what": what,
        "from": ref,
        "estimate_usd": 0.15,
    }


def test_build_job_stops_second_item_at_remaining_budget(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#133.3: each item gets what's left; the job never overspends."""
    from builder.pipeline import BudgetExceeded

    store = FakeStore()
    plan = _code_plan("z10_0581_0294")
    plan["code_only"] = False
    plan["items"] = [
        _file_item("animal_lion", "animal", "lion", "fox"),
        _file_item("animal_tiger", "animal", "tiger", "fox"),
    ]
    plan["improve"] = []
    store.plans["z10_0581_0294"] = plan
    store.build_jobs["j1"] = _build_job(
        "z10_0581_0294", ["animal_lion", "animal_tiger"], budget_usd=1.0
    )
    worker = _flow_worker(tmp_path, store, boreal_3x3())

    def pricey(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
        cast: Any = None,
        enrich_tiles: Any = None,
    ) -> None:
        # One $0.60 call, reserved the way MetaClient reserves it:
        # refused when the worst case no longer fits what's left.
        if ctx.tracker.spent_usd + 0.6 > ctx.tracker.budget_usd:
            raise BudgetExceeded("a $0.60 call no longer fits")
        ctx.tracker.spent_usd += 0.6

    monkeypatch.setattr(worker, "_run_stages", pricey)
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["cost_usd"] == 0.6
    assert job["cost_usd"] <= job["budget_usd"]
    assert job["error"] == "budget"
    by_id = {e["id"]: e["status"] for e in job["progress"]}
    assert by_id == {"animal_lion": "checking", "animal_tiger": "failed"}


def test_ledger_failure_reports_unavailable_and_stops_items(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#159: a down ledger stops the job as unavailable, not as content."""
    from builder.ledger import LedgerError

    store = FakeStore()
    plan = _code_plan("z10_0581_0294")
    plan["code_only"] = False
    plan["items"] = [
        _file_item("animal_lion", "animal", "lion", "fox"),
        _file_item("animal_tiger", "animal", "tiger", "fox"),
    ]
    plan["improve"] = []
    store.plans["z10_0581_0294"] = plan
    store.build_jobs["j1"] = _build_job(
        "z10_0581_0294", ["animal_lion", "animal_tiger"], budget_usd=1.0
    )
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    runs: list[str] = []

    def down(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
        cast: Any = None,
        enrich_tiles: Any = None,
    ) -> None:
        runs.append(item["id"])
        raise LedgerError("cannot reserve spend: locked")

    monkeypatch.setattr(worker, "_run_stages", down)
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["error"] == "ledger"
    assert job["status"] == "failed"
    by_id = {e["id"]: e["status"] for e in job["progress"]}
    assert by_id == {"animal_lion": "failed"}
    assert runs == ["animal_lion"]


def _fake_animal_stages(worker: Worker, monkeypatch: Any, tmp_path: Path) -> None:
    def fake_stages(
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
        cast: Any = None,
        enrich_tiles: Any = None,
    ) -> None:
        folder = item_dir / "animals"
        folder.mkdir(parents=True)
        (folder / f"{item['what']}.json").write_bytes(
            b'{"id": "' + item["what"].encode() + b'"}'
        )

    monkeypatch.setattr(worker, "_run_stages", fake_stages)


def test_review_job_publishes_shipped_items(tmp_path: Path, monkeypatch: Any) -> None:
    """#133.6: a reviewed job ships its items and publishes the tile."""
    store = FakeStore()
    plan = _code_plan("z10_0581_0294")
    plan["code_only"] = False
    plan["items"] = [_file_item("animal_lion", "animal", "lion", "fox")]
    plan["improve"] = []
    store.plans["z10_0581_0294"] = plan
    store.build_jobs["j1"] = _build_job("z10_0581_0294", ["animal_lion"])
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    _fake_animal_stages(worker, monkeypatch, tmp_path)
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "checking"
    (worker.world / "animals" / "lion.json").write_text('{"id": "lion"}')
    store.build_jobs["j1"]["review"] = {"animal_lion": {"result": "shipped"}}
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert [e["id"] for e in job["progress"]] == ["animal_lion"]
    assert "tiles/v2/z10/072_036.json" in store.blobs
    import json

    data = json.loads(store.read_blob("tiles/v2/z10/072_036.json")[0].decode("utf-8"))
    assert job["version"] == data["tiles"]["z10_0581_0294"]["version"] == 1


def test_review_job_drops_not_accepted_and_ships_the_rest(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#133.6: a turned-down item drops while the others ship."""
    store = FakeStore()
    plan = _code_plan("z10_0581_0294")
    plan["code_only"] = False
    plan["items"] = [
        _file_item("animal_lion", "animal", "lion", "fox"),
        _file_item("animal_tiger", "animal", "tiger", "fox"),
    ]
    plan["improve"] = []
    store.plans["z10_0581_0294"] = plan
    store.build_jobs["j1"] = _build_job(
        "z10_0581_0294", ["animal_lion", "animal_tiger"]
    )
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    _fake_animal_stages(worker, monkeypatch, tmp_path)
    worker.handle_build_job("j1")
    (worker.world / "animals" / "lion.json").write_text('{"id": "lion"}')
    store.build_jobs["j1"]["review"] = {
        "animal_lion": {"result": "shipped"},
        "animal_tiger": {"result": "not_accepted"},
    }
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert [e["id"] for e in job["progress"]] == ["animal_lion", "animal_tiger"]


def test_private_preview_is_rewritten_after_a_review(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#133.6: a private job's preview moves on as reviews land."""
    store = FakeStore()
    plan = _code_plan("z10_0581_0294")
    plan["code_only"] = False
    plan["items"] = [_file_item("animal_lion", "animal", "lion", "fox")]
    plan["improve"] = []
    store.plans["z10_0581_0294"] = plan
    store.build_jobs["j1"] = _build_job(
        "z10_0581_0294",
        ["animal_lion"],
        visibility="private",
        private_to="judge1",
    )
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    _fake_animal_stages(worker, monkeypatch, tmp_path)
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["preview"] == {"pack": "previews/j1/1/"}
    store.build_jobs["j1"]["review"] = {"animal_lion": {"result": "shipped"}}
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "checking"  # the world lacks lion.json yet
    assert job["preview"] == {"pack": "previews/j1/2/"}
    assert "previews/j1/2/manifest.json" in store.blobs


def test_claim_snapshot_survives_a_replan(tmp_path: Path, monkeypatch: Any) -> None:
    """#133.5: a re-plan to code_only strands no claimed job."""
    import json

    store = FakeStore()
    plan = _code_plan("z10_0581_0294")
    plan["code_only"] = False
    plan["items"] = [_file_item("animal_lion", "animal", "lion", "fox")]
    plan["improve"] = []
    store.plans["z10_0581_0294"] = plan
    store.build_jobs["j1"] = _build_job("z10_0581_0294", ["animal_lion"])
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    _fake_animal_stages(worker, monkeypatch, tmp_path)
    worker.handle_build_job("j1")
    snapshot = json.loads((tmp_path / "jobs" / "j1" / "plan.json").read_text())
    assert [i["id"] for i in snapshot["items"]] == ["animal_lion"]
    store.plans["z10_0581_0294"] = _code_plan("z10_0581_0294")  # the world moved on
    (worker.world / "animals" / "lion.json").write_text('{"id": "lion"}')
    store.build_jobs["j1"]["review"] = {"animal_lion": {"result": "shipped"}}
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "published"


def test_plant_item_ships_and_publishes(tmp_path: Path, monkeypatch: Any) -> None:
    """#133.4: a shipped plant's file in the world publishes the tile."""
    import json

    from builder import build as build_mod

    store = FakeStore()
    plan = _code_plan("z10_0581_0294")
    plan["code_only"] = False
    plan["items"] = [
        {
            "id": "plant_cloudberry",
            "kind": "plant",
            "what": "cloudberry",
            "from": "bilberry",
            "estimate_usd": 0.02,
        }
    ]
    plan["improve"] = []
    store.plans["z10_0581_0294"] = plan
    store.build_jobs["j1"] = _build_job("z10_0581_0294", ["plant_cloudberry"])
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    home_path = worker.world / "homes" / "boreal_forest.json"
    home = json.loads(home_path.read_text())
    home["food"] = [{"kind": "grass"}, {"kind": "berries", "names": ["cloudberry"]}]
    home_path.write_text(json.dumps(home))

    class Pipe:
        def run(self, ctx: Any) -> dict[str, Any]:
            return {}

    monkeypatch.setattr(build_mod, "item_pipeline", lambda **k: Pipe())
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "checking"
    (worker.world / "models" / "plants" / "cloudberry.json").write_text(
        '{"id": "cloudberry"}'
    )
    store.build_jobs["j1"]["review"] = {"plant_cloudberry": {"result": "shipped"}}
    worker.handle_build_job("j1")
    assert store.build_jobs["j1"]["status"] == "published"


def test_code_job_fails_on_excluded_tile(tmp_path: Path, monkeypatch: Any) -> None:
    """#133.7: a code job on an excluded cell fails, never publishes."""
    import json

    store = FakeStore()
    store.plans["z10_0581_0294"] = _code_plan("z10_0581_0294")
    store.build_jobs["j1"] = _code_job()
    (tmp_path / "exclusions.json").write_text(json.dumps({"tiles": ["z10_0581_0294"]}))
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.builder_root = tmp_path
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "failed"
    assert job["error"] == "excluded"
    assert store.alerts[-1]["kind"] == "job_failed"


def test_start_line_reports_deploy_facts(tmp_path: Path) -> None:
    """#133.2: the start log names the host, ledger, world and switch."""
    import json

    from builder.worker import start_line

    store = FakeStore()
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"jobs": {}, "days": {}, "total": 14.5}))
    (tmp_path / "ledger-baseten.json").write_text(
        json.dumps({"jobs": {}, "days": {}, "total": 3.25})
    )
    config = Config(open_model_host="baseten", ledger_path=str(ledger))
    line = start_line(config, store, tmp_path / "world")
    assert "\n" not in line
    assert "open_host=baseten" in line
    assert f"ledger={ledger}" in line
    assert "meta_total=$14.50" in line
    assert "baseten_total=$3.25" in line
    assert "world=unknown" in line
    assert "enabled=true" in line


def test_listen_prints_the_start_line(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    """#133.2: `builder worker --listen` opens with the start line."""
    from builder import worker as worker_mod
    from builder import worker_store
    from builder.worker import listen

    started: list[str] = []

    class QuietListener:
        def __init__(self, worker: Any, store: Any, poll_seconds: float = 0) -> None:
            started.append("listening")

        def start(self) -> None:
            pass

    monkeypatch.setattr(worker_store, "FirebaseStore", lambda *a, **k: FakeStore())
    monkeypatch.setattr(worker_mod, "Listener", QuietListener)
    code = listen(
        Config(ledger_path=str(tmp_path / "ledger.json")),
        tmp_path / "jobs",
        tmp_path / "world",
        "demo",
    )
    assert code == 0
    assert started == ["listening"]
    assert "worker start:" in capsys.readouterr().out


def test_no_player_field_anywhere(tmp_path: Path, monkeypatch: Any) -> None:
    """#118.10: no player field anywhere the worker writes."""
    import json

    store = FakeStore()
    store.requests["z10_0581_0294"] = {
        "tile": "z10_0581_0294",
        "uid": "kid-1",
        "player": {"device": "x", "ip": "1.2.3.4"},
        "email": "kid@example.org",
    }
    store.plans["z10_0581_0294"] = _code_plan("z10_0581_0294")
    store.build_jobs["j1"] = _code_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    monkeypatch.setattr("builder.plan.fetch_smoothed", lambda tile, c: {})
    worker.handle_request("z10_0581_0294")
    worker.handle_build_job("j1")

    def keys_below(data: Any) -> set[str]:
        found = set()
        if isinstance(data, dict):
            for key, value in data.items():
                found.add(str(key))
                found |= keys_below(value)
        elif isinstance(data, list):
            for value in data:
                found |= keys_below(value)
        return found

    written = [
        store.plans["z10_0581_0294"],
        store.build_jobs["j1"],
        *[json.loads(store.read_blob(path)[0].decode("utf-8")) for path in store.blobs],
    ]
    leaked = keys_below(written) & {
        "uid",
        "player",
        "device",
        "ip",
        "email",
        "child",
        "kid",
        "parent",
        "family",
        "address",
        "phone",
    }
    assert leaked == set()


def _held_pair(tmp_path: Path):  # type: ignore[no-untyped-def]
    """A combined reservation left outstanding by a timeout (#157)."""
    from builder.ledger import Caps, CombinedLedger, FileLedger

    provider = FileLedger(tmp_path / "meta-pool.json")
    allowance = FileLedger(tmp_path / "allowance.json")
    ledger = CombinedLedger(provider, allowance, Caps(8.0, 20.0, 120.0))
    receipt = ledger.reserve("j1", 0.05, Caps(8.0, 20.0, 120.0))
    return provider, allowance, ledger, receipt


def test_timeout_pauses_item_and_resume_recovers(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#157: the reservation stays held; an operator resume retries it."""
    from types import SimpleNamespace

    from builder.meta import SpendUncertain

    store = FakeStore()
    store.build_jobs["j1"] = _owned_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.worker_id = "wA"
    provider, allowance, ledger, receipt = _held_pair(tmp_path)
    worker.chat = SimpleNamespace(ledger=ledger, model="muse-spark-1.3")

    uncertain = SpendUncertain("chat: sent, spend unknown (timed out)")
    uncertain.receipt = receipt
    uncertain.host = "meta"
    attempts: list[str] = []

    def flaky(*args: Any, **kwargs: Any) -> None:
        attempts.append("stages")
        if len(attempts) == 1:
            raise uncertain

    monkeypatch.setattr(worker, "_run_stages", flaky)
    worker.handle_build_job("j1")
    entry = store.build_jobs["j1"]["progress"][0]
    assert entry["status"] == "paused_spend_uncertain"
    assert entry["uncertain"]["host"] == "meta"
    assert entry["uncertain"]["provider"]["id"] == receipt.provider.id
    assert entry["uncertain"]["allowance"]["id"] == receipt.allowance.id
    assert store.build_jobs["j1"]["status"] == "building"
    assert store.build_jobs["j1"]["error"] == "paused_spend_uncertain"
    assert provider.job_spent("j1") == pytest.approx(0.05)
    assert allowance.job_spent("j1") == pytest.approx(0.05)

    # A later drive without an operator resume leaves it paused (#171.1).
    worker.handle_build_job("j1")
    entry = store.build_jobs["j1"]["progress"][0]
    assert entry["status"] == "paused_spend_uncertain"
    assert store.build_jobs["j1"]["status"] == "building"
    assert attempts == ["stages"]
    store.build_jobs["j1"]["operator_action"] = "resume"
    worker.handle_build_job("j1")
    entry = store.build_jobs["j1"]["progress"][0]
    assert entry["status"] == "checking"
    assert store.build_jobs["j1"]["status"] == "checking"
    assert store.build_jobs["j1"].get("error") is None
    assert attempts == ["stages", "stages"]
    # The resume settled the superseded attempt at its estimate.
    for pool, side in ((provider, receipt.provider), (allowance, receipt.allowance)):
        data = json.loads(pool.path.read_text(encoding="utf-8"))
        assert side.id in data["settled"]
        assert pool.job_spent("j1") == pytest.approx(0.05)


def test_reenable_recovers_queued_job_and_request_once(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#158: consumed-while-disabled work proceeds exactly once on re-enable."""
    from builder.worker import Listener

    store = FakeStore()
    store.build_jobs["j1"] = _enrich_job(status="queued")
    store.requests["z10_0581_0294"] = {"tile": "z10_0581_0294"}
    worker = _planned_worker(tmp_path, store, monkeypatch)
    worker.worker_id = "wA"
    listener = Listener(worker=worker, store=store)
    runs: list[str] = []

    def fake_stages(*args: Any, **kwargs: Any) -> None:
        runs.append("stages")

    monkeypatch.setattr(worker, "_run_stages", fake_stages)
    monkeypatch.setattr(Worker, "pull_world", lambda self: False)
    planned: list[str] = []
    orig_set_plan = store.set_plan

    def counting_set_plan(tile: str, plan: dict[str, Any]) -> None:
        planned.append(tile)
        orig_set_plan(tile, plan)

    monkeypatch.setattr(store, "set_plan", counting_set_plan)

    store.enabled = False
    listener._dispatch("build_job", "j1")
    listener._dispatch("request", "z10_0581_0294")
    assert worker.poll() == []
    assert store.build_jobs["j1"]["status"] == "queued"
    assert "z10_0581_0294" not in store.plans
    assert runs == []
    assert planned == []

    store.enabled = True
    found = worker.poll()
    assert ("build_job", "j1") in found
    assert ("request", "z10_0581_0294") in found
    assert worker.poll() == []
    for kind, doc_id in found:
        listener._dispatch(kind, doc_id)
    assert store.build_jobs["j1"]["status"] == "checking"
    assert "z10_0581_0294" in store.plans
    assert runs == ["stages"]
    assert planned == ["z10_0581_0294"]


def test_reenable_protects_owned_terminal_and_planned(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#158: live-owned, terminal and planned work survives the reconcile."""
    from builder.worker import Listener

    store = FakeStore()
    store.build_jobs["live"] = _owned_job(owner="wB")
    store.build_jobs["pub"] = _enrich_job(status="published")
    store.build_jobs["fail"] = _enrich_job(status="failed")
    store.requests["z10_0581_0294"] = {"tile": "z10_0581_0294"}
    store.plans["z10_0581_0294"] = {"tile": "z10_0581_0294"}
    worker = _planned_worker(tmp_path, store, monkeypatch)
    worker.worker_id = "wA"
    listener = Listener(worker=worker, store=store)
    runs: list[str] = []

    def fake_stages(*args: Any, **kwargs: Any) -> None:
        runs.append("stages")

    monkeypatch.setattr(worker, "_run_stages", fake_stages)
    monkeypatch.setattr(Worker, "pull_world", lambda self: False)

    store.enabled = False
    assert worker.poll() == []
    store.enabled = True
    found = worker.poll()
    ids = [doc_id for kind, doc_id in found if kind == "build_job"]
    assert "live" in ids
    assert "pub" not in ids
    assert "fail" not in ids
    assert ("request", "z10_0581_0294") not in found
    for kind, doc_id in found:
        listener._dispatch(kind, doc_id)
    assert runs == []
    assert store.build_jobs["live"]["worker_id"] == "wB"
    assert store.build_jobs["live"]["status"] == "building"


def _paused_job(store: FakeStore, action: str, receipt) -> None:  # type: ignore[no-untyped-def]
    """A paused enrich job with a held reservation, awaiting the operator."""
    from builder.ledger import receipt_refs

    job = _enrich_job(status="building")
    job["error"] = "paused_spend_uncertain"
    job["operator_action"] = action
    job["progress"] = [
        {
            "id": "enrich_lion",
            "status": "paused_spend_uncertain",
            "uncertain": {"host": "meta", **receipt_refs(receipt)},
        }
    ]
    store.build_jobs["j1"] = job


def test_operator_resume_goes_on(tmp_path: Path, monkeypatch: Any) -> None:
    """#166.3: resume settles the held spend, then the item runs."""
    from types import SimpleNamespace

    store = FakeStore()
    provider, allowance, ledger, receipt = _held_pair(tmp_path)
    _paused_job(store, "resume", receipt)
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.worker_id = "wA"
    worker.chat = SimpleNamespace(ledger=ledger, model="muse-spark-1.3")

    def fake_stages(*args: Any, **kwargs: Any) -> None:
        (args[5] / "animals").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worker, "_run_stages", fake_stages)
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "checking"
    assert "operator_action" not in job
    assert job.get("error") is None
    assert job["progress"][-1]["status"] == "checking"
    for pool, side in ((provider, receipt.provider), (allowance, receipt.allowance)):
        data = json.loads(pool.path.read_text(encoding="utf-8"))
        assert side.id in data["settled"]
        assert pool.job_spent("j1") == pytest.approx(0.05)


def test_operator_give_up_fails_and_records(tmp_path: Path, monkeypatch: Any) -> None:
    """#166.3: give-up settles the held spend, records it, fails the job."""
    from types import SimpleNamespace

    store = FakeStore()
    provider, allowance, ledger, receipt = _held_pair(tmp_path)
    _paused_job(store, "give_up", receipt)
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.worker_id = "wA"
    worker.chat = SimpleNamespace(ledger=ledger, model="muse-spark-1.3")

    def no_stages(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("no stages run on give-up")

    monkeypatch.setattr(worker, "_run_stages", no_stages)
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "failed"
    assert job["error"] == "gave_up"
    assert "operator_action" not in job
    assert job["progress"][0]["status"] == "failed"
    assert job["uncertain_usd"] == pytest.approx(0.05)
    for pool, side in ((provider, receipt.provider), (allowance, receipt.allowance)):
        data = json.loads(pool.path.read_text(encoding="utf-8"))
        assert side.id in data["settled"]


def test_stale_operator_action_clears_and_builds(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#166.3: a command with nothing paused clears; the job builds on."""
    store = FakeStore()
    job = _enrich_job(status="building")
    job["operator_action"] = "resume"
    store.build_jobs["j1"] = job
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.worker_id = "wA"

    def fake_stages(*args: Any, **kwargs: Any) -> None:
        (args[5] / "animals").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worker, "_run_stages", fake_stages)
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert "operator_action" not in job
    assert job["status"] == "checking"


def test_resume_settles_superseded_timeout_at_estimate(tmp_path: Path) -> None:
    """#157: a second attempt settles the first one's record at estimate."""
    from types import SimpleNamespace

    from builder.ledger import receipt_refs

    store = FakeStore()
    store.build_jobs["j1"] = _owned_job()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    provider, allowance, ledger, receipt = _held_pair(tmp_path)
    store.build_jobs["j1"]["progress"] = [
        {
            "id": "enrich_lion",
            "status": "paused_spend_uncertain",
            "uncertain": {"host": "meta", **receipt_refs(receipt)},
        }
    ]
    ctx = SimpleNamespace(client=SimpleNamespace(ledger=ledger), open_client=None)
    worker._settle_superseded("j1", {"id": "enrich_lion"}, ctx)
    worker._settle_superseded("j1", {"id": "enrich_lion"}, ctx)
    for pool, side in ((provider, receipt.provider), (allowance, receipt.allowance)):
        data = json.loads(pool.path.read_text(encoding="utf-8"))
        assert side.id in data["settled"]
        assert pool.job_spent("j1") == pytest.approx(0.05)


def test_enrich_review_needs_no_person_verdict() -> None:
    """#174: a checking enrich job is ready for its review step."""
    from builder.worker import Worker

    assert Worker._needs_review({"status": "checking", "kind": "enrich"}) is True
    build = {"status": "checking", "kind": "build", "review": {"a": 1}}
    assert Worker._needs_review(build) is True
    assert Worker._needs_review({"status": "checking", "kind": "build"}) is False
    assert Worker._needs_review({"status": "building", "kind": "enrich"}) is False


def test_ship_state_marks_the_item_shipped(tmp_path: Path) -> None:
    """#174, #176.2: the ship result carries the pack prefix."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    item_dir = tmp_path / "jobs" / "job-1" / "animal_hammerhead"
    item_dir.mkdir(parents=True)
    assert worker._ship_completed("job-1", "animal_hammerhead") is False
    assert worker._ship_pack("job-1", "animal_hammerhead") is None
    (item_dir / "state.json").write_text(
        json.dumps(
            {
                "completed": ["research", "ship"],
                "results": {"ship": {"pack": "packs/animal_hammerhead/1/"}},
            }
        )
    )
    assert worker._ship_completed("job-1", "animal_hammerhead") is True
    assert worker._ship_pack("job-1", "animal_hammerhead") == (
        "packs/animal_hammerhead/1/"
    )


def test_enrich_review_waits_for_the_ship(tmp_path: Path) -> None:
    """#174: no ship, no verdict: the job waits, untouched."""
    store = FakeStore()
    job = _enrich_job(status="checking")
    store.build_jobs["j1"] = job
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    worker.review_enrich_job("j1", job)
    assert store.build_jobs["j1"]["status"] == "checking"
    assert "pack" not in store.build_jobs["j1"]


def test_history_context_adds_the_tile_survey_cost(tmp_path: Path) -> None:
    """#195.2: the row carries the job cost plus the tile's survey."""
    store = FakeStore()
    store.build_jobs["j1"] = _enrich_job(status="checking", cost_usd=0.10)
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    (tmp_path / "ledger.json").write_text(
        json.dumps(
            {
                "jobs": {"survey:z10_0581_0294": {"spent": 0.02, "updated": "x"}},
                "days": {},
                "total": 0.02,
            }
        )
    )
    context = worker._history_context("j1", "code", "z10_0581_0294")
    assert context["cost_usd"] == pytest.approx(0.12)
    plain = worker._history_context("j1", "code")
    assert plain["cost_usd"] == 0.10


def test_enrich_review_ships_a_plant_pack_like_an_animal(
    tmp_path: Path,
) -> None:
    """#188.3: a shipped plant remakes; no reviewer waits in the way."""
    store = FakeStore()
    job = _enrich_job(
        status="checking",
        items=["enrich_table_coral"],
        enrich={"kind": "plant", "what": "table_coral", "from": "closed_brain_coral"},
    )
    store.build_jobs["j1"] = job
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert worker._claim("j1")
    item_dir = tmp_path / "jobs" / "j1" / "enrich_table_coral"
    item_dir.mkdir(parents=True)
    (item_dir / "state.json").write_text(
        json.dumps(
            {
                "completed": ["research", "ship"],
                "results": {"ship": {"pack": "packs/plant_table_coral/1/"}},
            }
        )
    )
    worker.review_enrich_job("j1", job)
    # No cached survey here, so the tiles skip; the point is the
    # job sails past the ship, instead of waiting for a hand.
    # Since #251.3 an empty overlay fails loud, never published.
    done = store.build_jobs["j1"]
    assert done["status"] == "failed"
    assert done["error"] == "nothing_overlaid"
    assert done["overlay"] == []
    assert sorted(done["skipped"]) == ["z10_0580_0295", "z10_0581_0294"]
    assert "cached" in done["skipped"]["z10_0581_0294"]


def _spread_job(**over: Any) -> dict[str, Any]:
    job: dict[str, Any] = {
        "format": 1,
        "kind": "spread",
        "tiles": ["z10_0581_0294"],
        "spread": {"kind": "animal", "what": "lion"},
        "status": "queued",
        "progress": [],
        "cost_usd": 0.0,
    }
    job.update(over)
    return job


def _pack_files(store: Any, prefix: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """An overlay pack's tiles doc and manifest (#220)."""
    raw, _ = store.read_blob(prefix + "additions/tiles.json")
    assert raw is not None
    tiles_doc = json.loads(raw.decode("utf-8"))
    raw, _ = store.read_blob(prefix + "manifest.json")
    assert raw is not None
    return tiles_doc, json.loads(raw.decode("utf-8"))


def _list_lynx(world: Path) -> None:
    """Lynx on the fake list, unfiled like the lion (#220)."""
    listed = json.loads((world / "animal-list.json").read_text())
    listed["animals"].append(
        {
            "id": "lynx",
            "name": "Lynx",
            "latin": "Lynx lynx",
            "gbif_key": 5219405,
            "habitats": ["boreal_forest"],
            "start_from": "fox",
            "can_make": True,
        }
    )
    (world / "animal-list.json").write_text(json.dumps(listed))


def _file_lynx(world: Path) -> None:
    """A filed lynx that hunts voles off grass (#220)."""
    (world / "animals" / "lynx.json").write_text(
        json.dumps(
            {
                "playable": True,
                "tags": ["predator"],
                "diet": ["grass"],
                "hunts": ["vole"],
            }
        )
    )
    listed = json.loads((world / "animal-list.json").read_text())
    next(e for e in listed["animals"] if e["id"] == "lynx")["in_library"] = "lynx"
    (world / "animal-list.json").write_text(json.dumps(listed))


def _spread_world(
    tmp_path: Path,
    store: FakeStore,
    surveys: dict[str, tuple[str, ...]],
    prepare: Any = None,
) -> tuple[Any, Path]:
    """Surveyed tiles with their cache, filed nothing yet (#199.4)."""
    from builder.grid import group_path
    from builder.species import Source as SpeciesSource
    from builder.tiles import make_tile

    block = boreal_3x3()
    edge = {f"{c},{r}": list(block["581,294"]) for r in (294, 295, 296) for c in (579,)}
    edge.update({f"{c},296": list(block["581,294"]) for c in (580, 581)})
    edge.update(
        {f"{c},{r}": list(block["581,294"]) for r in (293, 294, 295) for c in (583,)}
    )
    worker = _flow_worker(tmp_path, store, {**block, **edge, **boreal_3x3(590, 298)})
    assert worker.world is not None
    if prepare is not None:
        prepare(worker.world)
    _named_list(worker.world)
    cache = tmp_path / "cache"
    by_group: dict[str, dict[str, Any]] = {}
    for tile, species in surveys.items():
        made, blocked = make_tile(
            tile,
            worker._habitat_grid(),
            worker.world,
            builder_root=tmp_path,
            species_source=SpeciesSource(
                client=_AnswerClient(_forest_survey(*species)), cache_dir=cache
            ),
        )
        assert blocked is None and made is not None
        by_group.setdefault(group_path(tile), {})[tile] = made
    for blob_path, tiles in by_group.items():
        store.blobs[blob_path] = json.dumps(
            {
                "format": 1,
                "grid": "z10",
                "group": [0, 0],
                "version": 1,
                "tiles": tiles,
                "blocked": {},
            }
        ).encode("utf-8")
    store.blobs["tiles/v2/index.json"] = json.dumps(
        {
            "format": 1,
            "made_at": "",
            "world": "",
            "groups": {},
            "counts": {"ready": len(surveys), "blocked": {}},
        }
    ).encode("utf-8")
    return worker, cache


def test_spread_runs_keyless_and_skips_the_tile_without_it(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#220: the fit tile overlays with no call; the miss skips (#199.1)."""
    from builder.grid import group_path
    from builder.species import Source as SpeciesSource

    A, C = "z10_0581_0294", "z10_0590_0298"
    store = FakeStore()
    worker, cache = _spread_world(
        tmp_path,
        store,
        {
            A: ("fox", "mountain_hare", "vole", "lion"),
            C: ("fox", "mountain_hare", "vole"),
        },
    )
    assert worker.world is not None
    before_a = store.blobs[group_path(A)]
    before_c = store.blobs[group_path(C)]
    before_index = store.blobs["tiles/v2/index.json"]
    _file_lion(worker.world, ["grass"])
    store.build_jobs["j1"] = _spread_job(tiles=[A, C])
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["overlay"] == [A]
    assert job["gaps"] == {}
    assert job["skipped"] == {C: "lion is not in the tile's survey"}
    assert job["cost_usd"] == 0.0
    assert "versions" not in job
    assert job["pack"] == "packs/j1/1/"
    tiles_doc, manifest = _pack_files(store, job["pack"])
    assert tiles_doc == {"format": 1, "playable": ["lion"], "tiles": [A]}
    assert manifest["min_game"] == 2
    assert store.blobs[group_path(A)] == before_a
    assert store.blobs[group_path(C)] == before_c
    assert store.blobs["tiles/v2/index.json"] == before_index
    assert store.tile_history == []


def test_spread_overlays_a_fit_tile_despite_drift(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#227.1: shore/plant drift since publication never blocks the overlay."""
    from builder.grid import group_path
    from builder.species import Source as SpeciesSource

    A = "z10_0581_0294"
    store = FakeStore()
    worker, cache = _spread_world(
        tmp_path, store, {A: ("fox", "mountain_hare", "vole", "lion")}
    )
    assert worker.world is not None
    _file_lion(worker.world, ["grass"])
    blob_path = group_path(A)
    published = json.loads(store.blobs[blob_path].decode("utf-8"))
    published["tiles"][A]["shore"] = {"span": 999, "reach": 999, "d": "AAAA"}
    published["tiles"][A]["plants"] = {"cover": [{"id": "drifted_weed", "share": 0.9}]}
    store.blobs[blob_path] = json.dumps(published).encode("utf-8")
    store.build_jobs["j1"] = _spread_job(tiles=[A])
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["overlay"] == [A]
    assert job["skipped"] == {}
    assert job["pack"] == "packs/j1/1/"


def test_spread_skips_the_tile_whose_remake_raises(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#227.2: one raising remake skips its tile; the rest overlay."""
    from builder.species import Source as SpeciesSource

    A, B, F = "z10_0581_0294", "z10_0580_0295", "z10_0590_0298"
    FOUR = ("fox", "mountain_hare", "vole", "lion")
    store = FakeStore()
    worker, cache = _spread_world(tmp_path, store, {A: FOUR, B: FOUR, F: FOUR})
    assert worker.world is not None
    _file_lion(worker.world, ["grass"])
    store.build_jobs["j1"] = _spread_job(tiles=[A, B, F])
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
    )
    import builder.tiles as tiles_mod

    real_make = tiles_mod.make_tile

    def flaky(tile: str, *args: Any, **kwargs: Any) -> Any:
        if tile == B:
            raise ValueError("boom")
        return real_make(tile, *args, **kwargs)

    monkeypatch.setattr(tiles_mod, "make_tile", flaky)
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["overlay"] == [A, F]
    assert list(job["skipped"]) == [B]
    assert "boom" in job["skipped"][B]
    assert job["pack"] == "packs/j1/1/"


def test_spread_stages_a_pack_only_species(tmp_path: Path, monkeypatch: Any) -> None:
    """#220: a live pack's lion spreads to an overlay, then leaves no file."""
    from builder.species import Source as SpeciesSource

    A = "z10_0581_0294"
    store = FakeStore()
    worker, cache = _spread_world(
        tmp_path, store, {A: ("fox", "mountain_hare", "vole", "lion")}
    )
    assert worker.world is not None
    store.packs["p1"] = {"status": "live", "animals": ["lion"], "path": "packs/p1/"}
    store.blobs["packs/p1/manifest.json"] = json.dumps(
        {"files": [{"path": "animals/lion.json"}]}
    ).encode("utf-8")
    store.blobs["packs/p1/animals/lion.json"] = json.dumps(
        {"playable": True, "tags": ["big_cat"], "diet": ["grass"]}
    ).encode("utf-8")
    store.build_jobs["j1"] = _spread_job(tiles=[A])
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["overlay"] == [A]
    assert "versions" not in job
    tiles_doc, _manifest = _pack_files(store, job["pack"])
    assert tiles_doc["playable"] == ["lion"]
    assert not (worker.world / "animals" / "lion.json").exists()


def _hare_world(
    tmp_path: Path, store: FakeStore, surveys: dict[str, tuple[str, ...]]
) -> tuple[Any, Path]:
    """A spread world where the hare eats clover no tile has yet (#231)."""

    def unfile_hare(world: Path) -> None:
        (world / "animals" / "mountain_hare.json").unlink()

    worker, cache = _spread_world(tmp_path, store, surveys, prepare=unfile_hare)
    assert worker.world is not None
    (worker.world / "food").mkdir(exist_ok=True)
    (worker.world / "food" / "rules.json").write_text(json.dumps({"kinds": {}}))
    boreal = worker.world / "homes" / "boreal_forest.json"
    home = json.loads(boreal.read_text())
    home["ground_cover"] = {"grass": 0.15, "plants": [{"food": "berries"}]}
    boreal.write_text(json.dumps(home))
    (worker.world / "models" / "plants" / "red_clover.json").write_text(
        json.dumps({"id": "red_clover", "grows": {"food": "clover"}})
    )
    (worker.world / "animals" / "mountain_hare.json").write_text(
        json.dumps({"playable": True, "tags": ["small_mammal"], "diet": ["clover"]})
    )
    return worker, cache


def _clover_drift(monkeypatch: Any) -> None:
    """Today's maker grows red clover; the published tiles predate it."""
    import builder.tiles as tiles_mod

    real_make = tiles_mod.make_tile
    real_kinds = tiles_mod.tile_food_kinds

    def drifted(tile: str, *args: Any, **kwargs: Any) -> Any:
        tiles_mod.tile_food_kinds = lambda *a, **k: real_kinds(*a, **k) | {"clover"}  # noqa: E731
        try:
            made, blocked = real_make(tile, *args, **kwargs)
        finally:
            tiles_mod.tile_food_kinds = real_kinds
        assert made is not None
        made.setdefault("plants", {}).setdefault("cover", []).append(
            {"id": "red_clover", "density": 0.2}
        )
        return made, blocked

    monkeypatch.setattr(tiles_mod, "make_tile", drifted)


def test_spread_hare_stays_with_clover_gaps_and_no_plants(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#241.2: the remake's clover rides no doc; gaps list clover."""
    from builder.species import Source as SpeciesSource

    A = "z10_0581_0294"
    store = FakeStore()
    worker, cache = _hare_world(tmp_path, store, {A: ("fox", "mountain_hare", "vole")})
    assert worker.world is not None
    _link_food_rule(worker.world)
    store.build_jobs["j1"] = _spread_job(
        tiles=[A], spread={"kind": "animal", "what": "mountain_hare"}
    )
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
    )
    _clover_drift(monkeypatch)
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["overlay"] == [A]
    assert job["gaps"] == {A: {"diet": ["clover"], "hunts": []}}
    tiles_doc, _manifest = _pack_files(store, job["pack"])
    assert tiles_doc["playable"] == ["mountain_hare"]
    assert "plants" not in tiles_doc


def test_spread_hare_replay_covers_both_tiles_with_gaps(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#241.2: the #231 hare replay: both tiles overlaid, gaps list clover."""
    from builder.species import Source as SpeciesSource

    A = "z10_0581_0294"
    B = "z10_0582_0294"
    store = FakeStore()
    worker, cache = _hare_world(
        tmp_path,
        store,
        {
            A: ("fox", "mountain_hare", "vole"),
            B: ("fox", "mountain_hare", "vole"),
        },
    )
    assert worker.world is not None
    _link_food_rule(worker.world)
    store.build_jobs["j1"] = _spread_job(
        tiles=[A, B], spread={"kind": "animal", "what": "mountain_hare"}
    )
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["overlay"] == [A, B]
    assert job["gaps"] == {
        A: {"diet": ["clover"], "hunts": []},
        B: {"diet": ["clover"], "hunts": []},
    }
    tiles_doc, _manifest = _pack_files(store, job["pack"])
    assert tiles_doc["tiles"] == [A, B]
    assert "plants" not in tiles_doc


def test_spread_hare_stays_when_no_clover_grows_anywhere(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#241.2: no clover anywhere still overlays, with gaps."""
    from builder.species import Source as SpeciesSource

    A = "z10_0581_0294"
    store = FakeStore()
    worker, cache = _hare_world(tmp_path, store, {A: ("fox", "mountain_hare", "vole")})
    assert worker.world is not None
    _link_food_rule(worker.world)
    store.build_jobs["j1"] = _spread_job(
        tiles=[A], spread={"kind": "animal", "what": "mountain_hare"}
    )
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["overlay"] == [A]
    assert job["gaps"] == {A: {"diet": ["clover"], "hunts": []}}
    assert "pack" in job


def test_spread_hare_stays_when_the_tile_is_full(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#241.2: 24 published plants change nothing; gaps list clover."""
    from builder.grid import group_path
    from builder.species import Source as SpeciesSource

    A = "z10_0581_0294"
    store = FakeStore()
    worker, cache = _hare_world(tmp_path, store, {A: ("fox", "mountain_hare", "vole")})
    assert worker.world is not None
    _link_food_rule(worker.world)
    blob_path = group_path(A)
    published = json.loads(store.blobs[blob_path].decode("utf-8"))
    published["tiles"][A]["plants"] = {
        "cover": [{"id": f"weed_{i:02d}", "density": 0.1} for i in range(24)]
    }
    store.blobs[blob_path] = json.dumps(published).encode("utf-8")
    store.build_jobs["j1"] = _spread_job(
        tiles=[A], spread={"kind": "animal", "what": "mountain_hare"}
    )
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
    )
    _clover_drift(monkeypatch)
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["overlay"] == [A]
    assert job["gaps"] == {A: {"diet": ["clover"], "hunts": []}}
    assert "pack" in job


def test_spread_fits_a_listed_sea_home_on_a_coast_tile(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#199.4: the coast tile spreads what its sea home holds."""
    from types import SimpleNamespace

    from builder.species import Source as SpeciesSource
    from builder.species import write_cache

    T = "z10_0581_0294"
    store = FakeStore()
    worker = _flow_worker(tmp_path, store, boreal_3x3())
    assert worker.world is not None
    _named_list(worker.world)
    (worker.world / "homes" / "north_sea.json").write_text(
        json.dumps({"habitat": "north_sea", "ecoregions": [717]})
    )
    listed = json.loads((worker.world / "animal-list.json").read_text())
    next(e for e in listed["animals"] if e["id"] == "lion")["habitats"] = ["north_sea"]
    (worker.world / "animal-list.json").write_text(json.dumps(listed))
    cache = tmp_path / "cache"
    write_cache(
        cache,
        T,
        _forest_survey("fox", "lion"),
        1,
        "",
        "",
        "2026-10-28T00:00:00+00:00",
    )
    sea_home = json.loads((worker.world / "homes" / "north_sea.json").read_text())
    monkeypatch.setattr(worker, "_tile_home", lambda tile, plan, job: sea_home)
    monkeypatch.setattr(
        worker, "_habitat_grid", lambda: SimpleNamespace(habitat=lambda c, r: "coast")
    )
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda *a, **k: SpeciesSource(client=_SilentClient(), cache_dir=cache),
    )
    assert worker._spread_fits("j1", "animal", "lion", T) is None
    listed["animals"] = [e for e in listed["animals"] if e["id"] != "lion"]
    (worker.world / "animal-list.json").write_text(json.dumps(listed))
    assert worker._spread_fits("j1", "animal", "lion", T) is not None


def test_spread_lynx_publishes_an_overlay_pack(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#241.2: lynx spreads to all three tiles; the third gaps voles."""
    from builder.grid import group_path
    from builder.packs import check_pack, check_tiles_overlay
    from builder.species import Source as SpeciesSource

    A, B, C = "z10_0581_0294", "z10_0580_0295", "z10_0590_0298"
    store = FakeStore()
    worker, cache = _spread_world(
        tmp_path,
        store,
        {
            A: ("fox", "mountain_hare", "vole", "lynx"),
            B: ("fox", "mountain_hare", "vole", "lynx"),
            C: ("fox", "mountain_hare", "lynx"),
        },
        prepare=_list_lynx,
    )
    assert worker.world is not None
    _file_lynx(worker.world)
    _link_food_rule(worker.world)
    before = dict(store.blobs)
    store.build_jobs["j1"] = _spread_job(
        tiles=[A, B, C], spread={"kind": "animal", "what": "lynx"}
    )
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["overlay"] == [A, B, C]
    assert job["gaps"] == {C: {"diet": [], "hunts": ["vole"]}}
    assert job["skipped"] == {}
    assert "versions" not in job
    assert job["pack"] == "packs/j1/1/"
    tiles_doc, manifest = _pack_files(store, job["pack"])
    assert tiles_doc == {
        "format": 1,
        "playable": ["lynx"],
        "predators": ["lynx"],
        "tiles": [A, B, C],
    }
    assert manifest["min_game"] == 2
    assert check_tiles_overlay(tiles_doc) == []
    packed = {
        name.removeprefix(job["pack"]): store.blobs[name]
        for name in store.list_blobs(job["pack"])
    }
    assert check_pack(packed) == []
    for path, data in before.items():
        assert store.blobs[path] == data
    assert store.tile_history == []
    assert group_path(C) in before


def test_enrich_review_publishes_the_overlay_next_to_its_animal(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#241.2: the shipped lynx overlays three tiles in its pack's v2."""
    from builder.species import Source as SpeciesSource

    A, B, C = "z10_0581_0294", "z10_0580_0295", "z10_0590_0298"
    store = FakeStore()
    worker, cache = _spread_world(
        tmp_path,
        store,
        {
            A: ("fox", "mountain_hare", "vole", "lynx"),
            B: ("fox", "mountain_hare", "vole", "lynx"),
            C: ("fox", "mountain_hare", "lynx"),
        },
        prepare=_list_lynx,
    )
    assert worker.world is not None
    _file_lynx(worker.world)
    _link_food_rule(worker.world)
    v1 = {
        "animals/lynx.json": b"{}",
        "models/lynx.glb": b"glb-bytes",
        "models/previews/lynx.png": b"png-bytes",
        "locale/en/animals.json": b"{}",
        "locale/fi/animals.json": b"{}",
    }
    store.blobs["packs/animal_lynx/1/manifest.json"] = json.dumps(
        {
            "format": 1,
            "version": 1,
            "min_game": 1,
            "kind": "enrich",
            "files": [{"path": rel} for rel in sorted(v1)],
        }
    ).encode("utf-8")
    for rel, data in v1.items():
        store.blobs[f"packs/animal_lynx/1/{rel}"] = data
    item_dir = tmp_path / "jobs" / "j1" / "enrich_lynx"
    item_dir.mkdir(parents=True)
    (item_dir / "report.html").write_text("<html></html>", encoding="utf-8")
    (item_dir / "state.json").write_text(
        json.dumps(
            {
                "completed": ["research", "ship"],
                "results": {"ship": {"pack": "packs/animal_lynx/1/"}},
            }
        )
    )
    store.build_jobs["j1"] = _enrich_job(
        tile=A,
        tiles=[A, B, C],
        items=["enrich_lynx"],
        enrich={"kind": "animal", "what": "lynx", "from": "fox"},
        status="checking",
        progress=[
            {
                "id": "enrich_lynx",
                "status": "checking",
                "report": "review/j1/enrich_lynx/report.html",
            }
        ],
        review={"enrich_lynx": {"by": "kai", "at": "t", "result": "shipped"}},
    )
    before = dict(store.blobs)
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
    )
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["overlay"] == [A, B, C]
    assert job["gaps"] == {C: {"diet": [], "hunts": ["vole"]}}
    assert "versions" not in job
    assert job["pack"] == "packs/animal_lynx/2/"
    tiles_doc, manifest = _pack_files(store, job["pack"])
    assert tiles_doc["tiles"] == [A, B, C]
    assert manifest["min_game"] == 2
    assert {e["path"] for e in manifest["files"]} == set(v1) | {"additions/tiles.json"}
    for path, data in before.items():
        assert store.blobs[path] == data
    report = store.blobs["review/j1/enrich_lynx/report.html"].decode("utf-8")
    assert f"gaps {C}" in report


def test_enrich_overlay_refuses_a_pack_behind_its_models(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#248.3: a hand fix after ship blocks the overlay until synced."""
    from builder.species import Source as SpeciesSource

    A = "z10_0581_0294"
    store = FakeStore()
    worker, cache = _spread_world(
        tmp_path,
        store,
        {A: ("fox", "mountain_hare", "vole", "lynx")},
        prepare=_list_lynx,
    )
    assert worker.world is not None
    _file_lynx(worker.world)
    _link_food_rule(worker.world)
    item_dir = tmp_path / "jobs" / "j1" / "enrich_lynx"
    pack_dir = item_dir / "pack"
    (pack_dir / "models").mkdir(parents=True)
    (pack_dir / "animals" / "lynx.json").parent.mkdir(parents=True)
    (pack_dir / "animals" / "lynx.json").write_bytes(b"{}")
    (pack_dir / "models" / "lynx.glb").write_bytes(b"square-glb")
    (pack_dir / "manifest.json").write_bytes(b"{}")
    (item_dir / "models").mkdir(parents=True)
    (item_dir / "models" / "lynx.glb").write_bytes(b"round-glb")
    (item_dir / "report.html").write_text("<html></html>", encoding="utf-8")
    (item_dir / "state.json").write_text(
        json.dumps(
            {
                "completed": ["research", "ship"],
                "results": {"ship": {"pack": "packs/animal_lynx/1/"}},
            }
        )
    )
    store.build_jobs["j1"] = _enrich_job(
        tile=A,
        tiles=[A],
        items=["enrich_lynx"],
        enrich={"kind": "animal", "what": "lynx", "from": "fox"},
        status="checking",
        progress=[
            {
                "id": "enrich_lynx",
                "status": "checking",
                "report": "review/j1/enrich_lynx/report.html",
            }
        ],
        review={"enrich_lynx": {"by": "kai", "at": "t", "result": "shipped"}},
    )
    monkeypatch.setattr(
        worker,
        "_species_source",
        lambda: SpeciesSource(client=_SilentClient(), cache_dir=cache),
    )
    worker.handle_build_job("j1")
    failed = store.build_jobs["j1"]
    assert failed["status"] == "failed"
    assert failed["error"] == "bad_pack"
    (pack_dir / "models" / "lynx.glb").write_bytes(b"round-glb")
    store.build_jobs["j1"]["status"] = "checking"
    worker.handle_build_job("j1")
    job = store.build_jobs["j1"]
    assert job["status"] == "published"
    assert job["overlay"] == [A]
    assert store.blobs[job["pack"] + "models/lynx.glb"] == b"round-glb"


def test_overlay_pack_files_prefers_the_job_folder(tmp_path: Path) -> None:
    """#220: the ship's local files carry over; the manifest doesn't."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    pack_dir = tmp_path / "jobs" / "j1" / "enrich_lynx" / "pack"
    (pack_dir / "animals").mkdir(parents=True)
    (pack_dir / "animals" / "lynx.json").write_bytes(b"{}")
    (pack_dir / "manifest.json").write_bytes(b"{}")
    files = worker._overlay_pack_files("j1", "enrich_lynx", "packs/animal_lynx/1/")
    assert files == {"animals/lynx.json": b"{}"}


def test_overlay_pack_files_refuses_a_missing_ship_pack(tmp_path: Path) -> None:
    """#220: a ship pack in neither place fails loud."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    with pytest.raises(ValueError, match="holds no pack"):
        worker._overlay_pack_files("j1", "enrich_lynx", "packs/animal_lynx/1/")
    assert worker._overlay_pack_files("j1", "enrich_lynx", None) == {}


def test_enrich_label_takes_the_live_place_name(tmp_path: Path) -> None:
    """#217.3: the report area names the reef, not open ocean."""
    store = FakeStore()
    worker = _worker(tmp_path, store)
    places = worker.world / "places"
    places.mkdir(parents=True)
    (places / "au_test_reef.json").write_text(
        json.dumps(
            {
                "id": "au_test_reef",
                "name": {"en": "Test Reef", "fi": "Testiriutta"},
                "tiles": [[927, 560]],
                "live": True,
            }
        )
    )
    item_dir = tmp_path / "jobs" / "j1" / "plant_1"
    item_dir.mkdir(parents=True)
    worker._label_enrich_item(item_dir, "z10_0927_0560")
    state = json.loads((item_dir / "state.json").read_text())
    assert state["params"]["area"] == "z10_0927_0560 (Test Reef)"
