"""Local request runs: replay a recording keyless, record one live. No key."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from builder.records import ReplayError
from builder.replay import replay_request


def _ctx(item: Any, item_dir: Path) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(client=SimpleNamespace())


def _plan(tile: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "format": 1,
        "tile": tile,
        "code_only": False,
        "items": items,
        "improve": [],
        "estimate_usd": 0.0,
    }


def _recording(path: Path, plan: dict[str, Any]) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "plan.json").write_text(json.dumps(plan, indent=1))
    return path


def test_request_continues_after_a_failed_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """#146.1: the failed item is reported, the rest still build."""
    import fake_world

    from builder import build as build_mod
    from builder.pipeline import StageFailedError
    from builder.replay import run_request

    world = fake_world.make_world(tmp_path)
    grid_dir = fake_world.write_grids(tmp_path / "grids", fake_world.boreal_3x3())
    plan = _plan(
        "z10_0581_0294",
        [
            {
                "id": "animal_aardvark",
                "kind": "animal",
                "what": "fox",
                "from": "fox",
                "estimate_usd": 0.0,
            },
            {
                "id": "animal_zebra",
                "kind": "animal",
                "what": "fox",
                "from": "fox",
                "estimate_usd": 0.0,
            },
        ],
    )

    def fake_stages(ctx: Any, item_dir: Path, pipeline: Any) -> None:
        if item_dir.name == "animal_aardvark":
            raise StageFailedError("the model broke the build")

    monkeypatch.setattr(build_mod, "run_item_stages", fake_stages)
    reports = run_request(
        "z10_0581_0294", plan, tmp_path / "out", world, grid_dir, _ctx
    )
    assert set(reports) == {"animal_aardvark", "animal_zebra"}
    assert all(p.is_file() for p in reports.values())
    err = capsys.readouterr().err
    assert "animal_aardvark failed: the model broke the build" in err
    assert "1 of 2 items failed" in err


def test_request_skips_items_needing_a_missing_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """#146.1: without a home the plant waits, the rest still build."""
    import fake_world

    from builder import build as build_mod
    from builder.replay import run_request

    world = fake_world.make_world(tmp_path)
    for home in (world / "homes").glob("*.json"):
        home.unlink()
    grid_dir = fake_world.write_grids(tmp_path / "grids", fake_world.boreal_3x3())
    plan = _plan(
        "z10_0581_0294",
        [
            {
                "id": "home_717",
                "kind": "home",
                "what": "717",
                "from": "tundra",
                "estimate_usd": 0.0,
            },
            {
                "id": "plant_x",
                "kind": "plant",
                "what": "bilberry",
                "from": "bilberry",
                "estimate_usd": 0.0,
            },
        ],
    )
    monkeypatch.setattr(
        build_mod, "run_item_stages", lambda ctx, item_dir, pipeline: None
    )
    reports = run_request(
        "z10_0581_0294", plan, tmp_path / "out", world, grid_dir, _ctx
    )
    assert set(reports) == {"home_717"}
    assert "plant_x skipped: needs home_717" in capsys.readouterr().err


def test_request_raises_when_nothing_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#146.1: a run where every item failed still raises."""
    import fake_world

    from builder import build as build_mod
    from builder.pipeline import StageFailedError
    from builder.replay import run_request

    world = fake_world.make_world(tmp_path)
    grid_dir = fake_world.write_grids(tmp_path / "grids", fake_world.boreal_3x3())
    plan = _plan(
        "z10_0581_0294",
        [
            {
                "id": "animal_aardvark",
                "kind": "animal",
                "what": "fox",
                "from": "fox",
                "estimate_usd": 0.0,
            }
        ],
    )

    def fake_stages(ctx: Any, item_dir: Path, pipeline: Any) -> None:
        raise StageFailedError("the model broke the build")

    monkeypatch.setattr(build_mod, "run_item_stages", fake_stages)
    with pytest.raises(StageFailedError, match="the model broke the build"):
        run_request(
            "z10_0581_0294",
            plan,
            tmp_path / "out",
            world,
            grid_dir,
            _ctx,
        )


