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
import subprocess
import sys
from pathlib import Path
from string import Template
from typing import Any

import jsonschema

from .. import taxa
from ..animal_list import find_entry, load_list, match_by_name
from ..animal_list import find_list as find_animal_list
from ..prompts import PROMPTS_DIR, for_children
from ..taxa import taxon_block
from ..world import check_draft, problems_by_file
from . import animals as animal_writers
from . import plants as plant_builders
from .capability import NotPossibleError
from .models import _ensure_sandbox, build_in_sandbox
from .species_plan import recorded_near, taxon_same

#: The four verdicts that decide a made animal (spec 04).
VERDICTS = ("animals", "models", "taxa", "region")

#: Tries per thing: the write, then at most one fix round.
MAX_TRIES = 2

#: At most two new plant specs per home (spec 04).
MAX_NEW_PLANTS = 2

#: The worked example every home is written from.
CLOSEST_HOME = "boreal_forest"


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
        self._update_plan(ctx, brief, targets)
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

    def _update_plan(
        self, ctx: Any, brief: dict[str, Any], targets: list[dict[str, Any]]
    ) -> None:
        """The made prey joins the species plan, as new (#54).

        The home wrote the plan before anything was made; the
        pack reads it to decide what ships. Without this the
        prey's files would never leave the job.
        """
        plan_path = ctx.job_dir / "species-plan.json"
        if not plan_path.is_file():
            return
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        have = {entry.get("species") for entry in plan.get("species", [])}
        for target in targets:
            if target["id"] in have:
                continue
            entry = target["entry"]
            plan.setdefault("species", []).append(
                {
                    "species": target["id"],
                    "decision": "new",
                    "reason": (
                        f"made one-shot as {brief['latin']}'s prey"
                        if target["prey"]
                        else "made one-shot for this home"
                    ),
                    "wild": True,
                    "taxon": taxon_block(entry["gbif_key"], entry["latin"]),
                }
            )
        plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")

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
        match = None
        if prey.get("gbif_key") is not None:
            match = next(
                (
                    entry
                    for entry in entries
                    if entry.get("gbif_key") == prey["gbif_key"]
                ),
                None,
            )
        if match is None:
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


