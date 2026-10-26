"""Stage 5, species plan: reuse or new for every species, food first.

The code matches survey species to existing animal files by Latin name
and rewrites the region to the reused file ids. The model writes the
reasons, judges the food web, and flags anything a person must decide
(herded or domestic species, sensitive species). New species over the
cap stop the job before any model call.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from ..prompts import load_prompt
from ..schemas import load_schema

DEFAULT_MAX_NEW_SPECIES = 30
MAX_FIX_ROUNDS = 3


class SpeciesPlanStage:
    """Write `species-plan.json` and point the region at reused files."""

    name = "species_plan"

    def __init__(self, max_new_species: int = DEFAULT_MAX_NEW_SPECIES) -> None:
        self.max_new_species = max_new_species

    def run(self, ctx: Any) -> dict[str, Any]:
        if ctx.world_path is None:
            raise ValueError("species plan needs --world")
        world = Path(ctx.world_path)
        survey = json.loads((ctx.job_dir / "survey.json").read_text(encoding="utf-8"))
        region_path = self._region_path(ctx)
        decisions = self._match(world, survey)
        new_count = sum(1 for d in decisions.values() if d[0] == "new")
        if new_count > self.max_new_species:
            raise _Stopped(
                f"{new_count} new species over the cap of {self.max_new_species}; "
                "a person must decide what goes"
            )
        self._point_region_at_reuse(region_path, survey, decisions)
        plan = self._write_with_model(ctx, survey, decisions)
        out = ctx.job_dir / "species-plan.json"
        out.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        if not plan["food_web_ok"]:
            raise _Stopped(
                "food first fails: " + "; ".join(plan.get("missing_food", ["unknown"]))
            )
        return {
            "species_plan_file": "species-plan.json",
            "reuse": sum(1 for d in decisions.values() if d[0] == "reuse"),
            "new": new_count,
        }

    def _region_path(self, ctx: Any) -> Path:
        regions = sorted((ctx.job_dir / "regions").glob("*.json"))
        if len(regions) != 1:
            raise ValueError(f"expected one region file, found {len(regions)}")
        return regions[0]

    def _match(self, world: Path, survey: dict[str, Any]) -> dict[str, tuple[str, str]]:
        """Map each surveyed id to (`reuse`|`new`, file id or reason)."""
        latin_to_files: dict[str, list[str]] = {}
        for path in (world / "animals").glob("*.json"):
            if path.stem == "archetypes":
                continue
            latin = json.loads(path.read_text(encoding="utf-8")).get("latin", "")
            latin_to_files.setdefault(latin.strip().lower(), []).append(path.stem)
        all_files = {f for files in latin_to_files.values() for f in files}
        decisions = {}
        for species in survey.get("species", []):
            survey_id = species["species"]
            latin = (species.get("latin") or "").strip().lower()
            same_latin = latin_to_files.get(latin, []) if latin else []
            if survey_id in same_latin:
                decisions[survey_id] = ("reuse", survey_id)
            elif same_latin:
                grown = [f for f in same_latin if not f.startswith("young_")]
                chosen = sorted(grown or same_latin, key=len)[0]
                decisions[survey_id] = ("reuse", chosen)
            elif survey_id in all_files:
                raise _Stopped(
                    f"{survey_id} is a different species from the file with its "
                    "name; a person must rename one"
                )
            else:
                decisions[survey_id] = ("new", species.get("latin", ""))
        return decisions

    def _point_region_at_reuse(
        self,
        region_path: Path,
        survey: dict[str, Any],
        decisions: dict[str, tuple[str, str]],
    ) -> None:
        region = json.loads(region_path.read_text(encoding="utf-8"))
        rename = {
            survey_id: file_id
            for survey_id, (decision, file_id) in decisions.items()
            if decision == "reuse" and survey_id != file_id
        }
        if not rename:
            return
        for group in region.get("animals", {}).values():
            for index, animal_id in enumerate(group):
                if animal_id in rename:
                    group[index] = rename[animal_id]
        for fish in region.get("fish", []):
            if fish.get("kind") in rename:
                fish["kind"] = rename[fish["kind"]]
        region_path.write_text(json.dumps(region, indent=2), encoding="utf-8")

    def _write_with_model(
        self,
        ctx: Any,
        survey: dict[str, Any],
        decisions: dict[str, tuple[str, str]],
    ) -> dict[str, Any]:
        schema = load_schema("species-plan")
        table = "\n".join(
            f"- {survey_id}: {decision} ({detail})"
            for survey_id, (decision, detail) in sorted(decisions.items())
        )
        base = load_prompt(
            "species_plan",
            {"SURVEY_JSON": json.dumps(survey, indent=2), "MATCH_TABLE": table},
        )
        problems = ""
        for _ in range(MAX_FIX_ROUNDS + 1):
            ctx.check_budget()
            prompt = base if not problems else f"{base}\n\n{problems}"
            response = ctx.chat(
                [{"role": "user", "content": prompt}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "SpeciesPlan", "schema": schema},
                },
            )
            text = (response.choices[0].message.content or "").strip()
            problems, plan = self._check_write(text, decisions, schema)
            if not problems:
                return plan
        raise _Stopped("species plan did not settle: " + problems)

    def _check_write(
        self,
        text: str,
        decisions: dict[str, tuple[str, str]],
        schema: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        try:
            plan = json.loads(text)
        except json.JSONDecodeError as error:
            return f"PREVIOUS TRY was not JSON: {error}", {}
        try:
            jsonschema.validate(plan, schema)
        except jsonschema.ValidationError as error:
            return f"PREVIOUS TRY broke the schema: {error.message}", {}
        got = {entry["species"]: entry["decision"] for entry in plan["species"]}
        want = {species: decision for species, (decision, _) in decisions.items()}
        if got != want:
            return (
                "PREVIOUS TRY decided differently from the Latin match: "
                f"expected {want}, got {got}. Keep the match; write reasons.",
                {},
            )
        return "", plan


class _Stopped(Exception):
    """The plan needs a person. The job stops with a reason."""
