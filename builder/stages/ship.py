"""Stage 10, ship: an enrich item's checks, Finnish, and its pack.

An enrich item reaches the game with no hand steps (#174, #176):
the stage writes its Finnish, runs the child-safety review, gates
every check the reef review taught, and publishes the pack to
Storage. A failed check stops the item with the check and its
message, in the report and the progress entry, like any stage.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..enrich import region_additions_only, split_not_built
from ..packs import (
    assemble_enrich_pack,
    check_additions,
    check_pack,
    next_pack_version,
    publish_pack,
)
from ..tiles import place_cells
from ..world import check_draft
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
        if brief.get("species_key") is None:
            raise _Stopped("ship packs animals; this item ships by hand")
        animal_id = str(brief["id"])
        animal_path = ctx.job_dir / "animals" / f"{animal_id}.json"
        if not animal_path.is_file():
            raise _Stopped(f"{animal_id}: the maker left no animal file")
        animal = json.loads(animal_path.read_text(encoding="utf-8"))
        self._finnish(ctx, world, animal_id, animal)
        SafetyStage().run(ctx)
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
        host when set, else Muse Spark).
        """
        strings = animal.get("text")
        if not isinstance(strings, dict) or not strings:
            raise _Stopped(f"{animal_id}: the animal file carries no text")
        for key, text in strings.items():
            if not isinstance(text, str):
                raise _Stopped(f"{animal_id}: text.{key} is no string")
        translated = WordsStage()._translate(ctx, world, "fi", strings)
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

    def _pack(
        self,
        ctx: Any,
        world: Path,
        brief: dict[str, Any],
        animal_id: str,
        animal: dict[str, Any],
    ) -> dict[str, Any]:
        """The pack, game-checked before it uploads (#176.1, #176.3)."""
        item_id = str(ctx.job_id).split("/")[-1]
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
        regions = _laid_regions(ctx.job_dir, world, brief)
        if not regions:
            raise _Stopped("no hand-made place among the job's tiles")
        for region_id, region in regions:
            additions = _additions(region, animal_id, animal)
            refused = check_additions(additions)
            if refused:
                raise _Stopped(f"additions/{region_id}.json: {refused[0]}")
            files[f"additions/{region_id}.json"] = json.dumps(
                additions, indent=2
            ).encode("utf-8")
        version = (
            next_pack_version(self._store, item_id) if self._store is not None else 1
        )
        pack = assemble_enrich_pack(item_id, version, files)
        problems = check_pack(pack)
        if problems:
            raise _Stopped(f"pack: {problems[0]}")
        dest = ctx.job_dir / "pack"
        if dest.exists():
            import shutil

            shutil.rmtree(dest)
        for rel, data in pack.items():
            path = dest / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        prefix: str | None = None
        if self._store is not None:
            prefix = publish_pack(self._store, item_id, version, pack)
        return {"pack": prefix, "version": version, "files": sorted(pack)}


def _laid_regions(
    job_dir: Path, world: Path, brief: dict[str, Any]
) -> list[tuple[str, dict[str, Any]]]:
    """The hand-made places among the job's tiles, as laid (#176.1)."""
    tiles = (brief.get("enrich") or {}).get("tiles") or []
    cells = place_cells(world)
    region_ids = []
    for tile in tiles:
        hit = cells.get(tile)
        if hit is not None and hit[0] not in region_ids:
            region_ids.append(hit[0])
    regions = []
    for region_id in region_ids:
        path = job_dir / "regions" / f"{region_id}.json"
        if not path.is_file():
            raise _Stopped(f"{region_id}: no laid place to add to")
        regions.append((region_id, json.loads(path.read_text(encoding="utf-8"))))
    return regions


def _additions(
    region: dict[str, Any], animal_id: str, animal: dict[str, Any]
) -> dict[str, Any]:
    """The place's additions file: top-level lists only (#179.1).

    A fish rides its `fish` seat with its count; every other animal
    only joins the `playable` / `predators` / `prey` lists where the
    laid place seats it. Any other key refuses the whole pack, so
    there are no others, ever.
    """
    groups = region.get("animals", {}) or {}
    additions: dict[str, Any] = {"format": 1}
    if (animal.get("model") or {}).get("archetype") == "fish":
        count = 0
        for entry in region.get("fish", []) or []:
            if isinstance(entry, dict) and entry.get("kind") == animal_id:
                count = int(entry.get("count", 0) or 0)
        additions["fish"] = [{"kind": animal_id, "count": count}]
    for group in ("playable", "predators", "prey"):
        seated = groups.get(group, []) or []
        if animal_id in seated:
            additions[group] = [animal_id]
    return additions


#: How far the file's length may stray from the researched typical
#: adult before the gate calls it a record, not an adult (#174.1).
LENGTH_TOLERANCE = 0.20