def test_food_request_replays_with_no_records(tmp_path: Path) -> None:
    """#T39: a code-only request replays end to end, keyless."""
    import fake_world

    world = fake_world.make_world(tmp_path)
    grid_dir = fake_world.write_grids(tmp_path / "grids", fake_world.boreal_3x3())
    plan = _plan(
        "z10_0581_0294",
        [
            {
                "id": "food_berries",
                "kind": "food",
                "what": "berries",
                "from": "boreal_forest",
                "estimate_usd": 0.0,
            }
        ],
    )
    recording = _recording(tmp_path / "recording", plan)
    out = tmp_path / "out"
    reports = replay_request("z10_0581_0294", recording, out, world, grid_dir)
    assert list(reports) == ["food_berries"]
    assert reports["food_berries"].is_file()
    assert (out / "replay" / "plan.json").is_file()
    assert (out / "replay" / "food_berries" / "report.html").is_file()


def test_replay_refuses_another_tiles_recording(tmp_path: Path) -> None:
    """#T39: the recording's tile must be the requested one."""
    import fake_world

    world = fake_world.make_world(tmp_path)
    grid_dir = fake_world.write_grids(tmp_path / "grids", fake_world.boreal_3x3())
    recording = _recording(tmp_path / "recording", _plan("z10_0581_0294", []))
    with pytest.raises(ValueError, match="not z10_0580_0295"):
        replay_request("z10_0580_0295", recording, tmp_path / "out", world, grid_dir)


def test_replay_refuses_a_job_id(tmp_path: Path) -> None:
    """#T39: `--replay` takes a tile, not a job id."""
    import fake_world

    world = fake_world.make_world(tmp_path)
    grid_dir = fake_world.write_grids(tmp_path / "grids", fake_world.boreal_3x3())
    recording = _recording(tmp_path / "recording", _plan("z10_0581_0294", []))
    with pytest.raises(ValueError, match="not a tile id"):
        replay_request("j1", recording, tmp_path / "out", world, grid_dir)


def test_replay_needs_answers_for_model_items(tmp_path: Path) -> None:
    """#T39: an animal item without records stops loudly."""
    import fake_world

    world = fake_world.make_world(tmp_path)
    grid_dir = fake_world.write_grids(tmp_path / "grids", fake_world.boreal_3x3())
    plan = _plan(
        "z10_0581_0294",
        [
            {
                "id": "animal_lion",
                "kind": "animal",
                "what": "lion",
                "from": "fox",
                "estimate_usd": 0.15,
            }
        ],
    )
    recording = _recording(tmp_path / "recording", plan)
    with pytest.raises(ReplayError, match="no answers for this item"):
        replay_request("z10_0581_0294", recording, tmp_path / "out", world, grid_dir)


def test_replay_runs_out_of_answers_loudly(tmp_path: Path) -> None:
    """#T39: the pipeline plays the recording, nothing else."""
    import fake_world

    world = fake_world.make_world(tmp_path)
    grid_dir = fake_world.write_grids(tmp_path / "grids", fake_world.boreal_3x3())
    plan = _plan(
        "z10_0581_0294",
        [
            {
                "id": "animal_lion",
                "kind": "animal",
                "what": "lion",
                "from": "fox",
                "estimate_usd": 0.15,
            }
        ],
    )
    recording = _recording(tmp_path / "recording", plan)
    (recording / "items" / "animal_lion" / "records").mkdir(parents=True)
    with pytest.raises(ReplayError, match="out of answers"):
        replay_request("z10_0581_0294", recording, tmp_path / "out", world, grid_dir)


def test_record_run_ids_are_unique_per_run() -> None:
    """#180.1: each record run caps only its own spend, never an old run's."""
    from builder.replay import JOB_ID, run_job_id

    first = run_job_id("animal_humpback_whale")
    second = run_job_id("animal_humpback_whale")
    assert first != second
    assert first.startswith(f"{JOB_ID}/animal_humpback_whale/")
    assert second.startswith(f"{JOB_ID}/animal_humpback_whale/")


def test_recorded_job_spends_against_a_clean_cap(tmp_path: Path) -> None:
    """#180.1: $0.29 spent still allows the next $0.20 under a $1.50 cap."""
    from builder.ledger import Caps, FileLedger
    from builder.replay import run_job_id

    ledger = FileLedger(tmp_path / "ledger.json")
    caps = Caps(job_usd=1.50, day_usd=20.0, total_usd=80.0)
    stale = ledger.reserve("replay/animal_humpback_whale", 0.61, caps)
    ledger.settle(stale, 0.61)
    run = run_job_id("animal_humpback_whale")
    spent = ledger.reserve(run, 0.29, caps)
    ledger.settle(spent, 0.29)
    receipt = ledger.reserve(run, 0.20, caps)
    ledger.settle(receipt, 0.20)
    assert ledger.job_spent(run) == pytest.approx(0.49)
