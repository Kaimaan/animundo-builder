"""Reference models: where each new species starts, read from the world.

`models/README.md`'s "Reference models: where to start" table names, per
kind of animal, the existing spec to start from and what to change, plus
the kinds that are not possible yet. Stewards extend it after merging, so
the builder parses it from the checkout on every run and never copies it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .prompts import load_prompt
from .world import mentions

TABLE_HEADING = "## Reference models: where to start"

PICK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "row": {"type": "integer", "minimum": 1},
        "kind": {"type": "string", "enum": ["bird", "mammal"]},
    },
    "required": ["row", "kind"],
    "additionalProperties": False,
}

# Cell words too weak to prove a kind ("great" must not match whales).
STOPWORDS = frozenset(
    {
        "and",
        "the",
        "not",
        "yet",
        "over",
        "about",
        "with",
        "from",
        "great",
        "small",
        "other",
        "slow",
        "pairs",
        "that",
        "beat",
        "apart",
    }
)


def read_table(world: Path) -> list[dict[str, str]]:
    """Parse the reference table: `animal`, `start`, `change` per row.

    `start` is empty for the "not possible yet" rows, whose Start cell
    holds `none` instead of a spec name.
    """
    text = (world / "models" / "README.md").read_text(encoding="utf-8")
    start = text.find(TABLE_HEADING)
    if start < 0:
        raise ValueError("the world has no reference-models table; pull it")
    rest = text[start:]
    end = rest.find("\n## ", 1)
    rows = []
    for line in rest[: end if end > 0 else None].splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3 or cells[0].startswith("**"):
            continue
        if set(cells[0]) <= set("-: ") or cells[0].lower().startswith("new animal"):
            continue
        rows.append(
            {
                "animal": cells[0],
                "start": _start_of(cells[1]),
                "change": cells[2].strip(),
            }
        )
    if not rows:
        raise ValueError("the reference-models table has no rows; pull the world")
    return rows


def _start_of(cell: str) -> str:
    """The first spec name in the Start cell, or "" when unbuildable."""
    names = re.findall(r"`([^`]+)`", cell)
    return names[0].strip() if names else ""


def impossible_match(
    rows: list[dict[str, str]], species: dict[str, Any]
) -> dict[str, str] | None:
    """The "not possible yet" row naming this species, if any.

    Matches the survey's own words (id, Latin, description) against the
    row's significant words, singularised like the capability check, so
    "bats" meets `serotine_bat` and "whales" meets a blue whale.
    """
    text = (
        " ".join(
            str(species.get(key, ""))
            for key in ("species", "latin", "description", "evidence")
        )
        .lower()
        .replace("_", " ")
    )
    for row in rows:
        if row["start"]:
            continue
        words = [
            word
            for word in re.findall(r"[a-z]+", row["animal"].lower())
            if len(word) >= 3 and word not in STOPWORDS
        ]
        if any(mentions(text, _singular(word)) for word in words):
            return row
    return None


def _singular(word: str) -> str:
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def pick_row(
    rows: list[dict[str, str]],
    species: dict[str, Any],
    ask: Callable[[str], str],
) -> dict[str, str]:
    """Match one species to its table row: `row`, `start`, `change`, `kind`.

    `ask` sends one prompt and returns the model's text; two tries, then
    a loud stop. The row comes back verbatim from the table, never from
    memory.
    """
    table = "\n".join(
        f"{n}. {row['animal']} | {row['start'] or 'none'} | {row['change']}"
        for n, row in enumerate(rows, start=1)
    )
    base = load_prompt(
        "reference_pick",
        {"TABLE": table, "SPECIES_JSON": json.dumps(species, indent=2)},
    )
    problems = ""
    for _ in range(2):
        try:
            answer = json.loads(ask(base if not problems else f"{base}\n\n{problems}"))
        except json.JSONDecodeError as error:
            problems = f"PREVIOUS TRY was not JSON: {error}"
            continue
        n = answer.get("row")
        kind = answer.get("kind")
        if not isinstance(n, int) or not 1 <= n <= len(rows):
            problems = f"PREVIOUS TRY picked row {n!r}; pick 1 to {len(rows)}."
            continue
        if kind not in ("bird", "mammal"):
            problems = f"PREVIOUS TRY picked kind {kind!r}; bird or mammal."
            continue
        row = rows[n - 1]
        return {
            "row": row["animal"],
            "start": row["start"],
            "change": row["change"],
            "kind": kind,
        }
    raise ValueError(f"the reference table has no row for this species: {problems}")
