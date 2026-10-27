"""Stage 2r, research: one short call about the animal (animal_home jobs).

What it eats most (plus up to three other foods), about five
things around it, its size and its year, each with a source.
The minimum set, the one-shot maker and the new home all read
`research.json`; nothing else is researched (#45).
"""

from __future__ import annotations

import json
from typing import Any

from ..meta import count_search_calls, is_cut_off
from ..prompts import for_children, load_prompt
from ..schemas import validate
from .survey import SEARCH_TOOL, response_text

SEARCHES = 5


class _Stopped(Exception):
    """The research cannot be used as it came back."""


class ResearchStage:
    """Research the animal once; write `research.json` and its cost."""

    name = "research"

    def run(self, ctx: Any) -> dict[str, Any]:
        brief = json.loads((ctx.job_dir / "home.json").read_text(encoding="utf-8"))
        ctx.check_budget()
        before = ctx.tracker.spent_usd
        data, sources = self._ask(ctx, brief)
        (ctx.job_dir / "research.json").write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )
        cost = round(ctx.tracker.spent_usd - before, 6)
        checks = ctx.job_dir / "checks"
        checks.mkdir(parents=True, exist_ok=True)
        (checks / "research.json").write_text(
            json.dumps({"cost_usd": cost}), encoding="utf-8"
        )
        return {
            "research_file": "research.json",
            "main_food": data["main_food"]["name"],
            "sources": len(sources),
            "cost_usd": cost,
        }

    def _ask(self, ctx: Any, brief: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        prompt = load_prompt(
            "research",
            {
                "NAME": brief.get("name", ""),
                "LATIN": brief.get("latin", ""),
                "LAT": str(brief["center"]["lat"]),
                "LON": str(brief["center"]["lon"]),
                "FOR_CHILDREN": for_children(),
            },
        )
        response = ctx.respond(prompt, tools=SEARCH_TOOL, timeout=180)
        if count_search_calls(response) > SEARCHES + 5:
            raise _Stopped("the research searched far past its round")
        if is_cut_off(response, 0) and not response_text(response).strip():
            raise _Stopped("the research came back empty")
        try:
            data = json.loads(response_text(response))
            validate("research", data)
        except ValueError as error:
            raise _Stopped(f"the research came back unparsable: {error}")
        return data, data["sources"]
