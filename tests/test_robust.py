"""Never fail silently or stay stuck (#272). No Firebase, no network."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fake_store import FakeStore

from builder.config import Config
from builder.worker import Worker


def _worker(tmp_path: Path, store: FakeStore) -> Worker:
    world = tmp_path / "world"
    world.mkdir(exist_ok=True)
    grid = tmp_path / "grid"
    grid.mkdir(exist_ok=True)
    return Worker(
        store=store,
        jobs_dir=tmp_path / "jobs",
        world=world,
        config=Config(ledger_path=str(tmp_path / "ledger.json")),
        grid_dir=grid,
    )


def test_crash_ends_job_with_detail(tmp_path: Path) -> None:
    """#272.1: an uncaught build error fails the job, with the detail."""
    store = FakeStore()
    store.set_build_job(
        "j1", {"status": "queued", "kind": "code", "tile": "z10_0001_0001"}
    )
    worker = _worker(tmp_path, store)

    def _boom(job_id: str, job: dict[str, Any]) -> None:
        raise RuntimeError("the test crash goes here")

    worker.build_job = _boom  # type: ignore[method-assign]
    worker.handle_build_job("j1")
    job = store.get_build_job("j1")
    assert job is not None
    assert job["status"] == "failed"
    assert job["error"] == "crash"
    detail = job.get("error_detail") or {}
    for key in (
        "stage",
        "tile",
        "type",
        "message",
        "traceback",
        "worker_id",
        "builder_commit",
        "world",
        "at",
    ):
        assert key in detail, f"error_detail lacks {key}"
    assert detail["type"] == "RuntimeError"
    assert "the test crash goes here" in detail["message"]
    assert "RuntimeError" in detail["traceback"]
    assert detail["tile"] == "z10_0001_0001"


def test_crash_alert_carries_full_detail(tmp_path: Path) -> None:
    """#272.2: the crash alert is not cut to 200 characters."""
    store = FakeStore()
    store.set_build_job(
        "j1", {"status": "queued", "kind": "code", "tile": "z10_0001_0001"}
    )
    worker = _worker(tmp_path, store)
    long_message = "boom-" + "x" * 300

    def _boom(job_id: str, job: dict[str, Any]) -> None:
        raise RuntimeError(long_message)

    worker.build_job = _boom  # type: ignore[method-assign]
    worker.handle_build_job("j1")
    assert store.alerts, "no alert written"
    alert = store.alerts[-1]
    assert alert["kind"] == "job_failed"
    assert alert["id"] == "j1"
    assert long_message in str(alert.get("message", "")) or long_message in str(
        (alert.get("error_detail") or {}).get("message", "")
    )


def test_plan_crash_writes_error_doc(tmp_path: Path, monkeypatch: Any) -> None:
    """#272.4: a planning crash writes plans/{tile}.error, and alerts."""
    store = FakeStore()
    store.requests["z10_0581_0294"] = {"tile": "z10_0581_0294"}
    worker = _worker(tmp_path, store)

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the compass spun")

    monkeypatch.setattr("builder.worker.plan_tile", _boom)
    worker.handle_request("z10_0581_0294")
    doc = store.plans.get("z10_0581_0294")
    assert doc is not None, "no plan error written"
    error = doc.get("error") or {}
    assert error.get("reason") == "crash"
    assert error.get("attempts") == 1
    assert "detail" in str(error) or "detail" in error
    assert "next_try" in error
    assert store.alerts[-1]["kind"] == "plan_failed"


def test_plan_crash_bumps_attempts(tmp_path: Path, monkeypatch: Any) -> None:
    """#272.4: each crash raises the attempt count and the backoff."""
    store = FakeStore()
    store.requests["z10_0581_0294"] = {"tile": "z10_0581_0294"}
    store.plans["z10_0581_0294"] = {
        "tile": "z10_0581_0294",
        "error": {
            "reason": "crash",
            "detail": "old",
            "at": "2026-01-01T00:00:00+00:00",
            "attempts": 2,
            "next_try": "2026-01-01T00:00:00+00:00",
        },
    }
    worker = _worker(tmp_path, store)

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the compass spun again")

    monkeypatch.setattr("builder.worker.plan_tile", _boom)
    worker.handle_request("z10_0581_0294")
    error = store.plans["z10_0581_0294"]["error"]
    assert error["attempts"] == 3
    assert datetime.fromisoformat(error["next_try"]) > datetime.now(UTC)


