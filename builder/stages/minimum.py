"""Stage 2m, minimum: the set this home needs, decided in code.

A home is one animal in its home plus its food (#42): the
animal itself when the library lacks it, its home when no
template is built, and one prey when it hunts and no library
prey shares its habitat (the smallest-bodied prey the
research names that can be made). A place or enrich item
never makes prey; when none of its food is in the place the
report says `no_food` (#175.2). Free: no model calls.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..animal_list import find_entry, find_list, load_list
from ..taxa import match_entry
from .species_plan import animal_file


class MinimumStage:
    """Decide the set; write `minset.json`."""

    name = "minimum"

    def run(self, ctx: Any) -> dict[str, Any]:
        if ctx.world_path is None:
            raise ValueError("minimum needs --world")
        world = Path(ctx.world_path)
        brief = json.loads((ctx.job_dir / "home.json").read_text(encoding="utf-8"))
        research = json.loads(
            (ctx.job_dir / "research.json").read_text(encoding="utf-8")
        )
        data = load_list(find_list(world))
        if brief.get("species_key"):
            entry = find_entry(data["animals"], brief["species_key"])
            make_animal, animal_reason = self._need_animal(ctx, brief, entry)
            if brief.get("region_id") is not None or brief.get("enrich") is not None:
                # A place or enrich item never makes prey: food is
                # worked out from what is there (decision 0043).
                prey, prey_reason = None, _never_prey_reason(brief)
                food, food_reason = self._place_food(ctx, brief, research)
            else:
                prey, prey_reason = self._need_prey(world, data, brief, entry, research)
                food, food_reason = None, ""
        else:
            # A home item carries no species: only the home is made.
            make_animal, animal_reason = False, "this item makes no animal"
            prey, prey_reason = None, "this item makes no animal"
            food, food_reason = None, ""
        make_home, home_reason = self._need_home(ctx, brief)
        minset = {
            "make_animal": make_animal,
            "animal_reason": animal_reason,
            "make_home": make_home,
            "home_reason": home_reason,
            "home_habitat": brief["home"],
            "prey": prey,
            "prey_reason": prey_reason,
            "food": food,
            "food_reason": food_reason,
        }
        (ctx.job_dir / "minset.json").write_text(
            json.dumps(minset, indent=2), encoding="utf-8"
        )
        # When a home must be made the pipeline runs newhome next
        # itself (#109.3); stopping here stranded every build whose
        # home didn't exist yet.
        return {"minset_file": "minset.json", "make": _making(minset)}

    def _need_animal(
        self, ctx: Any, brief: dict[str, Any], entry: dict[str, Any]
    ) -> tuple[bool, str]:
        stem = brief.get("in_library")
        if stem:
            return False, f"{stem} is in the library"
        made = ctx.job_dir / "animals" / f"{entry.get('id', 'animal')}.json"
        if made.is_file():
            return False, f"{made.name} is already made in this job"
        return True, f"{brief['latin']} is not in the library yet"

    def _need_home(self, ctx: Any, brief: dict[str, Any]) -> tuple[bool, str]:
        if brief.get("reuse_place_id"):
            return False, f"{brief['reuse_place_id']} is built"
        if not brief.get("needs_home"):
            return False, f"{brief['home']} is built"
        home_id = brief.get("home_id") or brief["home"]
        made = ctx.job_dir / "homes" / f"{home_id}.json"
        if made.is_file():
            return False, f"{home_id} is already made in this job"
        return True, f"no {home_id} template is built yet"

    def _need_prey(
        self,
        world: Path,
        data: dict[str, Any],
        brief: dict[str, Any],
        entry: dict[str, Any],
        research: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        hunts = self._hunts(world, brief, research)
        if not hunts:
            return None, f"{brief['latin']} does not hunt"
        if self._library_prey(world, data, brief, entry, hunts):
            return None, "a library prey shares its habitat"
        candidates, skipped = self._named_prey(data, research, world)
        if not candidates:
            reason = "no named prey can be made yet"
            if skipped:
                reason += f" ({'; '.join(skipped)})"
            return None, reason
        name, size_cm, key, reason = candidates[0]
        prey: dict[str, Any] = {"name": name, "size_cm": size_cm}
        if key is not None:
            prey["gbif_key"] = key
        return prey, reason

    def _hunts(
        self, world: Path, brief: dict[str, Any], research: dict[str, Any]
    ) -> set[str]:
        """The tags it hunts: from its file, else from its main food."""
        stem = brief.get("in_library")
        if stem:
            data = animal_file(world, stem)
            if data.get("stage_of"):
                data = animal_file(world, data["stage_of"])
            return set(data.get("hunts", []) or [])
        if research["main_food"]["kind"] == "prey":
            return {"prey"}
        return set()

    def _library_prey(
        self,
        world: Path,
        data: dict[str, Any],
        brief: dict[str, Any],
        entry: dict[str, Any],
        hunts: set[str],
    ) -> bool:
        """Whether library prey already shares one of its habitats.

        A known hunter needs a library animal carrying a tag it
        hunts; a new hunter (generic `{"prey"}` hunts, which no
        file hunts) needs any library animal that hunts nothing.
        Only the home being built counts: a frog in a Finnish
        wetland feeds no Amazon anaconda.
        """
        habitats = {brief["home"]}
        for other in data["animals"]:
            if other is entry or not other.get("in_library"):
                continue
            if not habitats & set(other.get("habitats", [])):
                continue
            prey = animal_file(world, other["in_library"])
            if not prey:
                continue
            if hunts == {"prey"}:
                if not prey.get("hunts"):
                    return True
            elif set(prey.get("tags", []) or []) & hunts:
                return True
        return False

    def _named_prey(
        self,
        data: dict[str, Any],
        research: dict[str, Any],
        world: Path,
    ) -> tuple[list[tuple[str, float, int | None, str]], list[str]]:
        """Named prey that can be made, smallest first, and why not."""
        foods = [research["main_food"], *research["other_foods"]]
        found = []
        skipped = []
        for food in foods:
            if food["kind"] != "prey" or food.get("size_cm") is None:
                continue
            match, key = match_entry(data["animals"], world, food)
            if match is None:
                skipped.append(f"{food['name']} is not on the animal list")
                continue
            if match.get("in_library"):
                skipped.append(f"{food['name']} is already in the library")
                continue
            if not match.get("can_make", False):
                skipped.append(f"{food['name']} cannot be made yet")
                continue
            found.append(
                (
                    food["name"],
                    float(food["size_cm"]),
                    key,
                    f"smallest-bodied named prey that can be made "
                    f"({food['size_cm']} cm)",
                )
            )
        found.sort(key=lambda item: item[1])
        return found, skipped

    def _place_food(
        self, ctx: Any, brief: dict[str, Any], research: dict[str, Any]
    ) -> tuple[str | None, str]:
        """Whether its food is in the place, by name (#175.2).

        The item's laid regions (the scaffold, or an enrich
        item's converter places) carry the place's food names,
        fish and cast. A food counts when its name matches one
        of those; when none does, the report says `no_food` and
        the item goes on. With no regions laid there is nothing
        to judge against, so no verdict.
        """
        regions = sorted((ctx.job_dir / "regions").glob("*.json"))
        if not regions:
            return None, "no place was laid to check food against"
        have: set[str] = set()
        for path in regions:
            try:
                region = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(region, dict):
                continue
            for entry in region.get("food", []) or []:
                if not isinstance(entry, dict):
                    continue
                for name in entry.get("names", []) or []:
                    if isinstance(name, str) and name:
                        have.add(_slug(name))
            for fish in region.get("fish", []) or []:
                if isinstance(fish, dict) and fish.get("kind"):
                    have.add(_slug(str(fish["kind"])))
            animals = region.get("animals", {}) or {}
            if isinstance(animals, dict):
                for group in ("playable", "prey", "predators"):
                    for animal_id in animals.get(group, []) or []:
                        if isinstance(animal_id, str) and animal_id:
                            have.add(_slug(animal_id))
        foods = [research["main_food"], *research["other_foods"]]
        names = [
            str(food["name"])
            for food in foods
            if isinstance(food, dict) and food.get("name")
        ]
        matched = [name for name in names if _slug(name) in have]
        if brief.get("region_id") is not None:
            where = f"the {brief['home']}"
        else:
            where = "its tiles"
        if matched:
            return "in_place", f"{matched[0]} is in {where}"
        first = names[0] if names else "its food"
        return "no_food", f"{first} is not in {where}"


def _never_prey_reason(brief: dict[str, Any]) -> str:
    """Why no prey: a place or enrich item never makes one (#175.2)."""
    if brief.get("enrich") is not None:
        return "an enrich item never makes prey"
    return "a place item never makes prey"


def _slug(name: str) -> str:
    """A food name as an id, for matching against the place."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _making(minset: dict[str, Any]) -> list[str]:
    making = []
    if minset["make_animal"]:
        making.append("animal")
    if minset["make_home"]:
        making.append("home")
    if minset["prey"] is not None:
        making.append("prey")
    return making