def gate_checks(
    ctx: Any, world: Path, brief: dict[str, Any], animal_id: str, animal: dict[str, Any]
) -> list[str]:
    """Every gate's problems, or nothing when the item ships (#174.1).

    The world's own checks, the render review, the reef file rules
    and the additions-only region change. (Safety runs as its own
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
    regions = _laid_regions(ctx.job_dir, world, brief)
    if not regions:
        return [*problems, "region: no hand-made place among the job's tiles"]
    for region_id, region in regions:
        problems.extend(_gate_reef(animal, research, region_id, region, world))
        problems.extend(_gate_region(world, region_id, region, animal_id))
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


def _gate_render(job_dir: Path, brief: dict[str, Any], animal_id: str) -> list[str]:
    """The research-named marks show, and it reads as its species.

    An adapted model's marks review names what shows; the marks are
    what make the species known, so none missing is the species
    reading too. A new shape's final model review carries the same
    verdict in its issues.
    """
    if brief.get("in_library"):
        return []
    edits = job_dir / "tries" / animal_id / "edits"
    marks = sorted(edits.glob("*/marks_review.json")) if edits.is_dir() else []
    if marks:
        last = max(marks, key=lambda p: int(p.parent.name or 0))
        try:
            review = json.loads(last.read_text(encoding="utf-8"))
        except ValueError:
            return [f"render: {last.parent.name} wrote no marks review"]
        missing = [m for m in review.get("missing", []) if isinstance(m, str)]
        if missing:
            return [f"render: {', '.join(missing)} missing from the views"]
        try:
            unmakable = json.loads(
                (job_dir / "checks" / "oneshot.json").read_text(encoding="utf-8")
            ).get("unmakable", {})
        except (OSError, ValueError):
            unmakable = {}
        left = unmakable.get(animal_id) or []
        if left:
            return [f"render: unmakable marks: {', '.join(left)}"]
        return []
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
        if key not in en:
            problems.append(f"finnish: {key} is not in the English")
    return problems


def _gate_reef(
    animal: dict[str, Any],
    research: dict[str, Any],
    region_id: str,
    region: dict[str, Any],
    world: Path,
) -> list[str]:
    """The rules the reef review taught (#174.1, #173.4)."""
    model = animal.get("model", {}) or {}
    length = model.get("length")
    size = research.get("size", {}) or {}
    typical_cm = size.get("length_cm")
    problems = []
    if not isinstance(typical_cm, (int, float)) or typical_cm <= 0:
        problems.append("length: the research names no typical adult length")
    elif not isinstance(length, (int, float)) or length <= 0:
        problems.append("length: the file names no length")
    elif abs(length * 100 - typical_cm) / typical_cm > LENGTH_TOLERANCE:
        problems.append(
            f"length: {length} m is no typical adult ({typical_cm} cm researched)"
        )
    problems.extend(_gate_year(animal, research, region))
    hunts = animal.get("hunts", []) or []
    allowed = _world_hunts(world)
    for hunt in hunts:
        if hunt not in allowed:
            problems.append(f"hunts: {hunt} is no reviewed prey tag")
    tags = animal.get("tags", []) or []
    if model.get("archetype") == "fish" and "small_fish" not in tags:
        problems.extend(_gate_fish_count(animal, region_id, region, world))
    cam_dist = animal.get("cam_dist")
    if (
        isinstance(cam_dist, (int, float))
        and isinstance(length, (int, float))
        and cam_dist < length
    ):
        problems.append(
            f"camera: cam_dist {cam_dist} sits inside the body ({length} m)"
        )
    sea_depth = (region.get("terrain", {}) or {}).get("sea_depth")
    cam_height = animal.get("cam_height")
    if (
        isinstance(cam_height, (int, float))
        and isinstance(sea_depth, (int, float))
        and cam_height >= sea_depth
    ):
        problems.append(
            f"camera: cam_height {cam_height} is above the water ({sea_depth} m)"
        )
    return [f"{region_id}: {line}" for line in problems]


def _gate_year(
    animal: dict[str, Any], research: dict[str, Any], region: dict[str, Any]
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
    lat = (region.get("center", {}) or {}).get("lat")
    if not isinstance(lat, (int, float)) or abs(lat) < 10:
        return []
    leaves = _month(file_dates.get("leaves"))
    returns = _month(file_dates.get("returns"))
    if leaves is None or returns is None:
        return ["year: the migration dates are no MM-DD dates"]
    south = lat < 0
    autumn = range(2, 6) if south else range(8, 12)
    spring = range(8, 12) if south else range(2, 6)
    hemi = "southern" if south else "northern"
    if leaves not in autumn:
        return [f"year: it leaves in month {leaves}, no {hemi} autumn departure"]
    if returns not in spring:
        return [f"year: it returns in month {returns}, no {hemi} spring return"]
    return []


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
    hunts: set[str] = set()
    for path in sorted((world / "animals").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for hunt in data.get("hunts", []) or []:
            hunts.add(hunt)
    return hunts


def _gate_fish_count(
    animal: dict[str, Any],
    region_id: str,
    region: dict[str, Any],
    world: Path,
) -> list[str]:
    """A big fish is fewer than the place's small fish (#174.1)."""
    wanted = animal.get("id", "")
    mine: int | None = None
    smalls: list[int] = []
    for entry in region.get("fish", []) or []:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind")
        count = entry.get("count")
        if kind == wanted and isinstance(count, (int, float)):
            mine = int(count)
            continue
        if isinstance(kind, str) and _is_small_fish(world, kind):
            if isinstance(count, (int, float)):
                smalls.append(int(count))
    if mine is None:
        return [f"fish: {wanted} has no seat in the region's fish"]
    if smalls and mine >= min(smalls):
        return [f"fish: count {mine} is no fewer than the small fish ({min(smalls)})"]
    return []


def _is_small_fish(world: Path, kind: str) -> bool:
    try:
        data = json.loads((world / "animals" / f"{kind}.json").read_text())
    except (OSError, ValueError):
        return False
    return "small_fish" in (data.get("tags", []) or [])


def _gate_region(
    world: Path, region_id: str, region: dict[str, Any], animal_id: str
) -> list[str]:
    """The region change only adds, on a built-in place (#174.1)."""
    path = world / "regions" / f"{region_id}.json"
    if not path.is_file():
        return [f"region {region_id}: no such built-in place"]
    try:
        before = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return [f"region {region_id}: the built-in file won't read"]
    return [
        f"region {region_id}: {line}"
        for line in region_additions_only(before, region, animal_id)
    ]
