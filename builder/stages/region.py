"""Stage 4, region: write the region file from the survey, then prove it.

The model writes `regions/<area_id>.json` following the region rules and
examples read fresh from the world checkout. The pipeline then sets the
mechanical fields itself (id, author, status, seed, center), runs the
world's own `validate_region.py`, and sends failures back, up to 8 rounds.

New species don't exist as files until a later stage, so "unknown animal"
complaints about surveyed species are recorded as planned, not failures.
Everything else must pass.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from string import Template
from typing import Any

import jsonschema

from ..prompts import PROMPTS_DIR

HOSTED_AUTHOR = "Animundo Builder"
MIN_SOURCES = 3


class RegionStage:
    """Write and validate `regions/<area_id>.json` in the job folder."""

    name = "region"

    def __init__(self, author: str = HOSTED_AUTHOR, max_rounds: int = 8) -> None:
        self.author = author
        self.max_rounds = max_rounds
        self.rounds = 0

    def run(self, ctx: Any) -> dict[str, Any]:
        if ctx.world_path is None:
            raise ValueError("region needs --world")
        world = Path(ctx.world_path)
        area = json.loads((ctx.job_dir / "area.json").read_text(encoding="utf-8"))
        survey = json.loads((ctx.job_dir / "survey.json").read_text(encoding="utf-8"))
        capabilities = json.loads(
            (ctx.job_dir / "capabilities.json").read_text(encoding="utf-8")
        )
        region_id = area["area_id"]
        planned = {species["species"] for species in survey.get("species", [])}
        schema = json.loads(
            (world / "schemas" / "region.schema.json").read_text(encoding="utf-8")
        )
        template = (PROMPTS_DIR / "region_write.md").read_text(encoding="utf-8")
        template = _with_examples(template, world)
        prompt = Template(template).substitute(
            {
                "AREA_JSON": json.dumps(area, indent=2),
                "SURVEY_JSON": json.dumps(survey, indent=2),
                "CAPABILITIES_JSON": json.dumps(capabilities, indent=2),
                "REGION_RULES": _region_rules(world),
                "EXISTING_ANIMALS": ", ".join(sorted(_known_animals(world))),
                "BUILT_PLANTS": ", ".join(sorted(_built_plants(world))),
            }
        )
        problems = ""
        region_path = ctx.job_dir / "regions" / f"{region_id}.json"
        while True:
            self._spend_round(ctx)
            text = self._write_once(ctx, prompt, problems)
            try:
                draft = json.loads(text)
            except json.JSONDecodeError as error:
                problems = f"PREVIOUS TRY was not JSON: {error}"
                continue
            try:
                jsonschema.validate(draft, schema)
            except jsonschema.ValidationError as error:
                problems = f"PREVIOUS TRY broke the region schema: {error.message}"
                continue
            draft = self._mechanical(draft, area, region_id)
            region_path.parent.mkdir(parents=True, exist_ok=True)
            region_path.write_text(json.dumps(draft, indent=2), encoding="utf-8")
            errors = self._validate(world, region_path, planned)
            have_sources = len(draft.get("sources", []))
            if have_sources < MIN_SOURCES:
                errors.append(f"need {MIN_SOURCES} sources, file has {have_sources}")
            missing = sorted(planned - _used_species(region_path))
            if missing:
                errors.append(
                    "surveyed species missing from animals/fish: "
                    + ", ".join(missing)
                    + ". This overrides CONTRIBUTING: list every one by id "
                    "even if its animal file does not exist yet; new files "
                    "are built by a later stage."
                )
            if not errors:
                return {
                    "region_file": f"regions/{region_id}.json",
                    "planned_species": sorted(
                        _unknown_animals(world, region_path) & planned
                    ),
                    "rounds": self.rounds,
                }
            notes = "\n".join(f"- {error}" for error in errors)
            problems = f"PREVIOUS TRY had these problems, fix exactly them:\n{notes}"

    def _spend_round(self, ctx: Any) -> None:
        ctx.check_budget()
        if self.rounds >= self.max_rounds:
            raise _Stopped(f"region spent its {self.max_rounds} rounds")
        self.rounds += 1

    def _write_once(self, ctx: Any, prompt: str, problems: str) -> str:
        # Plain JSON mode, not structured output: the API's schema compiler
        # silently drops this schema's `animals` (and similar) properties.
        # The world schema still checks every draft, in code, below.
        full = prompt if not problems else f"{prompt}\n\n{problems}"
        response = ctx.chat([{"role": "user", "content": full}])
        return (response.choices[0].message.content or "").strip()

    def _mechanical(
        self, draft: dict[str, Any], area: dict[str, Any], region_id: str
    ) -> dict[str, Any]:
        draft["id"] = region_id
        draft["author"] = self.author
        draft["status"] = "reviewed"
        draft["center"] = area["centroid"]
        terrain = draft.setdefault("terrain", {})
        terrain["seed"] = seed_for_area(region_id)
        return draft

    def _validate(self, world: Path, region_path: Path, planned: set[str]) -> list[str]:
        checker = str(world / "tools" / "validate_region.py")
        proc = subprocess.run(
            [sys.executable, checker, str(region_path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        errors = []
        for line in proc.stdout.splitlines():
            if line.startswith("  - "):
                errors.append(line[4:])
        if proc.returncode != 0 and not errors:
            errors.append((proc.stderr or "validator failed silently").strip())
        kept = []
        saw_unknown = False
        for error in errors:
            if "has unknown animal" in error or "must be an animal" in error:
                saw_unknown = True
                continue
            kept.append(error)
        if saw_unknown:
            bad = sorted(_unknown_animals(world, region_path) - planned)
            if bad:
                kept.append(f"unknown animals not in the survey: {', '.join(bad)}")
        return kept


def seed_for_area(area_id: str) -> int:
    """The land's seed, derived from the area id: same area, same land."""
    digest = hashlib.sha256(area_id.encode()).digest()
    return int.from_bytes(digest[:4], "big")


