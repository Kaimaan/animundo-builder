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
    contracts = schema_path("tile").parent
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


def test_plan_refuses_player_fields() -> None:
    for field in ("player_id", "uid", "owner_uid"):
        plan = _valid("plan")
        plan[field] = "kid-1"
        with pytest.raises(jsonschema.ValidationError):
            validate("plan", plan)


def test_build_job_needs_private_to_when_private() -> None:
    job = _valid("build-job")
    job["visibility"] = "private"
    with pytest.raises(jsonschema.ValidationError):
        validate("build-job", job)
    job["private_to"] = "judge1"
    validate("build-job", job)


def test_build_job_takes_a_remake_pass_without_a_single_tile() -> None:
    job = _valid("build-job")
    del job["tile"]
    job.update(
        {
            "kind": "remake",
            "tiles": ["z10_0581_0294", "z10_0580_0295"],
            "versions": {"z10_0581_0294": 4, "z10_0580_0295": 3},
            "world": "abc123",
            "created_by": "builder",
            "status": "published",
        }
    )
    validate("build-job", job)
    bare = {k: v for k, v in job.items() if k != "tiles"}
    with pytest.raises(jsonschema.ValidationError):
        validate("build-job", bare)
    build = _valid("build-job")
    del build["tile"]
    with pytest.raises(jsonschema.ValidationError):
        validate("build-job", build)
    big = _valid("build-job")
    big.update({"kind": "enrich", "tiles": [f"z10_0581_{i:04d}" for i in range(201)]})
    with pytest.raises(jsonschema.ValidationError):
        validate("build-job", big)


def test_build_job_takes_an_enrich_job() -> None:
    job = _valid("build-job")
    job.update(
        {
            "kind": "enrich",
            "tiles": ["z10_0582_0294", "z10_0581_0294"],
            "items": ["enrich_lynx"],
            "enrich": {"kind": "animal", "what": "lynx", "from": "fox"},
            "created_by": "enrich",
        }
    )
    validate("build-job", job)
    job["versions"] = {"z10_0582_0294": 2, "z10_0581_0294": 3}
    validate("build-job", job)
    job["version"] = 2
    validate("build-job", job)
    job["enrich"] = {"kind": "animal"}
    with pytest.raises(jsonschema.ValidationError):
        validate("build-job", job)


def test_tile_height_is_32_or_64() -> None:
    tile = _valid("tile")
    tile["height"] = 48
    with pytest.raises(jsonschema.ValidationError):
        validate("tile", tile)
    tile["height"] = 64
    validate("tile", tile)


def test_tile_needs_level() -> None:
    tile = _valid("tile")
    del tile["level"]
    with pytest.raises(jsonschema.ValidationError):
        validate("tile", tile)


def test_tile_needs_a_name_in_both_languages() -> None:
    tile = _valid("tile")
    del tile["name"]
    with pytest.raises(jsonschema.ValidationError):
        validate("tile", tile)
    tile = _valid("tile")
    del tile["name"]["fi"]
    with pytest.raises(jsonschema.ValidationError):
        validate("tile", tile)


def test_open_ocean_tile_keeps_an_english_only_sea_name() -> None:
    tile = _valid("tile")
    tile["habitat"] = "open_ocean"
    tile["sea"] = "lake_superior"
    tile["land"] = "boreal_forest"
    tile["land_share"] = 0.05
    del tile["ecoregion"]
    del tile["name"]["fi"]
    validate("tile", tile)
