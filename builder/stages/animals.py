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
import re
from pathlib import Path
from string import Template
from typing import Any

import jsonschema

from ..prompts import PROMPTS_DIR, fix_messages, for_children
from ..world import check_draft, mentions, problems_by_file
from .region import MAX_NEW_PLANTS, new_plant_ids

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
                    texts[species_id],
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
        previous: str | None = None,
    ) -> str:
        ctx.check_budget()
        role = roles.get(species["species"], species.get("role", "playable"))
        template = (PROMPTS_DIR / "animal_write.md").read_text(encoding="utf-8")
        template = _with_examples(template, world, role)
        main = role == "playable"
        others = _other_tags(world, ctx.job_dir, species["species"])
        prior: dict[str, Any] = {}
        if previous is not None:
            try:
                parsed = json.loads(previous)
            except ValueError:
                parsed = {}
            if isinstance(parsed, dict):
                prior = parsed
        if prior:
            must, flagged = _missing_texts(prior, others, main, False)
        else:
            must, flagged = _first_texts(main, [])
        prompt = Template(template).substitute(
            {
                "SPECIES_JSON": json.dumps(species, indent=2),
                "TAXON_JSON": json.dumps(taxon, indent=2),
                "ROLE_IN_REGION": role,
                "REGION_FOODS": ", ".join(sorted(_region_foods(region))),
                "REGION_CAST": ", ".join(sorted(_region_cast(region))),
                "CAST_TAGS": ", ".join(sorted(_cast_tags(world, region))),
                "HUNT_TAGS": ", ".join(sorted(_hunt_tags(world))),
                "NEEDED_TAGS": ", ".join(sorted(_needed_tags(world, region))),
                "EXISTING_TAGS": _existing_tags(world),
                "ARCHETYPES": ", ".join(sorted(_archetypes(world))),
                "MODEL_PATH": f"res://assets/animals/{species['species']}.glb",
                "ANIMAL_RULES": _animal_rules(world),
                "REQUIRED_TEXTS": _required_block(must, flagged),
                "FOR_CHILDREN": for_children(),
            }
        )
        first = {"role": "user", "content": prompt}
        if errors and previous is not None:
            notes = "\n".join(f"- {error}" for error in errors)
            messages = fix_messages(first, previous, notes)
        else:
            messages = [first]
        # Plain JSON mode, not structured output: the API's schema compiler
        # silently drops this schema's `$ref` properties (`diet` vanishes and
        # every round fails the same way). The schema checks every draft, in
        # code, in `_validate` below. Capped thinking, like the maker (#289).
        response = ctx.chat(messages, reasoning_effort="low")
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
        region_problems = _unexcused_region_problems(
            region, world, region_entry.get("problems", [])
        )
        if region_entry.get("status") == "fail" and region_problems:
            raise _Stopped(
                "the region broke after stage 4: " + "; ".join(region_problems[:5])
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


PLANT_MODEL_LINE = re.compile(
    r'"ground_cover\.plants\[(\d+)\]\.model" must be a plant in models/plants/'
)


def _unexcused_region_problems(
    region: dict[str, Any], world: Path, problems: list[str]
) -> list[str]:
    """The region failures that stop the animals stage.

    Unknown-plant lines are excused exactly for the ids the plants
    stage will build (at most MAX_NEW_PLANTS, none in the checkout
    yet): their specs come later, and stopping here would never let
    the plants stage run. Anything else still stops the job.
    """
    plants = region.get("ground_cover", {}).get("plants", [])
    new = new_plant_ids(region, world)
    excusable = new if len(new) <= MAX_NEW_PLANTS else set()
    kept = []
    for line in problems:
        match = PLANT_MODEL_LINE.search(line)
        if match is None:
            kept.append(line)
            continue
        index = int(match.group(1))
        model = plants[index].get("model", "") if index < len(plants) else ""
        if model not in excusable:
            kept.append(line)
    return kept


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


def _cast(region: dict[str, Any]) -> dict[str, Any]:
    """The region's cast: `species` on a biome, `animals` elsewhere (#215.1)."""
    key = "species" if region.get("biome") else "animals"
    cast = region.get(key)
    return cast if isinstance(cast, dict) else {}


def _roles_in_region(region: dict[str, Any]) -> dict[str, str]:
    roles = {}
    cast = _cast(region)
    for group, role in (
        ("playable", "playable"),
        ("predators", "predator"),
        ("prey", "prey"),
    ):
        for animal_id in cast.get(group, []):
            roles.setdefault(animal_id, role)
    for fish in region.get("fish", []):
        roles.setdefault(fish.get("kind", ""), "fish")
    roles.pop("", None)
    return roles


def _region_foods(region: dict[str, Any]) -> set[str]:
    return {food.get("kind", "") for food in region.get("food", [])} - {""}


def _region_cast(region: dict[str, Any]) -> set[str]:
    cast = set()
    for group in _cast(region).values():
        cast.update(group)
    cast.update(fish.get("kind", "") for fish in region.get("fish", []))
    return cast - {""}


def _hunt_tags(world: Path) -> set[str]:
    """Every tag a shipped animal hunts: the reviewed prey tags (#193.3).

    The maker picks `hunts` from this list only, and the ship's
    gate checks against it, so the two agree.
    """
    hunts: set[str] = set()
    for path in sorted((world / "animals").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for hunt in data.get("hunts", []) or []:
            hunts.add(hunt)
    return hunts


def _cast_tags(world: Path, region: dict[str, Any]) -> set[str]:
    """Tags the cast's existing animals carry.

    Context for the new species' own tags; `hunts` picks from
    the reviewed prey tags (`_hunt_tags`) instead (#193.3).
    """
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


#: Texts a playable animal carries, from the world's check (#275.2).
_REQUIRED_PLAYABLE = (
    "title",
    "menu_sub",
    "menu_wild",
    "night_hint",
    "too_exposed",
    "sheltered",
    "hungry",
    "no_food",
    "rain_start",
    "rain_stop",
)

#: The intro wording, one per game mode (#275.2).
_INTRO_MODES = ("intro_safe", "intro_survival", "intro_wild")

#: Texts a hunter carries (#275.2).
_HUNT_TEXTS = ("safe_tag", "bite", "caught_you")


def _required_texts(animal: dict[str, Any], others: set[str]) -> list[str]:
    """The text keys the world's check demands from this file (#275.2).

    Playable, digs, hunts and territorial each pull their keys;
    `bite` and `caught_you` only when a hunts tag matches
    another animal's tags. Species wording wins over the
    archetype's defaults, so defaults never excuse a key.
    """
    need: list[str] = []
    text = animal.get("text") or {}
    if not isinstance(text, dict):
        text = {}
    hunts = [tag for tag in animal.get("hunts") or [] if isinstance(tag, str)]
    if animal.get("playable"):
        need.extend(_REQUIRED_PLAYABLE)
        if "intro" not in text:
            need.extend(key for key in _INTRO_MODES if key not in text)
    if animal.get("digs"):
        need.append("dig")
    if hunts and animal.get("playable"):
        need.append("safe_tag")
    if animal.get("territorial"):
        need.extend(("rival_bite", "rival_caught_you"))
    if hunts and any(tag in others for tag in hunts):
        need.extend(("bite", "caught_you"))
    return need


def _other_tags(world: Path, job_dir: Path, animal_id: str) -> set[str]:
    """Every other animal's tags: the library's and the job's (#275.2)."""
    tags: set[str] = set()
    for folder in (world / "animals", job_dir / "animals"):
        try:
            paths = sorted(folder.glob("*.json"))
        except OSError:
            continue
        for path in paths:
            if folder == job_dir / "animals" and path.stem == animal_id:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                tags.update(
                    tag for tag in data.get("tags", []) or [] if isinstance(tag, str)
                )
    return tags


def _required_block(must: list[str], flagged: list[tuple[str, str]]) -> str:
    """The prompt's required-texts block from code's lists (#275.2)."""
    lines = []
    if must:
        lines.append(
            "Your `text` MUST include these keys, worded for a child: "
            + ", ".join(must)
            + "."
        )
    if flagged:
        groups: dict[str, list[str]] = {}
        for key, flag in flagged:
            groups.setdefault(flag, []).append(key)
        parts = []
        for flag, keys in groups.items():
            if flag == "hunts":
                parts.append(f"{', '.join(keys)} (or empty `hunts`)")
            else:
                parts.append(f"{', '.join(keys)} (or set `{flag}` false)")
        head = "It MUST also include" if must else "Include"
        lines.append(
            f"{head} each key below, or take its escape "
            "when the surveyed animal does not do that: " + "; ".join(parts) + "."
        )
    if not lines:
        return "All required texts are present; keep them."
    return "\n".join(lines)


def _first_texts(
    main: bool, hunts: list[str]
) -> tuple[list[str], list[tuple[str, str]]]:
    """The first try's required texts, preemptive (#275.2).

    The file does not exist yet, so every conditional key is
    listed: the keys code locks go on the must list, the rest
    carry their flag's escape. Unused keys are legal; a fix
    round narrows to what the file still lacks.
    """
    must: list[str] = []
    flagged: list[tuple[str, str]] = []
    if main:
        must.extend(_REQUIRED_PLAYABLE)
        must.extend(_INTRO_MODES)
    else:
        flagged.extend((key, "playable") for key in _REQUIRED_PLAYABLE)
        flagged.extend((key, "playable") for key in _INTRO_MODES)
    flagged.append(("dig", "digs"))
    if hunts:
        must.extend(_HUNT_TEXTS)
    else:
        flagged.extend((key, "hunts") for key in _HUNT_TEXTS)
    flagged.extend((key, "territorial") for key in ("rival_bite", "rival_caught_you"))
    return must, flagged


def _missing_texts(
    animal: dict[str, Any], others: set[str], main: bool, hunts_locked: bool
) -> tuple[list[str], list[tuple[str, str]]]:
    """The still-missing required texts, split by escape (#275.2).

    Keys code locks (the main animal's playable set, hunts the
    research set) must be written; the rest carry the flag that
    excuses them.
    """
    present = animal.get("text") or {}
    if not isinstance(present, dict):
        present = {}
    must: list[str] = []
    flagged: list[tuple[str, str]] = []
    playables = set(_REQUIRED_PLAYABLE) | set(_INTRO_MODES)
    for key in _required_texts(animal, others):
        if key in present:
            continue
        if key in playables:
            if main:
                must.append(key)
            else:
                flagged.append((key, "playable"))
        elif key in _HUNT_TEXTS:
            if hunts_locked:
                must.append(key)
            else:
                flagged.append((key, "hunts"))
        elif key == "dig":
            flagged.append((key, "digs"))
        else:
            flagged.append((key, "territorial"))
    return must, flagged
