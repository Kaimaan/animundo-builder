"""Stage 2h, home: one animal's home from its template (animal_home jobs).

Reads intake's `home.json` and the research stage's
`research.json`; takes the template built (`homes.py`) or made
in this job (`homes/`); seats the animal plus its GBIF-recorded
neighbours (the job's own animals join without asking GBIF);
names food kinds the template lacks from the research; and
writes `species-plan.json` for the later stages. Free: the one
model call happened in research.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..animal_list import find_list, load_list, match_by_name, short_name
from ..schemas import validate
from .capability import NotPossibleError
from .species_plan import (
    RecordedFn,
    SpeciesPlanStage,
    animal_file,
    backfill_role,
    file_taxa,
    recorded_near,
)

HOMES_TIMEOUT = 60


class _Stopped(Exception):
    """The home cannot be made from what came back."""


class HomeStage:
    """Make `regions/<place_id>.json` and `species-plan.json` for a home."""

    name = "home"

    def __init__(self, recorded: RecordedFn | None = None) -> None:
        self._recorded = recorded or recorded_near

    def run(self, ctx: Any) -> dict[str, Any]:
        if ctx.world_path is None:
            raise ValueError("home needs --world")
        world = Path(ctx.world_path)
        overlay = ctx.job_dir
        brief = json.loads((ctx.job_dir / "home.json").read_text(encoding="utf-8"))
        research = json.loads(
            (ctx.job_dir / "research.json").read_text(encoding="utf-8")
        )
        taxa = file_taxa(world, overlay)
        stem = brief.get("in_library") or self._made_stem(ctx, brief)
        if stem is None:
            raise NotPossibleError(
                f"{brief['latin']} is made one-shot next; "
                "the maker lands after this review"
            )
        data = animal_file(world, stem, overlay)
        if not data:
            raise ValueError(f"the library lost its file for {stem}")
        if data.get("stage_of"):
            stem = data["stage_of"]
            data = animal_file(world, stem, overlay)
        place_id = _place_id(brief["cell"], stem, brief.get("country", ""))
        region = self._make_region(world, ctx, brief, place_id)
        neighbours, unchecked = self._seat_cast(
            world, overlay, region, brief, taxa, stem
        )
        added_kinds, added_young = self._feed_cast(
            world, overlay, region, brief, research, stem
        )
        region["sources"] = _home_sources(research)
        region["welcome"] = _welcome(world, overlay, region, brief, research, stem)
        region_path = ctx.job_dir / "regions" / f"{place_id}.json"
        region_path.write_text(json.dumps(region, indent=2), encoding="utf-8")
        brief["place_id"] = place_id
        brief["stem"] = stem
        brief["added_young"] = sorted(added_young)
        (ctx.job_dir / "home.json").write_text(
            json.dumps(brief, indent=2), encoding="utf-8"
        )
        self._write_plan(ctx, world, stem, neighbours, brief)
        return {
            "region_file": f"regions/{place_id}.json",
            "place_id": place_id,
            "animal": stem,
            "neighbours": sorted(neighbours),
            "backfill_unchecked": sorted(unchecked),
            "foods_added": sorted(added_kinds),
        }

    def _made_stem(self, ctx: Any, brief: dict[str, Any]) -> str | None:
        """The job's own file for the animal, if the maker wrote it."""
        animal_id = brief.get("id", "")
        if animal_id and (ctx.job_dir / "animals" / f"{animal_id}.json").is_file():
            return animal_id
        return None

    def _make_region(
        self,
        world: Path,
        ctx: Any,
        brief: dict[str, Any],
        place_id: str,
    ) -> dict[str, Any]:
        made = ctx.job_dir / "homes" / f"{brief['home']}.json"
        if made.is_file():
            return self._copy_template(made, ctx, brief, place_id)
        tool = world / "tools" / "homes.py"
        if not tool.is_file():
            raise _Stopped(f"no home templates in this checkout: {tool}")
        out = ctx.job_dir / "regions" / f"{place_id}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(tool),
                    "make",
                    brief["home"],
                    "--id",
                    place_id,
                    "--name",
                    brief["place_name"],
                    "--country",
                    brief["country_name"] or brief["country"],
                    "--lat",
                    str(brief["center"]["lat"]),
                    "--lon",
                    str(brief["center"]["lon"]),
                    "--out",
                    str(out),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=HOMES_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise _Stopped(f"homes.py did not run: {error}")
        if proc.returncode != 0 or not out.is_file():
            raise _Stopped(f"homes.py failed: {proc.stderr.strip()[-300:]}")
        try:
            return json.loads(out.read_text(encoding="utf-8"))
        except ValueError as error:
            raise _Stopped(f"homes.py wrote no region: {error}")

    def _copy_template(
        self,
        made: Path,
        ctx: Any,
        brief: dict[str, Any],
        place_id: str,
    ) -> dict[str, Any]:
        """The job's own template, filled like `homes.py make` fills one."""
        (ctx.job_dir / "regions").mkdir(parents=True, exist_ok=True)
        try:
            region = json.loads(made.read_text(encoding="utf-8"))
        except ValueError as error:
            raise _Stopped(f"the made template is no region: {error}")
        region["id"] = place_id
        region["name"] = brief["place_name"]
        region["country"] = brief["country_name"] or brief["country"]
        region["center"] = brief["center"]
        return region

    def _seat_cast(
        self,
        world: Path,
        overlay: Path,
        region: dict[str, Any],
        brief: dict[str, Any],
        taxa: dict[str, dict[str, Any]],
        stem: str,
    ) -> tuple[set[str], set[str]]:
        """The animal plus every recorded neighbour, in place.

        The job's own animals join without asking GBIF: they were
        made for this home.
        """
        main = animal_file(world, stem, overlay)
        made = set(file_taxa(overlay))
        hunts = set(main.get("hunts", []) or [])
        center = brief["center"]
        placed: dict[str, dict[str, Any]] = {}
        unchecked: set[str] = set()
        deferred: list[str] = []
        for candidate in sorted(taxa):
            if (
                candidate == stem
                or taxa[candidate].get("gbif_key") == brief["species_key"]
            ):
                continue
            data = animal_file(world, candidate, overlay)
            if data.get("stage_of"):
                continue
            role = backfill_role(data, hunts)
            if role is None or role == "prey":
                deferred.append(candidate)
                continue
            if candidate in made:
                placed[candidate] = {
                    "backfilled": True,
                    "file": candidate,
                    "role": role,
                }
                hunts |= set(data.get("hunts", []) or [])
            elif self._recorded_here(center, candidate, taxa, role, placed, unchecked):
                hunts |= set(data.get("hunts", []) or [])
        for candidate in deferred:
            data = animal_file(world, candidate, overlay)
            role = backfill_role(data, hunts)
            if role is None:
                continue
            if candidate in made:
                placed[candidate] = {
                    "backfilled": True,
                    "file": candidate,
                    "role": role,
                }
            else:
                self._recorded_here(center, candidate, taxa, role, placed, unchecked)
        main_role = backfill_role(main, hunts) or "playable"
        placed[stem] = {"backfilled": True, "file": stem, "role": main_role}
        SpeciesPlanStage.place_backfilled(world, region, placed, overlay)
        neighbours = set(placed) - {stem}
        return neighbours, unchecked

    def _recorded_here(
        self,
        center: dict[str, float],
        stem: str,
        taxa: dict[str, dict[str, Any]],
        role: str,
        placed: dict[str, dict[str, Any]],
        unchecked: set[str],
    ) -> bool:
        try:
            recorded = self._recorded(
                center["lat"], center["lon"], taxa[stem]["gbif_key"]
            )
        except Exception:
            unchecked.add(stem)
            return False
        if not recorded:
            return False
        placed[stem] = {"backfilled": True, "file": stem, "role": role}
        return True

    def _feed_cast(
        self,
        world: Path,
        overlay: Path,
        region: dict[str, Any],
        brief: dict[str, Any],
        research: dict[str, Any],
        stem: str,
    ) -> tuple[set[str], set[str]]:
        """Food for every cast diet kind, named from the research."""
        before = {food.get("kind", "") for food in region.get("food", [])}
        added_young = SpeciesPlanStage().heal_cast(world, region, overlay)
        kinds = sorted(
            {food.get("kind", "") for food in region.get("food", [])} - before
        )
        if not kinds:
            return set(), added_young
        names = _researched_names(research)
        by_kind = {food.get("kind"): food for food in region.get("food", [])}
        for kind in kinds:
            if kind not in names:
                raise _Stopped(
                    f"the research named no food for {kind}; a person must decide"
                )
            by_kind[kind]["names"] = names[kind]
        return set(kinds), added_young

    def _write_plan(
        self,
        ctx: Any,
        world: Path,
        stem: str,
        neighbours: set[str],
        brief: dict[str, Any],
    ) -> dict[str, Any]:
        overlay = ctx.job_dir
        taxa = file_taxa(world, overlay)
        made = set(file_taxa(overlay))
        species = [
            {
                "species": stem,
                "decision": "new" if stem in made else "reuse",
                "reason": (
                    "made one-shot for this home"
                    if stem in made
                    else "the home's animal, reused from the library"
                ),
                "wild": True,
                "taxon": taxa[stem],
            }
        ]
        for other in sorted(neighbours):
            if other in made:
                decision = "new"
                reason = "made one-shot for this home"
            else:
                decision = "reuse"
                reason = "a GBIF-recorded neighbour, reused from the library"
            species.append(
                {
                    "species": other,
                    "decision": decision,
                    "reason": reason,
                    "wild": True,
                    "taxon": taxa[other],
                }
            )
        plan = {
            "species": species,
            "food_web_ok": True,
            "missing_food": [],
            "added_young": sorted(brief.get("added_young", [])),
        }
        validate("species-plan", plan)
        (ctx.job_dir / "species-plan.json").write_text(
            json.dumps(plan, indent=2), encoding="utf-8"
        )
        return plan


