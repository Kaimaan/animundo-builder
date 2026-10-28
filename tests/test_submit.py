"""builder submit: branch layout, gate, PR body, link back. No network."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from builder.cli import main
from builder.submit import SubmitFailed, run_submit

PASS_STUB = (
    "import json; print(json.dumps({'ok': True, 'results': ["
    "{'check': 'locale', 'status': 'pass', 'problems': []},"
    "{'check': 'schema', 'status': 'pass', 'problems': []}]}))\n"
)

FAIL_STUB = (
    "import json; print(json.dumps({'ok': False, 'results': ["
    "{'check': 'locale', 'status': 'fail', 'problems': ['bad key']}]}))\n"
)


def _git(world: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=True,
        cwd=world,
        timeout=60,
    )


def _world(tmp_path: Path, stub: str = PASS_STUB) -> Path:
    world = tmp_path / "world"
    world.mkdir()
    _git(world, "init", "-b", "main")
    _git(world, "config", "user.email", "test@example.org")
    _git(world, "config", "user.name", "Test")
    (world / "tools").mkdir()
    (world / "tools" / "check_draft.py").write_text(stub)
    (world / "locale" / "fi").mkdir(parents=True)
    (world / "locale" / "fi" / "animals.json").write_text(
        json.dumps({"animal.moose.title": "Hirvi"})
    )
    (world / "locale" / "fi" / "regions.json").write_text(json.dumps({}))
    _git(world, "add", "-A")
    _git(world, "commit", "-m", "base")
    return world


def _gh(tmp_path: Path, name: str = "gh") -> Path:
    script = tmp_path / "bin" / name
    script.parent.mkdir(exist_ok=True)
    script.write_text(
        "#!/bin/sh\necho https://github.com/o/r/pull/1\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _job(tmp_path: Path) -> Path:
    job = tmp_path / "jobs" / "draft-1"
    (job / "regions").mkdir(parents=True)
    (job / "regions" / "xx_test.json").write_text(
        json.dumps(
            {
                "id": "xx_test",
                "name": "Test Meadow",
                "country": "FI",
                "sources": ["https://example.org/meadow"],
            }
        )
    )
    (job / "animals").mkdir()
    (job / "animals" / "red_deer.json").write_text(
        json.dumps(
            {
                "id": "red_deer",
                "name": "Red deer",
                "sources": ["https://example.org/deer"],
            }
        )
    )
    (job / "models").mkdir()
    (job / "models" / "red_deer.json").write_text(json.dumps({"id": "red_deer"}))
    (job / "models" / "red_deer.glb").write_bytes(b"GLB")
    (job / "models" / "red_deer.png").write_bytes(b"PNG")
    (job / "models" / "plants").mkdir()
    (job / "models" / "plants" / "wood_sorrel.json").write_text(
        json.dumps({"id": "wood_sorrel", "sources": ["https://example.org/sorrel"]})
    )
    (job / "models" / "plants" / "wood_sorrel.glb").write_bytes(b"GLB")
    (job / "locale" / "fi").mkdir(parents=True)
    (job / "locale" / "fi" / "animals.json").write_text(
        json.dumps({"animal.red_deer.title": "Saksanhirvi"})
    )
    (job / "reviews").mkdir()
    (job / "reviews" / "red_deer-1.json").write_text(
        json.dumps({"species": "red_deer", "round": 1, "issues": []})
    )
    (job / "plants").mkdir()
    (job / "plants" / "progress.json").write_text(
        json.dumps({"rounds": {}, "passed": [], "reused": {}, "expert": []})
    )
    (job / "feedback.json").write_text(
        json.dumps({"note": "Grown-up note: my child loves deer"})
    )
    (job / "pack").mkdir()
    (job / "pack" / "manifest.json").write_text(
        json.dumps(
            {
                "draft_id": "draft-1",
                "draft_place_id": "draft_draft-1",
                "place_id": "xx_test",
                "version": 2,
                "format": 1,
                "files": [
                    {
                        "path": "models/red_deer.glb",
                        "sha256": "x",
                        "bytes": 3,
                    },
                    {
                        "path": "models/plants/wood_sorrel.glb",
                        "sha256": "x",
                        "bytes": 3,
                    },
                ],
                "new_species": ["red_deer"],
                "reused_species": [],
            }
        )
    )
    return job


def _branches(world: Path) -> list[str]:
    out = _git(world, "branch", "--list", "builder/*")
    return [line.strip().lstrip("* ") for line in out.stdout.splitlines()]


def test_submit_lays_out_branch_and_opens_pr(tmp_path: Path) -> None:
    world = _world(tmp_path)
    job = _job(tmp_path)
    record = run_submit(
        "draft-1",
        tmp_path / "jobs",
        world,
        place_id="fi_test",
        gh=str(_gh(tmp_path)),
    )
    assert record["pr_url"] == "https://github.com/o/r/pull/1"
    assert record["branch"] == "builder/draft-1"
    assert _branches(world) == ["builder/draft-1"]
    region = json.loads((world / "regions" / "fi_test.json").read_text())
    assert region["id"] == "fi_test"
    assert region["name"] == "Test Meadow"
    assert (world / "animals" / "red_deer.json").is_file()
    assert (world / "models" / "red_deer.json").is_file()
    assert not (world / "assets" / "animals" / "red_deer.glb").exists()
    assert (world / "models" / "previews" / "red_deer.png").is_file()
    assert (world / "models" / "plants" / "wood_sorrel.json").is_file()
    assert not (world / "assets" / "plants" / "wood_sorrel.glb").exists()
    assert not (world / "models" / "previews" / "plants" / "wood_sorrel.png").exists()
    merged = json.loads((world / "locale" / "fi" / "animals.json").read_text())
    assert merged == {
        "animal.moose.title": "Hirvi",
        "animal.red_deer.title": "Saksanhirvi",
    }
    log = _git(world, "log", "--format=%B", "-1")
    assert "Add Test Meadow (fi_test) via builder submit" in log.stdout
    assert "Signed-off-by:" in log.stdout
    saved = json.loads((job / "submit.json").read_text())
    assert saved["pr_url"] == "https://github.com/o/r/pull/1"
    assert (job / "checks" / "submit.json").is_file()


def test_submit_update_rewrites_the_built_region(tmp_path: Path) -> None:
    world = _world(tmp_path)
    (world / "regions").mkdir()
    (world / "regions" / "xx_test.json").write_text(
        json.dumps({"id": "xx_test", "name": "Test Meadow"})
    )
    _git(world, "add", "-A")
    _git(world, "commit", "-m", "the built place")
    job = _job(tmp_path)
    (job / "home.json").write_text(json.dumps({"reuse_place_id": "xx_test"}))
    record = run_submit("draft-1", tmp_path / "jobs", world, gh=str(_gh(tmp_path)))
    assert record["place_id"] == "xx_test"
    region = json.loads((world / "regions" / "xx_test.json").read_text())
    assert region["id"] == "xx_test"
    assert region["country"] == "FI"
    log = _git(world, "log", "--format=%B", "-1")
    assert "Update Test Meadow (xx_test) via builder submit" in log.stdout
    body = (job / "pr.md").read_text()
    assert "# Update place: Test Meadow (xx_test)" in body
    assert "Updated for a family in " in body


def test_submit_update_refuses_a_gone_region(tmp_path: Path) -> None:
    world = _world(tmp_path)
    job = _job(tmp_path)
    (job / "home.json").write_text(json.dumps({"reuse_place_id": "xx_test"}))
    with pytest.raises(SubmitFailed, match="is gone from the world"):
        run_submit("draft-1", tmp_path / "jobs", world, gh=str(_gh(tmp_path)))


def test_submit_pr_body_is_complete_and_has_no_family_note(tmp_path: Path) -> None:
    world = _world(tmp_path)
    job = _job(tmp_path)
    run_submit("draft-1", tmp_path / "jobs", world, gh=str(_gh(tmp_path)))
    body = (job / "pr.md").read_text()
    assert "# New place: Test Meadow (xx_test)" in body
    assert "for a family" in body
    assert "https://example.org/meadow" in body
    assert "https://example.org/deer" in body
    assert "https://example.org/sorrel" in body
    assert "locale: pass" in body
    assert "red_deer" in body and "wood_sorrel" in body
    assert "![red_deer preview](models/previews/red_deer.png)" in body
    assert "submitted after 1 improvement round" in body
    assert "final review (round 1)" in body
    assert "Nothing is waiting for an expert." in body
    assert "my child loves deer" not in body
    assert "Grown-up note" not in body
    assert "## Build report" in body
    assert "- Total: $0.000 over 0 calls." in body
    assert (job / "report.html").is_file()


def test_submit_home_with_nothing_new_says_so(tmp_path: Path) -> None:
    world = _world(tmp_path)
    job = _job(tmp_path)
    manifest = json.loads((job / "pack" / "manifest.json").read_text())
    manifest["new_species"] = []
    manifest["files"] = [
        entry
        for entry in manifest["files"]
        if not entry["path"].startswith("models/plants/")
    ]
    manifest["reused_species"] = ["fox", "vole"]
    (job / "pack" / "manifest.json").write_text(json.dumps(manifest))
    run_submit("draft-1", tmp_path / "jobs", world, gh=str(_gh(tmp_path)))
    body = (job / "pr.md").read_text()
    assert "- Nothing new: this place reuses the library." in body
    assert "## Build report" in body


def test_submit_expert_flags_and_reviews_reach_the_body(tmp_path: Path) -> None:
    world = _world(tmp_path)
    job = _job(tmp_path)
    (job / "plants" / "progress.json").write_text(
        json.dumps({"rounds": {}, "passed": [], "reused": {}, "expert": ["fungus_x"]})
    )
    (job / "reviews" / "red_deer-1.json").write_text(
        json.dumps(
            {
                "species": "red_deer",
                "round": 1,
                "issues": [
                    {
                        "part": "tail",
                        "problem": "tail floats",
                        "severity": "blocking",
                        "fix": "reattach",
                    }
                ],
            }
        )
    )
    (job / "models" / "APPROVED.txt").write_text("Round 2, approved by Kai.\n")
    run_submit("draft-1", tmp_path / "jobs", world, gh=str(_gh(tmp_path)))
    body = (job / "pr.md").read_text()
    assert "tail floats" in body and "Fix: reattach" in body
    assert "approved by Kai" in body
    assert "fungus_x: needs an expert look." in body


def test_submit_final_review_prefers_verified_and_counts_rewrites(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    job = _job(tmp_path)
    (job / "reviews" / "red_deer-2-unverified.json").write_text(
        json.dumps({"species": "red_deer", "round": 2, "issues": []})
    )
    (job / "reviews" / "red_deer-2.json").write_text(
        json.dumps(
            {
                "species": "red_deer",
                "round": 2,
                "issues": [{"problem": "verified note", "severity": "note"}],
            }
        )
    )
    (job / "reviews" / "red_deer-10.json").write_text(
        json.dumps({"species": "red_deer", "round": 10, "issues": []})
    )
    (job / "checks").mkdir()
    (job / "checks" / "safety.json").write_text(
        json.dumps({"reviewed": 3, "verdicts": [], "rewritten": ["a", "b"]})
    )
    run_submit("draft-1", tmp_path / "jobs", world, gh=str(_gh(tmp_path)))
    body = (job / "pr.md").read_text()
    assert "final review (round 10)" in body
    assert "verified note" not in body
    assert "rewrote 2 texts" in body


def test_submit_refuses_a_changed_shipped_text(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _job(tmp_path)
    (world / "locale" / "fi" / "animals.json").write_text(
        json.dumps({"animal.red_deer.title": "Hirvi?"})
    )
    _git(world, "commit", "-am", "shipped text")
    with pytest.raises(SubmitFailed, match="would change shipped text"):
        run_submit("draft-1", tmp_path / "jobs", world, gh=str(_gh(tmp_path)))
    assert _branches(world) == []
    assert _git(world, "status", "--porcelain").stdout == ""


def test_submit_gate_failure_removes_the_branch(tmp_path: Path) -> None:
    world = _world(tmp_path, FAIL_STUB)
    job = _job(tmp_path)
    with pytest.raises(SubmitFailed, match="submit gate failed on locale"):
        run_submit("draft-1", tmp_path / "jobs", world, gh=str(_gh(tmp_path)))
    assert _branches(world) == []
    assert _git(world, "status", "--porcelain").stdout == ""
    assert (job / "checks" / "submit.json").is_file()


def test_submit_refuses_a_pack_for_another_draft(tmp_path: Path) -> None:
    world = _world(tmp_path)
    job = _job(tmp_path)
    (tmp_path / "jobs" / "other" / "pack").mkdir(parents=True)
    (job / "pack" / "manifest.json").rename(
        tmp_path / "jobs" / "other" / "pack" / "manifest.json"
    )
    with pytest.raises(SubmitFailed, match="the pack is for draft-1"):
        run_submit("other", tmp_path / "jobs", world, gh=str(_gh(tmp_path)))


def test_submit_needs_a_pack(tmp_path: Path) -> None:
    world = _world(tmp_path)
    job = _job(tmp_path)
    (job / "pack" / "manifest.json").unlink()
    with pytest.raises(SubmitFailed, match="no pack"):
        run_submit("draft-1", tmp_path / "jobs", world, gh=str(_gh(tmp_path)))


def test_submit_refuses_a_dirty_world(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _job(tmp_path)
    (world / "locale" / "fi" / "animals.json").write_text("{}")
    with pytest.raises(SubmitFailed, match="uncommitted changes"):
        run_submit("draft-1", tmp_path / "jobs", world, gh=str(_gh(tmp_path)))


def test_submit_continues_after_gh_fails(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _job(tmp_path)
    failing = tmp_path / "bin" / "gh-fail"
    failing.parent.mkdir(exist_ok=True)
    failing.write_text("#!/bin/sh\necho denied >&2\nexit 1\n", encoding="utf-8")
    failing.chmod(failing.stat().st_mode | stat.S_IEXEC)
    with pytest.raises(SubmitFailed, match="gh refused"):
        run_submit("draft-1", tmp_path / "jobs", world, gh=str(failing))
    assert _branches(world) == ["builder/draft-1"]
    record = run_submit("draft-1", tmp_path / "jobs", world, gh=str(_gh(tmp_path)))
    assert record["pr_url"] == "https://github.com/o/r/pull/1"
    count = _git(world, "rev-list", "--count", "main..builder/draft-1")
    assert count.stdout.strip() == "1"


def test_submit_refuses_a_second_pr(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _job(tmp_path)
    run_submit("draft-1", tmp_path / "jobs", world, gh=str(_gh(tmp_path)))
    with pytest.raises(SubmitFailed, match="already submitted"):
        run_submit("draft-1", tmp_path / "jobs", world, gh=str(_gh(tmp_path)))


def test_submit_command_prints_the_url(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    world = _world(tmp_path)
    _job(tmp_path)
    gh = _gh(tmp_path)
    monkeypatch.setenv("PATH", f"{gh.parent}{os.pathsep}{os.environ['PATH']}")
    code = main(
        [
            "submit",
            "draft-1",
            "--world",
            str(world),
            "--jobs-dir",
            str(tmp_path / "jobs"),
        ]
    )
    assert code == 0
    assert "https://github.com/o/r/pull/1" in capsys.readouterr().out
