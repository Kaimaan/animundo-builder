"""Read the world checkout; run its checkers against job files.

Checkers validate a whole checkout, but new files live in the job folder
until the pull request. The overlay is a temporary directory that looks
like the checkout (symlinks, nothing copied) with the job's files laid
over it. The real checkout is never written.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def overlay(world: Path, files: dict[str, str]) -> Iterator[Path]:
    """A checkout view with `files` (relative path -> content) laid over it."""
    world = world.resolve()
    dest = Path(os.environ.get("TMPDIR", "/tmp")) / f"animundo-overlay-{os.getpid()}"
    if dest.exists():
        shutil.rmtree(dest)
    for root, dirs, names in os.walk(world):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        rel = Path(root).relative_to(world)
        (dest / rel).mkdir(parents=True, exist_ok=True)
        for name in names:
            if name.startswith("."):
                continue
            os.symlink(Path(root) / name, dest / rel / name)
    for relpath, content in files.items():
        target = dest / relpath
        if target.is_symlink() or target.exists():
            target.unlink()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    try:
        yield dest
    finally:
        shutil.rmtree(dest, ignore_errors=True)


def run_checker(
    world: Path, script: str, *args: str, timeout: int = 300
) -> subprocess.CompletedProcess[str]:
    """Run one of the checkout's tools, capturing its output."""
    return subprocess.run(
        [sys.executable, str(world / "tools" / script), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