def _researched_names(research: dict[str, Any]) -> dict[str, list[str]]:
    """Food names per kind, from the main food and the surroundings."""
    names: dict[str, list[str]] = {}
    foods = [research["main_food"], *research.get("other_foods", [])]
    for food in foods:
        if food["kind"] != "prey":
            names.setdefault(food["kind"], []).append(food["name"])
    for thing in research.get("surroundings", []):
        if thing.get("food_kind"):
            names.setdefault(thing["food_kind"], []).append(thing["name"])
    return names


def _home_sources(research: dict[str, Any]) -> list[str]:
    """The research's sources plus GBIF.org for the neighbours.

    The template's own pages (its survey ground) do not travel
    with the home (#47).
    """
    seen = set()
    ordered = []
    for url in [*research.get("sources", []), "GBIF.org"]:
        if url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered


def _welcome(
    world: Path,
    overlay: Path,
    region: dict[str, Any],
    brief: dict[str, Any],
    research: dict[str, Any],
    stem: str,
) -> str:
    """The first-session hello, from the animal and its main food."""
    animal = short_name(brief.get("name", "")).lower() or stem.replace("_", " ")
    food = _main_food(world, overlay, region, research, stem)
    if food.endswith("s"):
        article = "some"
    elif food[:1].lower() in "aeiou":
        article = "an"
    else:
        article = "a"
    return (
        "This is a new world, and not many animals live here yet. "
        f"Play as the {animal}, and add more animals later. "
        f"Go and find {article} {food} to eat!"
    )


