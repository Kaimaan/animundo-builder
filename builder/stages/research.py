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
from ..prompts import for_children, load_prompt, place_line
from ..schemas import validate
from ..strict import HabitatResearch, Research, strict_schema, strip_nulls
from .survey import SEARCH_TOOL, final_text

SEARCHES = 5

#: Structured research, the Responses way (#58): decoding itself
#: follows the strict schema, so the answer parses as it arrives.
RESEARCH_FORMAT = {
    "type": "json_schema",
    "name": "Research",
    "schema": strict_schema(Research),
    "strict": True,
}

#: The habitat variant: foods and surroundings for a new home,
#: no size, looks or year. A brief without `species_key` (a home
#: item) takes this path.
HABITAT_FORMAT = {
    "type": "json_schema",
    "name": "HabitatResearch",
    "schema": strict_schema(HabitatResearch),
    "strict": True,
}

#: Headroom past the round: the model doesn't keep to the count, so
#: the API caps the runaway and a parsing answer is kept (#51.2).
SEARCH_SLACK = 5


def _source_urls(sources: Any) -> Any:
    """Sources as URL strings: objects give their `url` (#53.2).

    Anything without a usable URL is dropped, so one chatty
    source never fails the schema; anything else passes through
    for the schema to judge.
    """
    if not isinstance(sources, list):
        return sources
    urls = []
    for source in sources:
        if isinstance(source, dict):
            url = source.get("url")
            if isinstance(url, str) and url.strip():
                urls.append(url)
        else:
            urls.append(source)
    return urls


class _Stopped(Exception):
    """The research cannot be used as it came back."""


class ResearchStage:
    """Research once; write `research.json` and its cost.

    The animal, or the habitat's surroundings when the brief
    carries no species (a home item).
    """

    name = "research"

    def run(self, ctx: Any) -> dict[str, Any]:
        brief = json.loads((ctx.job_dir / "home.json").read_text(encoding="utf-8"))
        ctx.check_budget()
        before = ctx.tracker.spent_usd
        data, sources, searches = self._ask(ctx, brief)
        (ctx.job_dir / "research.json").write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )
        cost = round(ctx.tracker.spent_usd - before, 6)
        checks = ctx.job_dir / "checks"
        checks.mkdir(parents=True, exist_ok=True)
        (checks / "research.json").write_text(
            json.dumps({"cost_usd": cost, "searches": searches}), encoding="utf-8"
        )
        return {
            "research_file": "research.json",
            "main_food": data["main_food"]["name"],
            "sources": len(sources),
            "searches": searches,
            "cost_usd": cost,
        }

    def _ask(
        self, ctx: Any, brief: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str], int]:
        habitat_mode = not brief.get("species_key")
        if habitat_mode:
            prompt = load_prompt(
                "habitat_research",
                {
                    "HABITAT": str(brief.get("home", "land")),
                    "LAT": str(brief["center"]["lat"]),
                    "LON": str(brief["center"]["lon"]),
                    "PLACE_LINE": place_line(brief),
                    "FOR_CHILDREN": for_children(),
                },
            )
            text_format = HABITAT_FORMAT
            schema = "habitat-research"
        else:
            prompt = load_prompt(
                "research",
                {
                    "NAME": brief.get("name", ""),
                    "LATIN": brief.get("latin", ""),
                    "LAT": str(brief["center"]["lat"]),
                    "LON": str(brief["center"]["lon"]),
                    "PLACE_LINE": place_line(brief),
                    "FOR_CHILDREN": for_children(),
                },
            )
            text_format = RESEARCH_FORMAT
            schema = "research"
        response = ctx.respond(
            prompt,
            tools=SEARCH_TOOL,
            timeout=180,
            max_tool_calls=SEARCHES + SEARCH_SLACK,
            text_format=text_format,
        )
        searches = count_search_calls(response)
        text = final_text(response)
        if is_cut_off(response, 0) and not text.strip():
            raise _Stopped("the research came back empty")
        try:
            data = strip_nulls(json.loads(text))
            if isinstance(data, dict):
                data["sources"] = _source_urls(data.get("sources"))
            validate(schema, data)
        except ValueError as error:
            raise _Stopped(f"the research came back unparsable: {error}")
        return data, data["sources"], searches
