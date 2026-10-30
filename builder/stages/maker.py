"""The one-shot animal maker (spec 04 "The makers (animal first)").

The minimum set says what's missing; this stage makes the animals:
the one prey first, then the animal itself, each in one go. Two
calls per animal (#60): `animal_write` for the animal file, then a
strict edits call describing how the model differs from its
`start_from` reference (colours, factors, marks, repaints). Code
adds the sourced sizes and runs the world's `tools/adapt_model.py`;
`tools/build_model.sh` builds it in a scratch copy of the world;
the world's own checks decide. One try, then at most one fix round
with the checks' errors. Still failing: the build stops with the
reason, and the thing goes to a person, as the eagle did.

Runs alone: `builder stage oneshot --job <dir> --record`. Writes
into the job's own folder, never into the world checkout.
"""

from __future__ import annotations

import base64
import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from string import Template
from typing import Any

import jsonschema

from ..animal_list import find_entry, load_list, match_by_name
from ..animal_list import find_list as find_animal_list
from ..meta import DEFAULT_MAX_TOKENS, is_cut_off
from ..prompts import (
    PROMPTS_DIR,
    fix_messages,
    for_children,
    improve_blocks,
    load_prompt,
    place_line,
)
from ..taxa import match_entry, taxon_block
from ..world import check_draft, problems_by_file
from . import animals as animal_writers
from .capability import NotPossibleError
from .home import refresh_welcome
from .models import (
    ModelsStage,
    _build_first_specs,
    _build_only,
    _ensure_sandbox,
    build_in_sandbox,
    ensure_plant_models,
    ensure_specs,
)
from .models import (
    _Stopped as _LoopStopped,
)
from .species_plan import SpeciesPlanStage, animal_file

#: Tries per thing: the write, then at most one fix round.
MAX_TRIES = 2

#: Factors the edits call may set, 1 meaning as the reference.
EDITS_FACTORS = (
    "head",
    "muzzle",
    "ears",
    "leg_thickness",
    "paws",
    "body_girth",
    "tail_width",
)

#: Bird factors the tool has: offered only when the start spec has
#: parts with those names, so a mammal is never asked for a bill.
EDITS_BIRD_FACTORS = ("neck", "bill", "bill_depth")

#: The free-spec loop's rounds for a `newshape` animal (D19).
NEWSHAPE_ROUNDS = 3


def _offered_factors(ref: dict[str, Any]) -> tuple[str, ...]:
    """The base factors plus the bird ones this reference can take."""
    names = [part.get("name", "") for part in ref.get("parts", [])]
    extra = tuple(
        factor
        for factor in EDITS_BIRD_FACTORS
        if any(name.startswith(factor.split("_")[0]) for name in names)
    )
    return EDITS_FACTORS + extra


#: Tree shapes the game draws without a spec.
TREE_SHAPES = ("spruce", "pine", "birch", "round", "rowan")

#: The home choices form's words, as the lab tried them (#65.1).
CHOICE_FOOD_KINDS = [
    "grass",
    "bark",
    "carrion",
    "clover",
    "berries",
    "insects",
    "water_bugs",
    "browse",
    "water_plants",
    "seeds",
    "cones",
    "bottom_bugs",
    "carrot",
]
CHOICE_LEVELS = {
    "land": ["flat", "rolling", "hilly", "mountains"],
    "water": ["river", "lake", "pools", "sea", "none"],
    "wetness": ["dry", "wet", "very_wet"],
    "density": ["none", "sparse", "medium", "dense"],
    "grass": ["none", "some", "lush"],
    "life": ["none", "some", "many"],
    "ice": ["none", "frozen_in_winter", "glaciers", "polar"],
    "caves": ["none", "some"],
    "seabed": ["mud", "sand"],
}
CHOICE_PALETTE = [
    "ground_lush",
    "ground_dry",
    "ground_high",
    "dirt",
    "rock",
    "sand",
    "water_deep",
    "water_shallow",
]
_CHOICE_HEX = {"type": "string", "description": "a colour as hex, like #5f9445"}

#: The adapt tool runs the region checks too; give it two minutes.
HOME_ADAPT_TIMEOUT = 120


