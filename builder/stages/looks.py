"""Stage 2l, looks: what the made things look like (#58.5).

After the minimum: one short strict call (three searches at
most) for each thing the makers will make sight unseen: the
prey, and every plant-kind surrounding without a spec. Each
returns latin, size, colours, marks or shape notes, the year
for plants, and sources. Library items get no call. The
animal maker reads the prey's looks, the home maker the
plants'.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..animal_list import find_list, load_list, match_by_name
from ..prompts import load_prompt
from ..strict import ThingLooks, strict_schema, strip_nulls
from .survey import SEARCH_TOOL, final_text

#: Three searches at most per looks call (#58.5).
SEARCHES = 3

#: Structured looks, the Responses way: strict `text.format`.
LOOKS_FORMAT = {
    "type": "json_schema",
    "name": "ThingLooks",
    "schema": strict_schema(ThingLooks),
    "strict": True,
}


class _Stopped(Exception):
    """The looks stop here, with the reason, for a person."""


class LooksStage:
    """Ground the looks of the prey and the new plants."""

    name = "looks"

    def run(self, ctx: Any) -> dict[str, Any]:
        if ctx.world_path is None:
            raise ValueError("looks needs --world")
        world = Path(ctx.world_path)
        cached = ctx.job_dir / "looks.json"
        if cached.is_file():
            return {"looks_file": "looks.json", "cost_usd": 0.0}
        brief = json.loads((ctx.job_dir / "home.json").read_text(encoding="utf-8"))
        research = json.loads(
            (ctx.job_dir / "research.json").read_text(encoding="utf-8")
        )
        minset = json.loads((ctx.job_dir / "minset.json").read_text(encoding="utf-8"))
        if (
            not minset.get("make_animal")
            and not minset.get("make_home")
            and minset.get("prey") is None
        ):
            # Nothing is made, so nobody reads looks: the empty
            # result at $0, no call (#81.2).
            return self._finish(ctx.job_dir, None, {}, 0.0)
        before = ctx.tracker.spent_usd
        data = load_list(find_list(world))
        prey = self._prey_looks(ctx, data["animals"], brief, minset)
        plants = self._plant_looks(ctx, world, brief, research)
        return self._finish(
            ctx.job_dir, prey, plants, round(ctx.tracker.spent_usd - before, 6)
        )

    @staticmethod
    def _finish(
        job_dir: Path, prey: dict[str, Any] | None, plants: dict[str, Any], cost: float
    ) -> dict[str, Any]:
        """The looks file and its checks entry, at the given cost."""
        (job_dir / "looks.json").write_text(
            json.dumps({"prey": prey, "plants": plants}, indent=2),
            encoding="utf-8",
        )
        checks = job_dir / "checks"
        checks.mkdir(parents=True, exist_ok=True)
        (checks / "looks.json").write_text(
            json.dumps({"cost_usd": cost}, indent=2), encoding="utf-8"
        )
        return {"looks_file": "looks.json", "cost_usd": cost}

    def _prey_looks(
        self,
        ctx: Any,
        entries: list[dict[str, Any]],
        brief: dict[str, Any],
        minset: dict[str, Any],
    ) -> dict[str, Any] | None:
        """The prey's looks, unless the library already has it."""
        prey = minset.get("prey")
        if not prey:
            return None
        match = match_by_name(entries, prey["name"])
        if match is not None and match.get("in_library"):
            return None
        latin = match.get("latin", "") if match else ""
        size = f", about {prey['size_cm']} cm long" if prey.get("size_cm") else ""
        return self._ask(
            ctx,
            prey["name"],
            latin,
            f"the prey of {brief.get('name', 'the main animal')}{size}",
        )

    def _plant_looks(
        self,
        ctx: Any,
        world: Path,
        brief: dict[str, Any],
        research: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        """Looks for plant surroundings the game has no spec for."""
        specs = {path.stem for path in (world / "models" / "plants").glob("*.json")}
        plants = {}
        for item in research.get("surroundings", []):
            if item.get("kind") != "plant":
                continue
            slug = item["name"].lower().replace(" ", "_").replace("-", "_")
            if slug in specs:
                continue
            plants[item["name"]] = self._ask(
                ctx,
                item["name"],
                item.get("latin", ""),
                f"a plant in the {brief.get('home', 'home')} home",
            )
        return plants

    def _ask(self, ctx: Any, name: str, latin: str, context: str) -> dict[str, Any]:
        """One short strict call about one thing."""
        ctx.check_budget()
        prompt = load_prompt(
            "looks",
            {
                "NAME": name,
                "LATIN_HINT": f" ({latin})" if latin else "",
                "CONTEXT": context,
            },
        )
        response = ctx.respond(
            prompt,
            tools=SEARCH_TOOL,
            timeout=180,
            max_tool_calls=SEARCHES,
            text_format=LOOKS_FORMAT,
        )
        try:
            data = ThingLooks.model_validate(json.loads(final_text(response)))
        except (ValueError, ValidationError) as error:
            raise _Stopped(f"the {name} looks came back unparsable: {error}")
        return strip_nulls(data.model_dump(mode="json"))
