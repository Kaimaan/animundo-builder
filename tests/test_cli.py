"""Tests for the builder command line skeleton."""

from __future__ import annotations

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
    assert main(["build", "--area", "x", "--world", str(tmp_path)]) == NOT_BUILT_YET
    assert "not built yet" in capsys.readouterr().err
    assert main(["resume", "job-1"]) == NOT_BUILT_YET
    assert main(["worker"]) == NOT_BUILT_YET
    assert main(["eval"]) == NOT_BUILT_YET


def test_entry_point_runs() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "builder.cli", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "build" in proc.stdout and "check" in proc.stdout