def test_poll_retries_due_plan_errors(tmp_path: Path) -> None:
    """#272.4: the poll requeues plan errors past their backoff."""
    store = FakeStore()
    store.requests["z10_0581_0294"] = {"tile": "z10_0581_0294"}
    store.requests["z10_0581_0295"] = {"tile": "z10_0581_0295"}
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    store.plans["z10_0581_0294"] = {"tile": "z1", "error": {"next_try": past}}
    store.plans["z10_0581_0295"] = {"tile": "z2", "error": {"next_try": future}}
    worker = _worker(tmp_path, store)
    queued = worker.poll()
    assert ("request", "z10_0581_0294") in queued
    assert ("request", "z10_0581_0295") not in queued


def _stuck_job(owner: str = "dead-host:99999", attempts: int = 0) -> dict[str, Any]:
    past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    return {
        "status": "building",
        "kind": "code",
        "tile": "z10_0001_0001",
        "worker_id": owner,
        "lease_until": past,
        "attempts": attempts,
    }


def test_sweep_requeues_dead_building_job(tmp_path: Path) -> None:
    """#272.3: a building job with a dead lease goes back to queued."""
    store = FakeStore()
    store.set_build_job("j1", _stuck_job())
    worker = _worker(tmp_path, store)
    queued = worker.poll()
    assert ("build_job", "j1") in queued
    job = store.get_build_job("j1")
    assert job is not None
    assert job["status"] == "queued"
    assert job["attempts"] == 1


def test_sweep_fails_crash_loop_after_3(tmp_path: Path) -> None:
    """#272.3: three dead pickups fail the job as crash_loop, with detail."""
    store = FakeStore()
    store.set_build_job("j1", _stuck_job(attempts=3))
    worker = _worker(tmp_path, store)
    queued = worker.poll()
    assert ("build_job", "j1") not in queued
    job = store.get_build_job("j1")
    assert job is not None
    assert job["status"] == "failed"
    assert job["error"] == "crash_loop"
    assert (job.get("error_detail") or {}).get("stage")
    assert store.alerts[-1]["kind"] == "job_failed"


def test_sweep_skips_live_and_fresh(tmp_path: Path) -> None:
    """#272.3: fresh leases, fresh queued jobs and paused jobs stay put."""
    store = FakeStore()
    live = _stuck_job()
    live["lease_until"] = (datetime.now(UTC) + timedelta(minutes=20)).isoformat()
    store.set_build_job("live", live)
    store.set_build_job("fresh", {"status": "queued", "kind": "code"})
    paused = _stuck_job()
    paused["error"] = "paused_spend_uncertain"
    store.set_build_job("paused", paused)
    worker = _worker(tmp_path, store)
    queued = worker.poll()
    assert ("build_job", "live") not in queued
    assert ("build_job", "fresh") not in queued
    assert ("build_job", "paused") not in queued
    assert store.get_build_job("live")["status"] == "building"
    assert "attempts" not in store.get_build_job("fresh")


def test_refused_claim_logs_owner_and_lease(tmp_path: Path, caplog: Any) -> None:
    """#272.3: a refused claim logs who owns the job and its lease."""
    import logging

    store = FakeStore()
    live = _stuck_job(owner="live-host:4242")
    live["lease_until"] = (datetime.now(UTC) + timedelta(minutes=20)).isoformat()
    store.set_build_job("j1", live)
    worker = _worker(tmp_path, store)
    with caplog.at_level(logging.WARNING, logger="builder.worker"):
        assert worker._claim("j1") is False
    assert "live-host:4242" in caplog.text


