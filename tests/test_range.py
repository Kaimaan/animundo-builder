"""Range jobs: a new animal reaches its published tiles, $0 (#277)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fake_store import FakeStore
from fake_world import make_world, write_grids

from builder.config import Config
from builder.presence import cell_wkt
from builder.worker import Worker

T1 = "z10_0581_0294"  # boreal, published, fox on it
T2 = "z10_0582_0294"  # boreal, published
T3 = "z10_0580_0294"  # tundra, published
T4 = "z10_0581_0293"  # boreal, published
T5 = "z10_0581_0295"  # boreal, published, neighbour-only records
T7 = "z10_0582_0295"  # never published
T8 = "z10_0590_0290"  # published, no grid cell

FOX_KEY = 5219243
VOLE_KEY = 1

_FOOD_RULE = """
def eats_here(diet, hunts, kinds, tags):
    return all(d in kinds for d in diet)


def food_gaps(diet, hunts, kinds, tags):
    return {
        "diet": [d for d in diet if d not in kinds],
        "hunts": [h for h in hunts if h not in tags],
    }
"""


def _world(tmp_path: Path) -> Path:
    world = make_world(tmp_path)
    (world / "biomes").mkdir(exist_ok=True)
    (world / "biomes" / "boreal_forest.json").write_text(
        json.dumps(
            {
                "habitat": "boreal_forest",
                "ecoregions": [717],
                "style": "boreal",
                "food": [{"kind": "grass"}],
            }
        )
    )
    (world / "biomes" / "tundra.json").write_text(
        json.dumps(
            {
                "habitat": "tundra",
                "ecoregions": [780],
                "style": "tundra",
                "food": [{"kind": "grass"}],
            }
        )
    )
    tools = world / "tools"
    tools.mkdir(exist_ok=True)
    (tools / "food.py").write_text(_FOOD_RULE)
    listed = json.loads((world / "animal-list.json").read_text())
    listed["animals"].append({"id": "mole", "habitats": ["boreal_forest"]})
    (world / "animal-list.json").write_text(json.dumps(listed))
    return world


def _cells() -> dict[str, list[Any]]:
    boreal: list[Any] = [717, 1.0, None, 0.0, 6, 1.0, 0.0, 80.0]
    tundra: list[Any] = [780, 1.0, None, 0.0, 11, 1.0, 0.0, 80.0]
    return {
        "581,294": list(boreal),
        "582,294": list(boreal),
        "581,293": list(boreal),
        "581,295": list(boreal),
        "580,294": list(tundra),
    }


def _worker(tmp_path: Path, store: FakeStore) -> Worker:
    worker = Worker(
        store=store,
        jobs_dir=tmp_path / "jobs",
        world=_world(tmp_path),
        config=Config(ledger_path=str(tmp_path / "ledger.json")),
        grid_dir=write_grids(tmp_path / "grids", _cells()),
    )
    worker.world_commit = "abc123"
    return worker


def _publish(
    store: FakeStore,
    tile: str,
    home: str = "boreal_forest",
    species: list[str] | None = None,
) -> None:
    from builder.grid import group_path

    blob = group_path(tile)
    raw = store.blobs.get(blob)
    group = json.loads(raw.decode("utf-8")) if raw else {}
    tiles = group.setdefault("tiles", {})
    cast = species if species is not None else ["fox"]
    tiles[tile] = {
        "version": 3,
        "home": home,
        "species": {"playable": cast, "prey": [], "predators": []},
    }
    store.write_blob(blob, json.dumps(group).encode("utf-8"))


def _seed_tiles(store: FakeStore) -> None:
    for tile in (T1, T2, T4, T5):
        _publish(store, tile)
    _publish(store, T3, home="tundra")
    _publish(store, T8)


def _range_job(
    job_id: str,
    what: str = "fox",
    tiles: list[str] | None = None,
    priority: int = 1,
    **over: Any,
) -> dict[str, Any]:
    tiles = [T1] if tiles is None else tiles
    job: dict[str, Any] = {
        "kind": "range",
        "status": "queued",
        "spread": {"kind": "animal", "what": what},
        "tiles": tiles,
        "tile": tiles[0],
        "priority": priority,
        "items": [],
        "budget_usd": 0,
    }
    job.update(over)
    return job


def _stub_gbif(monkeypatch: Any, counts: dict[str, int], key: int) -> list[str]:
    """Single-taxon counts by cell, logging each fetched cell's WKT."""
    import builder.presence as presence_mod

    wkt_to_cell = {cell_wkt(cell): cell for cell in counts}
    calls: list[str] = []

    def fake(params: dict[str, Any], base: str = "") -> dict[str, Any]:
        wkt = params["geometry"]
        calls.append(wkt)
        assert params["taxon_key"] == str(key)
        assert params["limit"] == 0
        return {"count": counts.get(wkt_to_cell.get(wkt, ""), 0)}

    monkeypatch.setattr(presence_mod, "_gbif", fake)
    return calls


