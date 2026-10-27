"""Species identity through GBIF, the same way as animals (#51.4).

A food or a surrounding is known by the GBIF ID of its Latin
name, resolved with the world's `tools/taxon.py` (free, live),
never by matching English names ("blueberry" is not our
bilberry, and a field vole is not our bank vole).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from .animal_list import match_by_name


def taxon_block(gbif_key: int, latin: str) -> dict[str, Any]:
    """A plan-shaped GBIF block from a key the list already gave."""
    rank = "SUBSPECIES" if len(latin.split()) > 2 else "SPECIES"
    return {"gbif_key": gbif_key, "rank": rank, "name": latin}


def match_entry(
    entries: list[dict[str, Any]],
    world: Path,
    food: dict[str, Any],
    plant: bool = False,
) -> tuple[dict[str, Any] | None, int | None]:
    """The list entry for a researched food, and its GBIF key.

    By species key when the research names the Latin and GBIF
    resolves it; by English name otherwise (old answers, odd
    names). A resolved key that is on no entry stays unmatched:
    a same-named other species must never stand in.
    """
    latin = food.get("latin")
    if latin:
        taxon = resolve(world, latin, plant=plant)
        if taxon is not None:
            key = taxon["gbif_key"]
            for entry in entries:
                if entry.get("gbif_key") == key:
                    return entry, key
            return None, key
    match = match_by_name(entries, food["name"])
    if match is None:
        return None, None
    key = match.get("gbif_key")
    return match, key if isinstance(key, int) else None


def resolve(world: Path, latin: str, plant: bool = False) -> dict[str, Any] | None:
    """The GBIF taxon for one Latin name, or None when GBIF says no.

    Only an exact accepted match counts; anything else (a fuzzy
    match, genus level, no match, no network) resolves to None and
    the caller falls back to the English name.
    """
    tool = world / "tools" / "taxon.py"
    if not tool.is_file():
        return None
    args = [sys.executable, str(tool)]
    if plant:
        args.append("--plant")
    args.append(latin)
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        taxon = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(taxon, dict) or not isinstance(taxon.get("gbif_key"), int):
        return None
    return taxon
