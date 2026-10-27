"""The one-shot animal maker (spec 04 "The makers (animal first)").

The minimum set says what's missing; this stage makes the animals:
the one prey first, then the animal itself, each in one go. One
model call writes the animal file and its model spec together (the
`animal_write` and `model_spec` prompts, one shot: the `start_from`
spec as the only example, no reference sheet); `tools/build_model.sh`
builds it in a scratch copy of the world; the world's own checks
decide. One try, then at most one fix round with the checks' errors.
Still failing: the build stops with the reason, and the thing goes
to a person, as the eagle did.

Runs alone: `builder stage oneshot --job <dir> --record`. Writes
into the job's own folder, never into the world checkout.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from string import Template
from typing import Any

import jsonschema

from ..animal_list import find_entry, load_list, match_by_name
from ..animal_list import find_list as find_animal_list
from ..prompts import PROMPTS_DIR, for_children
from ..world import check_draft, problems_by_file
from . import animals as animal_writers
from .capability import NotPossibleError
from .models import _ensure_sandbox, build_in_sandbox

#: The four verdicts that decide a made animal (spec 04).
VERDICTS = ("animals", "models", "taxa", "region")

#: Tries per thing: the write, then at most one fix round.
MAX_TRIES = 2


class AnimalMakerStage:
    """Make the minimum set's animals: prey first, then the animal."""

    name = "oneshot"

    def __init__(
        self,
        author: str = "Animundo Builder",
        chat: Any | None = None,
        build: Any | None = None,
    ) -> None:
        self.author = author
        self._chat = chat
        self._build = build or build_in_sandbox

    def run(self, ctx: Any) -> dict[str, Any]:
        if ctx.world_path is None:
            raise ValueError("oneshot needs --world")
        world = Path(ctx.world_path)
        brief = json.loads((ctx.job_dir / "home.json").read_text(encoding="utf-8"))
        research = json.loads(
            (ctx.job_dir / "research.json").read_text(encoding="utf-8")
        )
        minset = json.loads((ctx.job_dir / "minset.json").read_text(encoding="utf-8"))
        region = json.loads(
            next((ctx.job_dir / "regions").glob("*.json")).read_text(encoding="utf-8")
        )
        data = load_list(find_animal_list(world))
        targets = _targets(brief, minset, data["animals"], ctx.job_dir)
        before = ctx.tracker.spent_usd
        made = []
        for target in targets:
            made.append(self._make(ctx, world, brief, research, region, target))
        cost = round(ctx.tracker.spent_usd - before, 6)
        checks = ctx.job_dir / "checks"
        checks.mkdir(parents=True, exist_ok=True)
        (checks / "oneshot.json").write_text(
            json.dumps({"cost_usd": cost, "made": made}, indent=2), encoding="utf-8"
        )
        return {
            "animal_files": [f"animals/{animal_id}.json" for animal_id in made],
            "cost_usd": cost,
        }

    def _make(
        self,
        ctx: Any,
        world: Path,
        brief: dict[str, Any],
        research: dict[str, Any],
        region: dict[str, Any],
        target: dict[str, Any],
    ) -> str:
        """One animal, one try plus one fix round. Returns its id."""
        animal_id = target["id"]
        prompt = self._prompt(world, brief, research, region, target)
        errors: list[str] | None = None
        for attempt in range(1, MAX_TRIES + 1):
            ctx.check_budget()
            answer = self._ask(ctx, prompt, errors)
            files, file_errors = self._files(world, answer, animal_id)
            if files is not None:
                self._place(ctx, world, animal_id, files, attempt)
                build_errors = self._build_and_check(ctx, world, animal_id, attempt)
                file_errors = build_errors
            self._save_try(ctx, animal_id, attempt, answer, files, file_errors)
            if not file_errors:
                self._save_final(ctx, world, animal_id)
                return animal_id
            errors = file_errors
        raise _Stopped(
            f"{animal_id}: still failing after one fix round "
            f"({'; '.join(errors or [])}); a person makes it"
        )

    def _ask(self, ctx: Any, prompt: str, errors: list[str] | None) -> str:
        if errors:
            notes = "\n".join(f"- {error}" for error in errors)
            prompt = (
                f"{prompt}\n\nPREVIOUS TRY had these problems,"
                f" fix exactly them:\n{notes}"
            )
        chat = self._chat or ctx.chat
        response = chat([{"role": "user", "content": prompt}])
        return (response.choices[0].message.content or "").strip()

    def _files(
        self, world: Path, answer: str, animal_id: str
    ) -> tuple[dict[str, Any] | None, list[str]]:
        """The try's two files, or why the answer holds none."""
        try:
            wrapped = json.loads(_unfence(answer))
        except json.JSONDecodeError as error:
            return None, [f"not JSON: {error}"]
        if not isinstance(wrapped, dict) or sorted(wrapped) != ["animal", "model"]:
            return None, [
                'reply with one object {"animal": ..., "model": ...}, nothing else'
            ]
        problems = []
        animal_schema = json.loads(
            (world / "schemas" / "animal.schema.json").read_text(encoding="utf-8")
        )
        try:
            jsonschema.validate(wrapped["animal"], animal_schema)
        except jsonschema.ValidationError as error:
            problems.append(f"the animal file broke its schema: {error.message}")
        spec_schema = json.loads(
            (world / "schemas" / "model.schema.json").read_text(encoding="utf-8")
        )
        try:
            jsonschema.validate(wrapped["model"], spec_schema)
        except jsonschema.ValidationError as error:
            problems.append(f"the model spec broke its schema: {error.message}")
        if problems:
            return None, problems
        if wrapped["model"].get("id") != animal_id:
            return None, [
                f"the model spec's id is {wrapped['model'].get('id')!r},"
                f" not {animal_id!r}"
            ]
        return {"animal": wrapped["animal"], "model": wrapped["model"]}, []

    def _place(
        self,
        ctx: Any,
        world: Path,
        animal_id: str,
        files: dict[str, Any],
        attempt: int,
    ) -> None:
        """Lay the try's files where the build and the checker find them."""
        animals_dir = ctx.job_dir / "animals"
        animals_dir.mkdir(exist_ok=True)
        (animals_dir / f"{animal_id}.json").write_text(
            json.dumps(files["animal"], indent=2), encoding="utf-8"
        )
        models_dir = ctx.job_dir / "models"
        models_dir.mkdir(exist_ok=True)
        (models_dir / f"{animal_id}.json").write_text(
            json.dumps(files["model"], indent=2), encoding="utf-8"
        )
        sandbox = _ensure_sandbox(ctx.job_dir, world)
        (sandbox / "models" / f"{animal_id}.json").write_text(
            json.dumps(files["model"], indent=2), encoding="utf-8"
        )

    def _build_and_check(
        self, ctx: Any, world: Path, animal_id: str, attempt: int
    ) -> list[str]:
        """Build the spec, then read the four verdicts. Errors, if any."""
        sandbox = _ensure_sandbox(ctx.job_dir, world)
        built = self._build(sandbox, animal_id)
        folder = ctx.job_dir / "tries" / animal_id / str(attempt)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "build.log").write_text(built["log"], encoding="utf-8")
        if not built["ok"] and not built["log"].strip():
            raise _Stopped(
                f"{animal_id}: the build failed with no output: Blender itself "
                "did not run here, not the spec. A person looks."
            )
        for name, rel in (
            (f"{animal_id}.glb", Path("assets") / "animals" / f"{animal_id}.glb"),
            (f"{animal_id}.png", Path("models") / "previews" / f"{animal_id}.png"),
        ):
            src = sandbox / rel
            if src.is_file():
                dest = ctx.job_dir / "models" / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(src.read_bytes())
        errors = [] if built["ok"] else [f"the build failed: {built['log']}"]
        verdicts = _verdicts(world, ctx, animal_id, attempt)
        for name in VERDICTS:
            entry = verdicts.get(name, {})
            if entry.get("status") != "fail":
                continue
            if not entry.get("problems"):
                raise _Stopped(
                    f"{animal_id}: the {name} check failed without problems; "
                    "a person looks"
                )
            for file, lines in problems_by_file(entry["problems"]).items():
                for line in lines:
                    text = line[2:].strip() if line.startswith("- ") else line.strip()
                    errors.append(f"{file}: {text}" if file else text)
        (ctx.job_dir / "tries" / animal_id / str(attempt) / "errors.json").write_text(
            json.dumps(errors, indent=2), encoding="utf-8"
        )
        return errors

    def _save_try(
        self,
        ctx: Any,
        animal_id: str,
        attempt: int,
        answer: str,
        files: dict[str, Any] | None,
        errors: list[str],
    ) -> None:
        folder = ctx.job_dir / "tries" / animal_id / str(attempt)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "answer.json").write_text(answer, encoding="utf-8")
        if files is not None:
            (folder / "animal.json").write_text(
                json.dumps(files["animal"], indent=2), encoding="utf-8"
            )
            (folder / "spec.json").write_text(
                json.dumps(files["model"], indent=2), encoding="utf-8"
            )
        if not (folder / "errors.json").is_file():
            (folder / "errors.json").write_text(
                json.dumps(errors, indent=2), encoding="utf-8"
            )

    def _save_final(self, ctx: Any, world: Path, animal_id: str) -> None:
        """The passing files, where the pack and the report find them."""
        sandbox = _ensure_sandbox(ctx.job_dir, world)
        preview = sandbox / "models" / "previews" / f"{animal_id}.png"
        if preview.is_file():
            dest = ctx.job_dir / "models" / "previews" / f"{animal_id}.png"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(preview.read_bytes())

    def _prompt(
        self,
        ctx_world: Path,
        brief: dict[str, Any],
        research: dict[str, Any],
        region: dict[str, Any],
        target: dict[str, Any],
    ) -> str:
        world = ctx_world
        animal_id = target["id"]
        entry = target["entry"]
        start_from = target["start_from"]
        template = (PROMPTS_DIR / "animal_write.md").read_text(encoding="utf-8")
        template = _with_start_from(template, world, start_from)
        roles = animal_writers._roles_in_region(region)
        role = roles.get(animal_id, "prey" if target["prey"] else "playable")
        animal_prompt = Template(template).substitute(
            {
                "SPECIES_JSON": json.dumps(
                    _species_json(animal_id, entry, brief, research, target),
                    indent=2,
                ),
                "TAXON_JSON": json.dumps(_taxon(entry), indent=2),
                "ROLE_IN_REGION": role,
                "REGION_FOODS": ", ".join(sorted(animal_writers._region_foods(region))),
                "REGION_CAST": ", ".join(sorted(animal_writers._region_cast(region))),
                "CAST_TAGS": ", ".join(
                    sorted(animal_writers._cast_tags(world, region))
                ),
                "NEEDED_TAGS": ", ".join(
                    sorted(animal_writers._needed_tags(world, region))
                ),
                "EXISTING_TAGS": animal_writers._existing_tags(world),
                "ARCHETYPES": ", ".join(sorted(animal_writers._archetypes(world))),
                "MODEL_PATH": f"res://assets/animals/{animal_id}.glb",
                "ANIMAL_RULES": animal_writers._animal_rules(world),
                "FOR_CHILDREN": for_children(),
            }
        )
        spec_template = (PROMPTS_DIR / "model_spec.md").read_text(encoding="utf-8")
        rules = (
            (world / "ART.md").read_text(encoding="utf-8")
            + "\n\n"
            + (world / "models" / "README.md").read_text(encoding="utf-8")
        )
        spec_prompt = Template(spec_template).substitute(
            {
                "SPECIES": animal_id,
                "TITLE": entry.get("name", animal_id),
                "LATIN": entry.get("latin", ""),
                "AUTHOR": self.author,
                "DESCRIPTION": _description(entry, research, target),
                "MEASUREMENTS": json.dumps(_measurements(research, target), indent=2),
                "CLOSEST_NAME": start_from,
                "CLOSEST_SPEC": (world / "models" / f"{start_from}.json").read_text(
                    encoding="utf-8"
                ),
                "CHANGE": (
                    "One shot, no reference sheet: adapt the closest spec's "
                    "bones and layout to this species' body plan and the "
                    "sourced sizes below; do not copy its sizes."
                ),
                "RULES": rules,
            }
        )
        return (
            f"{animal_prompt}\n\n---\n\n{spec_prompt}\n\n"
            "## Combined output\n\n"
            "Reply with one JSON object, nothing else: "
            '{"animal": <the animal file>, "model": <the model spec>}.'
        )


