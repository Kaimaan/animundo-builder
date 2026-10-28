"""Stage 2h, home: one animal's home from its template (animal_home jobs).

Reads intake's `home.json` and the research stage's
`research.json`; takes the template built (`homes.py`) or made
in this job (`homes/`); seats the animal plus the neighbours one
GBIF facet search records in the cell (the job's own animals join
without asking GBIF), kept only if their taxon is sound and the
home has their food, at most eight; names food kinds the template
lacks from the research; and writes `species-plan.json` for the
later stages. Free: the one model call happened in research.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..animal_list import find_list, load_list, short_name
from ..schemas import validate
from ..taxa import match_entry, taxon_block
from .capability import NotPossibleError
from .species_plan import (
    TAXON_TRIES,
    SpeciesPlanStage,
    animal_file,
    available_food,
    backfill_role,
    file_taxa,
)

HOMES_TIMEOUT = 60

#: At most eight neighbours, so a place stays light (#82.4).
MAX_NEIGHBOURS = 8

#: Facet buckets per search: plenty past the cap and the gates.
FACET_LIMIT = 100

#: The cell's facet counts: GBIF species keys to sightings.
FacetsFn = Callable[[str], dict[int, int]]


def facet_counts(cell: str) -> dict[int, int]:
    """Species keys recorded in the cell, with counts: one GBIF call.

    A facet search over the cell's 0.1-degree box: real sightings,
    not a range guess. Transient failures are retried; a stable
    failure raises, and the home seats no neighbours visibly (they
    land in `backfill_unchecked`, never silently).
    """
    south, west = (float(part) for part in cell.split("_"))
    box = (
        f"POLYGON(({west} {south}, {west + 0.1} {south}, "
        f"{west + 0.1} {south + 0.1}, {west} {south + 0.1}, {west} {south}))"
    )
    query = urllib.parse.urlencode(
        {
            "geometry": box,
            "facet": "speciesKey",
            "facetLimit": FACET_LIMIT,
            "limit": 0,
        }
    )
    last_error = ""
    for attempt in range(1, TAXON_TRIES + 1):
        try:
            url = f"https://api.gbif.org/v1/occurrence/search?{query}"
            with urllib.request.urlopen(url, timeout=20) as response:
                return _parse_facets(json.load(response))
        except (OSError, ValueError) as error:
            last_error = str(error)
            time.sleep(2.0**attempt)
    raise _Stopped(f"could not read GBIF facets for cell {cell} ({last_error})")


def _parse_facets(payload: dict[str, Any]) -> dict[int, int]:
    """Species-key counts from one facet search answer."""
    counts: dict[int, int] = {}
    for facet in payload.get("facets", []):
        if facet.get("field") != "SPECIES_KEY":
            continue
        for bucket in facet.get("counts", []):
            try:
                key = int(bucket["name"])
            except (TypeError, ValueError):
                continue
            counts[key] = counts.get(key, 0) + int(bucket.get("count", 0))
    return counts


def _taxon_sound(data: dict[str, Any], taxon: dict[str, Any]) -> bool:
    """The check_taxa per-file rules: a real key and a matching name."""
    return isinstance(taxon.get("gbif_key"), int) and (
        taxon.get("name") == data.get("latin")
    )


class _Stopped(Exception):
    """The home cannot be made from what came back."""


class HomeStage:
    """Make `regions/<place_id>.json` and `species-plan.json` for a home."""

    name = "home"

    def __init__(
        self, facets: FacetsFn | None = None, maker: Any | None = None
    ) -> None:
        self._facets = facets or facet_counts
        self._maker = maker

    def run(self, ctx: Any) -> dict[str, Any]:
        if ctx.world_path is None:
            raise ValueError("home needs --world")
        world = Path(ctx.world_path)
        overlay = ctx.job_dir
        brief = json.loads((ctx.job_dir / "home.json").read_text(encoding="utf-8"))
        if brief.get("reuse_place_id"):
            # A built place keeps its home: there is no template to
            # fix and no region to build (#82.6).
            place = brief["reuse_place_id"]
            return {
                "place_id": place,
                "cost_usd": 0.0,
                "improved": [],
                "summary": (
                    f"{place} is a built place; its home can't be changed here"
                ),
            }
        research = json.loads(
            (ctx.job_dir / "research.json").read_text(encoding="utf-8")
        )
        improve_path = ctx.job_dir / "improve.json"
        improve: dict[str, Any] = (
            json.loads(improve_path.read_text(encoding="utf-8"))
            if improve_path.is_file()
            else {}
        )
        choices = {entry.get("choice") for entry in improve.get("choices", [])}
        before = ctx.tracker.spent_usd
        summary: str | None = None
        improved: list[str] = []
        if "colours_off" in choices:
            fixed, summary = self._fix_colours(ctx, world, brief, research, improve)
            if fixed:
                improved = ["home"]
        taxa = file_taxa(world, overlay)
        minset = _read_minset(ctx)
        stem = brief.get("in_library") or self._made_stem(ctx, brief)
        if stem is None and minset.get("make_animal"):
            stem = brief.get("id") or None
        if stem is None:
            raise NotPossibleError(
                f"{brief['latin']} is made one-shot next; run minimum first"
            )
        data = animal_file(world, stem, overlay)
        will_make = minset.get("make_animal") and stem == brief.get("id")
        if not data and not will_make:
            raise ValueError(f"the library lost its file for {stem}")
        if data.get("stage_of"):
            stem = data["stage_of"]
            data = animal_file(world, stem, overlay)
        place_id = _place_id(brief["cell"], stem, brief.get("country", ""))
        region = self._make_region(world, ctx, brief, place_id)
        if abs(float(brief["center"]["lat"])) < 23.0:
            # The game's tropics branch needs no seasons config
            # (season.gd reads the latitude, not a key); a template
            # seasons block would give the equator autumns (#84.4).
            region.pop("seasons", None)
        if "too_few_animals" in choices:
            cap = MAX_NEIGHBOURS + 4
        else:
            cap = MAX_NEIGHBOURS
        neighbours, unchecked = self._seat_cast(
            world, overlay, region, brief, taxa, stem, cap
        )
        added_kinds, added_young = self._feed_cast(
            world, overlay, region, brief, research, stem
        )
        region["sources"] = _home_sources(research)
        region["welcome"] = _welcome(
            world, overlay, region, brief, research, stem, minset.get("prey")
        )
        region_path = ctx.job_dir / "regions" / f"{place_id}.json"
        region_path.write_text(json.dumps(region, indent=2), encoding="utf-8")
        brief["place_id"] = place_id
        brief["stem"] = stem
        brief["added_young"] = sorted(added_young)
        (ctx.job_dir / "home.json").write_text(
            json.dumps(brief, indent=2), encoding="utf-8"
        )
        self._write_plan(ctx, world, stem, neighbours, brief)
        result: dict[str, Any] = {
            "region_file": f"regions/{place_id}.json",
            "place_id": place_id,
            "animal": stem,
            "neighbours": sorted(neighbours),
            "backfill_unchecked": sorted(unchecked),
            "foods_added": sorted(added_kinds),
            "cost_usd": round(ctx.tracker.spent_usd - before, 6),
        }
        if improve:
            result["improved"] = improved
            if summary:
                result["summary"] = summary
        return result

    def _fix_colours(
        self,
        ctx: Any,
        world: Path,
        brief: dict[str, Any],
        research: dict[str, Any],
        improve: dict[str, Any],
    ) -> tuple[bool, str]:
        """A D8 choices fix on the made template, or why there is none."""
        habitat = brief["home"]
        if not (ctx.job_dir / "homes" / f"{habitat}.json").is_file():
            return False, (
                f"the {habitat} home is from the library and can't be changed here"
            )
        # Deferred: the maker already imports this module.
        from .maker import HomeMakerStage

        maker = self._maker or HomeMakerStage()
        maker.run_fix(
            ctx,
            world,
            brief,
            research,
            improve.get("note"),
            improve.get("round", 2),
        )
        return True, "fixed the home's colours"

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
                    _region_name(brief),
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
        # A region is not a home: drop the builders' fields, as
        # the world's `homes.py make` does with HOME_ONLY (#66.2).
        for key in ("based_on", "about"):
            region.pop(key, None)
        region["id"] = place_id
        region["name"] = _region_name(brief)
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
        cap: int = MAX_NEIGHBOURS,
    ) -> tuple[set[str], set[str]]:
        """The animal plus recorded neighbours the home can feed, in place.

        One facet search over the cell, matched to the files'
        gbif_keys; a neighbour stays only if its taxon is sound
        (the check_taxa rules) and the home has food for its diet.
        The job's own animals join without asking GBIF: they were
        made for this home. At most `cap` join, most-recorded
        first; the picked animal stays first in playable.
        """
        main = animal_file(world, stem, overlay)
        made = set(file_taxa(overlay))
        hunts = set(main.get("hunts", []) or [])
        if not main and brief.get("start_from"):
            # No file yet: seat roles against what the one-shot
            # writer starts from, not an empty set.
            proxy = animal_file(world, brief["start_from"], overlay)
            hunts = set(proxy.get("hunts", []) or [])
        try:
            counts = self._facets(brief["cell"])
        except Exception:
            counts = {}
            failed = True
        else:
            failed = False
        ranked, pool = self._ranked(world, overlay, taxa, brief, stem, made, counts)
        kinds = available_food(region)
        seen: set[int | None] = set()
        selected: list[str] = []
        for candidate in ranked:
            if len(selected) >= cap:
                break
            data = animal_file(world, candidate, overlay)
            if candidate not in made and not _taxon_sound(data, taxa[candidate]):
                continue
            key = taxa[candidate].get("gbif_key")
            if key in seen:
                continue
            if candidate not in made and not set(data.get("diet") or []) <= kinds:
                continue
            selected.append(candidate)
            seen.add(key)
        placed: dict[str, dict[str, Any]] = {}
        deferred: list[str] = []
        for candidate in selected:
            data = animal_file(world, candidate, overlay)
            role = backfill_role(data, hunts)
            if role is None or role == "prey":
                deferred.append(candidate)
                continue
            placed[candidate] = {
                "backfilled": True,
                "file": candidate,
                "role": role,
            }
            hunts |= set(data.get("hunts", []) or [])
        for candidate in deferred:
            data = animal_file(world, candidate, overlay)
            role = backfill_role(data, hunts)
            if role is None:
                continue
            placed[candidate] = {
                "backfilled": True,
                "file": candidate,
                "role": role,
            }
        main_role = backfill_role(main, hunts) or "playable"
        placed[stem] = {"backfilled": True, "file": stem, "role": main_role}
        SpeciesPlanStage.place_backfilled(world, region, placed, overlay)
        if not main:
            # No file yet: the maker writes it next, but the cast
            # seats the animal now, as the one to play.
            playable = region.setdefault("animals", {}).setdefault("playable", [])
            if stem not in playable:
                playable.append(stem)
        playable = region.setdefault("animals", {}).setdefault("playable", [])
        if stem in playable:
            playable.remove(stem)
            playable.insert(0, stem)
        neighbours = set(placed) - {stem}
        unchecked = set(pool) if failed else set()
        return neighbours, unchecked

    @staticmethod
    def _rank_key(item: tuple[int | None, str]) -> tuple[int, int, str]:
        count, candidate = item
        if count is None:
            return (0, 0, candidate)
        return (1, -count, candidate)

    def _ranked(
        self,
        world: Path,
        overlay: Path,
        taxa: dict[str, dict[str, Any]],
        brief: dict[str, Any],
        stem: str,
        made: set[str],
        counts: dict[int, int],
    ) -> tuple[list[str], list[str]]:
        """Candidates, most-recorded first; the job's own first of all.

        Returns the ranked stems plus the library pool, so a failed
        search can mark every unasked candidate unchecked.
        """
        ranked: list[tuple[int | None, str]] = []
        pool: list[str] = []
        for candidate in taxa:
            if (
                candidate == stem
                or taxa[candidate].get("gbif_key") == brief["species_key"]
            ):
                continue
            if animal_file(world, candidate, overlay).get("stage_of"):
                continue
            if candidate in made:
                ranked.append((None, candidate))
                continue
            pool.append(candidate)
            key = taxa[candidate].get("gbif_key")
            if key in counts:
                ranked.append((counts[key], candidate))
        ranked.sort(key=self._rank_key)
        return [candidate for _, candidate in ranked], pool

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
        names = researched_names(research)
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
        minset = _read_minset(ctx)
        new = stem in made or (minset.get("make_animal") and stem == brief.get("id"))
        taxon = taxa.get(stem) or taxon_block(brief["species_key"], brief["latin"])
        species = [
            {
                "species": stem,
                "decision": "new" if new else "reuse",
                "reason": (
                    "made one-shot for this home"
                    if new
                    else "the home's animal, reused from the library"
                ),
                "wild": True,
                "taxon": taxon,
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


def researched_names(research: dict[str, Any]) -> dict[str, list[str]]:
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


def refresh_welcome(
    world: Path,
    overlay: Path,
    region: dict[str, Any],
    brief: dict[str, Any],
    research: dict[str, Any],
    stem: str,
) -> str:
    """The welcome from the seated cast, once the set is complete (#84.3).

    The first welcome names what the minset promises; this one names
    what is really there, so it can't go stale.
    """
    return _welcome(world, overlay, region, brief, research, stem, None)


def _welcome(
    world: Path,
    overlay: Path,
    region: dict[str, Any],
    brief: dict[str, Any],
    research: dict[str, Any],
    stem: str,
    minset_prey: dict[str, Any] | None,
) -> str:
    """The first-session hello, from the animal and its main food."""
    animal = short_name(brief.get("name", "")).lower() or stem.replace("_", " ")
    food = _main_food(world, overlay, region, research, stem, minset_prey)
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
    minset_prey: dict[str, Any] | None,
) -> str:
    """The research's main food, never carrion, always findable.

    A hunter's prey only when it sits in this cast, or the minset
    says it will be made in this build (#73.1); otherwise the
    first named food in diet order that is not carrion (#48.4).
    """
    cast = _cast_files(region)
    entries = load_list(find_list(world))["animals"]
    for food in [research["main_food"], *research.get("other_foods", [])]:
        if food["kind"] == "carrion":
            continue
        if food["kind"] == "prey" and not _prey_seated(
            world, entries, cast, food, minset_prey
        ):
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
        # Fish swim under `kind`, like the regions and the game (#85.2).
        files.add(fish.get("kind", ""))
    return files - {""}


def _prey_seated(
    world: Path,
    entries: list[dict[str, Any]],
    cast: set[str],
    food: dict[str, Any],
    minset_prey: dict[str, Any] | None,
) -> bool:
    """Whether the named prey sits in this cast, via the list.

    A prey the minset says will be made counts as seated: the
    welcome names it, and the maker seats it later (#73.1).
    """
    match, _ = match_entry(entries, world, food)
    if match is None:
        return False
    if (match.get("in_library") or match.get("id", "")) in cast:
        return True
    if not minset_prey:
        return False
    key = minset_prey.get("gbif_key")
    if isinstance(key, int) and key == match.get("gbif_key"):
        return True
    want = minset_prey.get("name", "").strip().lower()
    return want != "" and want == food["name"].strip().lower()


def _read_minset(ctx: Any) -> dict[str, Any]:
    """The minimum set, or {} when home runs before minimum."""
    path = ctx.job_dir / "minset.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _region_name(brief: dict[str, Any]) -> str:
    """The place name without the country (#64.4).

    Old briefs carry "Place, Country"; the game adds the
    country itself, so it would show twice.
    """
    name = brief.get("place_name", "")
    for country in (brief.get("country_name") or "", brief.get("country") or ""):
        if country and name.endswith(f", {country}"):
            return name[: -len(country) - 2]
    return name


def _place_id(cell: str, stem: str, country: str) -> str:
    prefix = country.lower() if len(country) == 2 else "xx"
    if not prefix.isalpha():
        prefix = "xx"
    safe = cell.replace(".", "").replace("-", "m")
    return f"{prefix}_home_{safe}_{stem}"
