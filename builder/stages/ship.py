"""Stage 10, ship: an enrich item's checks, Finnish, and its pack.

An enrich item reaches the game with no hand steps (#174, #176):
the stage writes its Finnish, runs the child-safety review, gates
every check the reef review taught, and publishes the pack to
Storage. A failed check stops the item with the check and its
message, in the report and the progress entry, like any stage.
A plant item ships its own pack, like an animal: the game reads
plants in packs now (#188.3).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from ..enrich import (
    ConvertError,
    split_not_built,
)
from ..grid import center
from ..packs import (
    assemble_enrich_pack,
    check_pack,
    next_pack_version,
    publish_pack,
)
from ..world import check_draft
from .animals import _hunt_tags
from .safety import SafetyStage
from .words import WordsStage


class _Stopped(Exception):
    """A gate check failed, or the pack is unshippable."""


class ShipStage:
    """Finnish, safety, the gates, and the pack upload, in order."""

    name = "ship"

    def __init__(self, store: Any | None = None) -> None:
        self._store = store

    def run(self, ctx: Any) -> dict[str, Any]:
        if ctx.world_path is None:
            raise ValueError("ship needs --world")
        world = Path(ctx.world_path)
        brief = json.loads((ctx.job_dir / "home.json").read_text(encoding="utf-8"))
        if brief.get("species_key") is None and not brief.get("plant"):
            raise _Stopped("ship packs animals; this item ships by hand")
        if brief.get("plant") is not None and brief.get("species_key") is None:
            return self._run_plant(ctx, world, brief)
        animal_id = str(brief["id"])
        animal_path = ctx.job_dir / "animals" / f"{animal_id}.json"
        if not animal_path.is_file():
            raise _Stopped(f"{animal_id}: the maker left no animal file")
        animal = json.loads(animal_path.read_text(encoding="utf-8"))
        self._finnish(ctx, world, animal_id, animal)
        SafetyStage().run(ctx)
        self._render_look(ctx, brief, animal_id, animal)
        problems = gate_checks(ctx, world, brief, animal_id, animal)
        checks = ctx.job_dir / "checks"
        checks.mkdir(parents=True, exist_ok=True)
        (checks / "ship.json").write_text(
            json.dumps(
                {
                    "status": "fail" if problems else "pass",
                    "problems": problems,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        if problems:
            raise _Stopped("; ".join(problems[:5]))
        return self._pack(ctx, world, brief, animal_id, animal)

    def _finnish(
        self, ctx: Any, world: Path, animal_id: str, animal: dict[str, Any]
    ) -> None:
        """Its English and Finnish texts, this animal's keys only (#174.2).

        The animal file stays English; the chat translates (the open
        host when set, else Muse Spark). The Finnish file also
        carries the map name, translated from the file's `name`;
        English needs none: the map reads it from the file (#197.2).
        """
        strings = animal.get("text")
        if not isinstance(strings, dict) or not strings:
            raise _Stopped(f"{animal_id}: the animal file carries no text")
        for key, text in strings.items():
            if not isinstance(text, str):
                raise _Stopped(f"{animal_id}: text.{key} is no string")
        name = animal.get("name")
        if not isinstance(name, str) or not name:
            raise _Stopped(f"{animal_id}: the animal file carries no name")
        translated = WordsStage()._translate(
            ctx, world, "fi", {"map_name": name, **strings}
        )
        locale = ctx.job_dir / "locale"
        for language, texts in (("en", strings), ("fi", translated)):
            path = locale / language / "animals.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {f"animal.{animal_id}.{key}": text for key, text in texts.items()},
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

    def _render_look(
        self, ctx: Any, brief: dict[str, Any], animal_id: str, animal: dict[str, Any]
    ) -> None:
        """The ship's own look at the final preview's key marks (#197.4).

        The edit round's review judged an older model; the ship
        looks at the final preview itself, mark by mark, and the
        gate compares the two looks. Always writes
        `checks/marks_ship.json`, skipped when there is nothing
        to judge (library animals, markless research).
        """
        from .maker import review_marks

        job_dir = ctx.job_dir
        try:
            research = json.loads(
                (job_dir / "research.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            research = {}
        marks = _research_marks(research)
        verdict: dict[str, Any] = {"shown": [], "missing": [], "skipped": None}
        if not brief.get("in_library") and marks:
            preview = job_dir / "models" / f"{animal_id}.png"
            if not preview.is_file():
                raise _Stopped(f"{animal_id}: no final preview to look at")
            try:
                review = review_marks(
                    ctx.chat,
                    ctx.check_budget,
                    animal.get("name", animal_id),
                    marks,
                    preview.read_bytes(),
                )
            except ValueError as error:
                raise _Stopped(
                    f"{animal_id}: the ship's look never wrote valid JSON: {error}"
                ) from error
            verdict = {
                "shown": review["shown"],
                "missing": review["missing"],
                "skipped": None,
            }
        else:
            verdict["skipped"] = (
                "in_library" if brief.get("in_library") else "no key marks"
            )
        checks = job_dir / "checks"
        checks.mkdir(parents=True, exist_ok=True)
        (checks / "marks_ship.json").write_text(
            json.dumps(verdict, indent=2), encoding="utf-8"
        )

    def _run_plant(
        self, ctx: Any, world: Path, brief: dict[str, Any]
    ) -> dict[str, Any]:
        """A plant item's gates and its pack, like an animal (#188.3).

        Finnish, safety, the spec, the render review, the Finnish
        texts, and the world's checks on a world with the built
        model; then the pack uploads. Nothing commits to the
        world from the cloud: the world's copy job files the
        pack's content from the public files (0049 §7).
        """
        plant_id = str(brief["plant"])
        spec_path = ctx.job_dir / "models" / "plants" / f"{plant_id}.json"
        if not spec_path.is_file():
            raise _Stopped(f"{plant_id}: the maker left no plant file")
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        WordsStage()._plant_texts(ctx, world, ["fi"])
        SafetyStage().run(ctx)
        self._gate_checks_plant(ctx, world, plant_id, spec)
        return self._pack_plant(ctx, world, brief, plant_id, spec)

    def _gate_checks_plant(
        self, ctx: Any, world: Path, plant_id: str, spec: dict[str, Any]
    ) -> None:
        """Every plant gate's pass, or the ship stops (#184.4, #188.3)."""
        job_dir = ctx.job_dir
        problems = []
        problems.extend(_gate_spec_plant(plant_id, spec))
        problems.extend(_gate_render_plant(job_dir, plant_id))
        problems.extend(_gate_finnish_plant(job_dir, plant_id))
        overlay: Path | None = None
        if not problems:
            try:
                overlay = self._overlay_world(ctx, world, plant_id, spec)
            except ConvertError as error:
                problems.append(str(error))
        if overlay is not None and not problems:
            verdict = check_draft(overlay, job_dir, "ship-world")
            for entry in verdict.get("results", []) or []:
                if not isinstance(entry, dict) or entry.get("status") != "fail":
                    continue
                real, _unbuilt = split_not_built(entry.get("problems", []) or [])
                for line in real:
                    problems.append(f"world {entry.get('check')}: {line}")
        checks = job_dir / "checks"
        checks.mkdir(parents=True, exist_ok=True)
        (checks / "ship.json").write_text(
            json.dumps(
                {"status": "pass" if not problems else "fail", "problems": problems},
                indent=2,
            ),
            encoding="utf-8",
        )
        if problems or overlay is None:
            raise _Stopped("; ".join(problems) if problems else "no laid-over world")

    def _overlay_world(
        self, ctx: Any, world: Path, plant_id: str, spec: dict[str, Any]
    ) -> Path:
        """The checkout with the built model, for the checks (#188.3).

        Check-only scaffolding, never shipped: the world's own
        checks run against the new files as the game will see
        them. The pack itself carries only the model.
        """
        overlay = ctx.job_dir / "ship-world"
        if overlay.exists():
            shutil.rmtree(overlay)
        shutil.copytree(world, overlay, ignore=shutil.ignore_patterns(".git"))
        greens = overlay / "models" / "plants"
        greens.mkdir(parents=True, exist_ok=True)
        for name in (f"{plant_id}.json", f"{plant_id}.glb"):
            src = ctx.job_dir / "models" / "plants" / name
            if src.is_file():
                shutil.copy(src, greens / name)
        png = ctx.job_dir / "models" / "plants" / f"{plant_id}.png"
        if png.is_file():
            previews = overlay / "models" / "previews" / "plants"
            previews.mkdir(parents=True, exist_ok=True)
            shutil.copy(png, previews / f"{plant_id}.png")
        return overlay

    def _pack_plant(
        self,
        ctx: Any,
        world: Path,
        brief: dict[str, Any],
        plant_id: str,
        spec: dict[str, Any],
    ) -> dict[str, Any]:
        """The plant pack, game-checked before it uploads (#188.3).

        The spec, its built model and preview, and its own
        locale keys: no additions, no place files. Same shape as
        an animal pack's result, so the worker publishes the job
        alike.
        """
        # The pack id is the item's kind and id (spec 02 §8b), not
        # the enrich item id: `plant_table_coral`, never
        # `enrich_table_coral` (#188.3).
        item_id = f"plant_{plant_id}"
        greens = ctx.job_dir / "models" / "plants"
        files: dict[str, bytes] = {}
        files[f"models/plants/{plant_id}.json"] = (
            greens / f"{plant_id}.json"
        ).read_bytes()
        model = greens / f"{plant_id}.glb"
        preview = greens / f"{plant_id}.png"
        if not model.is_file():
            raise _Stopped(f"{plant_id}: no built model to pack")
        if not preview.is_file():
            raise _Stopped(f"{plant_id}: no model preview to pack")
        files[f"models/plants/{plant_id}.glb"] = model.read_bytes()
        files[f"models/previews/plants/{plant_id}.png"] = preview.read_bytes()
        for language in ("en", "fi"):
            path = ctx.job_dir / "locale" / language / "plants.json"
            files[f"locale/{language}/plants.json"] = path.read_bytes()
        version = (
            next_pack_version(self._store, item_id) if self._store is not None else 1
        )
        pack = assemble_enrich_pack(item_id, version, files)
        problems = check_pack(pack)
        if problems:
            raise _Stopped(f"pack: {problems[0]}")
        dest = ctx.job_dir / "pack"
        if dest.exists():
            shutil.rmtree(dest)
        for rel, data in pack.items():
            path = dest / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        prefix: str | None = None
        if self._store is not None:
            prefix = publish_pack(self._store, item_id, version, pack)
        return {"pack": prefix, "version": version, "files": sorted(pack)}

    def _pack(
        self,
        ctx: Any,
        world: Path,
        brief: dict[str, Any],
        animal_id: str,
        animal: dict[str, Any],
    ) -> dict[str, Any]:
        """The pack, game-checked before it uploads (#176.1, #176.3)."""
        # The pack id is the item's kind and id (spec 02 §8b), not
        # the enrich item id: `animal_hammerhead`, never
        # `enrich_hammerhead` (#188.3).
        item_id = f"animal_{animal_id}"
        files: dict[str, bytes] = {}
        files[f"animals/{animal_id}.json"] = (
            ctx.job_dir / "animals" / f"{animal_id}.json"
        ).read_bytes()
        model = ctx.job_dir / "models" / f"{animal_id}.glb"
        preview = ctx.job_dir / "models" / f"{animal_id}.png"
        if not model.is_file():
            raise _Stopped(f"{animal_id}: no built model to pack")
        if not preview.is_file():
            raise _Stopped(f"{animal_id}: no model preview to pack")
        files[f"models/{animal_id}.glb"] = model.read_bytes()
        files[f"models/previews/{animal_id}.png"] = preview.read_bytes()
        for language in ("en", "fi"):
            path = ctx.job_dir / "locale" / language / "animals.json"
            files[f"locale/{language}/animals.json"] = path.read_bytes()
        version = (
            next_pack_version(self._store, item_id) if self._store is not None else 1
        )
        pack = assemble_enrich_pack(item_id, version, files)
        problems = check_pack(pack)
        if problems:
            raise _Stopped(f"pack: {problems[0]}")
        dest = ctx.job_dir / "pack"
        if dest.exists():
            shutil.rmtree(dest)
        for rel, data in pack.items():
            path = dest / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        prefix: str | None = None
        if self._store is not None:
            prefix = publish_pack(self._store, item_id, version, pack)
        return {"pack": prefix, "version": version, "files": sorted(pack)}


def _gate_spec_plant(plant_id: str, spec: dict[str, Any]) -> list[str]:
    """The spec ships what the game packs: parts and colors (#188.3)."""
    if not isinstance(spec, dict):
        return [f"spec: {plant_id} is no object"]
    problems = []
    if not isinstance(spec.get("parts"), list) or not spec["parts"]:
        problems.append(f"spec: {plant_id} has no parts to build")
    if not isinstance(spec.get("colors"), dict) or not spec["colors"]:
        problems.append(f"spec: {plant_id} has no colors to paint")
    return problems


#: How small the file's length may run against the researched
#: typical adult; above, the researched largest adult bounds it,
#: doubled for small species a child must see (#197.1).
LENGTH_MINIMUM = 0.80

#: Typical adults below this length read at twice their size,
#: so small fish stay visible (#197.1).
SMALL_SPECIES_CM = 20


def gate_checks(
    ctx: Any, world: Path, brief: dict[str, Any], animal_id: str, animal: dict[str, Any]
) -> list[str]:
    """Every gate's problems, or nothing when the item ships (#174.1).

    The world's own checks, the render review, the Finnish
    texts and the file's own gates. (Safety runs as its own
    step before this, and stops the ship itself when it fails.)
    """
    problems = []
    problems.extend(_gate_world(ctx, world))
    problems.extend(_gate_render(ctx.job_dir, brief, animal_id))
    problems.extend(_gate_finnish(ctx.job_dir, animal_id))
    research_path = ctx.job_dir / "research.json"
    if not research_path.is_file():
        return [*problems, "research: no research to check the file against"]
    research = json.loads(research_path.read_text(encoding="utf-8"))
    tiles = (brief.get("enrich") or {}).get("tiles") or []
    problems.extend(_gate_file(animal, research, tiles, world))
    return problems


def _gate_render_plant(job_dir: Path, plant_id: str) -> list[str]:
    """The final plant review passes: it reads as its species (#184.4)."""
    reviews = sorted(
        (job_dir / "reviews").glob(f"{plant_id}-*.json"),
        key=lambda p: (
            int(p.stem.rsplit("-", 1)[-1]) if p.stem.rsplit("-", 1)[-1].isdigit() else 0
        ),
    )
    if not reviews:
        return [f"render: no render review for {plant_id}"]
    final = json.loads(reviews[-1].read_text(encoding="utf-8"))
    blocking = [
        issue
        for issue in final.get("issues", [])
        if isinstance(issue, dict) and issue.get("severity") == "blocking"
    ]
    if blocking:
        first = blocking[0]
        return [
            f"render: the review blocks: {first.get('part')}: {first.get('problem')}"
        ]
    if not (job_dir / "models" / "plants" / f"{plant_id}.png").is_file():
        return [f"render: no preview to ship for {plant_id}"]
    return []


def _gate_finnish_plant(job_dir: Path, plant_id: str) -> list[str]:
    """Every English plant key has its Finnish (#184.4)."""
    problems = []
    texts: dict[str, dict[str, str]] = {}
    for language in ("en", "fi"):
        path = job_dir / "locale" / language / "plants.json"
        if not path.is_file():
            problems.append(f"finnish: no locale/{language}/plants.json to ship")
            continue
        loaded = json.loads(path.read_text(encoding="utf-8"))
        texts[language] = loaded if isinstance(loaded, dict) else {}
    if problems:
        return problems
    missing = sorted(k for k in texts["en"] if k not in texts["fi"])
    if missing:
        problems.append(f"finnish: the Finnish texts leave out {missing}")
    for key in list(texts["en"]) + [k for k in texts["fi"] if k not in texts["en"]]:
        if not key.startswith(f"plant.{plant_id}."):
            problems.append(f"finnish: {key} belongs to no shipped plant")
    return problems


def _gate_world(ctx: Any, world: Path) -> list[str]:
    """The world's own checks, not-built models unknown (#174.1)."""
    verdict = check_draft(world, ctx.job_dir, "ship-world")
    problems = []
    for entry in verdict.get("results", []) or []:
        if not isinstance(entry, dict) or entry.get("status") != "fail":
            continue
        real, _unbuilt = split_not_built(entry.get("problems", []) or [])
        for line in real:
            problems.append(f"world {entry.get('check')}: {line}")
    return problems


def _research_marks(research: dict[str, Any]) -> list[str]:
    """The research's key mark words, in order (#197.4)."""
    marks = (research.get("looks", {}) or {}).get("marks", []) or []
    return [
        mark["mark"]
        for mark in marks
        if isinstance(mark, dict) and isinstance(mark.get("mark"), str)
    ]


def _ship_look(job_dir: Path) -> dict[str, Any] | None:
    """The ship's own look at the final preview, or nothing (#197.4)."""
    try:
        review = json.loads(
            (job_dir / "checks" / "marks_ship.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    return review if isinstance(review, dict) else None


def _gate_render(job_dir: Path, brief: dict[str, Any], animal_id: str) -> list[str]:
    """The research-named marks show, and it reads as its species.

    An adapted model's marks review names what shows; the marks are
    what make the species known, so none missing is the species
    reading too. A new shape's final model review carries the same
    verdict in its issues. The ship's own look at the final
    preview must agree with the edit round's: when the two looks
    disagree it fails with both (#197.4).
    """
    if brief.get("in_library"):
        return []
    try:
        research = json.loads((job_dir / "research.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        research = {}
    owed = bool(_research_marks(research))
    ship = _ship_look(job_dir)
    ship_missing = (
        [m for m in (ship.get("missing", []) if ship else []) if isinstance(m, str)]
        if ship and not ship.get("skipped")
        else None
    )
    edits = job_dir / "tries" / animal_id / "edits"
    marks = sorted(edits.glob("*/marks_review.json")) if edits.is_dir() else []
    if marks:
        last = max(marks, key=lambda p: int(p.parent.name or 0))
        try:
            review = json.loads(last.read_text(encoding="utf-8"))
        except ValueError:
            return [f"render: {last.parent.name} wrote no marks review"]
        missing = [m for m in review.get("missing", []) if isinstance(m, str)]
        problems = []
        if ship_missing is None:
            if owed:
                problems.append("render: the ship never looked at the final preview")
            elif missing:
                problems.append(f"render: {', '.join(missing)} missing from the views")
        else:
            if ship_missing:
                problems.append(
                    f"render: the final look misses: {', '.join(ship_missing)}"
                )
            if set(missing) != set(ship_missing):
                edit = ", ".join(missing) if missing else "none"
                final = ", ".join(ship_missing) if ship_missing else "none"
                problems.append(
                    "render: the looks disagree "
                    f"(edit round missing: {edit}; final look missing: {final})"
                )
        try:
            unmakable = json.loads(
                (job_dir / "checks" / "oneshot.json").read_text(encoding="utf-8")
            ).get("unmakable", {})
        except (OSError, ValueError):
            unmakable = {}
        left = unmakable.get(animal_id) or []
        if left:
            problems.append(f"render: unmakable marks: {', '.join(left)}")
        return problems
    rounds = [
        p
        for p in (job_dir / "reviews").glob(f"{animal_id}-*.json")
        if (job_dir / "reviews").is_dir() and not p.name.endswith("-unverified.json")
    ]
    if rounds:
        last = max(rounds, key=lambda p: int(p.stem.rsplit("-", 1)[-1]))
        try:
            review = json.loads(last.read_text(encoding="utf-8"))
        except ValueError:
            return ["render: the final review wrote no JSON"]
        if ship_missing is None:
            if owed:
                return ["render: the ship never looked at the final preview"]
        elif ship_missing:
            return [f"render: the final look misses: {', '.join(ship_missing)}"]
        blocking = [
            i
            for i in review.get("issues", [])
            if isinstance(i, dict) and i.get("severity") == "blocking"
        ]
        if blocking:
            first = blocking[0].get("problem", "blocking issue")
            return [f"render: the final review still blocks: {first}"]
        return []
    return ["render: no render review ran"]


def _gate_finnish(job_dir: Path, animal_id: str) -> list[str]:
    """Every English key has Finnish, nothing invented (#174.2).

    Presence and key shape here; the world's language check reads
    the translations themselves when the copy job files the pack.
    A Finnish `map_name` with no English key is the world's rule,
    not an error: the English map name is the file's `name` (#197.2).
    """
    problems = []
    try:
        en = json.loads(
            (job_dir / "locale" / "en" / "animals.json").read_text(encoding="utf-8")
        )
        fi = json.loads(
            (job_dir / "locale" / "fi" / "animals.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return ["finnish: no locale files to check"]
    for key, text in en.items():
        if not key.startswith(f"animal.{animal_id}."):
            problems.append(f"finnish: {key} is not this animal's key")
        value = fi.get(key)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"finnish: {key} has no Finnish")
        elif value == text:
            problems.append(f"finnish: {key} is still English")
    for key in fi:
        if key not in en and not key.endswith(".map_name"):
            problems.append(f"finnish: {key} is not in the English")
    return problems


def _gate_file(
    animal: dict[str, Any],
    research: dict[str, Any],
    tiles: list[str],
    world: Path,
) -> list[str]:
    """The file's own gates: length, year, hunts and camera (#174.1).

    What the reef review taught, without the reef: the length
    bounds (#197.1), the winter and migration dates against the
    research, reviewed prey tags only, and a camera outside the
    body. Migration seasons check against the target tiles'
    own latitudes.
    """
    model = animal.get("model", {}) or {}
    length = model.get("length")
    size = research.get("size", {}) or {}
    typical_cm = size.get("length_cm")
    largest_cm = size.get("largest_cm")
    problems = []
    if not isinstance(typical_cm, (int, float)) or typical_cm <= 0:
        problems.append("length: the research names no typical adult length")
    elif not isinstance(largest_cm, (int, float)) or largest_cm <= 0:
        problems.append("length: the research names no largest adult length")
    elif not isinstance(length, (int, float)) or length <= 0:
        problems.append("length: the file names no length")
    else:
        top = largest_cm
        if typical_cm < SMALL_SPECIES_CM:
            top = max(top, 2 * typical_cm)
        length_cm = length * 100
        if length_cm < LENGTH_MINIMUM * typical_cm:
            problems.append(
                f"length: {length} m is below 0.8 x typical adult "
                f"({typical_cm} cm researched)"
            )
        elif length_cm > top:
            problems.append(
                f"length: {length} m is past the largest adult ({top:g} cm researched)"
            )
    problems.extend(_gate_year(animal, research, tiles))
    hunts = animal.get("hunts", []) or []
    allowed = _world_hunts(world)
    for hunt in hunts:
        if hunt not in allowed:
            problems.append(f"hunts: {hunt} is no reviewed prey tag")
    cam_dist = animal.get("cam_dist")
    if (
        isinstance(cam_dist, (int, float))
        and isinstance(length, (int, float))
        and cam_dist < length
    ):
        problems.append(
            f"camera: cam_dist {cam_dist} sits inside the body ({length} m)"
        )
    return problems


def _gate_year(
    animal: dict[str, Any], research: dict[str, Any], tiles: list[str]
) -> list[str]:
    """The file's winter follows the research, in season (#174.1)."""
    file_year = animal.get("year", {}) or {}
    found_year = research.get("year", {}) or {}
    file_winter = file_year.get("winter")
    found_winter = found_year.get("winter")
    if file_winter != found_winter:
        return [f"year: the file says {file_winter}, the research says {found_winter}"]
    if file_winter != "migrates":
        return []
    file_dates = file_year.get("migration", {}) or {}
    found_dates = found_year.get("migration", {}) or {}
    if file_dates.get("leaves") != found_dates.get("leaves") or file_dates.get(
        "returns"
    ) != found_dates.get("returns"):
        return ["year: the migration dates are not the researched ones"]
    leaves = _month(file_dates.get("leaves"))
    returns = _month(file_dates.get("returns"))
    if leaves is None or returns is None:
        return ["year: the migration dates are no MM-DD dates"]
    problems = []
    for tile in tiles:
        try:
            lat, _lon = center(tile)
        except ValueError:
            continue
        if abs(lat) < 10:
            continue
        south = lat < 0
        autumn = range(2, 6) if south else range(8, 12)
        spring = range(8, 12) if south else range(2, 6)
        hemi = "southern" if south else "northern"
        if leaves not in autumn:
            problems.append(
                f"year: {tile} leaves in month {leaves}, no {hemi} autumn departure"
            )
        if returns not in spring:
            problems.append(
                f"year: {tile} returns in month {returns}, no {hemi} spring return"
            )
    return problems


def _month(day: Any) -> int | None:
    if not isinstance(day, str):
        return None
    try:
        month, _rest = day.split("-", 1)
        value = int(month)
    except ValueError:
        return None
    return value if 1 <= value <= 12 else None


def _world_hunts(world: Path) -> set[str]:
    """Every tag a shipped animal hunts: the reviewed prey tags."""
    return _hunt_tags(world)