def _targets(
    brief: dict[str, Any],
    minset: dict[str, Any],
    entries: list[dict[str, Any]],
    job_dir: Path,
) -> list[dict[str, Any]]:
    """What to make, in order: the prey, then the animal. Made ones skip."""
    targets = []
    prey = minset.get("prey") or {}
    if prey:
        match = match_by_name(entries, prey["name"])
        if match is None:
            raise NotPossibleError(
                f"{prey['name']} is no longer on the animal list; "
                "a person picks the prey"
            )
        if not _made(job_dir, match["id"]):
            if not match.get("start_from"):
                raise NotPossibleError(
                    f"{prey['name']} has no starting spec on the list; "
                    "a person picks one"
                )
            targets.append(
                {
                    "id": match["id"],
                    "entry": match,
                    "start_from": match["start_from"],
                    "prey": True,
                    "size_cm": prey.get("size_cm"),
                }
            )
    if minset.get("make_animal"):
        entry = find_entry(entries, brief["species_key"])
        if not _made(job_dir, entry["id"]):
            start_from = entry.get("start_from") or brief.get("start_from")
            if not start_from:
                raise NotPossibleError(
                    f"{brief['latin']} has no starting spec; a person picks one"
                )
            targets.append(
                {
                    "id": entry["id"],
                    "entry": entry,
                    "start_from": start_from,
                    "prey": False,
                    "size_cm": None,
                }
            )
    return targets


