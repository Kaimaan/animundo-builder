"""Draft checking: assemble the job's files, run the world's tool."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from builder.world import DraftError, check_draft, problems_by_file


def _world(tmp_path: Path, script: str) -> Path:
    world = tmp_path / "world"
    (world / "tools").mkdir(parents=True)
    (world / "tools" / "check_draft.py").write_text(script)
    return world


def _job(tmp_path: Path) -> Path:
    job = tmp_path / "job"
    (job / "regions").mkdir(parents=True)
    (job / "regions" / "xx_test.json").write_text("{}")
    (job / "animals").mkdir(parents=True)
    (job / "animals" / "zz_new.json").write_text("{}")
    (job / "animals" / "progress.json").write_text("{}")
    (job / "models").mkdir(parents=True)
    (job / "models" / "zz_new.json").write_text("{}")
    (job / "models" / "zz_new.glb").write_bytes(b"glb-bytes")
    (job / "models" / "zz_new.png").write_bytes(b"png-bytes")
    return job


def test_assembles_the_draft_layout(tmp_path: Path) -> None:
    world = _world(
        tmp_path,
        "import json, os, sys\n"
        "files = sorted(os.path.relpath(os.path.join(r, f), sys.argv[1])\n"
        "                 for r, _, fs in os.walk(sys.argv[1]) for f in fs)\n"
        "print(json.dumps({'ok': True, 'draft_files': files, 'results': []}))\n",
    )
    result = check_draft(world, _job(tmp_path), "region")
    assert result["draft_files"] == [
        "animals/zz_new.json",
        "assets/animals/zz_new.glb",
        "models/zz_new.json",
        "regions/xx_test.json",
    ]


def test_extra_files_win_over_job(tmp_path: Path) -> None:
    world = _world(
        tmp_path,
        "import json, sys\n"
        "text = open(sys.argv[1] + '/models/zz_new.json').read()\n"
        "print(json.dumps({'ok': True, 'results': [], 'seen': text}))\n",
    )
    job = _job(tmp_path)
    overlay = tmp_path / "round-spec.json"
    overlay.write_text('{"id": "zz_new"}')
    result = check_draft(world, job, "models", extra={"models/zz_new.json": overlay})
    assert result["seen"] == '{"id": "zz_new"}'


def test_runs_tool_saves_json_returns_it(tmp_path: Path) -> None:
    result = {"ok": True, "draft_files": ["regions/xx_test.json"], "results": []}
    world = _world(tmp_path, f"import json;print(json.dumps({result!r}))")
    job = _job(tmp_path)
    assert check_draft(world, job, "region") == result
    saved = json.loads((job / "checks" / "region.json").read_text())
    assert saved == result


def test_passes_draft_and_world(tmp_path: Path) -> None:
    world = _world(
        tmp_path,
        "import json, sys\n"
        "open(sys.argv[0] + '.argv', 'w').write(json.dumps(sys.argv[1:]))\n"
        "print(json.dumps({'ok': True, 'results': []}))\n",
    )
    job = _job(tmp_path)
    check_draft(world, job, "region")
    argv = json.loads((world / "tools" / "check_draft.py.argv").read_text())
    assert argv[0] == str(job / "draft")
    assert argv[1:] == ["--world", str(world)]


def test_exit_two_is_unreadable(tmp_path: Path) -> None:
    world = _world(
        tmp_path, "import json, sys;print(json.dumps({'ok': False, 'error': 'empty'}))"
    )
    job = tmp_path / "job"
    job.mkdir()
    (job / "regions").mkdir()
    with pytest.raises(DraftError, match="empty"):
        check_draft(world, job, "region")


def test_broken_output_is_an_error(tmp_path: Path) -> None:
    world = _world(tmp_path, "print('not json')")
    with pytest.raises(DraftError, match="no JSON"):
        check_draft(world, _job(tmp_path), "region")


def test_missing_tool_is_an_error(tmp_path: Path) -> None:
    world = tmp_path / "world"
    world.mkdir()
    with pytest.raises(DraftError, match="no draft checker"):
        check_draft(world, _job(tmp_path), "region")


def test_problems_group_under_fail_files() -> None:
    grouped = problems_by_file(
        ["FAIL animals/a.json", "- first", "- second", "ERROR: b: bad"]
    )
    assert grouped == {
        "animals/a.json": ["- first", "- second"],
        None: ["ERROR: b: bad"],
    }
    assert problems_by_file([]) == {}
