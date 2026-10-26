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


def _valid(name: str) -> dict:
    return json.loads((EXAMPLES / "valid" / f"{name}.json").read_text())


def test_add_animal_kind_is_gone() -> None:
    draft = _valid("draft")
    draft["kind"] = "add_animal"
    with pytest.raises(jsonschema.ValidationError):
        validate("draft", draft)
    job = _valid("job")
    job["kind"] = "add_animal"
    with pytest.raises(jsonschema.ValidationError):
        validate("job", job)


def test_add_animal_fields_are_gone() -> None:
    draft = _valid("draft")
    draft["base_place_id"] = "fi_nuuksio"
    with pytest.raises(jsonschema.ValidationError):
        validate("draft", draft)
    draft = _valid("draft")
    draft["species"] = "lynx"
    with pytest.raises(jsonschema.ValidationError):
        validate("draft", draft)
    pack = _valid("pack-manifest")
    pack["base_place_id"] = "fi_nuuksio"
    with pytest.raises(jsonschema.ValidationError):
        validate("pack-manifest", pack)


def test_something_missing_choice_is_gone() -> None:
    draft = _valid("draft")
    draft["improve"] = {
        "choices": [{"choice": "something_missing", "species": "red_deer"}],
        "at": "2026-11-02T10:00:00+02:00",
    }
    with pytest.raises(jsonschema.ValidationError):
        validate("draft", draft)