def test_range_seats_present_habitat_match(tmp_path: Path, monkeypatch: Any) -> None:
    """#277.1: records + habitat seat the animal from its file."""
    store = FakeStore()
    _seed_tiles(store)
    store.build_jobs["range_20261104_fox"] = _range_job("range_20261104_fox")
    worker = _worker(tmp_path, store)
    _stub_gbif(monkeypatch, {T1: 5}, FOX_KEY)
    worker.range_job("range_20261104_fox", store.build_jobs["range_20261104_fox"])
    job = store.build_jobs["range_20261104_fox"]
    assert job["status"] == "published"
    assert job["overlay"] == [T1]
    assert job["cost_usd"] == 0
    overlay = json.loads(
        store.blobs[job["pack"] + "additions/tiles.json"].decode("utf-8")
    )
    assert overlay["playable"] == ["fox"]
    assert overlay["predators"] == ["fox"]
    assert "prey" not in overlay
    assert overlay["tiles"] == [T1]


def test_range_skips_no_records(tmp_path: Path, monkeypatch: Any) -> None:
    """#277.1: nothing wild there skips the tile with a short code."""
    store = FakeStore()
    _seed_tiles(store)
    store.build_jobs["range_20261104_fox"] = _range_job(
        "range_20261104_fox", tiles=[T2]
    )
    worker = _worker(tmp_path, store)
    _stub_gbif(monkeypatch, {}, FOX_KEY)
    worker.range_job("range_20261104_fox", store.build_jobs["range_20261104_fox"])
    job = store.build_jobs["range_20261104_fox"]
    assert job["status"] == "published"
    assert job["overlay"] == []
    assert job["skipped"] == {T2: "no_records"}
    assert "pack" not in job


def test_range_skips_wrong_habitat(tmp_path: Path, monkeypatch: Any) -> None:
    """#277.1: records where it doesn't live skip on habitat."""
    store = FakeStore()
    _seed_tiles(store)
    store.build_jobs["range_20261104_fox"] = _range_job(
        "range_20261104_fox", tiles=[T3]
    )
    worker = _worker(tmp_path, store)
    _stub_gbif(monkeypatch, {T3: 9}, FOX_KEY)
    worker.range_job("range_20261104_fox", store.build_jobs["range_20261104_fox"])
    job = store.build_jobs["range_20261104_fox"]
    assert job["overlay"] == []
    assert job["skipped"] == {T3: "habitat"}


def test_range_skips_unpublished_and_homeless(tmp_path: Path, monkeypatch: Any) -> None:
    """#277.1: unpublished tiles and cells without homes skip short."""
    store = FakeStore()
    _seed_tiles(store)
    store.build_jobs["range_20261104_fox"] = _range_job(
        "range_20261104_fox", tiles=[T7, T8]
    )
    worker = _worker(tmp_path, store)
    _stub_gbif(monkeypatch, {T7: 4, T8: 4}, FOX_KEY)
    worker.range_job("range_20261104_fox", store.build_jobs["range_20261104_fox"])
    job = store.build_jobs["range_20261104_fox"]
    assert job["overlay"] == []
    assert job["skipped"] == {T7: "not_published", T8: "no_home"}


