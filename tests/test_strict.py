"""Strict structured-output schemas: the subset, and the file shape."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from builder.schemas import validate
from builder.strict import (
    Research,
    ThingLooks,
    assert_strict_subset,
    strict_schema,
    strip_nulls,
)


def test_research_schema_meets_the_strict_subset() -> None:
    assert_strict_subset(strict_schema(Research))


def test_looks_schema_meets_the_strict_subset() -> None:
    assert_strict_subset(strict_schema(ThingLooks))


def _strict_answer() -> dict[str, Any]:
    """What constrained decoding returns: every key, unknowns null."""
    return {
        "main_food": {
            "name": "mountain hare",
            "kind": "prey",
            "size_cm": 55.0,
            "latin": "Lepus timidus",
            "note": None,
        },
        "other_foods": [],
        "surroundings": [
            {
                "name": "Norway spruce",
                "kind": "tree",
                "food_kind": "cones",
                "latin": "Picea abies",
                "note": None,
            },
            {
                "name": "bilberry",
                "kind": "plant",
                "food_kind": "berries",
                "latin": "Vaccinium myrtillus",
                "note": None,
            },
            {
                "name": "moss",
                "kind": "ground",
                "food_kind": None,
                "latin": None,
                "note": None,
            },
        ],
        "size": {"length_cm": 100.0, "weight_kg": 20.0},
        "year": {
            "winter": "active",
            "shelter": "It dens in rocky hollows.",
            "young": "Kittens are born in spring.",
            "food": "It hunts at dusk.",
        },
        "sources": ["https://example.org/lynx"],
        "looks": {
            "shoulder_cm": 60.0,
            "shoulder_source": "https://example.org/lynx",
            "height_cm": None,
            "height_source": None,
            "tail_cm": 15.0,
            "tail_source": "https://example.org/lynx",
            "coat": {
                "main": "grey-brown",
                "main_hex": "#7a6a5a",
                "belly": "white",
                "belly_hex": "#f2ede2",
                "source": "https://example.org/lynx",
            },
            "marks": [
                {"mark": "ear tufts", "source": "https://example.org/lynx"},
                {
                    "mark": "short black-tipped tail",
                    "source": "https://example.org/lynx",
                },
            ],
        },
    }


def test_strict_answer_parses_and_strips_to_the_file_shape() -> None:
    answer = _strict_answer()
    Research.model_validate(answer)
    stripped = strip_nulls(answer)
    assert stripped["main_food"].get("note") is None
    assert "note" not in stripped["main_food"]
    assert stripped["looks"].get("height_cm") is None
    validate("research", stripped)


def test_looks_answer_parses() -> None:
    ThingLooks.model_validate(
        {
            "latin": "Lepus timidus",
            "size_cm": 55.0,
            "colours": ["grey-brown", "white"],
            "marks": ["white tail", "long ears"],
            "year": None,
            "sources": ["https://example.org/hare"],
        }
    )


def test_every_model_field_is_required() -> None:
    """No defaults: strict `required` names every key by construction."""
    for model in (Research, ThingLooks):
        assert_model_fields(model)


def assert_model_fields(model: type[BaseModel]) -> None:
    for name, field in model.model_fields.items():
        assert field.is_required(), f"{model.__name__}.{name} needs no default"
