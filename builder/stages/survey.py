"""Stage 2, survey: research the place with search grounding, then write it.

Fixed shape, no model-decided flow: one land call, one species call, an
extra gaps call only when fewer than three independent source sites turned
up, then one structured write call with fix retries. A research answer cut
off at max_tokens is continued, never taken as ok. Eight model calls, 40
searches and 1.2M input tokens at most, no call over 0.5M input; going
over stops the job.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import jsonschema

from ..meta import (
    DEFAULT_MAX_TOKENS,
    count_search_calls,
    is_cut_off,
    usage_from_response,
)
from ..prompts import load_prompt
from ..schemas import load_schema, validate

SEARCH_TOOL = [{"type": "web_search"}]
MIN_DOMAINS = 3
MAX_CONTINUATIONS = 2
MAX_INPUT_PER_CALL = 500_000


def domain_of(url: str) -> str:
    """The site behind a URL, down to its last two hostname labels.

    A rough stand-in for "independent source": pages on one site share a
    domain no matter their subdomains. Good enough for counting; the pull
    request reviewer still judges independence for real.
    """

    host = (urlparse(url).hostname or "").lower()
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


class SurveyStage:
    """Research one area and write `survey.json` into the job folder."""

    name = "survey"

    def __init__(
        self,
        max_rounds: int = 8,
        max_searches: int = 40,
        max_input_tokens: int = 1_200_000,
    ) -> None:
        self.max_rounds = max_rounds
        self.max_searches = max_searches
        self.max_input_tokens = max_input_tokens
        self.rounds = 0
        self.searches = 0
        self.input_tokens = 0

    def run(self, ctx: Any) -> dict[str, Any]:
        area = json.loads((ctx.job_dir / "area.json").read_text(encoding="utf-8"))
        area_json = json.dumps(area, indent=2)
        land_text, land_urls = self._research(
            ctx, "survey_land", {"AREA_JSON": area_json}
        )
        species_text, species_urls = self._research(
            ctx, "survey_species", {"AREA_JSON": area_json}
        )
        cited = {url for url in land_urls | species_urls if urlparse(url).hostname}
        gaps_text = ""
        if len({domain_of(url) for url in cited}) < MIN_DOMAINS:
            extra_text, extra_urls = self._research(
                ctx,
                "survey_gaps",
                {
                    "AREA_JSON": area_json,
                    "SOURCES_SO_FAR": "\n".join(sorted(cited)),
                },
            )
            gaps_text = f"The gap research:\n\n{extra_text}"
            cited |= {url for url in extra_urls if urlparse(url).hostname}
        survey = self._write(ctx, area, land_text, species_text, gaps_text, cited)
        (ctx.job_dir / "research_land.md").write_text(land_text, encoding="utf-8")
        (ctx.job_dir / "research_species.md").write_text(species_text, encoding="utf-8")
        if gaps_text:
            (ctx.job_dir / "research_gaps.md").write_text(gaps_text, encoding="utf-8")
        out = ctx.job_dir / "survey.json"
        out.write_text(json.dumps(survey, indent=2), encoding="utf-8")
        domains = self._survey_domains(survey)
        return {
            "survey_file": "survey.json",
            "facts": len(survey["facts"]),
            "species": len(survey["species"]),
            "sources": len(domains),
        }

    def _research(
        self, ctx: Any, prompt_name: str, values: dict[str, str]
    ) -> tuple[str, set[str]]:
        first = load_prompt(prompt_name, values)
        self._spend_round(ctx, searches=True)
        response = ctx.respond(
            first,
            tools=SEARCH_TOOL,
            timeout=180,
            max_tokens=DEFAULT_MAX_TOKENS,
        )
        text = response_text(response)
        found = _citation_urls(response)
        self._spend_call(prompt_name, response)
        for _ in range(MAX_CONTINUATIONS):
            if not is_cut_off(response, DEFAULT_MAX_TOKENS):
                return text, found
            self._spend_round(ctx, searches=True)
            response = ctx.respond(
                _continue_prompt(first, text),
                tools=SEARCH_TOOL,
                timeout=180,
                max_tokens=DEFAULT_MAX_TOKENS,
            )
            text += "\n" + response_text(response)
            found |= _citation_urls(response)
            self._spend_call(prompt_name, response)
        if is_cut_off(response, DEFAULT_MAX_TOKENS):
            raise _Stopped(
                f"{prompt_name} was still cut off "
                f"after {MAX_CONTINUATIONS} continuations"
            )
        return text, found

    def _write(
        self,
        ctx: Any,
        area: dict[str, Any],
        land_text: str,
        species_text: str,
        gaps_text: str,
        cited: set[str],
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        schema = schema if schema is not None else load_schema("survey")
        base = load_prompt(
            "survey_write",
            {
                "AREA_JSON": json.dumps(area, indent=2),
                "LAND_TEXT": land_text,
                "SPECIES_TEXT": species_text,
                "GAPS_TEXT": gaps_text,
            },
        )
        draft: dict[str, Any] = {}
        problems = ""
        while True:
            self._spend_round(ctx, searches=False)
            prompt = base if not problems else f"{base}\n\n{problems}"
            response = ctx.chat(
                [{"role": "user", "content": prompt}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "Survey", "schema": schema},
                },
            )
            text = (response.choices[0].message.content or "").strip()
            problems, draft = self._check_write(text, cited)
            if not problems:
                return draft

    def _spend_round(self, ctx: Any, searches: bool) -> None:
        ctx.check_budget()
        if self.rounds >= self.max_rounds:
            raise _Stopped(f"survey spent its {self.max_rounds} rounds")
        if searches and self.searches >= self.max_searches:
            raise _Stopped(f"survey spent its {self.max_searches} searches")
        self.rounds += 1

    def _spend_call(self, prompt_name: str, response: Any) -> None:
        """Count one research answer's searches and input against the caps."""
        self.searches += count_search_calls(response)
        call_input = usage_from_response(response, "").input_tokens
        if call_input > MAX_INPUT_PER_CALL:
            raise _Stopped(
                f"{prompt_name} read {call_input} input tokens, "
                f"over the {MAX_INPUT_PER_CALL} per-call cap"
            )
        self.input_tokens += call_input
        if self.input_tokens > self.max_input_tokens:
            raise _Stopped(f"survey spent its {self.max_input_tokens} input tokens")

    def _check_write(self, text: str, cited: set[str]) -> tuple[str, dict[str, Any]]:
        try:
            draft = json.loads(text)
        except json.JSONDecodeError as error:
            return f"PREVIOUS TRY was not JSON: {error}", {}
        try:
            validate("survey", draft)
        except jsonschema.ValidationError as error:
            return f"PREVIOUS TRY broke the schema: {error.message}", {}
        cited_domains = {domain_of(url) for url in cited}
        used = self._survey_urls(draft)
        invented = sorted({url for url in used if domain_of(url) not in cited_domains})
        if invented:
            return (
                "PREVIOUS TRY used sources the research never returned: "
                + ", ".join(invented)
                + ". Use only the research URLs.",
                {},
            )
        if len(self._survey_domains(draft)) < MIN_DOMAINS:
            return (
                f"PREVIOUS TRY used fewer than {MIN_DOMAINS} independent source "
                "sites. Spread the sources across the research URLs.",
                {},
            )
        return "", draft

    @staticmethod
    def _survey_urls(draft: dict[str, Any]) -> list[str]:
        urls = []
        for fact in draft.get("facts", []):
            urls.extend(fact.get("sources", []))
        for species in draft.get("species", []):
            urls.extend(species.get("sources", []))
            for measured in (species.get("measurements") or {}).values():
                if isinstance(measured, dict) and measured.get("source"):
                    urls.append(measured["source"])
        return urls

    @classmethod
    def _survey_domains(cls, draft: dict[str, Any]) -> set[str]:
        return {domain_of(url) for url in cls._survey_urls(draft)}


class _Stopped(Exception):
    """The stage ran out of rounds or searches. The job stops with a reason."""


def _continue_prompt(first: str, text_so_far: str) -> str:
    """Pick up cut-off research: same brief, no recap, no repeats."""
    return (
        f"{first}\n\nYou stopped mid-answer. Continue EXACTLY where you "
        "stopped: no recap, no repeats, no new searches unless a fact "
        "needs one.\n\nSo far:\n\n" + text_so_far
    )


def response_text(response: Any) -> str:
    """Plain text of every output_text part, joined."""
    parts = []
    for item in getattr(response, "output", []) or []:
        for part in getattr(item, "content", []) or []:
            if getattr(part, "type", "") == "output_text":
                parts.append(getattr(part, "text", ""))
    return "\n".join(parts)


def _citation_urls(response: Any) -> set[str]:
    urls = set()
    for item in getattr(response, "output", []) or []:
        for part in getattr(item, "content", []) or []:
            for ann in getattr(part, "annotations", []) or []:
                if getattr(ann, "type", "") == "url_citation":
                    urls.add(getattr(ann, "url", ""))
    return {url for url in urls if url}
