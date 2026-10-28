"""Load agent prompts. `$NAME` tokens are filled from the caller's values."""

from __future__ import annotations

from pathlib import Path
from string import Template
from typing import Any

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_prompt(name: str, values: dict[str, str]) -> str:
    """Read `prompts/<name>.md` and fill its `$TOKENS`."""
    template = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    return Template(template).substitute(values)


def for_children() -> str:
    """The shared Writing-for-children block, for `$FOR_CHILDREN`."""
    return (PROMPTS_DIR / "for_children.md").read_text(encoding="utf-8")


def fix_messages(
    first: dict[str, Any], previous: str, problems: str
) -> list[dict[str, Any]]:
    """A fix round as a real conversation (#59).

    The first message again as is, the previous answer as the
    assistant's own words, then what to change and nothing else,
    so the model fixes instead of rewriting blind.
    """
    return [
        first,
        {"role": "assistant", "content": previous},
        {
            "role": "user",
            "content": (
                "Change only what these problems name, keep everything "
                f"else exactly as it is:\n{problems}"
            ),
        },
    ]


#: A grown-up's note in a fix prompt, at most this many characters (#83).
NOTE_CHARS = 300


def improve_blocks(previous: str | None, note: str | None) -> str:
    """What a fix call sees beyond its prompt: the previous form, the note.

    Each round's fix call sees the previous form and the note, so it
    changes only what was asked (#82.6). The note goes in as the
    grown-up's words with the children's rules; it can only change
    the strict form, never text shown in the game (#83).
    """
    out = ""
    if previous:
        out += (
            "\n\n## The previous form\n\n"
            "Change only what is asked below; keep everything else exactly:\n\n"
            f"{previous}\n"
        )
    words = (note or "").strip()[:NOTE_CHARS]
    if words:
        out += (
            "\n\n## The grown-up's words\n\n"
            f'"{words}"\n\n'
            "Change only what they ask, and only inside this form: "
            "never any text shown in the game.\n\n"
            f"{for_children()}"
        )
    return out
