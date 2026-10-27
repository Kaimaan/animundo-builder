"""Strict structured-output schemas, the way Meta's docs say (#58).

One Pydantic model per builder schema the API fills: the
research and the looks. Every field is required; what the
model may not know is nullable, never left out. `strict_schema`
flattens the model to the strict subset (plain object root,
`additionalProperties: false` everywhere, `required` naming
every key, no `allOf`/`oneOf`, `$ref`s inlined): chat sends it
as `response_format`, Responses as `text.format`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    """No extra keys, ever: the subset demands the flag."""

    model_config = ConfigDict(extra="forbid")


class DietKind(StrEnum):
    """Food kinds, plus `prey` for a hunted animal, flat (#58.2)."""

    PREY = "prey"
    BARK = "bark"
    BERRIES = "berries"
    BOTTOM_BUGS = "bottom_bugs"
    BROWSE = "browse"
    CARRION = "carrion"
    CARROT = "carrot"
    CLOVER = "clover"
    CONES = "cones"
    GRASS = "grass"
    INSECTS = "insects"
    MUSHROOMS = "mushrooms"
    NECTAR = "nectar"
    NEST = "nest"
    SEEDS = "seeds"
    WATER_BUGS = "water_bugs"
    WATER_PLANTS = "water_plants"


class PlantFoodKind(StrEnum):
    """Food kinds a growing thing can be: never prey."""

    BARK = "bark"
    BERRIES = "berries"
    BOTTOM_BUGS = "bottom_bugs"
    BROWSE = "browse"
    CARRION = "carrion"
    CARROT = "carrot"
    CLOVER = "clover"
    CONES = "cones"
    GRASS = "grass"
    INSECTS = "insects"
    MUSHROOMS = "mushrooms"
    NECTAR = "nectar"
    NEST = "nest"
    SEEDS = "seeds"
    WATER_BUGS = "water_bugs"
    WATER_PLANTS = "water_plants"


class GroundKind(StrEnum):
    """What a surrounding is."""

    TREE = "tree"
    PLANT = "plant"
    GROUND = "ground"
    WATER = "water"


class Winter(StrEnum):
    """How it spends the winter."""

    ACTIVE = "active"
    HIBERNATES = "hibernates"
    MIGRATES = "migrates"


class Food(_Strict):
    """One food, hunted or found."""

    name: str = Field(description="Plain English name, like 'mountain hare'")
    kind: DietKind = Field(
        description="'prey' for a hunted animal, a food kind otherwise"
    )
    size_cm: float | None = Field(
        description="Body length in centimetres when it is an animal, else null"
    )
    latin: str | None = Field(
        description="Binomial like 'Lepus timidus' when it is a species, else null"
    )
    note: str | None = Field(description="One clarifying sentence, else null")


class Surrounding(_Strict):
    """One thing growing or lying around where it lives."""

    name: str = Field(description="Plain English name, like 'Norway spruce'")
    kind: GroundKind
    food_kind: PlantFoodKind | None = Field(
        description="The food kind when animals eat it, else null"
    )
    latin: str | None = Field(description="Binomial when it is a species, else null")
    note: str | None = Field(description="One clarifying sentence, else null")


class Size(_Strict):
    """Its own body."""

    length_cm: float = Field(description="Body length in centimetres")
    weight_kg: float | None = Field(
        description="Weight in kilograms when known, else null"
    )


class Year(_Strict):
    """How it lives through the seasons."""

    winter: Winter
    shelter: str = Field(description="Where it shelters, one or two sentences")
    young: str = Field(description="Its young, one or two sentences")
    food: str = Field(description="What it eats through the year, one sentence")


class Coat(_Strict):
    """Its colours, in words a sculptor can use."""

    main: str = Field(description="Main coat colour, like 'grey-brown'")
    main_hex: str = Field(description="The main colour as hex, like '#7a4a2b'")
    belly: str = Field(description="Belly or chest colour, like 'white'")
    belly_hex: str = Field(description="The belly colour as hex")
    source: str = Field(description="The page the colours came from")


class Mark(_Strict):
    """One field-guide mark that tells the species apart."""

    mark: str = Field(
        description="The mark, like 'ear tufts' or 'short black-tipped tail'"
    )
    source: str = Field(description="The page the mark came from")


class Looks(_Strict):
    """What it looks like, so a sculptor gets it right."""

    shoulder_cm: float | None = Field(
        description="Shoulder height in centimetres for mammals, else null"
    )
    shoulder_source: str | None = Field(
        description="The page the shoulder height came from, else null"
    )
    height_cm: float | None = Field(
        description="Height in centimetres for birds and fish, else null"
    )
    height_source: str | None = Field(
        description="The page the height came from, else null"
    )
    tail_cm: float | None = Field(description="Tail length in centimetres, else null")
    tail_source: str | None = Field(
        description="The page the tail length came from, else null"
    )
    coat: Coat
    marks: list[Mark] = Field(description="2 to 3 telling marks")


class Research(_Strict):
    """One animal's research: food, surroundings, size, looks, year."""

    main_food: Food = Field(description="What it eats most")
    other_foods: list[Food] = Field(description="Up to 3 more foods it eats")
    surroundings: list[Surrounding] = Field(
        description="3 to 7 things around it: trees, plants, ground, water"
    )
    size: Size
    year: Year
    sources: list[str] = Field(
        description="The pages the facts came from, most important first"
    )
    looks: Looks


class ThingLooks(_Strict):
    """A short looks call for one thing the minimum will make (#58.5).

    The prey or one new plant: latin, size, colours, marks or
    shape notes, the year for plants, and sources. Library
    items get no call. Shoulder and tail like the research,
    so prey keep more than their length (#63.3).
    """

    latin: str = Field(description="Its binomial, like 'Lepus timidus'")
    size_cm: float = Field(description="Body length (animals) or height (plants)")
    shoulder_cm: float | None = Field(
        description="Shoulder height in centimetres for mammals, else null"
    )
    shoulder_source: str | None = Field(
        description="The page the shoulder height came from, else null"
    )
    height_cm: float | None = Field(
        description="Height in centimetres for birds and fish, else null"
    )
    height_source: str | None = Field(
        description="The page the height came from, else null"
    )
    tail_cm: float | None = Field(description="Tail length in centimetres, else null")
    tail_source: str | None = Field(
        description="The page the tail length came from, else null"
    )
    colours: list[str] = Field(
        description="Main colours in plain words, like 'grey-brown'"
    )
    marks: list[str] = Field(
        description="2 to 3 marks or shape notes, like 'white tail' or 'needle leaves'"
    )
    year: str | None = Field(
        description="For plants: leaves, flowers and fruit through the year; else null"
    )
    sources: list[str] = Field(
        description="The pages the facts came from, most important first"
    )


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The model as a strict-subset JSON Schema dict, `$ref`s inlined."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})
    return _inline(schema, defs)


def assert_strict_subset(schema: dict[str, Any], path: str = "root") -> None:
    """Meta's strict subset: flat, closed, every key required.

    Plain object root, no `allOf`/`oneOf`/`$ref`/`const`,
    `additionalProperties: false` on every object, `required`
    naming every key. Raises AssertionError naming the spot.
    """
    assert schema.get("type") == "object", f"{path}: root must be a plain object"

    def walk(node: Any, at: str) -> None:
        if isinstance(node, dict):
            for bad in ("allOf", "oneOf", "$ref", "$defs", "const", "not"):
                assert bad not in node, f"{at}: forbids {bad}"
            if node.get("type") == "object" or "properties" in node:
                assert node.get("additionalProperties") is False, (
                    f"{at}: every object closes with additionalProperties: false"
                )
                assert sorted(node.get("required", [])) == sorted(
                    node.get("properties", {})
                ), f"{at}: required names every key"
            for key, value in node.items():
                walk(value, f"{at}.{key}")
        elif isinstance(node, list):
            for n, value in enumerate(node):
                walk(value, f"{at}[{n}]")

    walk(schema, path)


def _inline(node: Any, defs: dict[str, Any]) -> Any:
    """Inline internal `$ref`s; the schemas stay flat (#58.3).

    Pydantic puts a described nested model beside its `$ref`,
    or wraps it in a single-item `allOf`: the description
    survives, the reference and the wrapper do not (the
    subset forbids `allOf`).
    """
    if isinstance(node, dict):
        if set(node) == {"$ref"}:
            return _inline(defs[node["$ref"].split("/")[-1]], defs)
        if "$ref" in node:
            merged = _inline(defs[node["$ref"].split("/")[-1]], defs)
            rest = {
                key: _inline(value, defs)
                for key, value in node.items()
                if key != "$ref"
            }
            return {**merged, **rest}
        refs = node.get("allOf")
        if isinstance(refs, list) and len(refs) == 1 and set(refs[0]) == {"$ref"}:
            merged = _inline(defs[refs[0]["$ref"].split("/")[-1]], defs)
            rest = {
                key: _inline(value, defs)
                for key, value in node.items()
                if key != "allOf"
            }
            return {**merged, **rest}
        return {key: _inline(value, defs) for key, value in node.items()}
    if isinstance(node, list):
        return [_inline(value, defs) for value in node]
    return node


def strip_nulls(data: Any) -> Any:
    """Drop nulls: strict answers parse back to the file shape.

    Every key arrives (strict requires it); the files keep the
    loose shape, where unknown stays out instead of null.
    """
    if isinstance(data, dict):
        return {
            key: strip_nulls(value) for key, value in data.items() if value is not None
        }
    if isinstance(data, list):
        return [strip_nulls(value) for value in data]
    return data
