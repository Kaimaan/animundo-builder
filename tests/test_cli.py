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


def test_unbuilt_commands_say_so(tmp_path: Path, capsys) -> None:
    assert main(["worker", "--world", str(tmp_path)]) == NOT_BUILT_YET
    assert "not built yet" in capsys.readouterr().err
    assert main(["eval"]) == NOT_BUILT_YET


def test_worker_listen_calls_listen(tmp_path: Path, monkeypatch) -> None:
    import builder.worker

    seen: dict[str, object] = {}

    def fake_listen(*args: object) -> int:
        seen["args"] = args
        return 0

    monkeypatch.setattr(builder.worker, "listen", fake_listen)
    code = main(["worker", "--listen", "--world", str(tmp_path)])
    assert code == 0
    assert seen["args"][2] == tmp_path


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


def test_entry_point_runs() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "builder.cli", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "stage" in proc.stdout and "check" in proc.stdout


def _job(tmp_path: Path) -> Path:
    job_dir = tmp_path / "jobs" / "job-1"
    job_dir.mkdir(parents=True)
    (job_dir / "state.json").write_text(
        json.dumps(
            {
                "params": {
                    "world": str(tmp_path),
                    "author": "Animundo Builder",
                },
                "completed": [],
                "results": {},
                "failed": None,
            }
        )
    )
    return job_dir


def test_stage_unknown_name(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    job_dir = _job(tmp_path)
    assert main(["stage", "dragon", "--job", str(job_dir)]) == 1
    assert "no stage 'dragon'" in capsys.readouterr().err


def test_stage_missing_job(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["stage", "research", "--job", str(tmp_path / "nope")]) == 1
    assert "no such job" in capsys.readouterr().err


def test_stage_record_and_replay_conflict(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    job_dir = _job(tmp_path)
    code = main(["stage", "research", "--job", str(job_dir), "--record", "--replay"])
    assert code == 1
    assert "do not mix" in capsys.readouterr().err


def test_stage_replay_needs_records(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    job_dir = _job(tmp_path)
    code = main(["stage", "research", "--job", str(job_dir), "--replay"])
    assert code == 1
    assert "no records/" in capsys.readouterr().err
