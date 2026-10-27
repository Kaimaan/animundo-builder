"""Stage 6, animals: write every new species' file, English text inside.

One model draft per species, then validate-all rounds against the
world's own `check_draft.py`: the job's region plus every draft.
Errors carry the species id, so each species is fixed on its own,
6 rounds at most. Every draft is written to the job's `animals/`
folder at once, and each species that passes is recorded in
`progress.json`: a stop or crash keeps what was paid for, and resume
carries on with the rest. Translations come later (words stage).
"""

from __future__ import annotations

import json
from pathlib import Path
from string import Template
from typing import Any

import jsonschema

from ..prompts import PROMPTS_DIR
from ..world import check_draft, mentions, problems_by_file

MAX_ROUNDS_PER_SPECIES = 6
PROGRESS_FILE = "progress.json"

EXAMPLES_BY_ROLE = {
    "predator": ["fox", "kestrel"],
    "prey": ["mountain_hare", "vole"],
    "playable": ["moose", "mountain_hare"],
    "fish": ["perch", "pike"],
}


class AnimalsStage:
    """Write `animals/<id>.json` for every new species in the plan."""

    name = "animals"

    def run(self, ctx: Any) -> dict[str, Any]:
        if ctx.world_path is None:
            raise ValueError("animals needs --world")
        world = Path(ctx.world_path)
        survey = json.loads((ctx.job_dir / "survey.json").read_text(encoding="utf-8"))
        plan = json.loads(
            (ctx.job_dir / "species-plan.json").read_text(encoding="utf-8")
        )
        region = json.loads(next((ctx.job_dir / "regions").glob("*.json")).read_text())
        new_ids = [
            entry["species"] for entry in plan["species"] if entry["decision"] == "new"
        ]
        by_id = {species["species"]: species for species in survey.get("species", [])}
        taxa = {
            entry["species"]: entry["taxon"]
            for entry in plan["species"]
            if entry["decision"] == "new"
        }
        roles = _roles_in_region(region)
        animals_dir = ctx.job_dir / "animals"
        animals_dir.mkdir(exist_ok=True)
        progress = _load_progress(animals_dir)
        rounds: dict[str, int] = dict(progress.get("rounds", {}))
        passed = set(progress.get("passed", [])) & set(new_ids)
        texts: dict[str, str] = {}
        for species_id in new_ids:
            path = animals_dir / f"{species_id}.json"
            if path.is_file():
                texts[species_id] = path.read_text(encoding="utf-8")
                rounds.setdefault(species_id, 1)
        for species_id in new_ids:
            if species_id in texts:
                continue
            texts[species_id] = self._draft(
                ctx, world, by_id[species_id], taxa[species_id], roles, region
            )
            rounds[species_id] = 1
            (animals_dir / f"{species_id}.json").write_text(
                texts[species_id], encoding="utf-8"
            )
        active = set(new_ids) - passed
        problems = self._validate(world, ctx, region, texts, active | passed)
        passed |= active - set(problems)
        _save_progress(animals_dir, new_ids, rounds, passed)
        while problems:
            for species_id, errors in problems.items():
                passed.discard(species_id)
                rounds[species_id] += 1
                if rounds[species_id] > MAX_ROUNDS_PER_SPECIES:
                    raise _Stopped(
                        f"{species_id} spent its {MAX_ROUNDS_PER_SPECIES} rounds: "
                        + "; ".join(errors)
                    )
                texts[species_id] = self._draft(
                    ctx,
                    world,
                    by_id[species_id],
                    taxa[species_id],
                    roles,
                    region,
                    errors,
                )
                (animals_dir / f"{species_id}.json").write_text(
                    texts[species_id], encoding="utf-8"
                )
            active = set(new_ids) - passed
            problems = self._validate(world, ctx, region, texts, active | passed)
            passed |= active - set(problems)
            _save_progress(animals_dir, new_ids, rounds, passed)
        return {
            "animal_files": sorted(f"animals/{i}.json" for i in new_ids),
            "rounds": {i: rounds[i] for i in new_ids},
        }

    def _draft(
        self,
        ctx: Any,
        world: Path,
        species: dict[str, Any],
        taxon: dict[str, Any],
        roles: dict[str, str],
        region: dict[str, Any],
        errors: list[str] | None = None,
    ) -> str:
        ctx.check_budget()
        role = roles.get(species["species"], species.get("role", "playable"))
        template = (PROMPTS_DIR / "animal_write.md").read_text(encoding="utf-8")
        template = _with_examples(template, world, role)
        prompt = Template(template).substitute(
            {
                "SPECIES_JSON": json.dumps(species, indent=2),
                "TAXON_JSON": json.dumps(taxon, indent=2),
                "ROLE_IN_REGION": role,
                "REGION_FOODS": ", ".join(sorted(_region_foods(region))),
                "REGION_CAST": ", ".join(sorted(_region_cast(region))),
                "CAST_TAGS": ", ".join(sorted(_cast_tags(world, region))),
                "NEEDED_TAGS": ", ".join(sorted(_needed_tags(world, region))),
                "EXISTING_TAGS": _existing_tags(world),
                "ARCHETYPES": ", ".join(sorted(_archetypes(world))),
                "MODEL_PATH": f"res://assets/animals/{species['species']}.glb",
                "ANIMAL_RULES": _animal_rules(world),
            }
        )
        if errors:
            notes = "\n".join(f"- {error}" for error in errors)
            prompt = (
                f"{prompt}\n\nPREVIOUS TRY had these problems,"
                f" fix exactly them:\n{notes}"
            )
        # Plain JSON mode, not structured output: the API's schema compiler
        # silently drops this schema's `$ref` properties (`diet` vanishes and
        # every round fails the same way). The schema checks every draft, in
        # code, in `_validate` below.
        response = ctx.chat([{"role": "user", "content": prompt}])
        return (response.choices[0].message.content or "").strip()

    def _validate(
        self,
        world: Path,
        ctx: Any,
        region: dict[str, Any],
        texts: dict[str, str],
        checked: set[str],
    ) -> dict[str, list[str]]:
        schema = json.loads(
            (world / "schemas" / "animal.schema.json").read_text(encoding="utf-8")
        )
        problems: dict[str, list[str]] = {}
        for species_id in checked:
            try:
                data = json.loads(texts[species_id])
            except json.JSONDecodeError as error:
                problems[species_id] = [f"not JSON: {error}"]
                continue
            try:
                jsonschema.validate(data, schema)
            except jsonschema.ValidationError as error:
                problems[species_id] = [f"broke the animal schema: {error.message}"]
                continue
        result = check_draft(world, ctx.job_dir, "animals")
        by_check = {entry.get("check"): entry for entry in result.get("results", [])}
        region_entry = by_check.get("region", {})
        if region_entry.get("status") == "fail":
            raise _Stopped(
                "the region broke after stage 4: "
                + "; ".join(region_entry.get("problems", [])[:5])
            )
        fatal = []
        for name in ("locale", "schema", "taxa", "animals"):
            entry = by_check.get(name, {})
            if entry.get("status") != "fail":
                continue
            if not entry.get("problems"):
                fatal.append(f"the {name} check failed without problems")
                continue
            for file, lines in problems_by_file(entry["problems"]).items():
                for line in lines:
                    self._route(texts, checked, problems, fatal, file, line)
        if fatal:
            raise _Stopped(
                "the checker failed outside the new files: " + "; ".join(fatal[:5])
            )
        return problems

    @staticmethod
    def _route(
        texts: dict[str, str],
        checked: set[str],
        problems: dict[str, list[str]],
        fatal: list[str],
        file: str | None,
        line: str,
    ) -> None:
        """Send one verdict problem to its species, or fail the stage.

        The models verdict is never read: it fails on the world's own
        unbuilt models. Missing-model complaints inside the animals
        verdict are the same world state, and skipped the same way —
        even when they name a new species, whose model is only built
        later at the models stage.
        """
        if _missing_model(line):
            return
        owner = _file_owner(file, texts) if file else None
        owners = [owner] if owner else [i for i in texts if mentions(line, i)]
        if owners:
            for species_id in owners:
                if species_id in checked:
                    text = line[2:].strip() if line.startswith("- ") else line.strip()
                    problems.setdefault(species_id, []).append(text)
            return
        fatal.append(line.strip() if not file else f"{file}: {line.strip()}")