class HomeMakerStage:
    """Make the minimum set's home: the template plus its plants."""

    name = "newhome"

    def __init__(
        self,
        author: str = "Animundo Builder",
        chat: Any | None = None,
        build: Any | None = None,
        check_homes: Any | None = None,
        check_models: Any | None = None,
        match: Any | None = None,
        recorded: Any | None = None,
    ) -> None:
        self.author = author
        self._chat = chat
        self._build = build or _build_plant
        self._check_homes = check_homes or check_homes_in_sandbox
        self._check_models = check_models or check_models_in_sandbox
        self._match = match or _match_plant
        self._recorded = recorded or recorded_near

    def run(self, ctx: Any) -> dict[str, Any]:
        if ctx.world_path is None:
            raise ValueError("newhome needs --world")
        world = Path(ctx.world_path)
        brief = json.loads((ctx.job_dir / "home.json").read_text(encoding="utf-8"))
        research = json.loads(
            (ctx.job_dir / "research.json").read_text(encoding="utf-8")
        )
        minset = json.loads((ctx.job_dir / "minset.json").read_text(encoding="utf-8"))
        if not minset.get("make_home"):
            return {"home_files": [], "cost_usd": 0.0}
        habitat = brief["home"]
        made = ctx.job_dir / "homes" / f"{habitat}.json"
        if made.is_file():
            return {"home_files": [f"homes/{habitat}.json"], "cost_usd": 0.0}
        before = ctx.tracker.spent_usd
        plants = self._make(ctx, world, brief, research, habitat)
        cost = round(ctx.tracker.spent_usd - before, 6)
        checks = ctx.job_dir / "checks"
        checks.mkdir(parents=True, exist_ok=True)
        (checks / "newhome.json").write_text(
            json.dumps({"cost_usd": cost, "made": habitat, "plants": plants}, indent=2),
            encoding="utf-8",
        )
        return {
            "home_files": [f"homes/{habitat}.json"],
            "plant_files": [f"models/plants/{i}.json" for i in plants],
            "cost_usd": cost,
        }

    def _make(
        self,
        ctx: Any,
        world: Path,
        brief: dict[str, Any],
        research: dict[str, Any],
        habitat: str,
    ) -> list[str]:
        """One home, one try plus one fix round. Returns new plant ids."""
        prompt = self._prompt(world, brief, research, habitat)
        errors: list[str] | None = None
        for attempt in range(1, MAX_TRIES + 1):
            ctx.check_budget()
            answer = self._ask(ctx, prompt, errors)
            files, file_errors = self._files(world, answer, habitat, research, brief)
            if files is not None:
                file_errors = self._resolve_taxa(world, files)
            if files is not None and not file_errors:
                self._place(ctx, world, habitat, files, attempt)
                file_errors = self._build_and_check(ctx, world, habitat, files, attempt)
            self._save_try(ctx, habitat, attempt, answer, files, file_errors)
            if files is not None and not file_errors:
                self._save_final(ctx, world, habitat, files)
                return sorted(files["plants"])
            errors = file_errors or ["the answer held no usable home"]
        raise _Stopped(
            f"{habitat}: still failing after one fix round "
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
        self,
        world: Path,
        answer: str,
        habitat: str,
        research: dict[str, Any],
        brief: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, list[str]]:
        """The try's home and plant specs, or why the answer holds none."""
        try:
            wrapped = json.loads(_unfence(answer))
        except json.JSONDecodeError as error:
            return None, [f"not JSON: {error}"]
        if not isinstance(wrapped, dict) or sorted(wrapped) != ["home", "plants"]:
            return None, [
                'reply with one object {"home": ..., "plants": {...}}, nothing else'
            ]
        home, plants = wrapped["home"], wrapped["plants"]
        problems = []
        if not isinstance(home, dict) or home.get("id") != habitat:
            problems.append(f"home.id is not {habitat!r}")
        if not isinstance(plants, dict):
            problems.append("plants is not an object of specs")
            plants = {}
        if len(plants) > MAX_NEW_PLANTS:
            problems.append(f"at most {MAX_NEW_PLANTS} new plants, not {len(plants)}")
        for plant_id, spec in plants.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]*", plant_id):
                problems.append(f"{plant_id!r} is not a usable plant id")
            elif not isinstance(spec, dict) or spec.get("id") != plant_id:
                problems.append(f"the {plant_id} spec's id is not {plant_id!r}")
            else:
                try:
                    plant_builders._validate_plant(world, spec)
                except jsonschema.ValidationError as error:
                    problems.append(
                        f"the {plant_id} spec broke its schema: {error.message}"
                    )
        if not problems:
            known = {path.stem for path in (world / "models" / "plants").glob("*.json")}
            for plant in home.get("ground_cover", {}).get("plants", []):
                model = plant.get("model", "")
                if model not in known and model not in plants:
                    problems.append(
                        f"ground cover uses {model!r}, which is neither "
                        "an existing plant nor a new spec"
                    )
        if not problems:
            problems.extend(
                self._carryover_errors(world, home, set(plants), research, brief)
            )
        if problems:
            return None, problems
        return {"home": home, "plants": dict(plants)}, []

    def _carryover_errors(
        self,
        world: Path,
        home: dict[str, Any],
        new_ids: set[str],
        research: dict[str, Any],
        brief: dict[str, Any],
    ) -> list[str]:
        """Nothing living carries over from the example home (#55).

        Every fish, ground plant and tree plant id must be a
        species the research named or GBIF recorded near the
        spot; only land, sky and colour structure carry over.
        """
        surroundings = research.get("surroundings", [])
        names = {
            thing.get("name", "").strip().lower()
            for thing in surroundings
            if thing.get("name")
        }
        keys = set()
        for thing in surroundings:
            if thing.get("latin"):
                taxon = taxa.resolve(world, thing["latin"], plant=True)
                if taxon is not None:
                    keys.add(taxon["gbif_key"])
        center = brief.get("center", {})
        lat, lon = float(center.get("lat", 0.0)), float(center.get("lon", 0.0))
        errors = []
        for fish in home.get("fish", []):
            kind = fish.get("kind", "")
            path = world / "animals" / f"{kind}.json"
            if not path.is_file():
                errors.append(f"fish {kind!r} is no animal the library has")
                continue
            key = (json.loads(path.read_text()).get("taxon") or {}).get("gbif_key")
            if not isinstance(key, int) or not self._recorded_here(lat, lon, key):
                errors.append(
                    f"fish {kind!r} swims in the template's waters, not here; drop it"
                )
        for plant in home.get("ground_cover", {}).get("plants", []):
            model = plant.get("model", "")
            if model in new_ids:
                continue
            spec_path = world / "models" / "plants" / f"{model}.json"
            if not spec_path.is_file():
                continue  # reported above as neither existing nor new
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            labels = {
                model.replace("_", " "),
                str(spec.get("latin", "")).lower(),
                str(spec.get("name", "")).lower(),
            } - {""}
            named = spec.get("latin") and self._latin_key(world, spec["latin"]) in keys
            named = named or any(
                name == label or name in label or label in name
                for name in names
                for label in labels
            )
            key = (spec.get("taxon") or {}).get("gbif_key")
            recorded = isinstance(key, int) and self._recorded_here(lat, lon, key)
            if not named and not recorded:
                errors.append(
                    f"{model} grows in the template's land, not here; "
                    "drop it or write a spec for what the research names"
                )
        pages = _tree_pages(world)
        for entry in home.get("trees", {}).get("mix", []):
            plant = entry.get("plant") or ""
            if not plant:
                continue
            if plant not in pages:
                errors.append(
                    f"tree plant {plant!r} has no spec or page; leave plant out"
                )
                continue
            label = plant.replace("_", " ")
            if not any(name in label or label in name for name in names):
                errors.append(
                    f"tree plant {plant!r} is named nowhere in the surroundings; "
                    "leave plant out"
                )
        return errors

    def _latin_key(self, world: Path, latin: str) -> int | None:
        taxon = taxa.resolve(world, latin, plant=True)
        return taxon["gbif_key"] if taxon is not None else None

    def _recorded_here(self, lat: float, lon: float, key: int) -> bool:
        """GBIF occurrences near the spot; unknown on an outage.

        A failed query must not fail the try: the named path
        still guards, and the reviewer re-runs.
        """
        try:
            return bool(self._recorded(lat, lon, key))
        except Exception:  # noqa: BLE001 - GBIF is down, not the home
            return True

    def _resolve_taxa(self, world: Path, files: dict[str, Any]) -> list[str]:
        """Give every new plant its GBIF taxon, or reuse what exists.

        A latin the world already grows reuses its spec (the home
        points at it); anything else keeps its taxon on the spec,
        with GBIF's accepted name as its latin. Two new specs
        never share one species.
        """
        errors = []
        seen: dict[int, str] = {}
        for plant_id in sorted(files["plants"]):
            spec = files["plants"][plant_id]
            try:
                found = self._match(world, spec.get("latin", ""))
            except Exception as error:  # noqa: BLE001 - GBIF or the latin
                errors.append(f"{plant_id}: {error}")
                continue
            key = found["taxon"]["gbif_key"]
            if key in seen:
                errors.append(
                    f"{plant_id} is the same species as {seen[key]}; "
                    "one real species, one spec"
                )
                continue
            seen[key] = plant_id
            target = plant_builders._reuse_target(found)
            if target is not None:
                _rewrite_plant(files["home"], plant_id, target)
                del files["plants"][plant_id]
            else:
                spec["taxon"] = found["taxon"]
                spec["latin"] = found["taxon"]["name"]
        return errors

    def _place(
        self,
        ctx: Any,
        world: Path,
        habitat: str,
        files: dict[str, Any],
        attempt: int,
    ) -> None:
        """Lay the try's files where the build and the checker find them."""
        homes_dir = ctx.job_dir / "homes"
        homes_dir.mkdir(exist_ok=True)
        (homes_dir / f"{habitat}.json").write_text(
            json.dumps(files["home"], indent=2), encoding="utf-8"
        )
        plants_dir = ctx.job_dir / "models" / "plants"
        plants_dir.mkdir(parents=True, exist_ok=True)
        sandbox = _ensure_sandbox(ctx.job_dir, world)
        (sandbox / "homes" / f"{habitat}.json").write_text(
            json.dumps(files["home"], indent=2), encoding="utf-8"
        )
        for plant_id, spec in files["plants"].items():
            text = json.dumps(spec, indent=2)
            (plants_dir / f"{plant_id}.json").write_text(text, encoding="utf-8")
            (sandbox / "models" / "plants" / f"{plant_id}.json").write_text(
                text, encoding="utf-8"
            )

    def _build_and_check(
        self,
        ctx: Any,
        world: Path,
        habitat: str,
        files: dict[str, Any],
        attempt: int,
    ) -> list[str]:
        """Build the new plants, then read the world's verdicts."""
        sandbox = _ensure_sandbox(ctx.job_dir, world)
        folder = ctx.job_dir / "tries" / habitat / str(attempt)
        folder.mkdir(parents=True, exist_ok=True)
        errors = []
        logs = []
        for plant_id in sorted(files["plants"]):
            built = self._build(sandbox, plant_id)
            logs.append(f"== {plant_id} ==\n{built['log']}")
            if not built["ok"] and not built["log"].strip():
                raise _Stopped(
                    f"{plant_id}: the build failed with no output: Blender "
                    "itself did not run here, not the spec. A person looks."
                )
            if not built["ok"]:
                errors.append(f"the {plant_id} build failed: {built['log']}")
                continue
            for name, rel in (
                (f"{plant_id}.glb", Path("assets") / "plants" / f"{plant_id}.glb"),
                (
                    f"{plant_id}.png",
                    Path("models") / "previews" / "plants" / f"{plant_id}.png",
                ),
            ):
                src = sandbox / rel
                if src.is_file():
                    dest = ctx.job_dir / "models" / "plants" / name
                    dest.write_bytes(src.read_bytes())
        (folder / "build.log").write_text("\n".join(logs), encoding="utf-8")
        errors.extend(self._check_homes(sandbox, habitat))
        if files["plants"]:
            errors.extend(self._check_models(sandbox, sorted(files["plants"])))
        (folder / "errors.json").write_text(
            json.dumps(errors, indent=2), encoding="utf-8"
        )
        return errors

    def _save_try(
        self,
        ctx: Any,
        habitat: str,
        attempt: int,
        answer: str,
        files: dict[str, Any] | None,
        errors: list[str],
    ) -> None:
        folder = ctx.job_dir / "tries" / habitat / str(attempt)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "answer.json").write_text(answer, encoding="utf-8")
        if files is not None:
            (folder / "home.json").write_text(
                json.dumps(files["home"], indent=2), encoding="utf-8"
            )
            for plant_id, spec in files["plants"].items():
                (folder / f"plant-{plant_id}.json").write_text(
                    json.dumps(spec, indent=2), encoding="utf-8"
                )
        if not (folder / "errors.json").is_file():
            (folder / "errors.json").write_text(
                json.dumps(errors, indent=2), encoding="utf-8"
            )

    def _save_final(
        self,
        ctx: Any,
        world: Path,
        habitat: str,
        files: dict[str, Any],
    ) -> None:
        """The passing files, where the pack and the report find them."""
        sandbox = _ensure_sandbox(ctx.job_dir, world)
        for plant_id in files["plants"]:
            preview = sandbox / "models" / "previews" / "plants" / f"{plant_id}.png"
            if preview.is_file():
                dest = (
                    ctx.job_dir / "models" / "previews" / "plants" / f"{plant_id}.png"
                )
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(preview.read_bytes())

    def _prompt(
        self,
        world: Path,
        brief: dict[str, Any],
        research: dict[str, Any],
        habitat: str,
    ) -> str:
        template = (PROMPTS_DIR / "home_write.md").read_text(encoding="utf-8")
        existing = sorted(
            path.stem for path in (world / "models" / "plants").glob("*.json")
        )
        bush = (world / "models" / "plants" / "bilberry.json").read_text()
        ground = (world / "models" / "plants" / "wood_anemone.json").read_text()
        return Template(template).substitute(
            {
                "HABITAT": habitat,
                "SURROUNDINGS": json.dumps(research["surroundings"], indent=2),
                "CLOSEST_NAME": CLOSEST_HOME,
                "CLOSEST_HOME": (
                    (world / "homes" / f"{CLOSEST_HOME}.json").read_text()
                ).replace("$", "$$"),
                "MAX_PLANTS": str(MAX_NEW_PLANTS),
                "EXISTING_PLANTS": ", ".join(existing) or "(none yet)",
                "TREE_SHAPES": "spruce, pine, birch, round, rowan",
                "TREE_PLANTS": ", ".join(sorted(_tree_pages(world))) or "(none yet)",
                "PLANT_EXAMPLES": (
                    f"A bush (bilberry):\n{bush}\n\n"
                    f"A ground plant (wood anemone):\n{ground}"
                ).replace("$", "$$"),
                "REGION_RULES": _region_rules(world),
                "PLANT_RULES": plant_builders._plant_rules(world),
                "AUTHOR": self.author,
                "FOR_CHILDREN": for_children(),
            }
        )


