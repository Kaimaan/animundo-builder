"""Load and check the builder's JSON Schemas in `schemas/` and `contracts/`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

ROOT = Path(__file__).resolve().parent.parent


def schema_path(name: str) -> Path:
    """Find `<name>.schema.json`, preferring the shared contracts."""
    for folder in ("contracts", "schemas"):
        candidate = ROOT / folder / f"{name}.schema.json"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"no schema named {name!r}")


def load_schema(name: str) -> dict[str, Any]:
    """Read one schema by its short name, for example `"draft"`."""
    return json.loads(schema_path(name).read_text(encoding="utf-8"))


def validate(name: str, data: Any) -> None:
    """Check `data` against the named schema. Raises on the first problem."""
    jsonschema.validate(data, load_schema(name))
