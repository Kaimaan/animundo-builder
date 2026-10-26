"""Survey stage: research, write, fix, and stop on limits. No network."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from builder.pipeline import BudgetTracker, JobContext, Pipeline, StageFailedError
from builder.stages.survey import SurveyStage, domain_of

LAND_URLS = ["https://park.example.org/land", "https://nature.example.com/forest"]
SPECIES_URLS = ["https://nature.example.com/deer", "https://gbif.example.net/deer"]


def _research_response(
    text: str, urls: list[str], searches: int = 1
) -> SimpleNamespace:
    parts = [
        SimpleNamespace(
            type="output_text",
            text=text,
            annotations=[SimpleNamespace(type="url_citation", url=url) for url in urls],
        )
    ]
    output = [SimpleNamespace(type="message", content=parts)]
    output += [SimpleNamespace(type="web_search_call") for _ in range(searches)]
    return SimpleNamespace(
        model="fake-test-model",
        output=output,
        usage=SimpleNamespace(
            prompt_tokens=100, completion_tokens=50, prompt_tokens_details=None
        ),
    )


def _chat_response(content: str) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(
        model="fake-test-model",
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(
            prompt_tokens=100, completion_tokens=50, prompt_tokens_details=None
        ),
    )


class ScriptClient:
    """Plays back canned answers in order, recording the prompts."""

    model = "fake-test-model"

    def __init__(self, answers: list[SimpleNamespace]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def chat(self, messages: list[dict], **kwargs: Any) -> SimpleNamespace:
        self.prompts.append(messages[0]["content"])
        return self.answers.pop(0)

    def responses_create(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        self.prompts.append(str(args[0])[:80])
        return self.answers.pop(0)


def _good_survey() -> dict:
    return {
        "area_id": "gb_cairngorms",
        "facts": [
            {
                "topic": "forest",
                "text": "Old pine forest.",
                "sources": ["https://park.example.org/land"],
            },
            {
                "topic": "water",
                "text": "Cold clear lochs.",
                "sources": ["https://nature.example.com/forest"],
            },
        ],
        "species": [
            {
                "species": "red_deer",
                "latin": "Cervus elaphus",
                "role": "playable",
                "evidence": "Resident herds.",
                "sources": ["https://gbif.example.net/deer"],
                "conservation": "LC",
                "sensitive": False,
            }
        ],
    }


def _ctx(tmp_path: Path, client: ScriptClient) -> JobContext:
    (tmp_path / "area.json").write_text(
        json.dumps(
            {
                "area_id": "gb_cairngorms",
                "name": "Cairngorms",
                "country": "GB",
                "centroid": {"lat": 57.07, "lon": -3.67},
            }
        )
    )
    return JobContext(
        job_id="job-1",
        job_dir=tmp_path,
        client=client,  # type: ignore[arg-type]
        tracker=BudgetTracker(budget_usd=8.0),
    )


def test_research_write_and_validate(tmp_path: Path) -> None:
    client = ScriptClient(
        [
            _research_response("LAND ...", LAND_URLS),
            _research_response("SPECIES ...", SPECIES_URLS),
            _chat_response(json.dumps(_good_survey())),
        ]
    )
    result = SurveyStage().run(_ctx(tmp_path, client))
    assert result["survey_file"] == "survey.json"
    assert result["facts"] == 2
    assert result["species"] == 1
    assert result["sources"] == 3
    written = json.loads((tmp_path / "survey.json").read_text())
    assert written["species"][0]["species"] == "red_deer"
    assert (tmp_path / "research_land.md").read_text() == "LAND ..."
    assert (tmp_path / "research_species.md").read_text() == "SPECIES ..."
    assert len(client.prompts) == 3, "no gaps call with three sites"


def test_gaps_call_when_too_few_sites(tmp_path: Path) -> None:
    one_site = ["https://park.example.org/land"]
    survey = _good_survey()
    survey["facts"][1]["sources"] = ["https://park.example.org/lochs"]
    survey["species"][0]["sources"] = ["https://park.example.org/deer"]
    client = ScriptClient(
        [
            _research_response("LAND ...", one_site),
            _research_response("SPECIES ...", one_site),
            _research_response(
                "GAPS ...", ["https://gbif.example.net/x", "https://iucn.example.edu/y"]
            ),
            _chat_response(json.dumps(survey)),
        ]
    )
    # The writer may only cite researched URLs; park/gbif/iucn are all cited.
    survey["facts"][1]["sources"] = ["https://gbif.example.net/x"]
    survey["species"][0]["sources"] = ["https://iucn.example.edu/y"]
    client.answers[-1] = _chat_response(json.dumps(survey))
    result = SurveyStage().run(_ctx(tmp_path, client))
    assert result["sources"] == 3
    assert len(client.prompts) == 4


def test_invented_source_sent_back_for_fix(tmp_path: Path) -> None:
    bad = _good_survey()
    bad["facts"][0]["sources"] = ["https://invented.example/lie"]
    client = ScriptClient(
        [
            _research_response("LAND ...", LAND_URLS),
            _research_response("SPECIES ...", SPECIES_URLS),
            _chat_response(json.dumps(bad)),
            _chat_response(json.dumps(_good_survey())),
        ]
    )
    result = SurveyStage().run(_ctx(tmp_path, client))
    assert result["facts"] == 2
    assert len(client.prompts) == 4
    assert "invented.example/lie" in client.prompts[3]


def test_schema_break_sent_back_for_fix(tmp_path: Path) -> None:
    bad = _good_survey()
    del bad["species"][0]["evidence"]
    client = ScriptClient(
        [
            _research_response("LAND ...", LAND_URLS),
            _research_response("SPECIES ...", SPECIES_URLS),
            _chat_response(json.dumps(bad)),
            _chat_response(json.dumps(_good_survey())),
        ]
    )
    SurveyStage().run(_ctx(tmp_path, client))
    assert len(client.prompts) == 4


def test_rounds_exhausted_stops_the_job(tmp_path: Path) -> None:
    bad = _good_survey()
    del bad["species"][0]["evidence"]
    client = ScriptClient(
        [_research_response("LAND ...", LAND_URLS)]
        + [_research_response("SPECIES ...", SPECIES_URLS)]
        + [_chat_response(json.dumps(bad))] * 5
    )
    pipeline = Pipeline([SurveyStage(max_rounds=3)], tmp_path / "state.json")
    with pytest.raises(StageFailedError):
        pipeline.run(_ctx(tmp_path, client))


def test_searches_exhausted_stops_the_job(tmp_path: Path) -> None:
    many = _research_response("LAND ...", LAND_URLS, searches=80)
    client = ScriptClient([many, _research_response("SPECIES ...", SPECIES_URLS)])
    pipeline = Pipeline([SurveyStage()], tmp_path / "state.json")
    with pytest.raises(StageFailedError):
        pipeline.run(_ctx(tmp_path, client))
    assert len(client.prompts) == 1


def test_domain_counting() -> None:
    assert domain_of("https://WWW.Park.Example.org/a") == "example.org"
    assert domain_of("https://a.example.org/x") == domain_of("https://b.example.org/y")
    assert domain_of("https://example.org/x") != domain_of("https://example.com/x")
    assert domain_of("not a url") == ""