def _region_rules(world: Path) -> str:
    """The region-file section of the world's CONTRIBUTING.md."""
    text = (world / "CONTRIBUTING.md").read_text(encoding="utf-8")
    start = text.find("## The region file")
    rest = text.find("\n## ", start + 1)
    return text[start:rest].strip() if start >= 0 else ""


def _tree_pages(world: Path) -> set[str]:
    """Tree ids a `plant` may name: specs or locale plant pages."""
    ids = {path.stem for path in (world / "models" / "trees").glob("*.json")}
    pages = world / "locale" / "en" / "plants.json"
    if pages.is_file():
        try:
            keys = json.loads(pages.read_text(encoding="utf-8"))
        except ValueError:
            return ids
        for key in keys:
            if key.startswith("plant.") and key.count(".") >= 2:
                ids.add(key.split(".")[1])
    return ids


def _build_plant(sandbox: Path, plant_id: str) -> dict[str, Any]:
    """Run the world's build script on one plant spec, in the sandbox."""
    return build_in_sandbox(sandbox, f"models/plants/{plant_id}.json")


def _match_plant(world: Path, latin: str) -> dict[str, Any]:
    return taxon_same(world, latin, plant=True)


def _rewrite_plant(home: dict[str, Any], plant_id: str, target: str) -> None:
    """Point the home's ground cover at the existing spec."""
    for plant in home.get("ground_cover", {}).get("plants", []):
        if plant.get("model") == plant_id:
            plant["model"] = target