class AnimalMakerStage:
    """Make the minimum set's animals: prey first, then the animal."""

    name = "oneshot"

    def __init__(
        self,
        author: str = "Animundo Builder",
        chat: Any | None = None,
        build: Any | None = None,
        adapt: Any | None = None,
        check: Any | None = None,
    ) -> None:
        self.author = author
        self._chat = chat
        self._build = build or build_in_sandbox
        self._adapt = adapt or _run_adapt
        self._check = check

    def run(self, ctx: Any) -> dict[str, Any]:
        if ctx.world_path is None:
            raise ValueError("oneshot needs --world")
        world = Path(ctx.world_path)
        brief = json.loads((ctx.job_dir / "home.json").read_text(encoding="utf-8"))
        research = json.loads(
            (ctx.job_dir / "research.json").read_text(encoding="utf-8")
        )
        minset = json.loads((ctx.job_dir / "minset.json").read_text(encoding="utf-8"))
        if not minset.get("make_animal") and minset.get("prey") is None:
            # A home item makes no animals: nothing to seat, no call.
            checks = ctx.job_dir / "checks"
            checks.mkdir(parents=True, exist_ok=True)
            (checks / "oneshot.json").write_text(
                json.dumps({"cost_usd": 0.0, "made": [], "unmakable": {}}),
                encoding="utf-8",
            )
            return {"animal_files": [], "cost_usd": 0.0}
        region_path = next((ctx.job_dir / "regions").glob("*.json"))
        region = json.loads(region_path.read_text(encoding="utf-8"))
        data = load_list(find_animal_list(world))
        looks_path = ctx.job_dir / "looks.json"
        looks_file: dict[str, Any] = (
            json.loads(looks_path.read_text(encoding="utf-8"))
            if looks_path.is_file()
            else {}
        )
        all_targets = _all_targets(
            brief, minset, data["animals"], looks_file.get("prey")
        )
        # Earlier runs' targets count as made too: seat and plan
        # them first, so a resume heals a job that stopped halfway
        # (#84.1). Everything below is idempotent.
        for target in all_targets:
            if _made(ctx.job_dir, target["id"]):
                self._seat_target(world, ctx, region, region_path, target)
                self._plan_target(ctx, brief, target)
        targets = [
            target for target in all_targets if not _made(ctx.job_dir, target["id"])
        ]
        before = ctx.tracker.spent_usd
        improved, summary = self._improve(
            ctx, world, brief, research, all_targets, data["animals"]
        )
        made = []
        unmakable: dict[str, list[str]] = {}
        for target in targets:
            # The checks run on the whole region, which already
            # names the other targets; excuse the ones still to
            # be made, the way home seats them (#54.2, #66.1).
            excused = {
                other["id"]
                for other in targets
                if other["id"] != target["id"] and not _made(ctx.job_dir, other["id"])
            }
            animal_id, marks = self._make(
                ctx, world, brief, research, region, target, excused
            )
            made.append(animal_id)
            if marks:
                unmakable[animal_id] = marks
            self._seat_target(world, ctx, region, region_path, target)
            self._plan_target(ctx, brief, target)
        if "welcome" in region and not brief.get("reuse_place_id"):
            # A reused place keeps its own welcome; only a made home's
            # welcome is rewritten for its new cast (#82.3).
            stem = brief.get("in_library") or brief.get("id", "")
            region["welcome"] = refresh_welcome(
                world, ctx.job_dir, region, brief, research, stem
            )
            region_path.write_text(json.dumps(region, indent=2), encoding="utf-8")
        cost = round(ctx.tracker.spent_usd - before, 6)
        checks = ctx.job_dir / "checks"
        checks.mkdir(parents=True, exist_ok=True)
        (checks / "oneshot.json").write_text(
            json.dumps(
                {"cost_usd": cost, "made": made, "unmakable": unmakable}, indent=2
            ),
            encoding="utf-8",
        )
        made_all = [
            target["id"] for target in all_targets if _made(ctx.job_dir, target["id"])
        ]
        result: dict[str, Any] = {
            "animal_files": [f"animals/{animal_id}.json" for animal_id in made_all],
            "cost_usd": cost,
        }
        if improved is not None:
            result["improved"] = improved
            if summary:
                result["summary"] = summary
        return result

    def _improve(
        self,
        ctx: Any,
        world: Path,
        brief: dict[str, Any],
        research: dict[str, Any],
        all_targets: list[dict[str, Any]],
        entries: list[dict[str, Any]],
    ) -> tuple[list[str] | None, str | None]:
        """Fix the named made animals: previous form plus note (#82.6).

        Returns (improved, summary): (None, None) when this is no
        improve round at all.
        """
        path = ctx.job_dir / "improve.json"
        if not path.is_file():
            return None, None
        improve = json.loads(path.read_text(encoding="utf-8"))
        names = [
            entry.get("species", "")
            for entry in improve.get("choices", [])
            if entry.get("choice") == "animal_looks_wrong"
            or (entry.get("choice") == "other" and entry.get("species"))
        ]
        if not names:
            return [], None
        made = {
            target["id"] for target in all_targets if _made(ctx.job_dir, target["id"])
        }
        if not made:
            return [], "nothing to change here"
        tag = f"-r{improve.get('round', 2)}"
        improved: list[str] = []
        messages: list[str] = []
        for name in names:
            target = _match_target(all_targets, name)
            if target is not None and target["id"] in made:
                previous = _last_try_answer(
                    ctx.job_dir / "tries" / target["id"], "edits"
                )
                self._make_model(
                    ctx,
                    world,
                    brief,
                    research,
                    target,
                    previous_form=previous,
                    note=improve.get("note"),
                    tag=tag,
                )
                improved.append(target["id"])
            elif target is None:
                kind, display = _classify_name(
                    world, entries, brief, str(name or "").strip()
                )
                if kind == "library":
                    messages.append(
                        f"{display} is from the library and can't be changed here"
                    )
                else:
                    messages.append(
                        f'no animal called "{display}" was made here; nothing changed'
                    )
        parts = ([f"fixed {', '.join(improved)}"] if improved else []) + messages
        return improved, "; ".join(parts) if parts else None

    def _make(
        self,
        ctx: Any,
        world: Path,
        brief: dict[str, Any],
        research: dict[str, Any],
        region: dict[str, Any],
        target: dict[str, Any],
        excused: set[str],
    ) -> tuple[str, list[str]]:
        """One animal: its file, then its model from edits (#60).

        Two calls instead of one combined one: the animal file
        first, then the edits the world's adapt tool turns into
        the spec. A `newshape` animal instead loops a free spec
        (D19). Returns the id and the marks the tool can't
        make yet.
        """
        animal_id = target["id"]
        if not _animal_passed(ctx.job_dir, animal_id):
            self._make_animal(ctx, world, brief, research, region, target, excused)
        if target.get("shape") == "new":
            marks = self._make_newshape(ctx, world, brief, research, target)
        else:
            marks = self._make_model(ctx, world, brief, research, target)
        self._save_final(ctx, world, animal_id)
        return animal_id, marks

    def _make_animal(
        self,
        ctx: Any,
        world: Path,
        brief: dict[str, Any],
        research: dict[str, Any],
        region: dict[str, Any],
        target: dict[str, Any],
        excused: set[str],
    ) -> None:
        """The animal file, one try plus one fix round."""
        animal_id = target["id"]
        hunts = (
            self._code_hunts(world, ctx, research, region, animal_id)
            if not target["prey"]
            else []
        )
        diet = None if target["prey"] else _code_diet(research)
        prompt = self._animal_prompt(world, brief, research, region, target)
        errors: list[str] | None = None
        previous: str | None = None
        for attempt in range(1, MAX_TRIES + 1):
            ctx.check_budget()
            answer = self._ask_animal(ctx, prompt, errors, previous)
            previous = answer
            files, file_errors = self._animal_files(world, answer)
            if files is not None:
                if hunts:
                    files["animal"]["hunts"] = hunts
                if diet is not None:
                    files["animal"]["diet"] = diet
                self._place_animal(ctx, animal_id, files)
                file_errors = self._check_animal(
                    world, ctx, animal_id, attempt, excused
                )
            self._save_try(
                ctx, animal_id, "animal", attempt, answer, files, file_errors
            )
            if not file_errors:
                return
            errors = file_errors
        raise _Stopped(
            f"{animal_id}: its file still failing after one fix round "
            f"({'; '.join(errors or [])}); a person makes it"
        )

    def _make_model(
        self,
        ctx: Any,
        world: Path,
        brief: dict[str, Any],
        research: dict[str, Any],
        target: dict[str, Any],
        previous_form: str | None = None,
        note: str | None = None,
        tag: str = "",
    ) -> list[str]:
        """The model from the reference plus edits, then its checks.

        An improve fix passes the previous form and the grown-up's
        note, and tags its tries with the round (#82.6).
        """
        animal_id = target["id"]
        start_from = target["start_from"]
        ref = _reference_spec(world, start_from)
        if ref is None:
            raise _Stopped(
                f"{animal_id}: no reference spec {start_from!r} to adapt; "
                "a person picks one"
            )
        prompt = self._edits_prompt(world, brief, research, target, ref)
        prompt += improve_blocks(previous_form, note)
        request = {
            "type": "json_schema",
            "json_schema": {
                "name": "ModelEdits",
                "schema": _edits_schema(ref),
                "strict": True,
            },
        }
        sizes = _edits_sizes(research, target)
        errors: list[str] | None = None
        previous: str | None = None
        code_fixed = False
        for attempt in range(1, MAX_TRIES + 1):
            ctx.check_budget()
            answer = self._ask_edits(ctx, prompt, request, errors, previous)
            previous = answer
            edits, file_errors = _edits_files(answer, ref)
            spec: dict[str, Any] | None = None
            if edits is not None:
                if target["prey"]:
                    words = (target.get("looks") or {}).get("marks", [])
                else:
                    words = [
                        mark["mark"]
                        for mark in (research.get("looks") or {}).get("marks", [])
                        if isinstance(mark, dict) and "mark" in mark
                    ]
                marks, unmatched = _tool_marks(words)
                key_marks = [
                    mark
                    for mark in (edits.get("key_marks") or [])
                    if isinstance(mark, str) and mark.strip()
                ][:2]
                keyed, _ = _tool_marks(key_marks)
                marks = [*marks, *[m for m in keyed if m not in marks]]
                edits["marks"] = marks
                if "bands" not in marks:
                    # Unbanded animals carry no band fields; the
                    # adapt tool defaults banded ones (#149.1).
                    edits.pop("bands", None)
                    edits.pop("band_color", None)
                edits["unmakable"] = list(
                    dict.fromkeys([*(edits.get("unmakable") or []), *unmatched])
                )
                spec, file_errors = self._adapt_build_place(
                    ctx, world, animal_id, target, ref, edits, sizes, attempt
                )
                if spec is not None and not file_errors:
                    found = _measure_spec(spec)
                    sourced = _sourced_sizes(research, target)
                    misses = _size_misses(found, sourced, spec)
                    if misses and not code_fixed:
                        # Code owns the sizes: rescale and re-run,
                        # no model call spent (#60.4).
                        sizes = {
                            **sizes,
                            **{
                                key: sizes[key] * want / have
                                for key, (have, want) in misses.items()
                            },
                        }
                        code_fixed = True
                        spec, file_errors = self._adapt_build_place(
                            ctx, world, animal_id, target, ref, edits, sizes, attempt
                        )
                    if spec is not None and not file_errors:
                        found = _measure_spec(spec)
                        misses = _size_misses(found, sourced, spec)
                        file_errors += _size_errors(found, sourced, spec)
                        file_errors += _ratio_errors(found, target, world)
                        if (
                            code_fixed
                            and misses
                            and not [
                                error
                                for error in file_errors
                                if not error.startswith("the spec's ")
                            ]
                        ):
                            raise _Stopped(
                                f"{animal_id}: {'; '.join(file_errors)}: the tool "
                                "saturated its range; a person looks"
                            )
                    if spec is not None:
                        file_errors += self._marks_errors(
                            ctx, animal_id, target, key_marks, attempt
                        )
            self._save_try(
                ctx, animal_id, f"edits{tag}", attempt, answer, edits, file_errors
            )
            if edits is not None and not file_errors:
                marks = edits.get("unmakable") or []
                return [mark for mark in marks if isinstance(mark, str)]
            errors = file_errors
        raise _Stopped(
            f"{animal_id}: its model still failing after one fix round "
            f"({'; '.join(errors or [])}); a person makes it"
        )

    def _ask_animal(
        self,
        ctx: Any,
        prompt: str,
        errors: list[str] | None,
        previous: str | None = None,
    ) -> str:
        first = {"role": "user", "content": prompt}
        if errors and previous is not None:
            notes = "\n".join(f"- {_guide(error)}" for error in errors)
            messages = fix_messages(first, previous, notes)
        else:
            messages = [first]
        chat = self._chat or ctx.chat
        response = _ask_with_room(chat, messages)
        return (response.choices[0].message.content or "").strip()

    def _ask_edits(
        self,
        ctx: Any,
        prompt: str,
        request: dict[str, Any],
        errors: list[str] | None,
        previous: str | None = None,
    ) -> str:
        first = {"role": "user", "content": prompt}
        if errors and previous is not None:
            notes = "\n".join(f"- {error}" for error in errors)
            messages = fix_messages(first, previous, notes)
        else:
            messages = [first]
        chat = self._chat or ctx.chat
        response = _ask_with_room(chat, messages, response_format=request)
        return (response.choices[0].message.content or "").strip()

    def _marks_errors(
        self,
        ctx: Any,
        animal_id: str,
        target: dict[str, Any],
        key_marks: list[str],
        attempt: int,
    ) -> list[str]:
        """The key marks the built render lacks, if any (#146.3).

        Nothing to judge without key marks or without a
        render. Otherwise one look at the preview in the same
        round that checks the sizes; a missing mark fails the
        try like a missed size, so the fix round paints it.
        """
        if not key_marks:
            return []
        preview = ctx.job_dir / "models" / f"{animal_id}.png"
        if not preview.is_file():
            return []
        title = target["entry"].get("name", animal_id)
        prompt = load_prompt(
            "model_marks",
            {
                "TITLE": title,
                "MARKS": "\n".join(f"- {mark}" for mark in key_marks),
            },
        )
        encoded = base64.b64encode(preview.read_bytes()).decode()
        first = {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{encoded}"},
                },
            ],
        }
        request = {
            "type": "json_schema",
            "json_schema": {
                "name": "MarksReview",
                "schema": _marks_schema(),
                "strict": True,
            },
        }
        problems = ""
        previous: str | None = None
        for _ in range(2):
            ctx.check_budget()
            messages = (
                fix_messages(first, previous, problems)
                if problems and previous is not None
                else [first]
            )
            chat = self._chat or ctx.chat
            answer = (
                _ask_with_room(chat, messages, response_format=request)
                .choices[0]
                .message.content
                or ""
            ).strip()
            previous = answer
            try:
                review = json.loads(_unfence(answer))
                shown = review["shown"]
                missing = review["missing"]
                if not isinstance(shown, list) or not isinstance(missing, list):
                    raise ValueError("shown and missing are lists")
                if not all(isinstance(m, str) for m in shown + missing):
                    raise ValueError("shown and missing hold plain words")
            except (ValueError, KeyError, TypeError) as error:
                problems = f"PREVIOUS TRY was no marks review: {error}"
                continue
            folder = ctx.job_dir / "tries" / animal_id / "edits" / str(attempt)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "marks_review.json").write_text(
                json.dumps({"shown": shown, "missing": missing}, indent=2),
                encoding="utf-8",
            )
            return [_mark_miss(mark) for mark in missing if isinstance(mark, str)]
        raise _Stopped(
            f"{animal_id}: the marks review never wrote valid JSON: {problems}"
        )

    def _animal_files(
        self, world: Path, answer: str
    ) -> tuple[dict[str, Any] | None, list[str]]:
        """The try's animal file, or why the answer holds none."""
        try:
            animal = json.loads(_unfence(answer))
        except json.JSONDecodeError as error:
            return None, [f"not JSON: {error}"]
        if not isinstance(animal, dict):
            return None, ["reply with the animal file as one JSON object"]
        animal_schema = json.loads(
            (world / "schemas" / "animal.schema.json").read_text(encoding="utf-8")
        )
        try:
            jsonschema.validate(animal, animal_schema)
        except jsonschema.ValidationError as error:
            return None, [f"the animal file broke its schema: {error.message}"]
        return {"animal": animal}, []

    def _place_animal(self, ctx: Any, animal_id: str, files: dict[str, Any]) -> None:
        """Lay the animal file where the checker finds it."""
        animals_dir = ctx.job_dir / "animals"
        animals_dir.mkdir(exist_ok=True)
        (animals_dir / f"{animal_id}.json").write_text(
            json.dumps(files["animal"], indent=2), encoding="utf-8"
        )

    def _code_hunts(
        self,
        world: Path,
        ctx: Any,
        research: dict[str, Any],
        region: dict[str, Any],
        animal_id: str,
    ) -> list[str]:
        """The hunter's hunts, from its seated research prey (#84.2).

        Every tag of every research prey that has a file and a
        seat: the food check matches them against the cast, so the
        fix round can neither drop them nor swap prey for plants.
        When prey has files but no seat, the run stops here: the
        pipeline seats made prey before the hunter's file is
        checked, so no fix round can help. [] leaves the model's
        own hunts alone (no prey, or none with a file yet).
        """
        entries = load_list(find_animal_list(world))["animals"]
        overlay = ctx.job_dir
        tags: set[str] = set()
        unseated: list[str] = []
        saw_file = False
        seated_any = False
        for food in [research["main_food"], *research.get("other_foods", [])]:
            if food.get("kind") != "prey":
                continue
            match, _ = match_entry(entries, world, food)
            if match is None:
                continue
            stem = match.get("in_library") or match.get("id", "")
            data = animal_file(world, stem, overlay) if stem else {}
            if not data:
                continue
            saw_file = True
            if not _seated(region, stem):
                unseated.append(food["name"])
                continue
            seated_any = True
            tags.update(
                tag for tag in data.get("tags", []) or [] if isinstance(tag, str)
            )
        if saw_file and not seated_any:
            raise _Stopped(
                f"{animal_id}: its prey ({', '.join(unseated)}) isn't seated "
                "in the region; the pipeline seats made prey before the "
                "hunter's file is checked, so this run stops for a person"
            )
        return sorted(tags)

    def _check_animal(
        self, world: Path, ctx: Any, animal_id: str, attempt: int, excused: set[str]
    ) -> list[str]:
        """The file's verdicts: animals, taxa, region.

        The build hasn't run yet, so "model file not found" is
        noise the model phase resolves. The region already names
        the whole cast, so the check runs on a copy with the
        still-unmade targets taken out of the cast and the fish:
        no wording, now or later, can fault their missing files,
        and everything else is still checked (#67.1).
        """
        if excused:
            with tempfile.TemporaryDirectory(prefix="oneshot-pruned-") as tmp:
                extra = _pruned_regions(ctx.job_dir, excused, Path(tmp))
                errors = _verdict_errors(
                    _verdicts(world, ctx, animal_id, "animal", attempt, extra=extra),
                    ("animals", "taxa", "region"),
                    animal_id,
                )
        else:
            errors = _verdict_errors(
                _verdicts(world, ctx, animal_id, "animal", attempt),
                ("animals", "taxa", "region"),
                animal_id,
            )
        return [error for error in errors if "model file not found" not in error]

    def _make_newshape(
        self,
        ctx: Any,
        world: Path,
        brief: dict[str, Any],
        research: dict[str, Any],
        target: dict[str, Any],
    ) -> list[str]:
        """The model from the free-spec loop, then code sizing (D19).

        A `newshape` animal's `start_from` is only the closest
        spec: the loop adapts it freely, checked by the world's
        checks on every round, and code sizes the passed spec to
        the sourced numbers with no factors and no AI call. The
        leg-to-body check is skipped: upright animals fail it by
        shape, not by error. Misses and an unpassed loop go to
        review listed, never to a fix call.
        """
        animal_id = target["id"]
        start_from = target["start_from"]
        ref = _reference_spec(world, start_from)
        if ref is None:
            raise _Stopped(
                f"{animal_id}: no reference spec {start_from!r} to adapt; "
                "a person picks one"
            )
        self._write_newshape_inputs(ctx, world, research, target)
        winged = any(
            part.get("name", "").startswith("wing") for part in ref.get("parts", [])
        )
        line = _reference_row(world, start_from, target["entry"].get("name", animal_id))
        refs = {
            animal_id: {
                "start": start_from,
                "change": line[1] if line else "",
                "kind": "bird" if winged else "mammal",
            }
        }
        stage = ModelsStage(
            reference="none",
            refs=refs,
            author=self.author,
            max_rounds=NEWSHAPE_ROUNDS,
            chat=self._chat,
            build=self._build,
            check=self._check,
        )
        try:
            stage.run(ctx)
        except _LoopStopped as error:
            rounds = ctx.job_dir / "rounds" / animal_id
            if not rounds.is_dir() or not any(rounds.iterdir()):
                raise
            return self._newshape_not_passed(ctx, animal_id, error)
        return self._newshape_size(ctx, world, research, target)

    def _write_newshape_inputs(
        self,
        ctx: Any,
        world: Path,
        research: dict[str, Any],
        target: dict[str, Any],
    ) -> None:
        """The loop's inputs: the plan, the looks, the sourced numbers."""
        entry = target["entry"]
        animal_id = target["id"]
        (ctx.job_dir / "species-plan.json").write_text(
            json.dumps({"species": [{"species": animal_id, "decision": "new"}]}),
            encoding="utf-8",
        )
        (ctx.job_dir / "survey.json").write_text(
            json.dumps(
                {
                    "species": [
                        {
                            "species": animal_id,
                            "latin": entry.get("latin", ""),
                            "description": _description(entry, research, target),
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        measurements = ctx.job_dir / "measurements"
        measurements.mkdir(parents=True, exist_ok=True)
        (measurements / f"{animal_id}.json").write_text(
            json.dumps(
                {
                    "species": animal_id,
                    "reference": "none",
                    "sourced": _sourced_sizes(research, target),
                    "measured": None,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def _newshape_not_passed(
        self, ctx: Any, animal_id: str, error: Exception
    ) -> list[str]:
        """The last render goes to review, marked not passed (D19).

        The report's rounds already show every review; the checks
        file marks the verdict, and the blocking issues return as
        marks so the unmakable lines list them too.
        """
        job = ctx.job_dir
        rounds = job / "rounds" / animal_id
        previews = (
            sorted(rounds.glob("*/preview.png"), key=lambda p: int(p.parent.name))
            if rounds.is_dir()
            else []
        )
        blocking: list[str] = []
        if previews:
            models = job / "models"
            models.mkdir(parents=True, exist_ok=True)
            (models / f"{animal_id}.png").write_bytes(previews[-1].read_bytes())
            review_path = (
                job / "reviews" / f"{animal_id}-{previews[-1].parent.name}.json"
            )
            if review_path.is_file():
                review = json.loads(review_path.read_text(encoding="utf-8"))
                blocking = [
                    f"{issue.get('part')}: {issue.get('problem')} "
                    f"(fix: {issue.get('fix')})"
                    for issue in review.get("issues", [])
                    if issue.get("severity") == "blocking"
                ]
        if not blocking:
            blocking = [f"the loop stopped: {error}"]
        checks = job / "checks"
        checks.mkdir(parents=True, exist_ok=True)
        (checks / f"newshape-{animal_id}.json").write_text(
            json.dumps(
                {
                    "ok": False,
                    "results": [
                        {
                            "check": "newshape",
                            "status": "not passed",
                            "reason": (
                                f"no round passed in {NEWSHAPE_ROUNDS} rounds; "
                                "a person decides"
                            ),
                            "problems": blocking,
                        }
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return blocking

    def _newshape_size(
        self,
        ctx: Any,
        world: Path,
        research: dict[str, Any],
        target: dict[str, Any],
    ) -> list[str]:
        """Code sizing for the passed spec: sourced sizes only (D19).

        The loop's spec becomes the reference; adapt runs on it
        with only length, shoulder and tail from the sourced
        numbers. Then it builds, and the size checks run without
        the leg-to-body ratio. What still misses goes to review
        listed; the adapt or the build failing is breakage, loud.
        """
        animal_id = target["id"]
        spec_path = ctx.job_dir / "models" / f"{animal_id}.json"
        sourced = _sourced_sizes(research, target)
        sizes = _sizing_edits(
            animal_id, target["entry"].get("name", animal_id), self.author, sourced
        )
        folder = ctx.job_dir / "tries" / animal_id / "newshape" / "1"
        folder.mkdir(parents=True, exist_ok=True)
        sizes_path = folder / "sizes.json"
        sizes_path.write_text(json.dumps(sizes, indent=2), encoding="utf-8")
        sized_path = folder / "spec.json"
        adapted = self._adapt(world, spec_path, sizes_path, sized_path)
        if not adapted["ok"]:
            raise _Stopped(f"{animal_id}: the sizing adapt refused: {adapted['log']}")
        sized = json.loads(sized_path.read_text(encoding="utf-8"))
        self._place_spec(ctx, world, animal_id, sized)
        sandbox = _ensure_sandbox(ctx.job_dir, world)
        built = self._build(sandbox, animal_id)
        (folder / "build.log").write_text(built["log"], encoding="utf-8")
        if not built["ok"] and not built["log"].strip():
            raise _Stopped(
                f"{animal_id}: the build failed with no output: Blender itself "
                "did not run here, not the spec. A person looks."
            )
        if not built["ok"]:
            raise _Stopped(f"{animal_id}: the sized build failed: {built['log']}")
        self._collect_built(ctx, sandbox, animal_id)
        found = _measure_spec(sized)
        errors = _size_errors(found, sourced)
        checks = ctx.job_dir / "checks"
        checks.mkdir(parents=True, exist_ok=True)
        (checks / f"newshape-{animal_id}.json").write_text(
            json.dumps(
                {
                    "ok": True,
                    "results": [
                        {
                            "check": "sizes",
                            "status": "pass",
                            "reason": "code sizing from the sourced numbers",
                            "problems": errors,
                        }
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return errors

    def _collect_built(self, ctx: Any, sandbox: Path, animal_id: str) -> None:
        """The built model and preview, from the sandbox to the job."""
        for name, rel in (
            (f"{animal_id}.glb", Path("assets") / "animals" / f"{animal_id}.glb"),
            (f"{animal_id}.png", Path("models") / "previews" / f"{animal_id}.png"),
        ):
            src = sandbox / rel
            if src.is_file():
                dest = ctx.job_dir / "models" / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(src.read_bytes())

    def _adapt_build_place(
        self,
        ctx: Any,
        world: Path,
        animal_id: str,
        target: dict[str, Any],
        ref: dict[str, Any],
        edits: dict[str, Any],
        sizes: dict[str, float],
        attempt: int,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        """Edits plus code sizes through adapt, placed, built, checked."""
        folder = ctx.job_dir / "tries" / animal_id / "edits" / str(attempt)
        folder.mkdir(parents=True, exist_ok=True)
        full = {
            **edits,
            **sizes,
            "id": animal_id,
            "name": target["entry"].get("name", animal_id),
            "author": self.author,
        }
        for extra in ("new_colors", "unmakable"):
            full.pop(extra, None)
        if edits.get("new_colors"):
            full["colors"] = {
                **full.get("colors", {}),
                **{
                    item["name"]: item["hex"]
                    for item in edits["new_colors"]
                    if isinstance(item, dict)
                },
            }
        edits_path = folder / "edits.json"
        edits_path.write_text(json.dumps(full, indent=2), encoding="utf-8")
        spec_path = folder / "spec.json"
        adapted = self._adapt(world, target["start_from"], edits_path, spec_path)
        if not adapted["ok"]:
            return None, [f"the adapt tool refused the edits: {adapted['log']}"]
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
        except ValueError as error:
            return None, [f"the adapt tool wrote no spec: {error}"]
        if not isinstance(spec, dict):
            return None, ["the adapt tool wrote no spec"]
        self._place_spec(ctx, world, animal_id, spec)
        return spec, self._build_and_check(ctx, world, animal_id, attempt)

    def _place_spec(
        self, ctx: Any, world: Path, animal_id: str, spec: dict[str, Any]
    ) -> None:
        """Lay the adapted spec where the build and the checker find it."""
        models_dir = ctx.job_dir / "models"
        models_dir.mkdir(exist_ok=True)
        (models_dir / f"{animal_id}.json").write_text(
            json.dumps(spec, indent=2), encoding="utf-8"
        )
        sandbox = _ensure_sandbox(ctx.job_dir, world)
        (sandbox / "models" / f"{animal_id}.json").write_text(
            json.dumps(spec, indent=2), encoding="utf-8"
        )

    def _build_and_check(
        self, ctx: Any, world: Path, animal_id: str, attempt: int
    ) -> list[str]:
        """Build the adapted spec, then read the models verdict. Errors, if any."""
        sandbox = _ensure_sandbox(ctx.job_dir, world)
        built = self._build(sandbox, animal_id)
        folder = ctx.job_dir / "tries" / animal_id / "edits" / str(attempt)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "build.log").write_text(built["log"], encoding="utf-8")
        if not built["ok"] and not built["log"].strip():
            raise _Stopped(
                f"{animal_id}: the build failed with no output: Blender itself "
                "did not run here, not the spec. A person looks."
            )
        if built["ok"]:
            self._write_measured_length(ctx, animal_id, built["log"])
        self._collect_built(ctx, sandbox, animal_id)
        errors = [] if built["ok"] else [f"the build failed: {built['log']}"]
        errors += _verdict_errors(
            _verdicts(world, ctx, animal_id, "edits", attempt), ("models",), animal_id
        )
        (folder / "errors.json").write_text(
            json.dumps(errors, indent=2), encoding="utf-8"
        )
        return errors

    def _write_measured_length(self, ctx: Any, animal_id: str, log: str) -> None:
        """The file's game length, from the build's measurement (#140.3).

        The build prints what `animals/{id}.json` carries; the
        model's guess never stands. A good build with no line
        means the world's script changed under us: a person
        looks, no fix round can help.
        """
        measured = _measured_length(log, animal_id)
        if measured is None:
            raise _Stopped(
                f"{animal_id}: the build printed no measured length; a person looks"
            )
        path = ctx.job_dir / "animals" / f"{animal_id}.json"
        try:
            animal = json.loads(path.read_text(encoding="utf-8"))
            animal["model"]["length"] = measured
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise _Stopped(
                f"{animal_id}: cannot write the measured length: {error}; "
                "a person looks"
            ) from error
        path.write_text(json.dumps(animal, indent=2), encoding="utf-8")

    def _save_try(
        self,
        ctx: Any,
        animal_id: str,
        phase: str,
        attempt: int,
        answer: str,
        files: dict[str, Any] | None,
        errors: list[str],
    ) -> None:
        folder = ctx.job_dir / "tries" / animal_id / phase / str(attempt)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "answer.json").write_text(answer, encoding="utf-8")
        if files is not None:
            if "animal" in files:
                (folder / "animal.json").write_text(
                    json.dumps(files["animal"], indent=2), encoding="utf-8"
                )
            else:
                (folder / "edits_answer.json").write_text(
                    json.dumps(files, indent=2), encoding="utf-8"
                )
        # Always: later checks append after earlier ones wrote it.
        (folder / "errors.json").write_text(
            json.dumps(errors, indent=2), encoding="utf-8"
        )

    def _seat_target(
        self,
        world: Path,
        ctx: Any,
        region: dict[str, Any],
        region_path: Path,
        target: dict[str, Any],
    ) -> None:
        """Seat a made target in the region, in place and on disk (#84.1)."""
        seat_made(world, ctx.job_dir, region, region_path, target["id"], target["prey"])

    def _plan_target(
        self, ctx: Any, brief: dict[str, Any], target: dict[str, Any]
    ) -> None:
        """The made target joins the species plan, as new (#54, #84.1).

        The home wrote the plan before anything was made; the
        pack reads it to decide what ships. Without this the
        target's files would never leave the job.
        """
        plan_path = ctx.job_dir / "species-plan.json"
        if not plan_path.is_file():
            return
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        have = {entry.get("species") for entry in plan.get("species", [])}
        if target["id"] in have:
            return
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

    def _animal_prompt(
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
        # The file example comes from `file_from`: `start_from` is the
        # model reference and may be a model-only file (#70). A row
        # without `file_from` (an old list) falls back to `start_from`.
        example_from = entry.get("file_from") or target["start_from"]
        template = (PROMPTS_DIR / "animal_write.md").read_text(encoding="utf-8")
        template = _with_example(template, world, example_from)
        roles = animal_writers._roles_in_region(region)
        role = roles.get(animal_id, "prey" if target["prey"] else "playable")
        return Template(template).substitute(
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

    def _edits_prompt(
        self,
        world: Path,
        brief: dict[str, Any],
        research: dict[str, Any],
        target: dict[str, Any],
        ref: dict[str, Any],
    ) -> str:
        """What the edits call sees: looks, the table line, the part list."""
        entry = target["entry"]
        start_from = target["start_from"]
        template = (PROMPTS_DIR / "model_edits.md").read_text(encoding="utf-8")
        return Template(template).substitute(
            {
                "TITLE": entry.get("name", target["id"]),
                "LATIN": entry.get("latin", ""),
                "CLOSEST_NAME": start_from,
                "LOOKS": (
                    _description(entry, research, target)
                    + "\n"
                    + json.dumps(_measurements(research, target), indent=2)
                ),
                "REFERENCE_LINE": _reference_line(world, start_from, entry),
                "PART_LIST": _part_list(ref),
            }
        )


def _targets(
    brief: dict[str, Any],
    minset: dict[str, Any],
    entries: list[dict[str, Any]],
    job_dir: Path,
    prey_looks: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """What to make, in order: the prey, then the animal. Made ones skip."""
    return [
        target
        for target in _all_targets(brief, minset, entries, prey_looks)
        if not _made(job_dir, target["id"])
    ]


def _all_targets(
    brief: dict[str, Any],
    minset: dict[str, Any],
    entries: list[dict[str, Any]],
    prey_looks: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Every target, made or not: the prey, then the animal (#84.1)."""
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
        if not match.get("start_from"):
            raise NotPossibleError(
                f"{prey['name']} has no starting spec on the list; a person picks one"
            )
        targets.append(
            {
                "id": match["id"],
                "entry": match,
                "start_from": match["start_from"],
                "prey": True,
                "size_cm": prey.get("size_cm"),
                "looks": prey_looks,
                "shape": match.get("shape"),
            }
        )
    if minset.get("make_animal"):
        entry = find_entry(entries, brief["species_key"])
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
                "shape": entry.get("shape"),
            }
        )
    return targets


def seat_made(
    world: Path,
    job_dir: Path,
    region: dict[str, Any],
    region_path: Path,
    animal_id: str,
    is_prey: bool,
) -> None:
    """Seat an animal in the region, in place and on disk (#84.1).

    A prey swims or is preyed upon; the home's animal plays.
    Idempotent: seating twice seats once.
    """
    role = "prey" if is_prey else "playable"
    SpeciesPlanStage.place_backfilled(
        world,
        region,
        {animal_id: {"backfilled": True, "file": animal_id, "role": role}},
        job_dir,
    )
    if not is_prey:
        playable = region.setdefault("animals", {}).setdefault("playable", [])
        if animal_id not in playable:
            playable.append(animal_id)
    region_path.write_text(json.dumps(region, indent=2), encoding="utf-8")


def _seated(region: dict[str, Any], stem: str) -> bool:
    """Whether the cast seats this stem, on land or as a fish."""
    for group in region.get("animals", {}).values():
        if stem in (group or []):
            return True
    for fish in region.get("fish", []) or []:
        if not isinstance(fish, dict):
            continue
        if stem in (fish.get("kind"), fish.get("file")):
            return True
    return False


def _made(job_dir: Path, animal_id: str) -> bool:
    """Whether a run already made this animal: both files in the job."""
    return (job_dir / "animals" / f"{animal_id}.json").is_file() and (
        job_dir / "models" / f"{animal_id}.json"
    ).is_file()


def _match_target(
    all_targets: list[dict[str, Any]], name: str
) -> dict[str, Any] | None:
    """The target the grown-up's "which animal" names, if any."""
    lowered = (name or "").strip().lower()
    for target in all_targets:
        entry = target.get("entry") or {}
        if lowered in {
            target["id"].lower(),
            str(entry.get("id", "")).lower(),
            str(entry.get("name", "")).lower(),
        } - {""}:
            return target
    return None


def _classify_name(
    world: Path, entries: list[dict[str, Any]], brief: dict[str, Any], name: str
) -> tuple[str, str]:
    """A named animal that is no target: "library" or "unknown".

    Returns the kind plus the display name for the message.
    """
    lowered = name.lower()
    for entry in entries:
        if lowered in {
            str(entry.get("id", "")).lower(),
            str(entry.get("name", "")).lower(),
        } - {""}:
            return "library", str(entry.get("name") or entry.get("id"))
    stem = lowered.replace(" ", "_")
    if (world / "animals" / f"{stem}.json").is_file() or lowered == (
        brief.get("in_library") or ""
    ).lower():
        return "library", name
    return "unknown", name


def _last_try_answer(tries_dir: Path, phase: str) -> str | None:
    """The latest saved answer for this phase, or None without one.

    An empty phase reads the tries' own answers (the home maker's
    two-level tries); otherwise the phase's answers below them.
    """
    found: list[Path] = []
    if tries_dir.is_dir():
        if phase:
            attempts = [
                attempt_dir / "answer.json"
                for phase_dir in sorted(tries_dir.iterdir())
                if phase_dir.name.startswith(phase) and phase_dir.is_dir()
                for attempt_dir in sorted(phase_dir.iterdir())
            ]
        else:
            attempts = [
                attempt_dir / "answer.json"
                for attempt_dir in sorted(tries_dir.iterdir())
            ]
        found = [answer for answer in attempts if answer.is_file()]
    if not found:
        return None
    found.sort(key=lambda path: path.stat().st_mtime)
    return found[-1].read_text(encoding="utf-8")


def _animal_passed(job_dir: Path, animal_id: str) -> bool:
    """Whether a run already passed this animal's file (#77.2).

    The file is there and its last try's errors are empty: a
    resume goes straight to the model and never pays for the
    file again. Anything else — no file, no tries, a failing
    last try — makes it again.
    """
    if not (job_dir / "animals" / f"{animal_id}.json").is_file():
        return False
    tries = job_dir / "tries" / animal_id / "animal"
    if not tries.is_dir():
        return False
    attempts = sorted(
        (path for path in tries.iterdir() if path.name.isdigit()),
        key=lambda path: int(path.name),
    )
    if not attempts:
        return False
    errors_path = attempts[-1] / "errors.json"
    if not errors_path.is_file():
        return False
    try:
        errors = json.loads(errors_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return errors == []


def _pruned_regions(job_dir: Path, excused: set[str], dest: Path) -> dict[str, Path]:
    """Copies of the job's regions without the still-unmade targets (#67.1).

    The cast (`animals.*`) and the `fish` lose every excused id;
    regions naming none keep their own bytes. The paths overlay
    the draft the checks run on, so the job itself never changes.
    """
    overlaid = {}
    for path in sorted((job_dir / "regions").glob("*.json")):
        region = json.loads(path.read_text(encoding="utf-8"))
        cast = region.get("animals")
        changed = False
        if isinstance(cast, dict):
            for group in ("playable", "predators", "prey"):
                seated = cast.get(group)
                if isinstance(seated, list):
                    kept = [animal for animal in seated if animal not in excused]
                    if kept != seated:
                        cast[group] = kept
                        changed = True
        fish = region.get("fish")
        if isinstance(fish, list):
            kept = []
            for entry in fish:
                kind = entry.get("kind") if isinstance(entry, dict) else entry
                if kind not in excused:
                    kept.append(entry)
            if kept != fish:
                region["fish"] = kept
                changed = True
        if changed:
            out = dest / path.name
            out.write_text(json.dumps(region, indent=2), encoding="utf-8")
            overlaid[f"regions/{path.name}"] = out
    return overlaid


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
    if research.get("looks"):
        species["looks"] = research["looks"]
    return species


def _measurements(research: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """Sourced sizes in centimetres; the spec divides by 100."""
    if target["prey"]:
        return {"length_cm": target["size_cm"]}
    size = research["size"]
    measurements = {"length_cm": size["length_cm"]}
    if size.get("weight_kg") is not None:
        measurements["weight_kg"] = size["weight_kg"]
    for key in ("shoulder_cm", "height_cm", "tail_cm"):
        if research.get("looks", {}).get(key) is not None:
            measurements[key] = research["looks"][key]
    return measurements


def _description(
    entry: dict[str, Any], research: dict[str, Any], target: dict[str, Any]
) -> str:
    """What the sculptor sees: looks first, shape from the start."""
    if target["prey"]:
        base = (
            f"{entry['name']} ({entry['latin']}), about "
            f"{target['size_cm']} cm long: the main animal's prey."
        )
        looks = target.get("looks")
        if not looks:
            return base
        bits = [base]
        if looks.get("colours"):
            bits.append(f"Colours: {', '.join(looks['colours'])}.")
        if looks.get("marks"):
            bits.append(f"Marks: {', '.join(looks['marks'])}.")
        return " ".join(bits)
    looks = research.get("looks")
    if looks:
        return _looks_description(entry, looks)
    year = research["year"]
    foods = [research["main_food"], *research["other_foods"]]
    dinner = ", ".join(food["name"] for food in foods)
    return (
        f"{entry['name']} ({entry['latin']}): {year['shelter']} "
        f"{year['young']} Eats {dinner}."
    )


def _looks_description(entry: dict[str, Any], looks: dict[str, Any]) -> str:
    """The research's looks as sculpting direction (#57.1)."""
    bits = [f"{entry['name']} ({entry['latin']})"]
    sizes = []
    if looks.get("shoulder_cm") is not None:
        sizes.append(f"shoulder {looks['shoulder_cm']} cm")
    if looks.get("height_cm") is not None:
        sizes.append(f"height {looks['height_cm']} cm")
    if looks.get("tail_cm") is not None:
        sizes.append(f"tail {looks['tail_cm']} cm")
    if sizes:
        bits.append("Sizes: " + ", ".join(sizes) + ".")
    coat = looks.get("coat", {})
    if coat:
        bits.append(
            f"Coat {coat.get('main', '')} ({coat.get('main_hex', '')}) "
            f"with {coat.get('belly', '')} ({coat.get('belly_hex', '')}) belly."
        )
    marks = [mark.get("mark", "") for mark in looks.get("marks", [])]
    if marks:
        bits.append("Field marks: " + "; ".join(marks) + ".")
    return " ".join(bits)


# What the references look like, until the table carries a Looks
# column of its own (#63.2). 1.0 keeps this.
_REFERENCE_LOOKS = {
    "fox": (
        "tall pointed dark ears, a long bushy tail as long as half "
        "its body, a long narrow muzzle, thin black legs with small "
        "paws, a red coat with a white chest and tail tip"
    ),
    "vole": (
        "a small round body, short legs, small round ears, a short "
        "thin tail, a blunt short muzzle, brown fur"
    ),
    "moose": (
        "very long legs, a deep heavy body, a long heavy head with "
        "a big overhanging nose, broad flat antlers, a hump at the "
        "shoulders, a very short tail, dark brown fur"
    ),
}


def _reference_looks(world: Path, start_from: str) -> str | None:
    """What the reference model looks like (#63.2).

    The reference table's Looks column when it lands there,
    else the three lines above, else the reference spec's own
    name — the examples describe themselves (#77.1).
    """
    readme = world / "models" / "README.md"
    if readme.is_file():
        for line in readme.read_text(encoding="utf-8").splitlines():
            if not line.startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) != 4 or cells[0].startswith("---"):
                continue
            starts = set(re.findall(r"[a-z][a-z0-9_]*", cells[1].lower()))
            if start_from.lower() in starts and cells[3]:
                return cells[3]
    if start_from in _REFERENCE_LOOKS:
        return _REFERENCE_LOOKS[start_from]
    ref = _reference_spec(world, start_from)
    if ref is not None and isinstance(ref.get("name"), str) and ref["name"]:
        return ref["name"]
    return None


def _reference_line(world: Path, start_from: str, entry: dict[str, Any]) -> str:
    """The reference's looks, then its table line (#60.2, #63.2)."""
    bits = []
    looks = _reference_looks(world, start_from)
    if looks:
        bits.append(
            f"What the `{start_from}` model looks like (1.0 keeps this): {looks}"
        )
    row = _reference_row(world, start_from, entry.get("name", ""))
    if row is None:
        bits.append("(the species is not on the reference table)")
    else:
        new, change = row
        bits.append(f"{new}: {change}")
    return "\n".join(bits)


# Table rows name a kind ("Snakes"), the animal a species ("anaconda").
_KIND_ALIASES = {
    "anaconda": "snake",
    "python": "snake",
    "boa": "snake",
    "cobra": "snake",
    "viper": "snake",
    "adder": "snake",
    "rattlesnake": "snake",
    "mamba": "snake",
}


def _reference_row(world: Path, start_from: str, name: str) -> tuple[str, str] | None:
    """The Reference-models line for this start and species (#57.2)."""
    readme = world / "models" / "README.md"
    if not readme.is_file():
        return None
    rows = _reference_table(readme.read_text(encoding="utf-8"))
    words = {word.strip().lower() for word in name.replace("-", " ").split()}
    words.update(_KIND_ALIASES[word] for word in list(words) if word in _KIND_ALIASES)
    for new, start, change in rows:
        starts = set(re.findall(r"[a-z][a-z0-9_]*", start.lower()))
        if start_from.lower() not in starts:
            continue
        kinds = set(re.findall(r"[a-z][a-z0-9_]*", new.lower()))
        kinds.update(token[:-1] for token in list(kinds) if token.endswith("s"))
        if words & kinds:
            return new, change
    return None


def _reference_table(text: str) -> list[tuple[str, str, str]]:
    """(New animal, Start from, Change) rows of the table."""
    rows = []
    in_table = False
    for line in text.splitlines():
        if line.startswith("## Reference models"):
            in_table = True
            continue
        if in_table and line.startswith("## "):
            break
        if not in_table or not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 3 or cells[0].startswith("---") or cells[0] == "New animal":
            continue
        rows.append((cells[0], cells[1], cells[2]))
    return rows


def _part_list(ref: dict[str, Any]) -> str:
    """The reference spec's parts: name, colour, bone role (#60.2)."""
    roles = {
        bone["name"]: bone.get("role", "none")
        for bone in ref.get("bones", [])
        if isinstance(bone, dict) and "name" in bone
    }
    lines = []
    for part in ref.get("parts", []):
        if not isinstance(part, dict) or "name" not in part:
            continue
        role = roles.get(part.get("bone"), "none")
        lines.append(f"- {part['name']}: {part.get('color', '?')}, {role}")
    return "\n".join(lines)


def _with_example(template: str, world: Path, example_id: str) -> str:
    """The one-shot example: the `file_from` animal, and only it (#70)."""
    content = (world / "animals" / f"{example_id}.json").read_text(encoding="utf-8")
    content = content.replace("$", "$$")
    template = template.replace("$EXAMPLE_1_NAME", example_id)
    template = template.replace("$EXAMPLE_1", content)
    template = template.replace("$EXAMPLE_2_NAME", "(only one example: one shot)")
    template = template.replace("$EXAMPLE_2", "")
    return template


def _rotated(axis: list[float], angle: float, vec: list[float]) -> list[float]:
    """vec turned about a unit axis (Rodrigues), the tool's rotate (#134)."""
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    dot = sum(axis[i] * vec[i] for i in range(3))
    cross = [
        axis[1] * vec[2] - axis[2] * vec[1],
        axis[2] * vec[0] - axis[0] * vec[2],
        axis[0] * vec[1] - axis[1] * vec[0],
    ]
    return [
        vec[i] * cos_a + cross[i] * sin_a + axis[i] * dot * (1 - cos_a)
        for i in range(3)
    ]


def _part_axis_along(part: dict[str, Any], direction: list[float]) -> int:
    """The part's local axis (0, 1, 2) closest to a world direction (#134).

    The tool's long_axis: pitch/yaw/roll turn each local axis to
    the world first, then the best-aligned one wins.
    """
    pitch, yaw, roll = (math.radians(a) for a in (part.get("rot") or [0, 0, 0]))
    best, best_score = 0, -1.0
    for axis in range(3):
        vec = [0.0, 0.0, 0.0]
        vec[axis] = 1.0
        turned = [vec[0], -vec[2], vec[1]]
        turned = _rotated([0.0, -1.0, 0.0], roll, turned)
        turned = _rotated([1.0, 0.0, 0.0], pitch, turned)
        turned = _rotated([0.0, 0.0, 1.0], yaw, turned)
        world_vec = [turned[0], turned[2], -turned[1]]
        score = abs(sum(world_vec[i] * direction[i] for i in range(3)))
        if score > best_score:
            best, best_score = axis, score
    return best


def _tail_reach(tail_parts: list[dict[str, Any]], base: list[float]) -> float:
    """The tail's reach from its base (#134): each part's centre
    distance plus its half extent along its own line, the furthest
    wins. The tool's definition - a tail may hang down or reach
    back, so a z span alone undershoots it."""
    reach = 0.0
    for part in tail_parts:
        length = math.dist(part["at"], base)
        if length:
            line = [(part["at"][i] - base[i]) / length for i in range(3)]
            axis = _part_axis_along(part, line)
        else:
            axis = max(range(3), key=lambda i: part["size"][i])
        reach = max(reach, length + part["size"][axis] / 2)
    return reach


def _on_back_legs(spec: dict[str, Any]) -> bool:
    """Whether the spec stands on its back legs (#134): its leg
    bones reach below zero. Then shoulders have no meaning and
    the tool skips them, so the builder must not demand them."""
    return any(
        isinstance(bone.get("tail"), list)
        and len(bone["tail"]) > 2
        and bone["tail"][2] < 0
        for bone in spec.get("bones", [])
        if isinstance(bone, dict) and bone.get("role") == "leg"
    )


def _measure_spec(spec: dict[str, Any]) -> dict[str, float]:
    """What the spec measures, in metres (#57.4, #60).

    The tool's own definitions, so code and adapt agree:
    shoulder is the top of the back above the front legs,
    tail its parts' reach from the tail base, length the
    torso's span. Legs are the top of the leg parts (they
    reach y = 0); depth the body part's length axis. Specs
    without bone roles fall back to bone names. Anything
    the spec doesn't name stays out.
    """
    bones = {
        bone["name"]: bone
        for bone in spec.get("bones", [])
        if isinstance(bone, dict) and "name" in bone
    }
    parts = [
        part
        for part in spec.get("parts", [])
        if isinstance(part, dict)
        and isinstance(part.get("at"), list)
        and isinstance(part.get("size"), list)
    ]
    roles = {name: bone.get("role", "none") for name, bone in bones.items()}

    def role_of(part: dict[str, Any]) -> str:
        return roles.get(part.get("bone") or "", "none")

    found: dict[str, float] = {}
    front = [
        bone
        for bone in bones.values()
        if bone.get("role") == "leg"
        and isinstance(bone.get("head"), list)
        and bone["head"][2] > 0
    ]
    backs = [part for part in parts if role_of(part) == "body"]
    if front and backs:
        z = front[0]["head"][2]
        tops = [
            part["at"][1] + part["size"][1] / 2
            for part in backs
            if abs(part["at"][2] - z) <= part["size"][2] / 2 + 0.05
        ]
        if tops:
            found["shoulder_m"] = max(tops)
    if "shoulder_m" not in found:
        body_bone = bones.get("body")
        if body_bone and isinstance(body_bone.get("head"), list):
            found["shoulder_m"] = float(body_bone["head"][1])
    torso = [
        part
        for part in parts
        if not (part.get("bone") or "").startswith(("tail", "leg"))
    ]
    if torso:
        found["length_m"] = max(
            part["at"][2] + part["size"][2] / 2 for part in torso
        ) - min(part["at"][2] - part["size"][2] / 2 for part in torso)
    tail_parts = [
        part
        for part in parts
        if (part.get("bone") or "").startswith("tail") or role_of(part) == "tail"
    ]
    tail_base = next(
        (
            bone["head"]
            for bone in bones.values()
            if bone.get("role") == "tail" and isinstance(bone.get("head"), list)
        ),
        None,
    )
    if tail_parts and tail_base is not None:
        found["tail_m"] = _tail_reach(tail_parts, tail_base)
    elif tail_parts:
        found["tail_m"] = max(
            part["at"][2] + part["size"][2] / 2 for part in tail_parts
        ) - min(part["at"][2] - part["size"][2] / 2 for part in tail_parts)
    else:
        tails = [
            bone
            for name, bone in bones.items()
            if name.startswith("tail")
            and isinstance(bone.get("head"), list)
            and isinstance(bone.get("tail"), list)
        ]
        if tails:
            found["tail_m"] = sum(
                math.dist(bone["head"], bone["tail"]) for bone in tails
            )
    legs = [part for part in parts if (part.get("bone") or "").startswith("leg")]
    if legs:
        found["leg_m"] = max(part["at"][1] + part["size"][1] / 2 for part in legs)
    body = next((part for part in parts if part.get("name") == "body"), None)
    if body is not None:
        found["depth_m"] = float(body["size"][2])
    if parts:
        found["top_m"] = max(part["at"][1] + part["size"][1] / 2 for part in parts)
    return found


def _measure_errors(
    spec: dict[str, Any],
    research: dict[str, Any],
    target: dict[str, Any],
    world: Path,
) -> list[str]:
    """The spec's sizes against the sourced ones, free (#57.4).

    Length, shoulder, tail and height within 20% of the research;
    legs-against-depth within 30% of the reference's ratio (ratios
    shift between species). Each miss names exact numbers for the
    fix round. Missing numbers on either side skip quietly.
    """
    found = _measure_spec(spec)
    sourced = _sourced_sizes(research, target)
    return _size_errors(found, sourced, spec) + _ratio_errors(found, target, world)


def _reference_path(world: Path, start_from: str) -> Path | None:
    """Where the reference spec lives: models, then examples (#77.1)."""
    for path in (
        world / "models" / f"{start_from}.json",
        world / "models" / "examples" / f"{start_from}.json",
    ):
        if path.is_file():
            return path
    return None


def _reference_spec(world: Path, start_from: str) -> dict[str, Any] | None:
    """The reference spec, or nothing when it isn't a plain file."""
    path = _reference_path(world, start_from)
    if path is None:
        return None
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return spec if isinstance(spec, dict) else None


def _run_adapt(
    world: Path, start_from: str | Path, edits_path: Path, out_path: Path
) -> dict[str, Any]:
    """The world's adapt tool: reference plus edits become the spec.

    The reference is a list id, or (for `newshape` sizing) the
    loop's spec file itself.
    """
    tool = world / "tools" / "adapt_model.py"
    if isinstance(start_from, Path):
        ref = start_from
    else:
        ref = (
            _reference_path(world, start_from)
            or world / "models" / f"{start_from}.json"
        )
    try:
        done = subprocess.run(
            [
                sys.executable,
                str(tool),
                str(ref),
                str(edits_path),
                "--out",
                str(out_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"ok": False, "log": str(error)}
    log = (done.stderr or done.stdout or "").strip()
    return {"ok": done.returncode == 0, "log": log or "adapted"}


def _edits_schema(ref: dict[str, Any]) -> dict[str, Any]:
    """The edits call's strict schema, built from the reference (#60.2).

    Colours and part names come from the reference spec itself,
    so the answer can only name what exists; factors stay in the
    tool's 0.4–2.5 (bill depth to 4.0). Bird factors join only
    when the reference has such parts. Every key required,
    nothing extra, flat.
    """
    colours = list(ref.get("colors", {}))
    parts = [part["name"] for part in ref.get("parts", []) if "name" in part]
    offered = _offered_factors(ref)
    factors = {
        name: {
            "type": "number",
            "minimum": 0.4,
            "maximum": 4.0 if name == "bill_depth" else 2.5,
            "description": f"{name}, 1 meaning as the reference",
        }
        for name in offered
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "colors",
            "new_colors",
            *offered,
            "repaint",
            "unmakable",
            "key_marks",
            "bands",
            "band_color",
        ],
        "properties": {
            "colors": {
                "type": "object",
                "additionalProperties": False,
                "required": colours,
                "properties": {
                    name: {
                        "type": "string",
                        "description": f"{name} as hex, like #7a4a2b",
                    }
                    for name in colours
                },
            },
            "new_colors": {
                "type": "array",
                "description": "More colours the animal needs; usually empty",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "hex"],
                    "properties": {
                        "name": {"type": "string"},
                        "hex": {
                            "type": "string",
                            "description": "Hex, like #7a4a2b",
                        },
                    },
                },
            },
            **factors,
            "repaint": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["part", "color"],
                    "properties": {
                        "part": {"type": "string", "enum": parts},
                        "color": {"type": "string", "enum": colours},
                    },
                },
            },
            "unmakable": {
                "type": "array",
                "description": (
                    "Marks the animal has that no edit above can make "
                    "(a mane, antlers), as plain words"
                ),
                "items": {"type": "string"},
            },
            "key_marks": {
                "type": "array",
                "description": (
                    "The one or two marks that make the species known, "
                    "from the research's looks, as plain words; empty "
                    "only when the looks name none"
                ),
                "items": {"type": "string"},
            },
            "bands": {
                "type": "integer",
                "description": (
                    "Wide body bands, 1 to 5, when the looks name "
                    "bands or bars; 0 when they name none"
                ),
                "minimum": 0,
                "maximum": 5,
            },
            "band_color": {
                "type": "string",
                "description": (
                    "The bands' colour name from colors or "
                    "new_colors; empty when bands is 0"
                ),
            },
        },
    }


def _marks_schema() -> dict[str, Any]:
    """The marks review's strict schema: which key marks show (#146.3)."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["shown", "missing"],
        "properties": {
            "shown": {
                "type": "array",
                "description": "The key marks the render shows",
                "items": {"type": "string"},
            },
            "missing": {
                "type": "array",
                "description": "The key marks the render lacks",
                "items": {"type": "string"},
            },
        },
    }


def _edits_files(
    answer: str, ref: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[str]]:
    """The try's edits, or why the answer holds none.

    Decoding already follows the strict schema; code only
    checks the shape survived and the names exist.
    """
    try:
        edits = json.loads(_unfence(answer))
    except json.JSONDecodeError as error:
        return None, [f"not JSON: {error}"]
    if not isinstance(edits, dict):
        return None, ["reply with the edits as one JSON object"]
    want = {"colors", "repaint", "unmakable", *_offered_factors(ref)}
    missing = sorted(want - set(edits))
    if missing:
        return None, [f"the edits leave out {', '.join(missing)}"]
    colours = set(ref.get("colors", {}))
    if set(edits.get("colors") or {}) != colours:
        return None, ["the edits must colour every reference colour, no others"]
    parts = {part["name"] for part in ref.get("parts", []) if "name" in part}
    for item in edits.get("repaint") or []:
        if item.get("part") not in parts:
            return None, [f"repaint names no part {item.get('part')!r}"]
        if item.get("color") not in colours:
            return None, [f"repaint names no colour {item.get('color')!r}"]
    return edits, []


def _tool_marks(words: list[str]) -> tuple[list[str], list[str]]:
    """Look-mark words to the tool's marks (#63.1).

    "ruff" gives `ruff`, "tuft" gives `ear_tufts`, "tail"
    plus "tip" gives `tail_tip`; few wide bars or bands give
    `bands`, many thin stripes give `stripes` (#149.1), spots
    give `spots`, a hammer head gives `hammer`, and spines
    give `spines` unless they are fin spines, which the
    tool's hedgehog coat can't make (#146.3). The rest go
    unmakable, for the report.
    """
    marks: list[str] = []
    unmatched: list[str] = []
    for text in words:
        low = text.lower()
        found = []
        if "ruff" in low:
            found.append("ruff")
        if "tuft" in low:
            found.append("ear_tufts")
        if "tail" in low and "tip" in low:
            found.append("tail_tip")
        if re.search(r"\b(bands?|banded|bars?)\b", low):
            found.append("bands")
        if re.search(r"\b(stripes?|striped)\b", low):
            found.append("stripes")
        if re.search(r"\bspots?\b|\bspotted\b", low):
            found.append("spots")
        if "hammer" in low:
            found.append("hammer")
        spiny = re.search(r"\bspines?\b|\bspiny\b", low) is not None
        finny = "fin" in low or re.search(r"\brays?\b", low) is not None
        if spiny and not finny:
            found.append("spines")
        if found:
            marks.extend(mark for mark in found if mark not in marks)
        else:
            unmatched.append(text)
    return marks, unmatched


def _mark_miss(mark: str) -> str:
    """A missing key mark as a fix-round error (#146.3).

    When a tool mark paints it, the fix keeps the mark and
    its colours; otherwise colours and repaints must.
    """
    tools, _ = _tool_marks([mark])
    if not tools:
        return (
            f"the render shows no {mark}: paint it with colours "
            "and repaints, never with factors"
        )
    names = " and ".join(f"the {tool} mark" for tool in tools)
    return (
        f"the render shows no {mark}: {names} paints it, so keep "
        "the mark and its colours, and don't repaint its parts away"
    )


def _looks_sizes(looks: dict[str, Any]) -> dict[str, float]:
    """Shoulder and tail for the adapt tool, from one looks block.

    A missing shoulder stays out (the reference's proportions
    hold); a missing tail, or one under 3 cm, sends 0 (#63.4).
    """
    sizes: dict[str, float] = {}
    if looks.get("shoulder_cm") is not None:
        sizes["shoulder_m"] = looks["shoulder_cm"] / 100
    tail = looks.get("tail_cm")
    sizes["tail_m"] = 0.0 if tail is None or tail < 3 else tail / 100
    return sizes


def _edits_sizes(research: dict[str, Any], target: dict[str, Any]) -> dict[str, float]:
    """Sizes for the adapt tool, from the research, never the AI (#60.3).

    Length always; shoulder and tail from the looks when some
    exist (#63.3), so prey keep more than their length.
    """
    if target["prey"]:
        sizes = {"length_m": target["size_cm"] / 100}
        if target.get("looks"):
            sizes.update(_looks_sizes(target["looks"]))
        return sizes
    sizes = {"length_m": research["size"]["length_cm"] / 100}
    if research.get("looks"):
        sizes.update(_looks_sizes(research["looks"]))
    return sizes


def _sizing_edits(
    animal_id: str, name: str, author: str, sourced: dict[str, float]
) -> dict[str, Any]:
    """Sizes-only edits for code sizing: no factors, no AI call."""
    edits: dict[str, Any] = {"id": animal_id, "name": name, "author": author}
    for key in ("length_m", "shoulder_m", "tail_m"):
        if key in sourced:
            edits[key] = sourced[key]
    return edits


def _sourced_sizes(
    research: dict[str, Any], target: dict[str, Any]
) -> dict[str, float]:
    """What the spec must measure, in metres, from the research."""
    if target["prey"]:
        return {"length_m": (target.get("size_cm") or 0) / 100}
    looks = research.get("looks") or {}
    sourced = {"length_m": research["size"]["length_cm"] / 100}
    if looks.get("shoulder_cm") is not None:
        sourced["shoulder_m"] = looks["shoulder_cm"] / 100
    if looks.get("height_cm") is not None:
        sourced["top_m"] = looks["height_cm"] / 100
    if looks.get("tail_cm") is not None:
        sourced["tail_m"] = looks["tail_cm"] / 100
    return sourced


def _size_misses(
    found: dict[str, float],
    sourced: dict[str, float],
    spec: dict[str, Any] | None = None,
) -> dict[str, tuple[float, float]]:
    """Code-owned sizes off by more than a fifth: key to (have, want).

    Specs on their back legs skip the shoulder: it has no
    meaning there and the tool leaves it alone (#134).
    """
    misses = {}
    for key in ("length_m", "shoulder_m", "tail_m"):
        if key not in found or key not in sourced or not sourced[key]:
            continue
        if key == "shoulder_m" and spec is not None and _on_back_legs(spec):
            continue
        if abs(found[key] - sourced[key]) / sourced[key] > 0.20:
            misses[key] = (found[key], sourced[key])
    return misses


def _size_errors(
    found: dict[str, float],
    sourced: dict[str, float],
    spec: dict[str, Any] | None = None,
) -> list[str]:
    """Absolute misses with exact numbers, for the fix round.

    Specs on their back legs skip the shoulder (#134).
    """
    names = {
        "length_m": "body",
        "shoulder_m": "shoulder",
        "tail_m": "tail",
        "top_m": "height",
    }
    errors = []
    for key, name in names.items():
        if key not in found or key not in sourced or not sourced[key]:
            continue
        if key == "shoulder_m" and spec is not None and _on_back_legs(spec):
            continue
        if abs(found[key] - sourced[key]) / sourced[key] <= 0.20:
            continue
        errors.append(
            f"the spec's {name} is {found[key] * 100:.0f} cm, "
            f"sourced {sourced[key] * 100:.0f} cm: size it to the source"
        )
    return errors


def _ratio_errors(
    found: dict[str, float], target: dict[str, Any], world: Path
) -> list[str]:
    """Legs-against-depth drift from the reference's ratio."""
    if "leg_m" not in found or "depth_m" not in found or not found["depth_m"]:
        return []
    ratio = found["leg_m"] / found["depth_m"]
    reference = _reference_spec(world, target["start_from"])
    if reference is None:
        return []
    theirs = _measure_spec(reference)
    if "leg_m" not in theirs or "depth_m" not in theirs or not theirs["depth_m"]:
        return []
    want = theirs["leg_m"] / theirs["depth_m"]
    if abs(ratio - want) / want <= 0.30:
        return []
    return [
        f"legs are {ratio:.1f}x the body's depth, "
        f"the {target['start_from']}'s are {want:.1f}x: "
        "shorten the legs or deepen the body"
    ]


def _verdicts(
    world: Path,
    ctx: Any,
    animal_id: str,
    phase: str,
    attempt: int,
    extra: dict[str, Path] | None = None,
) -> dict[str, dict[str, Any]]:
    result = check_draft(
        world, ctx.job_dir, f"oneshot-{animal_id}-{phase}-{attempt}", extra=extra
    )
    return {entry.get("check", ""): entry for entry in result.get("results", [])}


def _verdict_errors(
    verdicts: dict[str, dict[str, Any]], names: tuple[str, ...], animal_id: str
) -> list[str]:
    """The named verdicts' problems as fix-round errors."""
    errors = []
    for name in names:
        entry = verdicts.get(name, {})
        if entry.get("status") != "fail":
            continue
        if not entry.get("problems"):
            raise _Stopped(
                f"{animal_id}: the {name} check failed without problems; a person looks"
            )
        for file, lines in problems_by_file(entry["problems"]).items():
            for line in lines:
                text = line[2:].strip() if line.startswith("- ") else line.strip()
                errors.append(f"{file}: {text}" if file else text)
    return errors


# Text the checks demand, and the flag that demands it (#56.1).
_TEXT_FLAGS = {
    "dig": "digs",
    "safe_tag": "hunts",
    "bite": "hunts",
    "caught_you": "hunts",
    "rival_bite": "territorial",
    "rival_caught_you": "territorial",
}


def _guide(error: str) -> str:
    """A missing-text error becomes fix guidance, not a stop (#56.1).

    Either write the text or set the flag false when the research
    says the animal doesn't do that; anything else passes through.
    """
    match = re.search(r"needs? texts? (.*)$", error)
    if not match:
        return error
    texts = set(re.findall(r'"([a-z_]+)"', match.group(1)))
    flags = sorted({_TEXT_FLAGS[text] for text in texts if text in _TEXT_FLAGS})
    if not flags:
        return error
    picks = " or ".join(f"`{flag}`" for flag in flags)
    return (
        f"{error} Fix it one of two ways: write the text, or set {picks} "
        "false when the surveyed animal doesn't do that."
    )


def _unfence(answer: str) -> str:
    """Strict JSON, with or without the fences models love to add."""
    text = answer.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    return match.group(1) if match else text


def _code_diet(research: dict[str, Any]) -> list[str]:
    """The animal's diet, from its research's foods (#140.3).

    Every food's kind in order, deduped, except `prey`: hunted
    animals become `hunts` through `_code_hunts`, never diet. A
    gull over mussels and fish eats `bottom_bugs`, the way the
    review wrote it; the model's own diet never stands.
    """
    kinds: list[str] = []
    foods = [research.get("main_food") or {}]
    foods.extend(research.get("other_foods") or [])
    for food in foods:
        kind = food.get("kind") if isinstance(food, dict) else None
        if kind and kind != "prey" and kind not in kinds:
            kinds.append(kind)
    return kinds


#: The build's measured game length, per animal it builds.
_MEASURED_RE = (
    r"for animals/{id}\.json:\s*\"length\":\s*([0-9]+\.[0-9]+)\s*\(real size\)"
)


def _measured_length(log: str, animal_id: str) -> float | None:
    """The build log's measured length for one animal (#140.3)."""
    match = re.search(_MEASURED_RE.format(id=re.escape(animal_id)), log)
    return float(match.group(1)) if match else None


class HomeMakerStage:
    """Make the minimum set's home: a choices call, then the adapt tool."""

    name = "newhome"

    def __init__(
        self,
        chat: Any | None = None,
        adapt: Any | None = None,
        plant_cache: Path | None = None,
        plant_build: Any | None = None,
    ) -> None:
        self._chat = chat
        self._adapt = adapt or _run_adapt_home
        self._plant_cache = plant_cache
        self._plant_build = plant_build

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
        home_id = brief.get("home_id") or habitat
        made = ctx.job_dir / "homes" / f"{home_id}.json"
        passed = ctx.job_dir / "checks" / "newhome.json"
        if made.is_file() and passed.is_file():
            # A failed try leaves its home behind too; only a
            # passing run counts as done, or the failure stands (#64.3).
            return {"home_files": [f"homes/{home_id}.json"], "cost_usd": 0.0}
        before = ctx.tracker.spent_usd
        base, plants, trees, looks = self._setup(world, ctx, brief)
        self._make(
            ctx,
            world,
            brief,
            research,
            habitat,
            base,
            plants,
            trees,
            looks,
            home_id=home_id,
        )
        cost = round(ctx.tracker.spent_usd - before, 6)
        checks = ctx.job_dir / "checks"
        checks.mkdir(parents=True, exist_ok=True)
        (checks / "newhome.json").write_text(
            json.dumps({"cost_usd": cost, "made": home_id, "plants": []}, indent=2),
            encoding="utf-8",
        )
        return {
            "home_files": [f"homes/{home_id}.json"],
            "plant_files": [],
            "cost_usd": cost,
        }

    @staticmethod
    def _setup(
        world: Path, ctx: Any, brief: dict[str, Any]
    ) -> tuple[Path, list[str], list[str], dict[str, Any]]:
        """The choices call's world inputs: base, plants, trees, looks."""
        habitat = brief["home"]
        if brief.get("base_home"):
            base = world / "homes" / f"{brief['base_home']}.json"
            if not base.is_file():
                raise _Stopped(
                    f"no home template {brief['base_home']} in this checkout"
                )
        else:
            base = _closest_home(world, habitat)
        plants = sorted(
            path.stem for path in (world / "models" / "plants").glob("*.json")
        )
        trees = _library_trees(world)
        looks_path = ctx.job_dir / "looks.json"
        looks: dict[str, Any] = (
            json.loads(looks_path.read_text(encoding="utf-8"))
            if looks_path.is_file()
            else {}
        )
        return base, plants, trees, looks

    def run_fix(
        self,
        ctx: Any,
        world: Path,
        brief: dict[str, Any],
        research: dict[str, Any],
        note: str | None,
        round_no: int,
    ) -> None:
        """A D8 choices fix on the made template: previous form plus note.

        The home stage drives this on an improve round, through a
        deferred import (this module already imports that one) (#82.6).
        """
        habitat = brief["home"]
        previous = _last_try_answer(ctx.job_dir / "tries" / habitat, "")
        base, plants, trees, looks = self._setup(world, ctx, brief)
        self._make(
            ctx,
            world,
            brief,
            research,
            habitat,
            base,
            plants,
            trees,
            looks,
            previous_form=previous,
            note=note,
            tag=f"r{round_no}-",
        )

    def _make(
        self,
        ctx: Any,
        world: Path,
        brief: dict[str, Any],
        research: dict[str, Any],
        habitat: str,
        base: Path,
        plants: list[str],
        trees: list[str],
        looks: dict[str, Any],
        previous_form: str | None = None,
        note: str | None = None,
        tag: str = "",
        home_id: str | None = None,
    ) -> None:
        """One home: a choices call, then the adapt tool, plus one fix round.

        An improve fix passes the previous form and the grown-up's
        note, and tags its tries with the round (#82.6). A border
        home lands under its own id, not its habitat.
        """
        prompt = self._prompt(brief, research, habitat, base, plants, trees, looks)
        prompt += improve_blocks(previous_form, note)
        request = {
            "type": "json_schema",
            "json_schema": {
                "name": "HomeChoices",
                "schema": _home_choices_schema(plants, trees),
                "strict": True,
            },
        }
        errors: list[str] | None = None
        previous: str | None = None
        for attempt in range(1, MAX_TRIES + 1):
            ctx.check_budget()
            answer = self._ask(ctx, prompt, request, errors, previous)
            previous = answer
            choices, file_errors = _choices_files(answer)
            if choices is not None:
                _code_choices(choices, habitat, brief, research)
                file_errors = self._adapt_try(
                    ctx, world, base, choices, habitat, attempt, tag
                )
            self._save_try(ctx, habitat, attempt, answer, file_errors, tag)
            if choices is not None and not file_errors:
                _place_home(ctx, habitat, attempt, tag, home_id or habitat)
                return
            errors = file_errors or ["the answer held no usable choices"]
        if _build_only(errors or []):
            raise _Stopped(
                f"{habitat}: still failing after one fix round "
                f"({'; '.join(errors or [])}); the worker could not build it"
            )
        raise _Stopped(
            f"{habitat}: still failing after one fix round "
            f"({'; '.join(errors or [])}); a person makes it"
        )

    def _ask(
        self,
        ctx: Any,
        prompt: str,
        request: dict[str, Any],
        errors: list[str] | None,
        previous: str | None = None,
    ) -> str:
        first = {"role": "user", "content": prompt}
        if errors and previous is not None:
            notes = "\n".join(f"- {error}" for error in errors)
            messages = fix_messages(first, previous, notes)
        else:
            messages = [first]
        chat = self._chat or ctx.chat
        response = _ask_with_room(chat, messages, response_format=request)
        return (response.choices[0].message.content or "").strip()

    def _adapt_try(
        self,
        ctx: Any,
        world: Path,
        base: Path,
        choices: dict[str, Any],
        habitat: str,
        attempt: int,
        tag: str = "",
    ) -> list[str]:
        """The try's choices through the world's adapt tool, with its checks."""
        folder = ctx.job_dir / "tries" / habitat / f"{tag}{attempt}"
        folder.mkdir(parents=True, exist_ok=True)
        base = _authored_base(base, folder)
        choices_path = folder / "choices.json"
        choices_path.write_text(json.dumps(choices, indent=2), encoding="utf-8")
        plant_ids = [
            plant["model"]
            for plant in choices.get("plants", [])
            if isinstance(plant, dict) and plant.get("model")
        ]
        missing = ensure_plant_models(
            world, plant_ids, self._plant_cache, self._plant_build
        )
        if missing:
            return missing
        errors = self._adapt(world, base, choices_path, folder / "home.json")
        specs = _build_first_specs(errors) if errors else []
        if errors and specs and _build_only(errors):
            # Missing builds are ours to fix: build what the check
            # names (tree specs too) and adapt again, no model call (#139.3).
            missing = ensure_specs(world, specs, self._plant_cache, self._plant_build)
            if missing:
                return missing
            return self._adapt(world, base, choices_path, folder / "home.json")
        return errors

    def _save_try(
        self,
        ctx: Any,
        habitat: str,
        attempt: int,
        answer: str,
        errors: list[str],
        tag: str = "",
    ) -> None:
        folder = ctx.job_dir / "tries" / habitat / f"{tag}{attempt}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "answer.json").write_text(answer, encoding="utf-8")
        (folder / "errors.json").write_text(
            json.dumps(errors, indent=2), encoding="utf-8"
        )

    def _prompt(
        self,
        brief: dict[str, Any],
        research: dict[str, Any],
        habitat: str,
        base: Path,
        plants: list[str],
        trees: list[str],
        looks: dict[str, Any],
    ) -> str:
        template = (PROMPTS_DIR / "home_choices.md").read_text(encoding="utf-8")
        closest = json.loads(base.read_text(encoding="utf-8"))
        excerpt = {
            key: closest.get(key, _BASE_FALLBACK[key])
            for key in ("description", "palette", "trees", "lake_life")
        }
        center = brief.get("center", {})
        return Template(template).substitute(
            {
                "HABITAT": habitat,
                "ANIMAL": brief.get("name", "") or "animal",
                "LAT": str(center.get("lat", 0.0)),
                "LON": str(center.get("lon", 0.0)),
                "PLACE_LINE": place_line(brief),
                "SURROUNDINGS": _surroundings_text(research, looks),
                "FOODS": _foods_text(research),
                "BASE": json.dumps(excerpt, indent=1).replace("$", "$$"),
                "PLANTS": ", ".join(plants),
                "TREES": "".join(f", {tree}" for tree in trees),
            }
        )


#: A choices-shaped base (a place example) lacks the filed
#: excerpt keys; the prompt shows empties instead of crashing.
_BASE_FALLBACK = {
    "description": "",
    "palette": {},
    "trees": {"density": "none", "mix": []},
    "lake_life": {},
}


def _authored_base(base: Path, folder: Path) -> Path:
    """The base with an author: an example base has none, and the
    region checks the adapt tool runs need one."""
    from .region import HOSTED_AUTHOR

    try:
        data = json.loads(base.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return base
    if isinstance(data.get("author"), str) and data["author"].strip():
        return base
    data["author"] = HOSTED_AUTHOR
    authored = folder / "base.json"
    authored.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return authored


def _library_trees(world: Path) -> list[str]:
    """Tree spec ids the game draws: the shipped library only (#69.1)."""
    return sorted(path.stem for path in (world / "models" / "trees").glob("*.json"))


def _closest_home(world: Path, habitat: str) -> Path:
    """The starting home: the lake one for lake and sea habitats (#65.3)."""
    name = "boreal_lake" if "lake" in habitat or "sea" in habitat else "boreal_forest"
    path = world / "homes" / f"{name}.json"
    if not path.is_file():
        raise _Stopped(f"no home template {name} in this checkout")
    return path


def _surroundings_text(research: dict[str, Any], looks: dict[str, Any]) -> str:
    """The surroundings as the lab listed them, with the plant looks."""
    lines = []
    for thing in research["surroundings"]:
        line = f"- {thing['name']} ({thing['kind']})"
        if thing.get("food_kind"):
            line += f", animals eat it as {thing['food_kind']}"
        lines.append(line)
    for name, found in (looks.get("plants") or {}).items():
        colours = ", ".join(found.get("colours", []))
        marks = ", ".join(found.get("marks", []))
        lines.append(f"  - {name}: {colours}; {marks}")
    return "\n".join(lines)


def _foods_text(research: dict[str, Any]) -> str:
    """The animal's foods as `name (kind)`, the main food first."""
    foods = [research["main_food"], *research.get("other_foods", [])]
    return ", ".join(f"{food['name']} ({food['kind']})" for food in foods)


def _home_choices_schema(plants: list[str], trees: list[str]) -> dict[str, Any]:
    """The choices form, strict: words, enums and hex colours only (#65.1)."""

    def obj(props: dict[str, Any], req: list[str] | None = None) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": req or list(props),
            "properties": props,
        }

    def enum(vals: list[str], description: str) -> dict[str, Any]:
        return {"type": "string", "enum": vals, "description": description}

    return obj(
        {
            "description": {
                "type": "string",
                "description": (
                    "one plain sentence a child understands: "
                    "the land, the trees, the water"
                ),
            },
            "land": enum(
                CHOICE_LEVELS["land"],
                (
                    "how the ground rises; mountains: high peaks "
                    "with bare rock above the trees"
                ),
            ),
            "water": enum(
                CHOICE_LEVELS["water"],
                (
                    "the main water: river (runs through the land), "
                    "lake (a big lake, the land around it), pools, "
                    "sea (a coast: a deep bay and the sea all round), none"
                ),
            ),
            "wetness": enum(
                CHOICE_LEVELS["wetness"],
                "ponds, bogs and side streams besides the main water",
            ),
            "water_name": {
                "type": "string",
                "description": (
                    "a plain name for the main water, like 'Slow river'; "
                    "no real place names"
                ),
            },
            "ice": enum(
                CHOICE_LEVELS["ice"],
                (
                    "none; frozen_in_winter (lakes or sea freeze in "
                    "winter); glaciers (ice on the mountain tops); polar "
                    "(ice over much of the land, icebergs, the water "
                    "frozen most of the year)"
                ),
            ),
            "caves": enum(
                CHOICE_LEVELS["caves"],
                (
                    "some: caves in the hills, only where the research "
                    "finds caves or dens in rock"
                ),
            ),
            "seabed": enum(
                CHOICE_LEVELS["seabed"],
                (
                    "the floor of the deep water: sand for warm seas "
                    "and reefs, mud otherwise"
                ),
            ),
            "palette": obj({key: _CHOICE_HEX for key in CHOICE_PALETTE}),
            "trees": obj(
                {
                    "density": enum(CHOICE_LEVELS["density"], "how many trees"),
                    "mix": {
                        "type": "array",
                        "maxItems": 4,
                        "items": obj(
                            {
                                "shape": enum(
                                    [*TREE_SHAPES, *trees],
                                    "the closest of the game's tree shapes",
                                ),
                                "leaves": _CHOICE_HEX,
                                "trunk": _CHOICE_HEX,
                                "weight": {
                                    "type": "integer",
                                    "description": (
                                        "share of the trees, all weights "
                                        "add to about 100"
                                    ),
                                },
                                "food": {
                                    "anyOf": [
                                        enum(
                                            [
                                                "browse",
                                                "cones",
                                                "bark",
                                                "berries",
                                                "seeds",
                                            ],
                                            "what animals eat from it",
                                        ),
                                        {"type": "null"},
                                    ]
                                },
                                "autumn": {
                                    "anyOf": [_CHOICE_HEX, {"type": "null"}],
                                    "description": (
                                        "leaf colour in autumn if the leaves "
                                        "turn and fall; null in the tropics "
                                        "or for evergreens"
                                    ),
                                },
                            }
                        ),
                    },
                }
            ),
            "grass": enum(CHOICE_LEVELS["grass"], "grass on open ground"),
            "flowers": {
                "type": "array",
                "maxItems": 4,
                "items": obj({"name": {"type": "string"}, "color": _CHOICE_HEX}),
            },
            "plants": {
                "type": "array",
                "items": obj(
                    {
                        "model": enum(
                            plants or ["none"],
                            (
                                "a plant the game already has; "
                                "only one that truly grows here"
                            ),
                        ),
                        "on": enum(
                            ["ground", "seabed"],
                            "seabed: corals and sea grass, under the water",
                        ),
                    }
                ),
            },
            "water_life": obj(
                {
                    key: enum(CHOICE_LEVELS["life"], key)
                    for key in ("reeds", "lilies", "pondweed")
                }
            ),
            "food": {
                "type": "array",
                "items": obj(
                    {
                        "kind": enum(CHOICE_FOOD_KINDS, "a food kind"),
                        "names": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "real foods of this kind here, plain names"
                            ),
                        },
                    }
                ),
            },
        }
    )


def _stripped(value: Any) -> Any:
    """Every string stripped: a stray space must not fail a colour,
    an enum or an id (#135.1)."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return [_stripped(item) for item in value]
    if isinstance(value, dict):
        return {key: _stripped(item) for key, item in value.items()}
    return value


def _ask_with_room(chat: Any, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
    """One chat call, retried once with more tokens when cut off (#135.2).

    Reasoning counts against the open host's cap, so an answer can
    stop mid-JSON; the retry doubles the room instead of spending a
    fix round on the truncation. Only an explicit length signal
    retries, never a guess from usage.
    """
    response = chat(messages, **kwargs)
    if not is_cut_off(response, 0):
        return response
    usage = getattr(response, "usage", None)
    used = 0
    if usage is not None:
        used = (
            getattr(usage, "output_tokens", 0)
            or getattr(usage, "completion_tokens", 0)
            or 0
        )
    return chat(messages, **{**kwargs, "max_tokens": max(DEFAULT_MAX_TOKENS, used * 2)})


def _choices_files(answer: str) -> tuple[dict[str, Any] | None, list[str]]:
    """The try's choices, without the shape's `none` plants."""
    try:
        choices = json.loads(_unfence(answer))
    except json.JSONDecodeError as error:
        return None, [f"not JSON: {error}"]
    if not isinstance(choices, dict):
        return None, ["reply with one choices object, nothing else"]
    choices = _stripped(choices)
    if isinstance(choices.get("plants"), list):
        choices["plants"] = [
            plant
            for plant in choices["plants"]
            if not (isinstance(plant, dict) and plant.get("model") == "none")
        ]
        for plant in choices["plants"]:
            if isinstance(plant, dict) and plant.get("on") == "ground":
                del plant["on"]
    return choices, []


def _code_choices(
    choices: dict[str, Any],
    habitat: str,
    brief: dict[str, Any],
    research: dict[str, Any],
) -> None:
    """Code's part of the choices: the id, the tropics, the sources (#65.2).

    A border home also carries its ecoregion, habitat and the home
    it was adapted from, set by code from the plan item; the model
    never writes them (#130.4).
    """
    lat = float((brief.get("center") or {}).get("lat", 0.0))
    tropical = abs(lat) < 23
    choices.update(
        {
            "id": brief.get("home_id") or habitat,
            "tropical": tropical,
            "sources": [*research["sources"], "GBIF.org"],
        }
    )
    if brief.get("ecoregion") is not None:
        choices["ecoregions"] = [brief["ecoregion"]]
        choices["habitat"] = habitat
        if brief.get("based_on"):
            choices["based_on"] = brief["based_on"]
    if tropical:
        # No ice in the tropics: no seasons there (#69.3).
        choices["ice"] = "none"


def _place_home(
    ctx: Any, habitat: str, attempt: int, tag: str = "", home_id: str | None = None
) -> None:
    """The passing try's home, where the home stage finds it."""
    homes_dir = ctx.job_dir / "homes"
    homes_dir.mkdir(exist_ok=True)
    src = ctx.job_dir / "tries" / habitat / f"{tag}{attempt}" / "home.json"
    (homes_dir / f"{home_id or habitat}.json").write_text(
        src.read_text(encoding="utf-8"), encoding="utf-8"
    )


def _run_adapt_home(
    world: Path, base: Path, choices_path: Path, out_path: Path
) -> list[str]:
    """The world's adapt tool with its checks; its ERROR/FAIL lines, if any."""
    try:
        proc = subprocess.run(
            [
                sys.executable,
                str(world / "tools" / "adapt_home.py"),
                str(base),
                str(choices_path),
                "--check",
                "--out",
                str(out_path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=HOME_ADAPT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise _Stopped(f"adapt_home.py did not run: {error}")
    if proc.returncode == 0:
        return []
    errors = []
    for line in (proc.stdout + proc.stderr).splitlines():
        if line.startswith("ERROR: "):
            errors.append(line[len("ERROR: ") :].strip())
        elif line.startswith("FAIL "):
            errors.append(line[len("FAIL ") :].strip())
    if errors:
        return errors
    raise _Stopped(f"adapt_home.py broke: {(proc.stdout + proc.stderr).strip()[:200]}")


class _Stopped(Exception):
    """The thing has no passing files. The job stops; a person makes it."""
