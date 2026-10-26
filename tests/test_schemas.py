"""Every schema accepts its example and rejects its broken copies."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from builder.schemas import load_schema, schema_path, validate

EXAMPLES = Path(__file__).parent / "examples"


def _cases(folder: str) -> list[str]:
    return sorted(p.stem for p in (EXAMPLES / folder).glob("*.json"))


@pytest.mark.parametrize("name", _cases("valid"))
def test_example_passes(name: str) -> None:
    data = json.loads((EXAMPLES / "valid" / f"{name}.json").read_text())
    validate(name, data)


@pytest.mark.parametrize("name", _cases("broken"))
def test_broken_example_fails(name: str) -> None:
    data = json.loads((EXAMPLES / "broken" / f"{name}.json").read_text())
    with pytest.raises(jsonschema.ValidationError):
        validate(name, data)


def _schema_names(folder: Path) -> set[str]:
    return {p.stem.removesuffix(".schema") for p in folder.glob("*.json")}


def test_every_schema_has_both_sides() -> None:
    contracts = schema_path("draft").parent
    schemas = _schema_names(contracts) | _schema_names(contracts.parent / "schemas")
    assert set(_cases("valid")) == schemas
    assert set(_cases("broken")) == schemas


def test_each_schema_is_valid_draft_2020_12() -> None:
    for name in _cases("valid"):
        jsonschema.Draft202012Validator.check_schema(load_schema(name))
