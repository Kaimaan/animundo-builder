"""Stage 9, safety: a separate reader checks every new word for children.

The child-safety reviewer never wrote the texts it judges. It reads
every item the job wrote (animal texts, the place description, food
and plant names, flower names, plant about texts, in English and
Finnish where both exist) plus the sources, and returns one verdict
per item: pass, rewrite, or fail. Rewrites go back to a writer call
once with the notes; anything still flagged, and every fail, stops
the job for a person. The verdicts land in `checks/safety.json`,
and every family problem report later becomes a new test case.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..prompts import load_prompt
from ..schemas import validate

TRIES = 2

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["pass", "rewrite", "fail"]},
                    "reasons": {"type": "array", "items": {"type": "string"}},
                    "rule": {"type": "string"},
                },
                "required": ["key", "verdict", "reasons", "rule"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


class SafetyStage:
    """Review every new text, rewrite once, or stop for a person."""

    name = "safety"

    def run(self, ctx: Any) -> dict[str, Any]:
        if ctx.world_path is None:
            raise ValueError("safety needs --world")
        world = Path(ctx.world_path)
        items = collect_texts(ctx.job_dir, world)
        sensitive = _sensitive(ctx.job_dir)
        examples = _examples(world)

        def ask(prompt: str, schema: dict[str, Any] | None) -> str:
            ctx.check_budget()
            kwargs: dict[str, Any] = {}
            if schema is not None:
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "Safety", "schema": schema},
                }
            response = ctx.chat([{"role": "user", "content": prompt}], **kwargs)
            return (response.choices[0].message.content or "").strip()

        verdicts = review_items(ask, items, sensitive, examples)
        rewritten: list[str] = []
        failed = [key for key, v in verdicts.items() if v["verdict"] == "fail"]
        todo = [key for key, v in verdicts.items() if v["verdict"] == "rewrite"]
        if todo:
            notes = {
                key: "; ".join([*verdicts[key]["reasons"], verdicts[key]["rule"]])
                for key in todo
            }
            fixed = fix_items(ask, items, todo, notes, examples)
            write_back(ctx.job_dir, fixed)
            rewritten = sorted(fixed)
            fresh = [
                item
                for item in collect_texts(ctx.job_dir, world)
                if item["key"] in fixed
            ]
            again = review_items(ask, fresh, sensitive, examples)
            verdicts.update(again)
            failed = sorted(
                {key for key, v in verdicts.items() if v["verdict"] != "pass"}
            )
        out = ctx.job_dir / "checks" / "safety.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(
            json.dumps(
                {
                    "reviewed": len(items),
                    "verdicts": [
                        {"key": key, **verdicts[key]} for key in sorted(verdicts)
                    ],
                    "rewritten": rewritten,
                    "failed": failed,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        record = {"items": [{"key": key, **verdicts[key]} for key in sorted(verdicts)]}
        if record["items"]:
            validate("safety", record)
            (ctx.job_dir / "safety.json").write_text(
                json.dumps(record, indent=2), encoding="utf-8"
            )
        if failed:
            first = failed[0]
            raise _Stopped(
                f"child-safety review failed {len(failed)} items, "
                f"first {first}: {'; '.join(verdicts[first]['reasons'])}; "
                "a person looks"
            )
        return {"reviewed": len(items), "rewritten": rewritten, "failed": []}


class _Stopped(Exception):
    """A text failed review, or the reviewer never answered cleanly."""


def review_items(
    ask: Callable[[str, dict[str, Any] | None], str],
    items: list[dict[str, Any]],
    sensitive: str,
    examples: str,
) -> dict[str, dict[str, Any]]:
    """One verdict per item, keyed. Two tries on the shape, then loud."""
    want = {item["key"] for item in items}
    base = load_prompt(
        "safety",
        {
            "ITEMS_JSON": json.dumps(items, indent=2, ensure_ascii=False),
            "SENSITIVE": sensitive,
            "EXAMPLES": examples,
        },
    )
    problems = ""
    for _ in range(TRIES):
        prompt = base if not problems else f"{base}\n\n{problems}"
        try:
            verdicts = json.loads(ask(prompt, REVIEW_SCHEMA))["verdicts"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            problems = f"PREVIOUS TRY was not a verdicts object: {error}"
            continue
        got = {v.get("key") for v in verdicts if isinstance(v, dict)}
        if got != want:
            problems = (
                f"PREVIOUS TRY covered {sorted(got)}, need exactly "
                f"{sorted(want)}; one verdict per item."
            )
            continue
        return {
            v["key"]: {
                "verdict": v["verdict"],
                "reasons": v.get("reasons", []),
                "rule": v.get("rule", ""),
            }
            for v in verdicts
        }
    raise _Stopped(f"the reviewer never wrote valid verdicts: {problems}")


def fix_items(
    ask: Callable[[str, dict[str, Any] | None], str],
    items: list[dict[str, Any]],
    todo: list[str],
    notes: dict[str, str],
    examples: str,
) -> dict[str, dict[str, str]]:
    """Rewrite flagged items once. Two tries on the shape, then loud."""
    want = {item["key"]: item for item in items if item["key"] in todo}
    base = load_prompt(
        "safety_fix",
        {
            "ITEMS_JSON": json.dumps(
                [
                    {
                        "key": key,
                        "en": want[key]["en"],
                        **({"fi": want[key]["fi"]} if want[key].get("fi") else {}),
                        "notes": notes[key],
                    }
                    for key in todo
                ],
                indent=2,
                ensure_ascii=False,
            ),
            "EXAMPLES": examples,
        },
    )
    problems = ""
    for _ in range(TRIES):
        prompt = base if not problems else f"{base}\n\n{problems}"
        try:
            fixed = json.loads(ask(prompt, None))
        except json.JSONDecodeError as error:
            problems = f"PREVIOUS TRY was not JSON: {error}"
            continue
        if set(fixed) != set(todo):
            problems = (
                f"PREVIOUS TRY rewrote {sorted(fixed)}, need exactly {sorted(todo)}."
            )
            continue
        bad = [
            key
            for key in todo
            if not isinstance(fixed[key], dict)
            or not fixed[key].get("en", "").strip()
            or (want[key].get("fi") and not fixed[key].get("fi", "").strip())
        ]
        if bad:
            problems = f"PREVIOUS TRY left {sorted(bad)} empty or shapeless."
            continue
        return fixed
    raise _Stopped(f"the rewrite never came back clean: {problems}")


def collect_texts(job_dir: Path, world: Path) -> list[dict[str, Any]]:
    """Every new text item: key, English, Finnish, sources."""
    survey = json.loads((job_dir / "survey.json").read_text(encoding="utf-8"))
    sources = {
        entry["species"]: entry.get("sources", [])
        for entry in survey.get("species", [])
    }
    items: list[dict[str, Any]] = []
    fi_animals = _locale(job_dir, "fi", "animals")
    for path in sorted((job_dir / "animals").glob("*.json")):
        if path.name == "progress.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for key, text in (data.get("text") or {}).items():
            full = f"animal.{path.stem}.{key}"
            items.append(
                {
                    "key": full,
                    "en": text,
                    "fi": fi_animals.get(full),
                    "sources": sources.get(path.stem, []),
                }
            )
    region_path = next((job_dir / "regions").glob("*.json"))
    region = json.loads(region_path.read_text(encoding="utf-8"))
    rid = region.get("id", region_path.stem)
    fi_regions = _locale(job_dir, "fi", "regions")
    items.append(
        {
            "key": f"region.{rid}.name",
            "en": region.get("name", ""),
            "fi": fi_regions.get(f"region.{rid}.name"),
            "sources": region.get("sources", []),
        }
    )
    items.append(
        {
            "key": f"region.{rid}.description",
            "en": region.get("description", ""),
            "fi": None,
            "sources": region.get("sources", []),
        }
    )
    for pos, place in enumerate(region.get("places", [])):
        items.append(
            {
                "key": f"region.{rid}.place.{pos}",
                "en": place.get("name", ""),
                "fi": None,
                "sources": region.get("sources", []),
            }
        )
    for pos, food in enumerate(region.get("food", [])):
        for npos, name in enumerate(food.get("names", [])):
            items.append(
                {
                    "key": f"region.{rid}.food.{pos}.{npos}",
                    "en": name,
                    "fi": None,
                    "sources": region.get("sources", []),
                }
            )
    for pos, name in enumerate(region.get("ground_cover", {}).get("flower_names", [])):
        items.append(
            {
                "key": f"region.{rid}.flower.{pos}",
                "en": name,
                "fi": None,
                "sources": region.get("sources", []),
            }
        )
    for pos, plant in enumerate(region.get("ground_cover", {}).get("plants", [])):
        for npos, name in enumerate(plant.get("names", [])):
            items.append(
                {
                    "key": f"region.{rid}.plantname.{pos}.{npos}",
                    "en": name,
                    "fi": None,
                    "sources": region.get("sources", []),
                }
            )
    en_plants = _locale(job_dir, "en", "plants")
    fi_plants = _locale(job_dir, "fi", "plants")
    for key in sorted(en_plants):
        if key.endswith(".latin"):
            continue
        items.append(
            {
                "key": key,
                "en": en_plants[key],
                "fi": fi_plants.get(key),
                "sources": _plant_sources(job_dir, key),
            }
        )
    return [item for item in items if item["en"]]


def _locale(job_dir: Path, language: str, name: str) -> dict[str, str]:
    path = job_dir / "locale" / language / f"{name}.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _plant_sources(job_dir: Path, key: str) -> list[str]:
    parts = key.split(".")
    path = job_dir / "models" / "plants" / f"{parts[1]}.json"
    if len(parts) < 2 or not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("sources", [])


def write_back(job_dir: Path, fixed: dict[str, dict[str, str]]) -> None:
    """Write rewritten texts to the files they came from."""
    for key, texts in fixed.items():
        parts = key.split(".")
        if parts[0] == "animal":
            _write_animal(job_dir, parts[1], ".".join(parts[2:]), texts)
        elif parts[0] == "region":
            _write_region(job_dir, parts[1], parts[2:], texts)
        elif parts[0] == "plant":
            _write_plant(job_dir, key, texts)
        else:
            raise _Stopped(f"cannot write back unknown key {key!r}")


def _write_animal(
    job_dir: Path, species_id: str, field: str, texts: dict[str, str]
) -> None:
    path = job_dir / "animals" / f"{species_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["text"][field] = texts["en"]
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if texts.get("fi"):
        key = f"animal.{species_id}.{field}"
        _merge_locale(job_dir, "fi", "animals", {key: texts["fi"]})


def _write_region(
    job_dir: Path, rid: str, rest: list[str], texts: dict[str, str]
) -> None:
    path = next((job_dir / "regions").glob("*.json"))
    region = json.loads(path.read_text(encoding="utf-8"))
    if rest == ["name"]:
        region["name"] = texts["en"]
        if texts.get("fi"):
            _merge_locale(job_dir, "fi", "regions", {f"region.{rid}.name": texts["fi"]})
    elif rest == ["description"]:
        region["description"] = texts["en"]
    elif rest[0] == "place":
        region["places"][int(rest[1])]["name"] = texts["en"]
    elif rest[0] == "food":
        region["food"][int(rest[1])]["names"][int(rest[2])] = texts["en"]
    elif rest[0] == "flower":
        region["ground_cover"]["flower_names"][int(rest[1])] = texts["en"]
    elif rest[0] == "plantname":
        region["ground_cover"]["plants"][int(rest[1])]["names"][int(rest[2])] = texts[
            "en"
        ]
    else:
        raise _Stopped(f"cannot write back unknown key region.{rid}.{'.'.join(rest)}")
    path.write_text(json.dumps(region, indent=2), encoding="utf-8")


def _write_plant(job_dir: Path, key: str, texts: dict[str, str]) -> None:
    _merge_locale(job_dir, "en", "plants", {key: texts["en"]})
    if texts.get("fi"):
        _merge_locale(job_dir, "fi", "plants", {key: texts["fi"]})


def _merge_locale(
    job_dir: Path, language: str, name: str, keys: dict[str, str]
) -> None:
    path = job_dir / "locale" / language / f"{name}.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    data.update(keys)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _sensitive(job_dir: Path) -> str:
    survey = json.loads((job_dir / "survey.json").read_text(encoding="utf-8"))
    flagged = sorted(
        entry["species"]
        for entry in survey.get("species", [])
        if entry.get("sensitive")
    )
    if not flagged:
        return "(no sensitive species in this place)"
    return (
        "Sensitive species here (never name or place a real breeding, "
        "nesting, denning or resting site for these): " + ", ".join(flagged)
    )


def _examples(world: Path) -> str:
    """The moose's own lines, for tone."""
    path = world / "animals" / "moose.json"
    if not path.is_file():
        return "(no examples in this checkout)"
    text = json.loads(path.read_text(encoding="utf-8")).get("text", {})
    lines = [
        f"{key}: {text[key]}"
        for key in ("title", "intro_safe", "hungry")
        if text.get(key)
    ]
    return "\n".join(lines) if lines else "(no examples in this checkout)"