def check_homes_in_sandbox(sandbox: Path, habitat: str) -> list[str]:
    """Our home's lines from the world's `homes.py check`.

    Other homes' failures are the world's own state, not this
    try's; only this habitat's block feeds back.
    """
    try:
        done = subprocess.run(
            [sys.executable, "tools/homes.py", "check"],
            cwd=sandbox,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise _Stopped(f"homes.py check did not run: {error}")
    want = f"homes/{habitat}.json"
    errors: list[str] = []
    ours = False
    for line in (done.stdout + done.stderr).splitlines():
        if line.startswith("FAIL ") or line.startswith("ok   "):
            ours = line[5:].strip() == want and line.startswith("FAIL ")
        elif ours and line.startswith("     - "):
            errors.append(line[7:].strip())
    if not ours and not errors and f"ok   {want}" not in done.stdout:
        out = (done.stdout + done.stderr).strip()[:200]
        raise _Stopped(f"homes.py check never mentioned {want}: {out}")
    return errors


def check_models_in_sandbox(sandbox: Path, plant_ids: list[str]) -> list[str]:
    """Our plants' lines from the world's `check_models.py`.

    It runs over the whole sandbox (the preview sheets only
    exist there), but only `ERROR: <ours>: ...` lines feed
    back; the world's own failures are not this try's. No
    summary line means the tool itself broke.
    """
    try:
        done = subprocess.run(
            [sys.executable, "tools/check_models.py"],
            cwd=sandbox,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise _Stopped(f"check_models.py did not run: {error}")
    if "models checked," not in done.stdout:
        raise _Stopped(
            f"check_models.py broke: {(done.stdout + done.stderr).strip()[:200]}"
        )
    wanted = set(plant_ids)
    errors = []
    for line in done.stderr.splitlines():
        if not line.startswith("ERROR: "):
            continue
        rest = line[len("ERROR: ") :]
        pid, _, msg = rest.partition(": ")
        if pid in wanted:
            errors.append(f"{pid}: {msg}" if msg else pid)
    return errors


class _Stopped(Exception):
    """The thing has no passing files. The job stops; a person makes it."""
