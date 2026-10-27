"""Stage 2h, home: one animal's home from its template (animal_home jobs).

Reads intake's `home.json`; makes the region with the world's
`homes.py`; seats the animal plus its GBIF-recorded neighbours;
researches names for food kinds the template lacks (one short
call, 5 searches at most); and writes `species-plan.json` for the
later stages. New animals wait for one-shot models (B): until
then they stop as not possible yet.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..animal_list import short_name
from ..meta import count_search_calls, is_cut_off
from ..prompts import load_prompt
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
from .survey import SEARCH_TOOL, response_text

HOMES_TIMEOUT = 60
RESEARCH_SEARCHES = 5


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
        brief = json.loads((ctx.job_dir / "home.json").read_text(encoding="utf-8"))
        taxa = file_taxa(world)
        stem = brief.get("in_library")
        if stem is None:
            raise NotPossibleError(
                f"{brief['latin']} needs its model built one-shot next"
            )
        data = animal_file(world, stem)
        if not data:
            raise ValueError(f"the library lost its file for {stem}")
        if data.get("stage_of"):
            stem = data["stage_of"]
            data = animal_file(world, stem)
        place_id = _place_id(brief["cell"], stem, brief.get("country", ""))
        region = self._make_region(world, ctx, brief, place_id)
        neighbours, unchecked = self._seat_cast(world, region, brief, taxa, stem)
        template_sources = list(region.get("sources", []))
        added_kinds, added_young = self._feed_cast(
            world, ctx, region, brief, stem, neighbours
        )
        region["sources"] = _home_sources(region, template_sources)
        region["welcome"] = _welcome(world, region, brief, stem)
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

    def _make_region(
        self,
        world: Path,
        ctx: Any,
        brief: dict[str, Any],
        place_id: str,
    ) -> dict[str, Any]:
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

    def _seat_cast(
        self,
        world: Path,
        region: dict[str, Any],
        brief: dict[str, Any],
        taxa: dict[str, dict[str, Any]],
        stem: str,
    ) -> tuple[set[str], set[str]]:
        """The animal plus every recorded library neighbour, in place."""
        main = animal_file(world, stem)
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
            data = animal_file(world, candidate)
            if data.get("stage_of"):
                continue
            role = backfill_role(data, hunts)
            if role is None or role == "prey":
                deferred.append(candidate)
                continue
            if self._recorded_here(
                world, center, candidate, taxa, role, placed, unchecked
            ):
                hunts |= set(data.get("hunts", []) or [])
        for candidate in deferred:
            data = animal_file(world, candidate)
            role = backfill_role(data, hunts)
            if role is None:
                continue
            self._recorded_here(world, center, candidate, taxa, role, placed, unchecked)
        main_role = backfill_role(main, hunts) or "playable"
        placed[stem] = {"backfilled": True, "file": stem, "role": main_role}
        SpeciesPlanStage.place_backfilled(world, region, placed)
        neighbours = set(placed) - {stem}
        return neighbours, unchecked

    def _recorded_here(
        self,
        world: Path,
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
        ctx: Any,
        region: dict[str, Any],
        brief: dict[str, Any],
        stem: str,
        neighbours: set[str],
    ) -> tuple[set[str], set[str]]:
        """Food for every cast diet kind, named by one short call."""
        before = {food.get("kind", "") for food in region.get("food", [])}
        added_young = SpeciesPlanStage().heal_cast(world, region)
        kinds = sorted(
            {food.get("kind", "") for food in region.get("food", [])} - before
        )
        if not kinds:
            return set(), added_young
        foods, sources = self._research_foods(ctx, brief, kinds)
        by_kind = {food.get("kind"): food for food in region.get("food", [])}
        for kind in kinds:
            names = foods.get(kind, [])
            if not names:
                raise _Stopped(f"the research named no food for {kind}")
            by_kind[kind]["names"] = names
        seen = set(region.get("sources", []))
        region["sources"] = region.get("sources", []) + [
            url for url in sources if url not in seen
        ]
        return set(kinds), added_young

    def _research_foods(
        self, ctx: Any, brief: dict[str, Any], kinds: list[str]
    ) -> tuple[dict[str, list[str]], list[str]]:
        ctx.check_budget()
        prompt = load_prompt(
            "home_research",
            {
                "LATIN": brief["latin"],
                "LAT": str(brief["center"]["lat"]),
                "LON": str(brief["center"]["lon"]),
                "KINDS": "\n".join(f"- {kind}" for kind in kinds),
            },
        )
        response = ctx.respond(prompt, tools=SEARCH_TOOL, timeout=180)
        if count_search_calls(response) > RESEARCH_SEARCHES + 5:
            raise _Stopped("the food research searched far past its round")
        if is_cut_off(response, 0) and not response_text(response).strip():
            raise _Stopped("the food research came back empty")
        try:
            data = json.loads(response_text(response))
            validate("home-research", data)
        except ValueError as error:
            raise _Stopped(f"the food research came back unparsable: {error}")
        return data["foods"], data["sources"]

    def _write_plan(
        self,
        ctx: Any,
        world: Path,
        stem: str,
        neighbours: set[str],
        brief: dict[str, Any],
    ) -> dict[str, Any]:
        taxa = file_taxa(world)
        species = [
            {
                "species": stem,
                "decision": "reuse",
                "reason": "the home's animal, reused from the library",
                "wild": True,
                "taxon": taxa[stem],
            }
        ]
        for other in sorted(neighbours):
            species.append(
                {
                    "species": other,
                    "decision": "reuse",
                    "reason": "a GBIF-recorded neighbour, reused from the library",
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


def _home_sources(region: dict[str, Any], template_sources: list[str]) -> list[str]:
    """The research's sources plus GBIF.org for the neighbours.

    The template's own pages (its survey ground) do not travel
    with the home (#47).
    """
    research = [url for url in region.get("sources", []) if url not in template_sources]
    seen = set()
    ordered = []
    for url in [*research, "GBIF.org"]:
        if url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered


def _welcome(
    world: Path, region: dict[str, Any], brief: dict[str, Any], stem: str
) -> str:
    """The first-session hello, from the animal and its main food."""
    animal = short_name(brief.get("name", "")).lower() or stem.replace("_", " ")
    food = _main_food(world, region, stem)
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


def _main_food(world: Path, region: dict[str, Any], stem: str) -> str:
    """The first named food in the animal's diet order."""
    data = animal_file(world, stem)
    by_kind: dict[str, list[str]] = {}
    for item in region.get("food", []):
        by_kind.setdefault(item.get("kind", ""), item.get("names", []) or [])
    for kind in data.get("diet") or []:
        if by_kind.get(kind):
            return by_kind[kind][0]
    for item in region.get("food", []):
        if item.get("names"):
            return item["names"][0]
    return "food"


def _place_id(cell: str, stem: str, country: str) -> str:
    prefix = country.lower() if len(country) == 2 else "xx"
    if not prefix.isalpha():
        prefix = "xx"
    safe = cell.replace(".", "").replace("-", "m")
    return f"{prefix}_home_{safe}_{stem}"
