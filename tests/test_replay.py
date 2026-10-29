"""Local request runs: replay a recording keyless, record one live. No key."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from builder.records import ReplayError
from builder.replay import replay_request


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


def test_food_request_replays_with_no_records(tmp_path: Path) -> None:
    """#T39: a code-only request replays end to end, keyless."""
    import fake_world

    world = fake_world.make_world(tmp_path)
    grid_dir = fake_world.write_grids(tmp_path / "grids", fake_world.boreal_3x3())
    plan = _plan(
        "n_413_478",
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
    reports = replay_request("n_413_478", recording, out, world, grid_dir)
    assert list(reports) == ["food_berries"]
    assert reports["food_berries"].is_file()
    assert (out / "replay" / "plan.json").is_file()
    assert (out / "replay" / "food_berries" / "report.html").is_file()


def test_replay_refuses_another_tiles_recording(tmp_path: Path) -> None:
    """#T39: the recording's tile must be the requested one."""
    import fake_world

    world = fake_world.make_world(tmp_path)
    grid_dir = fake_world.write_grids(tmp_path / "grids", fake_world.boreal_3x3())
    recording = _recording(tmp_path / "recording", _plan("n_413_478", []))
    with pytest.raises(ValueError, match="not n_413_479"):
        replay_request("n_413_479", recording, tmp_path / "out", world, grid_dir)


def test_replay_refuses_a_job_id(tmp_path: Path) -> None:
    """#T39: `--replay` takes a tile, not a job id."""
    import fake_world

    world = fake_world.make_world(tmp_path)
    grid_dir = fake_world.write_grids(tmp_path / "grids", fake_world.boreal_3x3())
    recording = _recording(tmp_path / "recording", _plan("n_413_478", []))
    with pytest.raises(ValueError, match="not a tile id"):
        replay_request("j1", recording, tmp_path / "out", world, grid_dir)


def test_replay_needs_answers_for_model_items(tmp_path: Path) -> None:
    """#T39: an animal item without records stops loudly."""
    import fake_world

    world = fake_world.make_world(tmp_path)
    grid_dir = fake_world.write_grids(tmp_path / "grids", fake_world.boreal_3x3())
    plan = _plan(
        "n_413_478",
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
        replay_request("n_413_478", recording, tmp_path / "out", world, grid_dir)


def test_replay_runs_out_of_answers_loudly(tmp_path: Path) -> None:
    """#T39: the pipeline plays the recording, nothing else."""
    import fake_world

    world = fake_world.make_world(tmp_path)
    grid_dir = fake_world.write_grids(tmp_path / "grids", fake_world.boreal_3x3())
    plan = _plan(
        "n_413_478",
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
        replay_request("n_413_478", recording, tmp_path / "out", world, grid_dir)
