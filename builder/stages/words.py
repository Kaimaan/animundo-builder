"""Stage 6b, words: every new key in every game language.

English lives inline (the animal files' `text`, the region's name);
this stage translates the new species' texts and the region's name
into each language in the world's `locale/languages.json`, and writes
the job's `locale/<lang>/animals.json` and `locale/<lang>/regions.json`
with only the new keys. The world's own draft checker proves the new
keys add text and change none.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..prompts import load_prompt
from ..world import check_draft

TRIES = 2
EXAMPLE_KEYS = ("title", "intro_safe", "hungry")


class WordsStage:
    """Write `locale/<lang>/*.json` for every new species and the region."""

    name = "words"

    def run(self, ctx: Any) -> dict[str, Any]:
        if ctx.world_path is None:
            raise ValueError("words needs --world")
        world = Path(ctx.world_path)
        plan = json.loads((ctx.job_dir / "species-plan.json").read_text())
        new_ids = [
            entry["species"] for entry in plan["species"] if entry["decision"] == "new"
        ]
        languages = [lang for lang in _languages(world) if lang != "en"]
        if not languages or not new_ids:
            return {"locale_files": [], "languages": languages}
        region = json.loads(next((ctx.job_dir / "regions").glob("*.json")).read_text())
        files = []
        for language in languages:
            animals = {
                f"animal.{species_id}.{key}": text
                for species_id in new_ids
                for key, text in self._translate_animal(
                    ctx, world, language, species_id
                ).items()
            }
            regions = {
                f"region.{region['id']}.name": self._translate_region(
                    ctx, world, language, region
                )
            }
            for name, keys in (("animals", animals), ("regions", regions)):
                path = ctx.job_dir / "locale" / language / f"{name}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(keys, indent=2), encoding="utf-8")
                files.append(f"locale/{language}/{name}.json")
        self._check(world, ctx)
        return {"locale_files": sorted(files), "languages": languages}

    def _translate_animal(
        self, ctx: Any, world: Path, language: str, species_id: str
    ) -> dict[str, str]:
        path = ctx.job_dir / "animals" / f"{species_id}.json"
        if not path.is_file():
            raise _Stopped(f"{species_id}: the animals stage left no file")
        data = json.loads(path.read_text(encoding="utf-8"))
        strings = data.get("text")
        if not isinstance(strings, dict) or not strings:
            raise _Stopped(f"{species_id}: the animals stage left no text")
        return self._translate(ctx, world, language, strings)

    def _translate_region(
        self, ctx: Any, world: Path, language: str, region: dict[str, Any]
    ) -> str:
        name = region.get("name")
        if not name:
            raise _Stopped("the region stage left no name to translate")
        return self._translate(ctx, world, language, {"name": name})["name"]

    def _translate(
        self,
        ctx: Any,
        world: Path,
        language: str,
        strings: dict[str, str],
    ) -> dict[str, str]:
        examples = _examples(world, language)
        language_name = _languages(world)[language]
        base = load_prompt(
            "words_translate",
            {
                "TARGET_LANG": f"{language_name} ({language})",
                "STRINGS_JSON": json.dumps(strings, indent=2, ensure_ascii=False),
                "EXAMPLES": examples,
            },
        )
        problems = ""
        for _ in range(TRIES):
            ctx.check_budget()
            prompt = base if not problems else f"{base}\n\n{problems}"
            response = ctx.chat(
                [{"role": "user", "content": prompt}], reasoning_effort="low"
            )
            text = (response.choices[0].message.content or "").strip()
            problems, translated = _settle(strings, text)
            if not problems:
                return translated
        raise _Stopped(f"the translation never came back clean: {problems}")

    def _check(self, world: Path, ctx: Any) -> None:
        result = check_draft(world, ctx.job_dir, "words")
        by_check = {entry.get("check"): entry for entry in result.get("results", [])}
        entry = by_check.get("locale", {})
        if entry.get("status") == "fail":
            raise _Stopped(
                "the new keys broke the locale check: "
                + "; ".join(entry.get("problems", [])[:5])
            )


class _Stopped(Exception):
    """The translation is unusable, or the checkout's checker refuses it."""


def _languages(world: Path) -> dict[str, str]:
    return json.loads((world / "locale" / "languages.json").read_text())


def _examples(world: Path, language: str) -> str:
    """The moose's own lines, English against this language's, for tone."""
    path = world / "locale" / language / "animals.json"
    if not path.is_file():
        return "(no examples in this checkout)"
    translated = json.loads(path.read_text(encoding="utf-8"))
    english = json.loads((world / "animals" / "moose.json").read_text())["text"]
    lines = []
    for key in EXAMPLE_KEYS:
        source = english.get(key)
        target = translated.get(f"animal.moose.{key}")
        if source and target:
            lines.append(f"{source} / {target}")
    return "\n".join(lines) if lines else "(no examples in this checkout)"


def _settle(strings: dict[str, str], text: str) -> tuple[str, dict[str, str]]:
    """Accept a translation only when its keys match exactly."""
    try:
        translated = json.loads(text)
    except json.JSONDecodeError as error:
        return f"PREVIOUS TRY was not JSON: {error}", {}
    if not isinstance(translated, dict) or not all(
        isinstance(value, str) for value in translated.values()
    ):
        return "PREVIOUS TRY was not a {key: text} object.", {}
    if set(translated) != set(strings):
        missing = sorted(set(strings) - set(translated))
        extra = sorted(set(translated) - set(strings))
        return (
            f"PREVIOUS TRY changed the keys (missing {missing}, "
            f"extra {extra}); return exactly the input keys.",
            {},
        )
    if any(not value.strip() for value in translated.values()):
        return "PREVIOUS TRY left a translation empty.", {}
    return "", translated
