"""Stage 2m, minimum: the set this home needs, decided in code.

A home is one animal in its home plus its food (#42): the
animal itself when the library lacks it, its home when no
template is built, and one prey when it hunts and no library
prey shares its habitat (the smallest-bodied prey the
research names that can be made). Free: no model calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..animal_list import find_entry, find_list, load_list, match_by_name
from .capability import NotPossibleError
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
        entry = find_entry(data["animals"], brief["species_key"])
        make_animal, animal_reason = self._need_animal(ctx, brief, entry)
        make_home, home_reason = self._need_home(ctx, brief)
        prey, prey_reason = self._need_prey(world, data, brief, entry, research)
        minset = {
            "make_animal": make_animal,
            "animal_reason": animal_reason,
            "make_home": make_home,
            "home_reason": home_reason,
            "home_habitat": brief["home"],
            "prey": prey,
            "prey_reason": prey_reason,
        }
        (ctx.job_dir / "minset.json").write_text(
            json.dumps(minset, indent=2), encoding="utf-8"
        )
        result = {"minset_file": "minset.json", "make": _making(minset)}
        if make_animal:
            raise NotPossibleError(
                f"{brief['latin']} is made one-shot next; "
                "the maker lands after this review"
            )
        if make_home:
            raise NotPossibleError(
                f"the {brief['home']} home is made next; "
                "the maker lands after this review"
            )
        if prey is not None:
            raise NotPossibleError(
                f"{prey['name']} is made one-shot next, as "
                f"{brief['latin']}'s prey; the maker lands after this review"
            )
        return result

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
        if not brief.get("needs_home"):
            return False, f"{brief['home']} is built"
        made = ctx.job_dir / "homes" / f"{brief['home']}.json"
        if made.is_file():
            return False, f"{brief['home']} is already made in this job"
        return True, f"no {brief['home']} template is built yet"

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
        if brief.get("in_library") and self._library_prey(
            world, data, brief, entry, hunts
        ):
            return None, "a library prey shares its habitat"
        candidates = self._named_prey(data, brief, research, world, hunts)
        if not candidates:
            return None, "no named prey can be made yet"
        name, size_cm, reason = candidates[0]
        return {"name": name, "size_cm": size_cm}, reason

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
        """Whether library prey already shares one of its habitats."""
        habitats = set(entry.get("habitats", []))
        for other in data["animals"]:
            if other is entry or not other.get("in_library"):
                continue
            if not habitats & set(other.get("habitats", [])):
                continue
            prey = animal_file(world, other["in_library"])
            if not prey:
                continue
            if set(prey.get("tags", []) or []) & hunts:
                return True
        return False

    def _named_prey(
        self,
        data: dict[str, Any],
        brief: dict[str, Any],
        research: dict[str, Any],
        world: Path,
        hunts: set[str],
    ) -> list[tuple[str, float, str]]:
        """Named prey that can be made, smallest first."""
        foods = [research["main_food"], *research["other_foods"]]
        found = []
        for food in foods:
            if food["kind"] != "prey" or food.get("size_cm") is None:
                continue
            match = match_by_name(data["animals"], food["name"])
            if match is None or not match.get("can_make", False):
                continue
            if brief.get("in_library") and not self._is_prey(world, match, hunts):
                continue
            found.append(
                (
                    food["name"],
                    float(food["size_cm"]),
                    f"smallest-bodied named prey that can be made "
                    f"({food['size_cm']} cm)",
                )
            )
        found.sort(key=lambda item: item[1])
        return found

    def _is_prey(self, world: Path, match: dict[str, Any], hunts: set[str]) -> bool:
        stem = match.get("in_library")
        if not stem:
            return True
        prey = animal_file(world, stem)
        if not prey:
            return True
        return bool(set(prey.get("tags", []) or []) & hunts)


def _making(minset: dict[str, Any]) -> list[str]:
    making = []
    if minset["make_animal"]:
        making.append("animal")
    if minset["make_home"]:
        making.append("home")
    if minset["prey"] is not None:
        making.append("prey")
    return making
