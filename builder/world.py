"""Run the world's draft checker against job files.

The world owns checking: `tools/check_draft.py` layers the job's new
files over a checkout copy and returns one JSON verdict per check.
This module only assembles the draft folder from the job, runs the
tool, saves its JSON for the build report, and returns it parsed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

DRAFT_TIMEOUT = 900


class DraftError(Exception):
    """The draft couldn't be checked. The job stops with the reason."""


def check_draft(
    world: Path,
    job_dir: Path,
    name: str,
    extra: dict[str, Path] | None = None,
) -> dict[str, Any]:
    """Check the job's new files; save the JSON verdict; return it parsed.

    Assembles `draft/` from the job (regions, animals, models, built
    models, locale), runs the world's checker against it, and writes
    the verdict to `checks/<name>.json`. `extra` lays files from
    elsewhere (a build sandbox) over the draft, winning on clash. A
    failed check is data, not an error; an unreadable draft or broken
    tool output raises.
    """
    draft = job_dir / "draft"
    if draft.exists():
        shutil.rmtree(draft)
    _assemble(job_dir, draft, extra or {})
    tool = world / "tools" / "check_draft.py"
    if not tool.is_file():
        raise DraftError(f"no draft checker in this checkout: {tool}")
    try:
        proc = subprocess.run(
            [sys.executable, str(tool), str(draft), "--world", str(world)],
            capture_output=True,
            text=True,
            check=False,
            timeout=DRAFT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DraftError(f"check_draft.py did not run: {error}") from error
    if proc.returncode == 2:
        raise DraftError(f"the draft can't be read: {_short(proc)}")
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as error:
        raise DraftError(f"check_draft.py printed no JSON: {_short(proc)}") from error
    if not isinstance(result, dict) or "results" not in result:
        raise DraftError(f"check_draft.py printed no JSON: {_short(proc)}")
    out = job_dir / "checks" / f"{name}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def _assemble(job_dir: Path, draft: Path, extra: dict[str, Path]) -> None:
    """Lay the job's new files out like the world repository."""
    pairs: list[tuple[Path, Path]] = []
    pairs += [
        (path, Path("regions") / path.name)
        for path in (job_dir / "regions").glob("*.json")
    ]
    pairs += [
        (path, Path("animals") / path.name)
        for path in (job_dir / "animals").glob("*.json")
        if path.name != "progress.json"
    ]
    pairs += [
        (path, Path("models") / path.name)
        for path in (job_dir / "models").glob("*.json")
    ]
    plants = job_dir / "models" / "plants"
    if plants.is_dir():
        pairs += [
            (path, Path("models/plants") / path.name) for path in plants.glob("*.json")
        ]
        pairs += [
            (path, Path("assets/plants") / path.name) for path in plants.glob("*.glb")
        ]
    pairs += [
        (path, Path("assets/animals") / path.name)
        for path in (job_dir / "models").glob("*.glb")
    ]
    locale = job_dir / "locale"
    if locale.is_dir():
        pairs += [
            (path, Path("locale") / path.relative_to(locale))
            for path in sorted(locale.rglob("*.json"))
        ]
    for src, rel in pairs:
        dest = draft / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    for rel, src in extra.items():
        dest = draft / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def _short(proc: subprocess.CompletedProcess[str]) -> str:
    """The tool's own error line, or the tail of whatever it printed."""
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        data = {}
    if isinstance(data, dict) and data.get("error"):
        return str(data["error"])
    tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
    return "; ".join(tail) if tail else f"exit {proc.returncode}"


def mentions(text: str, species_id: str) -> bool:
    """The text names this species: prefix, error tag, quote or word."""
    return (
        text.startswith(f"{species_id}:")
        or f"ERROR: {species_id}" in text
        or f'"{species_id}"' in text
        or re.search(rf"\b{re.escape(species_id)}\b", text) is not None
    )


def problems_by_file(problems: list[str]) -> dict[str | None, list[str]]:
    """Group tool problems under their FAIL file header, if any.

    Detail lines attach to the latest header; ERROR lines are
    self-contained (they name their own owner) and stay ungrouped.
    """
    grouped: dict[str | None, list[str]] = {}
    current: str | None = None
    for line in problems:
        if line.startswith("FAIL "):
            current = line[5:].strip()
            grouped.setdefault(current, [])
        elif line.startswith("ERROR"):
            grouped.setdefault(None, []).append(line)
        else:
            grouped.setdefault(current, []).append(line)
    return grouped
