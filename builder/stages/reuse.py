"""Stage `reuse`: a built place gets the picked animal.

When the place exists no home is made: the job copies the place's
region from the world, seats the picked animal as playable, feeds
it from the research, and writes the species plan with the picked
animal (reuse for a library animal, new for one the one-shot
maker is about to make). Free: no model calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..schemas import validate
from ..taxa import taxon_block
from .home import researched_names
from .maker import seat_made
from .species_plan import SpeciesPlanStage, file_taxa


class _Stopped(Exception):
    """The reuse cannot be done as the research came back."""


class ReuseStage:
    """Copy a built region and seat the picked animal (#82.3)."""

    name = "reuse"

    def run(self, ctx: Any) -> dict[str, Any]:
        if ctx.world_path is None:
            raise ValueError("reuse needs --world")
        world = Path(ctx.world_path)
        brief = json.loads((ctx.job_dir / "home.json").read_text(encoding="utf-8"))
        place = brief.get("reuse_place_id")
        if not place:
            raise ValueError("reuse needs a place: draft")
        research = json.loads(
            (ctx.job_dir / "research.json").read_text(encoding="utf-8")
        )
        minset = json.loads((ctx.job_dir / "minset.json").read_text(encoding="utf-8"))
        region_path = ctx.job_dir / "regions" / f"{place}.json"
        region_path.parent.mkdir(parents=True, exist_ok=True)
        region = json.loads(
            (world / "regions" / f"{place}.json").read_text(encoding="utf-8")
        )
        stem = brief.get("in_library") or brief["id"]
        seat_made(world, ctx.job_dir, region, region_path, stem, is_prey=False)
        added = self._feed(world, ctx.job_dir, region, research)
        region_path.write_text(json.dumps(region, indent=2), encoding="utf-8")
        self._write_plan(ctx, world, brief, minset, stem)
        return {
            "region_file": f"regions/{place}.json",
            "place_id": place,
            "food_added": sorted(added),
        }

    def _feed(
        self,
        world: Path,
        overlay: Path,
        region: dict[str, Any],
        research: dict[str, Any],
    ) -> set[str]:
        """Food for the picked animal's diet kinds, named from the research."""
        before = {food.get("kind", "") for food in region.get("food", [])}
        SpeciesPlanStage().heal_cast(world, region, overlay)
        kinds = sorted(
            {food.get("kind", "") for food in region.get("food", [])} - before
        )
        if not kinds:
            return set()
        names = researched_names(research)
        by_kind = {food.get("kind"): food for food in region.get("food", [])}
        for kind in kinds:
            if kind not in names:
                raise _Stopped(
                    f"the research named no food for {kind}; a person must decide"
                )
            by_kind[kind]["names"] = names[kind]
        return set(kinds)

    def _write_plan(
        self,
        ctx: Any,
        world: Path,
        brief: dict[str, Any],
        minset: dict[str, Any],
        stem: str,
    ) -> dict[str, Any]:
        taxa = file_taxa(world, ctx.job_dir)
        made = set(file_taxa(ctx.job_dir))
        new = stem in made or (minset.get("make_animal") and stem == brief.get("id"))
        taxon = taxa.get(stem) or taxon_block(brief["species_key"], brief["latin"])
        species = [
            {
                "species": stem,
                "decision": "new" if new else "reuse",
                "reason": (
                    "made one-shot for this place"
                    if new
                    else "the picked animal, reused from the library"
                ),
                "wild": True,
                "taxon": taxon,
            }
        ]
        plan = {
            "species": species,
            "food_web_ok": True,
            "missing_food": [],
            "added_young": [],
        }
        validate("species-plan", plan)
        (ctx.job_dir / "species-plan.json").write_text(
            json.dumps(plan, indent=2), encoding="utf-8"
        )
        return plan
