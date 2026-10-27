"""Research stage: one short call about the animal. No network."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from builder.pipeline import BudgetTracker, JobContext
from builder.stages.research import ResearchStage

VALID = {
    "main_food": {"name": "mountain hare", "kind": "prey", "size_cm": 55},
    "other_foods": [{"name": "bilberries", "kind": "berries"}],
    "surroundings": [
        {"name": "Norway spruce", "kind": "tree", "food_kind": "cones"},
        {"name": "bilberry", "kind": "plant", "food_kind": "berries"},
        {"name": "timothy grass", "kind": "plant", "food_kind": "grass"},
        {"name": "mossy rocks", "kind": "ground"},
        {"name": "forest pond", "kind": "water"},
    ],
    "size": {"length_cm": 70, "weight_kg": 6.5},
    "year": {
        "winter": "active",
        "shelter": "Sleeps curled up in the open.",
        "young": "Cubs are born in spring.",
        "food": "Hunts all year.",
    },
    "sources": ["https://example.org/fox"],
}


def _brief() -> dict[str, Any]:
    return {
        "latin": "Vulpes vulpes",
        "name": "Red fox",
        "species_key": 5219243,
        "center": {"lat": 61.55, "lon": 26.05},
    }


class ScriptClient:
    model = "fake-test-model"

    def __init__(self, answers: list[SimpleNamespace]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def responses_create(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        self.prompts.append(str(args[0]))
        return self.answers.pop(0)


def _response(text: str, searches: int = 1) -> SimpleNamespace:
    parts = [SimpleNamespace(type="output_text", text=text, annotations=[])]
    output = [SimpleNamespace(type="message", content=parts)]
    output += [SimpleNamespace(type="web_search_call") for _ in range(searches)]
    return SimpleNamespace(
        model="fake-test-model",
        output=output,
        usage=SimpleNamespace(
            prompt_tokens=100, completion_tokens=50, prompt_tokens_details=None
        ),
    )


def _ctx(tmp_path: Path, client: ScriptClient) -> JobContext:
    return JobContext(
        job_id="job-1",
        job_dir=tmp_path,
        client=client,
        tracker=BudgetTracker(budget_usd=8.0),
        world_path=tmp_path,
    )


def test_research_writes_research_and_cost(tmp_path: Path) -> None:
    (tmp_path / "home.json").write_text(json.dumps(_brief()))
    client = ScriptClient([_response(json.dumps(VALID))])
    ctx = _ctx(tmp_path, client)
    result = ResearchStage().run(ctx)
    assert result["research_file"] == "research.json"
    assert result["main_food"] == "mountain hare"
    assert result["sources"] == 1
    assert result["cost_usd"] == round(ctx.tracker.spent_usd, 6) > 0
    assert "Writing for children" in client.prompts[0]
    saved = json.loads((tmp_path / "research.json").read_text(encoding="utf-8"))
    assert saved == VALID
    cost = json.loads(
        (tmp_path / "checks" / "research.json").read_text(encoding="utf-8")
    )
    assert cost == {"cost_usd": result["cost_usd"], "searches": 1}
    assert result["searches"] == 1


def test_research_bad_answer_stops(tmp_path: Path) -> None:
    (tmp_path / "home.json").write_text(json.dumps(_brief()))
    with pytest.raises(Exception, match="unparsable"):
        ResearchStage().run(_ctx(tmp_path, ScriptClient([_response("nope")])))


def test_research_keeps_a_good_answer_past_its_round(tmp_path: Path) -> None:
    (tmp_path / "home.json").write_text(json.dumps(_brief()))
    answer = _response(json.dumps(VALID), searches=13)
    result = ResearchStage().run(_ctx(tmp_path, ScriptClient([answer])))
    assert result["main_food"] == "mountain hare"
    assert result["searches"] == 13
    cost = json.loads(
        (tmp_path / "checks" / "research.json").read_text(encoding="utf-8")
    )
    assert cost["searches"] == 13


def _live_response(text: str, searches: int = 2) -> SimpleNamespace:
    """The live shape (#51.1): reasoning, commentary, searches, answer."""
    output: list[SimpleNamespace] = [SimpleNamespace(type="reasoning")]
    for _ in range(searches):
        output.append(
            SimpleNamespace(
                type="message",
                phase="commentary",
                content=[
                    SimpleNamespace(
                        type="output_text",
                        text="I'll research the red fox's diet.",
                        annotations=[],
                    )
                ],
            )
        )
        output.append(SimpleNamespace(type="web_search_call"))
    output.append(SimpleNamespace(type="reasoning"))
    output.append(
        SimpleNamespace(
            type="message",
            phase=None,
            content=[SimpleNamespace(type="output_text", text=text, annotations=[])],
        )
    )
    return SimpleNamespace(
        model="fake-test-model",
        output=output,
        usage=SimpleNamespace(
            prompt_tokens=100, completion_tokens=50, prompt_tokens_details=None
        ),
    )


def test_research_reads_the_final_message(tmp_path: Path) -> None:
    (tmp_path / "home.json").write_text(json.dumps(_brief()))
    answer = _live_response(json.dumps(VALID))
    result = ResearchStage().run(_ctx(tmp_path, ScriptClient([answer])))
    assert result["main_food"] == "mountain hare"
    assert result["searches"] == 2