def test_heartbeat_renews_lease(tmp_path: Path) -> None:
    """#272.3: the beat renews an owned lease until stopped."""
    import threading
    import time

    store = FakeStore()
    worker = _worker(tmp_path, store)
    before = (datetime.now(UTC) - timedelta(minutes=25)).isoformat()
    store.set_build_job(
        "j1",
        {
            "status": "building",
            "kind": "code",
            "worker_id": worker.worker_id,
            "lease_until": before,
        },
    )
    stop = threading.Event()
    beat = threading.Thread(target=worker._heartbeat, args=("j1", stop, 0.01))
    beat.start()
    time.sleep(0.1)
    stop.set()
    beat.join(timeout=5)
    after = store.get_build_job("j1") or {}
    assert after.get("lease_until", "") > before


def test_plan_probe_reraises_meta_errors(tmp_path: Path, monkeypatch: Any) -> None:
    """#272.5: auth/cap/timeout errors are loud, not 'no answer'."""
    from fake_world import make_world

    from builder.meta import MetaAuthError
    from builder.plan import _resolve_species

    world = make_world(tmp_path)

    def _denied(*args: Any, **kwargs: Any) -> Any:
        raise MetaAuthError("bad key")

    monkeypatch.setattr("builder.plan.fetch_species", _denied)
    with pytest.raises(MetaAuthError):
        _resolve_species(
            "z10_0581_0294",
            world,
            "boreal_forest",
            60.0,
            24.0,
            tmp_path,
            None,
            None,
            {},
        )

    def _silent(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("no answer")

    monkeypatch.setattr("builder.plan.fetch_species", _silent)
    probe, fetched, blocked = _resolve_species(
        "z10_0581_0294",
        world,
        "boreal_forest",
        60.0,
        24.0,
        tmp_path,
        None,
        None,
        {},
    )
    assert (probe, fetched, blocked) == (None, None, False)


def test_world_eats_crash_raises(tmp_path: Path, monkeypatch: Any) -> None:
    """#272.5: a crashing food rule raises; only a missing one is None."""
    import subprocess

    from builder.tiles import world_eats

    tools = tmp_path / "world" / "tools"
    tools.mkdir(parents=True)
    (tools / "food.py").write_text("# a food rule exists\n")

    def _timeout(*args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="food", timeout=120)

    monkeypatch.setattr("builder.tiles.subprocess.run", _timeout)
    with pytest.raises(RuntimeError):
        world_eats(tmp_path / "world", [{"id": "x", "species": []}])

    empty = tmp_path / "empty-world"
    (empty / "tools").mkdir(parents=True)

    def _must_not_run(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("must not run")

    monkeypatch.setattr("builder.tiles.subprocess.run", _must_not_run)
    assert world_eats(empty, [{"id": "x", "species": []}]) is None


def test_check_missing_tool_raises_on_real_checkout(tmp_path: Path) -> None:
    """#272.5: converter without its check raises; a fake world passes."""
    from builder.tiles import check_with_world

    real = tmp_path / "world"
    (real / "tools").mkdir(parents=True)
    (real / "tools" / "tile_region.py").write_text("# converter\n")
    with pytest.raises(RuntimeError):
        check_with_world({"id": "z10_0001_0001"}, real)

    fake = tmp_path / "fake"
    (fake / "tools").mkdir(parents=True)
    assert check_with_world({"id": "z10_0001_0001"}, fake) == []


def test_open_sea_name_missing_seas_raises(tmp_path: Path) -> None:
    """#272.5: a missing baked seas file is loud, not open_water."""
    from builder.tiles import _open_sea_name

    with pytest.raises(FileNotFoundError):
        _open_sea_name("z10_0001_0001", tmp_path, tmp_path)


def test_corrupt_group_raises(tmp_path: Path) -> None:
    """#272.5: a corrupt group blob raises, never reads as {}."""
    from builder.build import publish_group
    from builder.grid import group_path

    store = FakeStore()
    path = group_path("z10_0581_0294")
    store.blobs[path] = b"{not json"
    with pytest.raises(ValueError, match="z10_0581_0294|115_070|\\.json"):
        publish_group("z10_0581_0294", {"id": "z10_0581_0294"}, store, "abc123")


def test_food_failures_keep_problems(tmp_path: Path, monkeypatch: Any) -> None:
    """#272.5: a food-only failure blocks no_animal with its problems."""
    import json

    from fake_world import make_world, write_grids

    from builder.habitats import load_grid
    from builder.tiles import make_tile

    world = make_world(tmp_path)
    (world / "homes" / "open_sea.json").write_text(
        json.dumps({"habitat": "open_ocean", "ecoregions": []})
    )
    (world / "places").mkdir()
    (world / "places" / "p1.json").write_text(
        json.dumps(
            {
                "id": "p1",
                "live": True,
                "tiles": [[581, 294]],
                "species": {"playable": ["fox"], "prey": [], "predators": []},
            }
        )
    )
    (world / "regions").mkdir()
    (world / "regions" / "p1.json").write_text(json.dumps({"biome": "boreal_forest"}))
    cell = [None, 0.0, None, 0.0, None, 0.0, 1.0, -50.0]
    cells = {f"{c},{r}": list(cell) for r in (293, 294, 295) for c in (580, 581, 582)}
    grid = load_grid(write_grids(tmp_path / "grid", cells))

    def _hungry(made_tile: Any, world_path: Any) -> list[tuple[str, list[str]]]:
        return [("food", ["nothing eats here"])]

    monkeypatch.setattr("builder.tiles.check_with_world", _hungry)
    _made, blocked = make_tile("z10_0581_0294", grid, world, builder_root=tmp_path)
    assert blocked is not None
    assert blocked["reason"] == "no_animal"
    assert "nothing eats here" in " ".join(blocked.get("problems", []))


def _placed_world(tmp_path: Path, with_biome: bool) -> Any:
    """Fake world with a live place on an open-ocean cell (#272.8)."""
    import json

    from fake_world import make_world, write_grids

    from builder.habitats import load_grid

    world = make_world(tmp_path)
    (world / "homes" / "open_sea.json").write_text(
        json.dumps({"habitat": "open_ocean", "ecoregions": []})
    )
    (world / "places").mkdir()
    (world / "places" / "p1.json").write_text(
        json.dumps(
            {
                "id": "p1",
                "live": True,
                "tiles": [[581, 294]],
                "species": {"playable": ["fox"], "prey": [], "predators": []},
            }
        )
    )
    if with_biome:
        (world / "regions").mkdir()
        (world / "regions" / "p1.json").write_text(
            json.dumps({"biome": "boreal_forest"})
        )
    cell = [None, 0.0, None, 0.0, None, 0.0, 1.0, -50.0]
    cells = {f"{c},{r}": list(cell) for r in (293, 294, 295) for c in (580, 581, 582)}
    grid = load_grid(write_grids(tmp_path / "grid", cells))
    return world, grid


def test_placed_tile_plans_like_maker(tmp_path: Path) -> None:
    """#272.8: a placed tile's plan agrees with its maker: code_only."""
    from builder.plan import plan_tile
    from builder.tiles import make_tile

    world, grid = _placed_world(tmp_path, with_biome=True)
    made, blocked = make_tile("z10_0581_0294", grid, world, builder_root=tmp_path)
    assert blocked is None, blocked
    assert made is not None
    plan = plan_tile(
        "z10_0581_0294",
        {"tile": "z10_0581_0294"},
        grid,
        world,
        "test",
        presence={},
        unfiled_presence={},
        builder_root=tmp_path,
    )
    assert plan["code_only"] is True
    assert plan["items"] == []


def test_placeless_biome_raises_both_sides(tmp_path: Path) -> None:
    """#272.8: a biomeless place raises the same for plan and maker."""
    from builder.plan import plan_tile
    from builder.tiles import make_tile, place_layer_for

    world, grid = _placed_world(tmp_path, with_biome=False)
    with pytest.raises(ValueError, match="a person decides"):
        make_tile("z10_0581_0294", grid, world, builder_root=tmp_path)
    with pytest.raises(ValueError, match="a person decides"):
        plan_tile(
            "z10_0581_0294",
            {"tile": "z10_0581_0294"},
            grid,
            world,
            "test",
            presence={},
            unfiled_presence={},
            builder_root=tmp_path,
        )
    assert place_layer_for is not None


def _live_world_and_grid() -> tuple[Any, Any]:
    """The real world checkout and baked grid, or skip without them."""
    import pytest

    from builder.habitats import load_grid
    from builder.schemas import ROOT

    world = ROOT.parent / "animundo-world"
    grid_dir = ROOT / "data" / "habitat-grid"
    if not (world / "animal-list.json").is_file():
        pytest.skip("needs the world checkout next to the builder")
    if not (grid_dir / "z10.json.gz").is_file():
        pytest.skip("needs the baked habitat grid")
    return world, load_grid(grid_dir)


def _recorded_survey(tile: str) -> dict[str, Any]:
    """The tile's survey, recorded from the cached answer."""
    import json

    from builder.schemas import ROOT

    path = ROOT / "tests" / "fixtures" / "species" / f"{tile}.json"
    return json.loads(path.read_text())


def _env_plan(tile: str, **over: Any) -> dict[str, Any]:
    """plan_tile with no network: presence and unfiled overridden."""
    from builder.plan import plan_tile
    from builder.schemas import ROOT

    world, grid = _live_world_and_grid()
    params: dict[str, Any] = {
        "presence": None,
        "unfiled_presence": {},
        "builder_root": ROOT,
    }
    params.update(over)
    return plan_tile(tile, {"tile": tile}, grid, world, "test", **params)


def test_env_boreal_land_publishes() -> None:
    """#272.8: taiga plans code_only."""
    plan = _env_plan("z10_0583_0295", species=_recorded_survey("z10_0583_0295"))
    assert plan["code_only"] is True
    assert plan["items"] == []
    assert plan.get("blocked") is None


def test_env_lakeland_publishes() -> None:
    """#272.8: Saimaa lakeland plans code_only."""
    plan = _env_plan("z10_0592_0284", species=_recorded_survey("z10_0592_0284"))
    assert plan["code_only"] is True
    assert plan["items"] == []
    assert plan.get("blocked") is None


def test_env_open_sea_publishes() -> None:
    """#272.8: open sea with recorded presence plans code_only."""
    # Recorded GBIF counts, Gulf of Finland, Nov 4 2026: gulls
    # and terns fish here; no dolphins. A deterministic replay.
    plan = _env_plan(
        "z10_0583_0298",
        presence={"herring_gull": 519.7, "common_tern": 20.45},
    )
    assert plan["code_only"] is True
    assert plan["items"] == []
    assert plan.get("blocked") is None


def test_env_reef_mask_publishes() -> None:
    """#272.8: a reef-mask sea tile plans code_only from the reef home."""
    # Green Island has no survey of its own; its neighbour's
    # recorded reef survey fits the reef tile (the check
    # confirms it), so the reef path is what this replays.
    plan = _env_plan("z10_0927_0560", species=_recorded_survey("z10_0926_0560"))
    assert plan["code_only"] is True
    assert plan["items"] == []
    assert plan.get("blocked") is None


def test_env_coast_publishes() -> None:
    """#272.8: the reef's coast tile plans code_only via wet_tropics."""
    plan = _env_plan("z10_0927_0561", species=_recorded_survey("z10_0927_0561"))
    assert plan["code_only"] is True
    assert plan["items"] == []
    assert plan.get("blocked") is None


def test_env_big_lake_blocks_named() -> None:
    """#272.8: a big non-boreal lake blocks no_home, naming it."""
    plan = _env_plan("z10_0315_0079")
    assert plan.get("blocked") == "no_home"
    detail = plan.get("blocked_detail") or {}
    assert detail.get("habitat") == "lake"
    assert detail.get("ecoregion") == 412
    assert detail.get("ecoregion_name")


def test_env_varzea_expands_on_first_animal() -> None:
    """#272.8: Amazon rainforest plans its first animal, determinately.

    Nothing Amazonian is filed yet, so neither publish nor
    block is honest here: the plan expands with the survey's
    first animal, and publishes once it files. If this starts
    failing with code_only, the forest got its cast: update
    the expectation to the published tile.
    """
    plan = _env_plan("z10_0297_0514", species=_recorded_survey("z10_0297_0514"))
    assert plan["code_only"] is False
    assert plan.get("blocked") is None
    assert [item["id"] for item in plan["items"]] == ["animal_jaguar"]


def test_env_date_line_blocks_named() -> None:
    """#272.8: an empty date-line tile blocks no_animal, naming it."""
    plan = _env_plan("z10_0000_0512", presence={})
    assert plan.get("blocked") == "no_animal"


def test_env_ecoless_lake_blocks_open_water() -> None:
    """#272.8: Victoria has no ecoregion and no home: open_water."""
    plan = _env_plan("z10_0605_0514")
    assert plan.get("blocked") == "open_water"


def test_near_wkt_wraps_date_line() -> None:
    """#272.8: a grown box across the line covers both sides."""
    from builder.presence import near_wkt

    wrapped = near_wkt("z10_0000_0512")
    assert wrapped.startswith("MULTIPOLYGON")
    assert "180.000000" in wrapped and "-180.000000" in wrapped
    wrapped_east = near_wkt("z10_1023_0512")
    assert wrapped_east.startswith("MULTIPOLYGON")
    mid = near_wkt("z10_0581_0294")
    assert mid.startswith("POLYGON")


def _hang(*args: Any, **kwargs: Any) -> Any:
    import subprocess

    raise subprocess.TimeoutExpired(cmd="hung", timeout=kwargs.get("timeout"))


def test_version_probe_timeout_falls_back(tmp_path: Path, monkeypatch: Any) -> None:
    """#272.6: a hung version probe falls back, never hangs the loop."""
    from builder.tiles import _versions

    monkeypatch.setattr("builder.tiles.subprocess.run", _hang)
    maker, commit = _versions(tmp_path, tmp_path)
    assert commit == "unknown"
    assert maker


def test_git_commit_timeout_returns_unknown(tmp_path: Path, monkeypatch: Any) -> None:
    """#272.6: a hung rev-parse reads unknown."""
    from builder.worker import git_commit

    monkeypatch.setattr("builder.worker.subprocess.run", _hang)
    assert git_commit(tmp_path) == "unknown"


def test_pull_timeout_returns_false(tmp_path: Path, monkeypatch: Any) -> None:
    """#272.6: a hung world pull fails the round, not the thread."""

    (tmp_path / ".git").mkdir()
    worker = _worker(tmp_path, FakeStore())
    object.__setattr__(worker, "world", tmp_path)
    monkeypatch.setattr("builder.worker.subprocess.run", _hang)
    assert worker.pull_world() is False


def test_check_timeout_raises_loud(tmp_path: Path, monkeypatch: Any) -> None:
    """#272.6: a hung tile check raises, with its name on it."""
    from builder.tiles import check_with_world

    tools = tmp_path / "world" / "tools"
    tools.mkdir(parents=True)
    (tools / "check_tile.py").write_text("# check\n")
    (tools / "tile_region.py").write_text("# converter\n")
    monkeypatch.setattr("builder.tiles.subprocess.run", _hang)
    with pytest.raises(RuntimeError, match="tile check did not run"):
        check_with_world({"id": "z10_0001_0001"}, tmp_path / "world")


def test_select_timeout_raises_loud(tmp_path: Path, monkeypatch: Any) -> None:
    """#272.6: a hung select round raises, never loops forever."""
    from builder.tiles import select_species

    tools = tmp_path / "world" / "tools"
    tools.mkdir(parents=True)
    (tools / "check_animals.py").write_text("# check\n")
    (tools / "tile_region.py").write_text("# converter\n")
    monkeypatch.setattr("builder.tiles.subprocess.run", _hang)
    with pytest.raises(RuntimeError, match="food-web check did not run"):
        select_species({"id": "z10_0001_0001"}, ["fox"], tmp_path / "world")


def test_convert_timeout_raises_loud(tmp_path: Path, monkeypatch: Any) -> None:
    """#272.6: a hung tile convert raises ConvertError."""
    from builder.enrich import ConvertError, tile_place

    tools = tmp_path / "world" / "tools"
    tools.mkdir(parents=True)
    (tools / "tile_region.py").write_text("# converter\n")
    monkeypatch.setattr("builder.enrich.subprocess.run", _hang)
    with pytest.raises(ConvertError, match="did not run"):
        tile_place(
            {"id": "z10_0001_0001"},
            tmp_path / "world",
            what="fox",
            kind="animal",
            start="vole",
        )
