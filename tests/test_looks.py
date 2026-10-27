"""Looks stage: short strict calls for the prey and new plants. No network."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from builder.pipeline import BudgetTracker, JobContext
from builder.stages.looks import LOOKS_FORMAT, LooksStage, _Stopped


def _world(tmp_path: Path) -> Path:
    world = tmp_path / "world"
    (world / "models" / "plants").mkdir(parents=True)
    (world / "models" / "plants" / "bilberry.json").write_text('{"id": "bilberry"}')
    (world / "animal-list.json").write_text(
        json.dumps(
            {
                "homes": ["rainforest"],
                "not_yet": {},
                "animals": [
                    {
                        "id": "capybara",
                        "name": "Capybara",
                        "latin": "Hydrochoerus hydrochaeris",
                        "gbif_key": 5786666,
                    },
                    {
                        "id": "mountain_hare",
                        "name": "Mountain hare",
                        "latin": "Lepus timidus",
                        "gbif_key": 2435240,
                        "in_library": "mountain_hare",
                    },
                ],
            }
        )
    )
    return world


def _inputs(
    tmp_path: Path,
    prey: dict[str, Any] | None = None,
    surroundings: list[dict[str, Any]] | None = None,
) -> None:
    (tmp_path / "home.json").write_text(
        json.dumps(
            {
                "name": "Green anaconda",
                "latin": "Eunectes murinus",
                "species_key": 5225712,
                "home": "rainforest",
            }
        )
    )
    (tmp_path / "research.json").write_text(
        json.dumps(
            {
                "surroundings": surroundings
                if surroundings is not None
                else [
                    {"name": "kapok", "kind": "tree"},
                    {"name": "water lettuce", "kind": "plant"},
                    {"name": "bilberry", "kind": "plant"},
                    {"name": "muddy bank", "kind": "ground"},
                ],
            }
        )
    )
    (tmp_path / "minset.json").write_text(
        json.dumps(
            {
                "make_animal": True,
                "make_home": True,
                "prey": prey
                if prey is not None
                else {"name": "capybara", "size_cm": 120},
            }
        )
    )


def _looks_answer(name: str, latin: str, plant: bool = False) -> str:
    return json.dumps(
        {
            "latin": latin,
            "size_cm": 30.0 if plant else 120.0,
            "shoulder_cm": None,
            "shoulder_source": None,
            "height_cm": None,
            "height_source": None,
            "tail_cm": None,
            "tail_source": None,
            "colours": ["green"],
            "marks": ["broad leaves"] if plant else ["blunt snout"],
            "year": "Leaves all year." if plant else None,
            "sources": ["https://example.org/" + name.replace(" ", "_")],
        }
    )


def _response(text: str) -> SimpleNamespace:
    parts = [SimpleNamespace(type="output_text", text=text, annotations=[])]
    output = [SimpleNamespace(type="message", content=parts)]
    output.append(SimpleNamespace(type="web_search_call"))
    return SimpleNamespace(
        model="fake-test-model",
        output=output,
        usage=SimpleNamespace(
            prompt_tokens=100, completion_tokens=50, prompt_tokens_details=None
        ),
    )


class ScriptClient:
    model = "fake-test-model"

    def __init__(self, answers: list[SimpleNamespace]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.options: list[dict[str, Any]] = []

    def responses_create(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        self.prompts.append(str(args[0]))
        self.options.append(kwargs)
        return self.answers.pop(0)


def _ctx(tmp_path: Path, world: Path, client: ScriptClient) -> JobContext:
    return JobContext(
        job_id="job-1",
        job_dir=tmp_path,
        client=client,
        tracker=BudgetTracker(budget_usd=8.0),
        world_path=world,
    )


def test_looks_grounds_prey_and_specless_plants(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient(
        [
            _response(_looks_answer("capybara", "Hydrochoerus hydrochaeris")),
            _response(_looks_answer("water lettuce", "Pistia stratiotes", plant=True)),
        ]
    )
    result = LooksStage().run(_ctx(tmp_path, world, client))
    assert result["looks_file"] == "looks.json"
    assert len(client.prompts) == 2, "prey plus one plant, nothing else"
    assert "capybara" in client.prompts[0]
    assert "water lettuce" in client.prompts[1]
    looks = json.loads((tmp_path / "looks.json").read_text())
    assert looks["prey"]["latin"] == "Hydrochoerus hydrochaeris"
    assert looks["plants"]["water lettuce"]["year"] == "Leaves all year."
    assert "bilberry" not in looks["plants"], "the library has a spec"
    assert "kapok" not in looks["plants"], "trees need no looks"
    assert "muddy bank" not in looks["plants"], "ground needs no looks"
    checks = json.loads((tmp_path / "checks" / "looks.json").read_text())
    assert checks["cost_usd"] == result["cost_usd"]


def test_looks_sends_strict_format_and_three_searches(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient(
        [
            _response(_looks_answer("capybara", "Hydrochoerus hydrochaeris")),
            _response(_looks_answer("water lettuce", "Pistia stratiotes", plant=True)),
        ]
    )
    LooksStage().run(_ctx(tmp_path, world, client))
    assert client.options[0]["max_tool_calls"] == 3
    assert client.options[0]["text_format"] == LOOKS_FORMAT
    assert LOOKS_FORMAT["strict"] is True
    assert LOOKS_FORMAT["name"] == "ThingLooks"


def test_looks_skips_library_prey(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(
        tmp_path,
        prey={"name": "mountain hare", "size_cm": 55},
        surroundings=[{"name": "kapok", "kind": "tree"}],
    )
    client = ScriptClient([])
    result = LooksStage().run(_ctx(tmp_path, world, client))
    assert result["cost_usd"] == 0.0
    looks = json.loads((tmp_path / "looks.json").read_text())
    assert looks == {"prey": None, "plants": {}}


def test_looks_skips_a_ground_job(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    (tmp_path / "looks.json").write_text(json.dumps({"prey": None, "plants": {}}))
    client = ScriptClient([])
    result = LooksStage().run(_ctx(tmp_path, world, client))
    assert result == {"looks_file": "looks.json", "cost_usd": 0.0}


def test_looks_stops_on_an_unparsable_answer(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    client = ScriptClient([_response("not json")])
    with pytest.raises(_Stopped, match="capybara looks came back unparsable"):
        LooksStage().run(_ctx(tmp_path, world, client))
