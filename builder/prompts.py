"""Load agent prompts. `$NAME` tokens are filled from the caller's values."""

from __future__ import annotations

from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_prompt(name: str, values: dict[str, str]) -> str:
    """Read `prompts/<name>.md` and fill its `$TOKENS`."""
    template = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    return Template(template).substitute(values)


def for_children() -> str:
    """The shared Writing-for-children block, for `$FOR_CHILDREN`."""
    return (PROMPTS_DIR / "for_children.md").read_text(encoding="utf-8")