def _made(job_dir: Path, animal_id: str) -> bool:
    """Whether a run already made this animal: both files in the job."""
    return (job_dir / "animals" / f"{animal_id}.json").is_file() and (
        job_dir / "models" / f"{animal_id}.json"
    ).is_file()


def _taxon(entry: dict[str, Any]) -> dict[str, Any]:
    """The GBIF block the writer copies exactly (list latin rules)."""
    latin = entry["latin"]
    rank = "SUBSPECIES" if len(latin.split()) > 2 else "SPECIES"
    return {"gbif_key": entry["gbif_key"], "rank": rank, "name": latin}


def _species_json(
    animal_id: str,
    entry: dict[str, Any],
    brief: dict[str, Any],
    research: dict[str, Any],
    target: dict[str, Any],
) -> dict[str, Any]:
    """What the writer knows: the research for the animal, size for prey."""
    species: dict[str, Any] = {
        "species": animal_id,
        "latin": entry["latin"],
        "habitats": entry.get("habitats", brief.get("habitats", [])),
    }
    if target["prey"]:
        species["length_cm"] = target["size_cm"]
        species["note"] = (
            "The main animal's prey; write its year and food from the "
            "region's foods and cast above."
        )
        return species
    species["size"] = research["size"]
    species["year"] = research["year"]
    species["foods"] = [research["main_food"], *research["other_foods"]]
    return species


