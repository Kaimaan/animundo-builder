"""One row per tile event, never rewritten (#165).

Every make, remake, blocked or left-out lands a row: the tile,
the event, when, whose job, what kind of run, whose builder and
world, the group version after the write, the reason, and the
cost when there is one. No player data: there is none in a job.
A failed history write is printed and never fails the tile.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def commit(path: Path | None) -> str:
    """The checkout's short commit, or "unknown" without git."""
    if path is None:
        return "unknown"
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=path,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    except OSError:
        return "unknown"
    return out or "unknown"


BUILDER_COMMIT = commit(Path(__file__).resolve().parent.parent)
"""The builder release that records: its own short commit."""


def row(
    tile: str,
    event: str,
    *,
    job: str | None,
    kind: str,
    world: str,
    version: int | None,
    reason: str | None = None,
    cost_usd: float | None = None,
    maker: str = BUILDER_COMMIT,
    at: str | None = None,
) -> dict[str, Any]:
    """One history row in the shape the console reads (#165.1)."""
    entry: dict[str, Any] = {
        "tile": tile,
        "event": event,
        "at": at or datetime.now(UTC).isoformat(),
        "job": job,
        "kind": kind,
        "maker": maker,
        "world": world,
        "version": version,
    }
    if reason is not None:
        entry["reason"] = reason
    if cost_usd is not None:
        entry["cost_usd"] = cost_usd
    return entry


def record(store: Any, entry: dict[str, Any]) -> str | None:
    """The row to `tile_history`; a failure prints, never raises."""
    try:
        return str(store.add_tile_history(entry))
    except Exception as error:  # noqa: BLE001 - history never fails the tile
        print(f"warning: tile history not recorded: {error}", file=sys.stderr)
        return None


def record_local(path: Path, entry: dict[str, Any]) -> None:
    """The row appended to a local jsonl; a failure prints, never raises."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except Exception as error:  # noqa: BLE001 - history never fails the tile
        print(
            f"warning: tile history not recorded in {path}: {error}",
            file=sys.stderr,
        )
