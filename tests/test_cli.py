"""Tests for the builder command line."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from builder.cli import NOT_BUILT_YET, main


def test_check_accepts_a_directory(tmp_path: Path, capsys) -> None:
    assert main(["check", "--world", str(tmp_path)]) == 0
    assert "checkers: none yet" in capsys.readouterr().out


def test_check_rejects_a_missing_directory(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "no-such-world"
    assert main(["check", "--world", str(missing)]) == 1
    assert "not a directory" in capsys.readouterr().err


def test_check_needs_world() -> None:
    with pytest.raises(SystemExit):
        main(["check"])


def test_unbuilt_commands_say_so(capsys) -> None:
    assert main(["worker"]) == NOT_BUILT_YET
    assert "not built yet" in capsys.readouterr().err
    assert main(["eval"]) == NOT_BUILT_YET


def test_build_stops_cleanly_without_a_key(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    code = main(["build", "--area", "gb_cairngorms", "--world", str(tmp_path)])
    assert code == NOT_BUILT_YET
    err = capsys.readouterr().err
    assert "MODEL_API_KEY is not set" in err
    (job_dir,) = (tmp_path / "jobs").iterdir()
    area = json.loads((job_dir / "area.json").read_text(encoding="utf-8"))
    assert area["area_id"] == "gb_cairngorms"
    state = json.loads((job_dir / "state.json").read_text(encoding="utf-8"))
    assert state["completed"] == ["intake"]


def test_build_by_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    code = main(
        ["build", "--name", "Cairngorms", "--country", "GB", "--world", str(tmp_path)]
    )
    assert code == NOT_BUILT_YET
    (job_dir,) = (tmp_path / "jobs").iterdir()
    area = json.loads((job_dir / "area.json").read_text(encoding="utf-8"))
    assert area["area_id"] == "gb_cairngorms"


def test_build_dry_run_writes_nothing(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    code = main(
        ["build", "--area", "gb_cairngorms", "--world", str(tmp_path), "--dry-run"]
    )
    assert code == 0
    assert "wrote nothing" in capsys.readouterr().out
    assert not (tmp_path / "jobs").exists()


def test_build_unknown_area_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["build", "--area", "xx_nowhere", "--world", str(tmp_path)]) == 1
    assert "unknown area" in capsys.readouterr().err


def test_build_needs_area_or_name(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["build", "--world", str(tmp_path)]) == 1
    assert "--area or --name" in capsys.readouterr().err


def test_build_needs_world(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    missing = tmp_path / "no-such-world"
    assert main(["build", "--area", "gb_cairngorms", "--world", str(missing)]) == 1
    assert "not a directory" in capsys.readouterr().err


def test_resume_carries_on_without_redoing_intake(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    assert main(["build", "--area", "gb_cairngorms", "--world", str(tmp_path)]) == (
        NOT_BUILT_YET
    )
    (job_dir,) = (tmp_path / "jobs").iterdir()
    before = (job_dir / "area.json").read_bytes()
    capsys.readouterr()
    assert main(["resume", job_dir.name]) == NOT_BUILT_YET
    assert (job_dir / "area.json").read_bytes() == before
    state = json.loads((job_dir / "state.json").read_text(encoding="utf-8"))
    assert state["completed"] == ["intake"]


def test_resume_unknown_job_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["resume", "no-such-job"]) == 1
    assert "no such job" in capsys.readouterr().err


def test_entry_point_runs() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "builder.cli", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "build" in proc.stdout and "check" in proc.stdout


def test_build_submit_says_not_built(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    code = main(
        [
            "build",
            "--area",
            "gb_cairngorms",
            "--world",
            str(tmp_path),
            "--kind",
            "submit",
        ]
    )
    assert code == NOT_BUILT_YET
    assert "not built yet" in capsys.readouterr().err


def test_build_improve_points_at_resume(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    code = main(
        [
            "build",
            "--area",
            "gb_cairngorms",
            "--world",
            str(tmp_path),
            "--kind",
            "improve",
        ]
    )
    assert code == 1
    assert "resume <job_id>" in capsys.readouterr().err


def test_resume_improve_needs_built_stages(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    assert main(["build", "--area", "gb_cairngorms", "--world", str(tmp_path)]) == (
        NOT_BUILT_YET
    )
    (job_dir,) = (tmp_path / "jobs").iterdir()
    capsys.readouterr()
    code = main(
        ["resume", job_dir.name, "--kind", "improve", "--choices", "colours_off"]
    )
    assert code == NOT_BUILT_YET
    assert "needs pack, not built yet" in capsys.readouterr().err


def test_resume_improve_validates_choices(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    assert main(["build", "--area", "gb_cairngorms", "--world", str(tmp_path)]) == (
        NOT_BUILT_YET
    )
    (job_dir,) = (tmp_path / "jobs").iterdir()
    capsys.readouterr()
    assert main(["resume", job_dir.name, "--kind", "improve"]) == 1
    assert "--choices" in capsys.readouterr().err
    assert (
        main(["resume", job_dir.name, "--kind", "improve", "--choices", "dragons"]) == 1
    )
    assert "unknown improve choice" in capsys.readouterr().err


def test_report_missing_job(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["report", "no-such-job"]) == 1
    assert "no such job" in capsys.readouterr().err


def test_report_writes_html(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    job_dir = tmp_path / "jobs" / "job-1"
    job_dir.mkdir(parents=True)
    (job_dir / "state.json").write_text(
        json.dumps({"params": {}, "completed": [], "failed": None})
    )
    assert main(["report", "job-1"]) == 0
    assert "report.html" in capsys.readouterr().out
    assert (job_dir / "report.html").is_file()