def _measurements(research: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """Sourced sizes in centimetres; the spec divides by 100."""
    if target["prey"]:
        return {"length_cm": target["size_cm"]}
    size = research["size"]
    measurements = {"length_cm": size["length_cm"]}
    if size.get("weight_kg") is not None:
        measurements["weight_kg"] = size["weight_kg"]
    return measurements


def _description(
    entry: dict[str, Any], research: dict[str, Any], target: dict[str, Any]
) -> str:
    """What the sculptor knows of its life; shape comes from the start."""
    if target["prey"]:
        return (
            f"{entry['name']} ({entry['latin']}), about "
            f"{target['size_cm']} cm long: the main animal's prey."
        )
    year = research["year"]
    foods = [research["main_food"], *research["other_foods"]]
    dinner = ", ".join(food["name"] for food in foods)
    return (
        f"{entry['name']} ({entry['latin']}): {year['shelter']} "
        f"{year['young']} Eats {dinner}."
    )


def _with_start_from(template: str, world: Path, start_from: str) -> str:
    """The one-shot example: the `start_from` animal, and only it."""
    content = (world / "animals" / f"{start_from}.json").read_text(encoding="utf-8")
    content = content.replace("$", "$$")
    template = template.replace("$EXAMPLE_1_NAME", start_from)
    template = template.replace("$EXAMPLE_1", content)
    template = template.replace("$EXAMPLE_2_NAME", "(only one example: one shot)")
    template = template.replace("$EXAMPLE_2", "")
    return template


def _verdicts(
    world: Path, ctx: Any, animal_id: str, attempt: int
) -> dict[str, dict[str, Any]]:
    result = check_draft(world, ctx.job_dir, f"oneshot-{animal_id}-{attempt}")
    return {entry.get("check", ""): entry for entry in result.get("results", [])}


def _unfence(answer: str) -> str:
    """Strict JSON, with or without the fences models love to add."""
    text = answer.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    return match.group(1) if match else text


class _Stopped(Exception):
    """The thing has no passing files. The job stops; a person makes it."""