def _region_rules(world: Path) -> str:
    text = (world / "CONTRIBUTING.md").read_text(encoding="utf-8")
    start = text.find("## The region file")
    rest = text.find("\n## ", start + 1)
    return text[start:rest].strip() if start >= 0 else ""


def _with_examples(template: str, world: Path) -> str:
    regions = sorted((world / "regions").glob("*.json"))
    for index, path in enumerate(regions[:2], start=1):
        # Escape dollars: example text must survive the substitute below.
        content = path.read_text(encoding="utf-8").replace("$", "$$")
        template = template.replace(f"$EXAMPLE_{index}_NAME", path.stem)
        template = template.replace(f"$EXAMPLE_{index}", content)
    for leftover in ("$EXAMPLE_1_NAME", "$EXAMPLE_1", "$EXAMPLE_2_NAME", "$EXAMPLE_2"):
        template = template.replace(leftover, "(no example)")
    return template


def _used_species(region_path: Path) -> set[str]:
    region = json.loads(region_path.read_text(encoding="utf-8"))
    used: set[str] = set()
    for group in region.get("animals", {}).values():
        used.update(group)
    used.update(fish.get("kind", "") for fish in region.get("fish", []))
    used.discard("")
    return used


def _known_animals(world: Path) -> set[str]:
    return {
        path.stem
        for path in (world / "animals").glob("*.json")
        if path.stem != "archetypes"
    }


def _built_plants(world: Path) -> set[str]:
    return {
        path.stem
        for path in (world / "models" / "plants").glob("*.json")
        if (world / "assets" / "plants" / f"{path.stem}.glb").is_file()
    }


def _unknown_animals(world: Path, region_path: Path) -> set[str]:
    return _used_species(region_path) - _known_animals(world)


class _Stopped(Exception):
    """The stage ran out of rounds. The job stops with a reason."""