def test_range_missing_diet_overlays_with_gaps(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#277.1: food never removes; the gap rides the job (0061)."""
    store = FakeStore()
    _seed_tiles(store)
    store.build_jobs["range_20261104_fox"] = _range_job("range_20261104_fox")
    worker = _worker(tmp_path, store)
    _stub_gbif(monkeypatch, {T1: 5}, FOX_KEY)
    worker.range_job("range_20261104_fox", store.build_jobs["range_20261104_fox"])
    job = store.build_jobs["range_20261104_fox"]
    assert job["overlay"] == [T1]
    assert job["gaps"][T1]["diet"] == ["berries"]


def test_range_seats_prey_from_tile_hunters(tmp_path: Path, monkeypatch: Any) -> None:
    """#277.1: the vole is prey where the tile's fox hunts it."""
    store = FakeStore()
    _seed_tiles(store)
    store.build_jobs["range_20261104_vole"] = _range_job(
        "range_20261104_vole", what="vole"
    )
    worker = _worker(tmp_path, store)
    _stub_gbif(monkeypatch, {T1: 7}, VOLE_KEY)
    worker.range_job("range_20261104_vole", store.build_jobs["range_20261104_vole"])
    job = store.build_jobs["range_20261104_vole"]
    assert job["overlay"] == [T1]
    assert job["gaps"] == {}
    overlay = json.loads(
        store.blobs[job["pack"] + "additions/tiles.json"].decode("utf-8")
    )
    assert overlay["prey"] == ["vole"]
    assert "playable" not in overlay
    assert "predators" not in overlay


def test_range_neighbour_record_counts(tmp_path: Path, monkeypatch: Any) -> None:
    """#277.1: smoothing seats a tile on next door's records alone."""
    store = FakeStore()
    _seed_tiles(store)
    store.build_jobs["range_20261104_fox"] = _range_job(
        "range_20261104_fox", tiles=[T5]
    )
    worker = _worker(tmp_path, store)
    _stub_gbif(monkeypatch, {"z10_0582_0295": 12}, FOX_KEY)
    worker.range_job("range_20261104_fox", store.build_jobs["range_20261104_fox"])
    assert store.build_jobs["range_20261104_fox"]["overlay"] == [T5]


def test_range_fetches_each_cell_once(tmp_path: Path, monkeypatch: Any) -> None:
    """#277.1: one job fetches each neighbourhood cell once, all chunks."""
    from builder.grid import neighbours

    store = FakeStore()
    _seed_tiles(store)
    store.build_jobs["range_20261104_fox"] = _range_job(
        "range_20261104_fox", tiles=[T1, T2]
    )
    worker = _worker(tmp_path, store)
    monkeypatch.setattr("builder.worker.RANGE_CHUNK", 1)
    counts = {cell: 3 for cell in ("z10_0581_0294", "z10_0582_0294")}
    calls = _stub_gbif(monkeypatch, counts, FOX_KEY)
    worker.range_job("range_20261104_fox", store.build_jobs["range_20261104_fox"])
    worker.range_job("range_20261104_fox", store.build_jobs["range_20261104_fox"])
    assert store.build_jobs["range_20261104_fox"]["status"] == "published"
    wanted: set[str] = set()
    for tile in (T1, T2):
        wanted.add(tile)
        wanted.update(n for n in neighbours(tile).values() if n is not None)
    assert len(calls) == len(wanted)
    assert len(set(calls)) == len(wanted)


def test_range_waits_for_a_queued_build(tmp_path: Path, monkeypatch: Any) -> None:
    """#277.2: a range job never holds up a player's waiting job."""
    store = FakeStore()
    _seed_tiles(store)
    store.build_jobs["range_20261104_fox"] = _range_job("range_20261104_fox")
    store.build_jobs["b1"] = {
        "kind": "build",
        "status": "queued",
        "tile": T1,
        "tiles": [T1],
        "items": [],
    }
    worker = _worker(tmp_path, store)
    _stub_gbif(monkeypatch, {T1: 5}, FOX_KEY)
    worker.range_job("range_20261104_fox", store.build_jobs["range_20261104_fox"])
    job = store.build_jobs["range_20261104_fox"]
    assert job["status"] == "queued"
    assert "progress" not in job
    assert "overlay" not in job
    assert "worker_id" not in job


def test_range_poll_takes_lowest_priority_first(tmp_path: Path) -> None:
    """#277.2: the poll queues the lowest priority range, then oldest id."""
    store = FakeStore()
    store.build_jobs["range_20261104_fox"] = _range_job(
        "range_20261104_fox", priority=5
    )
    store.build_jobs["range_20261104_vole"] = _range_job(
        "range_20261104_vole", what="vole", priority=1
    )
    store.build_jobs["range_20261104_vole_2"] = _range_job(
        "range_20261104_vole_2", what="vole", priority=1
    )
    worker = _worker(tmp_path, store)
    queued = worker.poll()
    assert queued == [("build_job", "range_20261104_vole")]


def test_range_resumes_without_redoing(tmp_path: Path, monkeypatch: Any) -> None:
    """#277.2: done tiles stand; only the rest judge again."""
    store = FakeStore()
    _seed_tiles(store)
    store.build_jobs["range_20261104_fox"] = _range_job(
        "range_20261104_fox",
        tiles=[T1, T2, T3],
        status="building",
        progress={"done": 1, "of": 3},
        overlay=[T1],
        skipped={},
        gaps={},
        seated={"playable": True, "prey": False, "predators": True},
    )
    worker = _worker(tmp_path, store)
    _stub_gbif(monkeypatch, {}, FOX_KEY)
    worker.range_job("range_20261104_fox", store.build_jobs["range_20261104_fox"])
    job = store.build_jobs["range_20261104_fox"]
    assert job["status"] == "published"
    assert job["overlay"] == [T1]
    assert job["skipped"] == {T2: "no_records", T3: "habitat"}
    assert job["progress"] == {"done": 3, "of": 3}


def test_range_overlay_passes_check_pack(tmp_path: Path, monkeypatch: Any) -> None:
    """#277.4: the pack checks clean and holds far under 5000 tiles."""
    import json as json_mod

    from builder.packs import check_pack

    store = FakeStore()
    _seed_tiles(store)
    store.build_jobs["range_20261104_fox"] = _range_job("range_20261104_fox")
    worker = _worker(tmp_path, store)
    _stub_gbif(monkeypatch, {T1: 5}, FOX_KEY)
    worker.range_job("range_20261104_fox", store.build_jobs["range_20261104_fox"])
    job = store.build_jobs["range_20261104_fox"]
    prefix = job["pack"]
    pack = {
        name.removeprefix(prefix): blob
        for name, blob in store.blobs.items()
        if name.startswith(prefix)
    }
    assert check_pack(pack) == []
    overlay = json_mod.loads(pack["additions/tiles.json"].decode("utf-8"))
    assert len(overlay["tiles"]) <= 5000


def test_range_without_gbif_key_fails(tmp_path: Path, monkeypatch: Any) -> None:
    """#277.3: an animal the border shouldn't have sent fails loud."""
    store = FakeStore()
    _seed_tiles(store)
    store.build_jobs["range_20261104_mole"] = _range_job(
        "range_20261104_mole", what="mole"
    )
    worker = _worker(tmp_path, store)
    _stub_gbif(monkeypatch, {}, FOX_KEY)
    worker.range_job("range_20261104_mole", store.build_jobs["range_20261104_mole"])
    job = store.build_jobs["range_20261104_mole"]
    assert job["status"] == "failed"
    assert job["error"] == "no_gbif_key"


def test_range_without_spec_fails(tmp_path: Path, monkeypatch: Any) -> None:
    """#277.1: a range job without its animal fails instead of guessing."""
    store = FakeStore()
    _seed_tiles(store)
    job = _range_job("range_20261104_fox")
    del job["spread"]
    store.build_jobs["range_20261104_fox"] = job
    worker = _worker(tmp_path, store)
    _stub_gbif(monkeypatch, {}, FOX_KEY)
    worker.range_job("range_20261104_fox", store.build_jobs["range_20261104_fox"])
    job = store.build_jobs["range_20261104_fox"]
    assert job["status"] == "failed"
    assert job["error"] == "bad_range"


def test_range_chunk_throw_fails_with_reason_and_partial(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#305.1: a chunk that throws fails range_failed, keeping partials."""
    import builder.presence as presence_mod

    store = FakeStore()
    _seed_tiles(store)
    store.build_jobs["range_20261104_fox"] = _range_job(
        "range_20261104_fox", tiles=[T8, T2]
    )
    worker = _worker(tmp_path, store)

    def boom(params: dict[str, Any], base: str = "") -> dict[str, Any]:
        raise ConnectionError("GBIF stayed down: boom")

    monkeypatch.setattr(presence_mod, "_gbif", boom)
    worker.range_job("range_20261104_fox", store.build_jobs["range_20261104_fox"])
    job = store.build_jobs["range_20261104_fox"]
    assert job["status"] == "failed"
    assert job["error"].startswith("range_failed: ")
    assert "boom" in job["error"]
    assert job["progress"] == {"done": 1, "of": 2}
    assert job["skipped"] == {T8: "no_home"}
    assert job["overlay"] == []


def test_range_busy_build_job_claims_nothing(tmp_path: Path) -> None:
    """#305.2: a waiting range holds no lease, so the watch doesn't spin."""
    store = FakeStore()
    _seed_tiles(store)
    store.build_jobs["range_20261104_fox"] = _range_job("range_20261104_fox")
    store.build_jobs["b1"] = {
        "kind": "build",
        "status": "queued",
        "tile": T1,
        "tiles": [T1],
        "items": [],
    }
    worker = _worker(tmp_path, store)
    worker.build_job("range_20261104_fox", store.build_jobs["range_20261104_fox"])
    job = store.build_jobs["range_20261104_fox"]
    assert job["status"] == "queued"
    assert "worker_id" not in job
    assert "lease_until" not in job


def test_range_expired_building_counts_as_due(tmp_path: Path) -> None:
    """#305.2: an expired building range job is due and comes back."""
    store = FakeStore()
    past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    store.build_jobs["range_20261104_fox"] = _range_job(
        "range_20261104_fox",
        status="building",
        worker_id="dead-host:1",
        lease_until=past,
        attempts=0,
    )
    worker = _worker(tmp_path, store)
    assert worker._range_due([]) == [("build_job", "range_20261104_fox")]
    queued = worker.poll()
    assert ("build_job", "range_20261104_fox") in queued
    job = store.build_jobs["range_20261104_fox"]
    assert job["status"] == "queued"
    assert job["attempts"] == 1