class _Stopped(Exception):
    """The stage ran out of rounds, or the checkout itself fails."""


def _load_progress(animals_dir: Path) -> dict[str, Any]:
    path = animals_dir / PROGRESS_FILE
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_progress(
    animals_dir: Path,
    new_ids: list[str],
    rounds: dict[str, int],
    passed: set[str],
) -> None:
    (animals_dir / PROGRESS_FILE).write_text(
        json.dumps(
            {
                "rounds": {i: rounds[i] for i in new_ids if i in rounds},
                "passed": sorted(passed),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _roles_in_region(region: dict[str, Any]) -> dict[str, str]:
    roles = {}
    for group, role in (
        ("playable", "playable"),
        ("predators", "predator"),
        ("prey", "prey"),
    ):
        for animal_id in region.get("animals", {}).get(group, []):
            roles.setdefault(animal_id, role)
    for fish in region.get("fish", []):
        roles.setdefault(fish.get("kind", ""), "fish")
    roles.pop("", None)
    return roles


def _region_foods(region: dict[str, Any]) -> set[str]:
    return {food.get("kind", "") for food in region.get("food", [])} - {""}


def _region_cast(region: dict[str, Any]) -> set[str]:
    cast = set()
    for group in region.get("animals", {}).values():
        cast.update(group)
    cast.update(fish.get("kind", "") for fish in region.get("fish", []))
    return cast - {""}


def _cast_tags(world: Path, region: dict[str, Any]) -> set[str]:
    """Tags the cast's existing animals carry: legal `hunts` entries."""
    tags: set[str] = set()
    for stem in _region_cast(region):
        path = world / "animals" / f"{stem}.json"
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        tags.update(data.get("tags", []))
    return tags


def _needed_tags(world: Path, region: dict[str, Any]) -> set[str]:
    """Hunts tags no existing cast animal carries: new species owe them."""
    hunts: set[str] = set()
    for stem in _region_cast(region):
        path = world / "animals" / f"{stem}.json"
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        hunts.update(data.get("hunts", []))
    return hunts - _cast_tags(world, region)


def _existing_tags(world: Path) -> str:
    by_tag: dict[str, list[str]] = {}
    for path in sorted((world / "animals").glob("*.json")):
        if path.stem == "archetypes":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for tag in data.get("tags", []):
            by_tag.setdefault(tag, []).append(path.stem)
    return "\n".join(
        f"{tag}: {', '.join(files)}" for tag, files in sorted(by_tag.items())
    )


def _archetypes(world: Path) -> list[str]:
    data = json.loads(
        (world / "animals" / "archetypes.json").read_text(encoding="utf-8")
    )
    return sorted(data) if isinstance(data, dict) else sorted(data)


def _animal_rules(world: Path) -> str:
    text = (world / "CONTRIBUTING.md").read_text(encoding="utf-8")
    start = text.find("## Adding animals")
    rest = text.find("\n## ", start + 1)
    return text[start:rest].strip() if start >= 0 else ""


def _with_examples(template: str, world: Path, role: str) -> str:
    names = [
        name
        for name in EXAMPLES_BY_ROLE.get(role, EXAMPLES_BY_ROLE["playable"])
        if (world / "animals" / f"{name}.json").is_file()
    ][:2]
    for index, name in enumerate(names, start=1):
        # Escape dollars: example text must survive the substitute below.
        content = (world / "animals" / f"{name}.json").read_text(encoding="utf-8")
        content = content.replace("$", "$$")
        template = template.replace(f"$EXAMPLE_{index}_NAME", name)
        template = template.replace(f"$EXAMPLE_{index}", content)
    for leftover in ("$EXAMPLE_1_NAME", "$EXAMPLE_1", "$EXAMPLE_2_NAME", "$EXAMPLE_2"):
        template = template.replace(leftover, "(no example)")
    return template


def _file_owner(file: str, texts: dict[str, str]) -> str | None:
    """The new species a FAIL file belongs to, if any."""
    for species_id in texts:
        if file == f"animals/{species_id}.json":
            return species_id
    return None


def _missing_model(line: str) -> bool:
    """The world's own unbuilt models: known red state, not the draft."""
    return "model file not found" in line or ".glb is missing" in line