def _main_food(
    world: Path,
    overlay: Path,
    region: dict[str, Any],
    research: dict[str, Any],
    stem: str,
) -> str:
    """The research's main food, never carrion, always findable.

    A hunter's prey only when it sits in this cast; otherwise the
    first named food in diet order that is not carrion (#48.4).
    """
    cast = _cast_files(region)
    entries = load_list(find_list(world))["animals"]
    for food in [research["main_food"], *research.get("other_foods", [])]:
        if food["kind"] == "carrion":
            continue
        if food["kind"] == "prey" and not _prey_seated(entries, cast, food["name"]):
            continue
        return food["name"]
    data = animal_file(world, stem, overlay)
    by_kind: dict[str, list[str]] = {}
    for item in region.get("food", []):
        if item.get("kind", "") == "carrion":
            continue
        by_kind.setdefault(item.get("kind", ""), item.get("names", []) or [])
    for kind in data.get("diet") or []:
        if by_kind.get(kind):
            return by_kind[kind][0]
    for item in region.get("food", []):
        if item.get("kind", "") != "carrion" and item.get("names"):
            return item["names"][0]
    return "food"


def _cast_files(region: dict[str, Any]) -> set[str]:
    """Every animal file stem seated in the region."""
    files = set()
    for group in region.get("animals", {}).values():
        files.update(group or [])
    for fish in region.get("fish", []) or []:
        files.add(fish.get("file", ""))
    return files - {""}


def _prey_seated(entries: list[dict[str, Any]], cast: set[str], name: str) -> bool:
    """Whether the named prey sits in this cast, via the list."""
    match = match_by_name(entries, name)
    if match is None:
        return False
    return (match.get("in_library") or match.get("id", "")) in cast


def _place_id(cell: str, stem: str, country: str) -> str:
    prefix = country.lower() if len(country) == 2 else "xx"
    if not prefix.isalpha():
        prefix = "xx"
    safe = cell.replace(".", "").replace("-", "m")
    return f"{prefix}_home_{safe}_{stem}"
