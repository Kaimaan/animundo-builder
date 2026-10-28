"""Every schema accepts its example and rejects its broken copies."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from builder.schemas import load_schema, schema_path, validate

EXAMPLES = Path(__file__).parent / "examples"


def _valid_names() -> list[str]:
    return sorted(p.stem for p in (EXAMPLES / "valid").glob("*.json"))


def _broken_files() -> list[str]:
    return sorted(p.stem for p in (EXAMPLES / "broken").glob("*.json"))


def _schema_for_broken(stem: str) -> str:
    """The schema a broken file tests: `border-request--1` -> `border-request`."""
    return stem.split("--")[0]


@pytest.mark.parametrize("name", _valid_names())
def test_example_passes(name: str) -> None:
    data = json.loads((EXAMPLES / "valid" / f"{name}.json").read_text())
    validate(name, data)


@pytest.mark.parametrize("stem", _broken_files())
def test_broken_example_fails(stem: str) -> None:
    data = json.loads((EXAMPLES / "broken" / f"{stem}.json").read_text())
    with pytest.raises(jsonschema.ValidationError):
        validate(_schema_for_broken(stem), data)


def _schema_names(folder: Path) -> set[str]:
    return {p.stem.removesuffix(".schema") for p in folder.glob("*.json")}


def test_every_schema_has_both_sides() -> None:
    contracts = schema_path("area").parent
    schemas = _schema_names(contracts) | _schema_names(contracts.parent / "schemas")
    assert set(_valid_names()) == schemas
    broken_by_schema: dict[str, int] = {}
    for stem in _broken_files():
        name = _schema_for_broken(stem)
        broken_by_schema[name] = broken_by_schema.get(name, 0) + 1
    assert set(broken_by_schema) == schemas
    for name, count in sorted(broken_by_schema.items()):
        assert count >= 2, f"{name} has {count} broken examples, want at least 2"


def test_each_schema_is_valid_draft_2020_12() -> None:
    for name in _valid_names():
        jsonschema.Draft202012Validator.check_schema(load_schema(name))


def _valid(name: str) -> dict:
    return json.loads((EXAMPLES / "valid" / f"{name}.json").read_text())


def test_add_animal_kind_is_gone() -> None:
    job = _valid("job")
    job["kind"] = "add_animal"
    with pytest.raises(jsonschema.ValidationError):
        validate("job", job)


def test_add_animal_fields_are_gone() -> None:
    pack = _valid("pack-manifest")
    pack["base_place_id"] = "fi_nuuksio"
    with pytest.raises(jsonschema.ValidationError):
        validate("pack-manifest", pack)


def test_border_request_refuses_player_fields() -> None:
    """No player id, device, account or name may ever be added to a request."""
    for field in (
        "player_id",
        "uid",
        "owner_uid",
        "device_id",
        "account",
        "email",
        "name",
        "ip",
    ):
        request = _valid("border-request")
        request[field] = "kid-1"
        with pytest.raises(jsonschema.ValidationError):
            validate("border-request", request)


def test_border_note_refuses_player_fields() -> None:
    for field in ("player_id", "uid", "device_id"):
        note = _valid("border-note")
        note[field] = "kid-1"
        with pytest.raises(jsonschema.ValidationError):
            validate("border-note", note)


def test_area_refuses_player_fields() -> None:
    for field in ("player_id", "uid", "owner_uid"):
        area = _valid("area")
        area[field] = "kid-1"
        with pytest.raises(jsonschema.ValidationError):
            validate("area", area)


def test_admin_action_needs_item_for_ship() -> None:
    action = _valid("admin-action")
    action["action"] = "ship_item"
    with pytest.raises(jsonschema.ValidationError):
        validate("admin-action", action)
    action["item"] = "tundra-home"
    validate("admin-action", action)


def test_admin_action_needs_reason_for_turn_down() -> None:
    action = _valid("admin-action")
    action["action"] = "turn_down_request"
    with pytest.raises(jsonschema.ValidationError):
        validate("admin-action", action)
    action["reason"] = "not_now"
    validate("admin-action", action)
