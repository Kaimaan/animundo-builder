"""Species plan: GBIF matching, food, rank and save, region rewrite."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from builder.pipeline import BudgetTracker, JobContext, Pipeline, StageFailedError
from builder.schemas import validate
from builder.stages.species_plan import SpeciesPlanStage, taxon_same

FOX = {"gbif_key": 5219243, "rank": "SPECIES", "name": "Vulpes vulpes"}
PERCH = {"gbif_key": 8140485, "rank": "SPECIES", "name": "Perca fluviatilis"}
DEER = {"gbif_key": 2440958, "rank": "SPECIES", "name": "Cervus elaphus"}
MOSQUITO = {"gbif_key": 1651255, "rank": "SPECIES", "name": "Aedes communis"}
BEAR = {"gbif_key": 2433433, "rank": "SPECIES", "name": "Ursus arctos"}
FENNEC = {"gbif_key": 5219244, "rank": "SPECIES", "name": "Vulpes zerda"}
FENNICUS = {
    "gbif_key": 5220116,
    "rank": "SUBSPECIES",
    "name": "Rangifer tarandus fennicus",
}
TARANDUS = {
    "gbif_key": 5220117,
    "rank": "SUBSPECIES",
    "name": "Rangifer tarandus tarandus",
}
BEE = {"gbif_key": 1340298, "rank": "SPECIES", "name": "Apis mellifera"}

MATCH = {
    "Vulpes vulpes": (FOX, ["animals/fox.json"]),
    "Perca fluviatilis": (PERCH, ["animals/perch.json", "animals/young_perch.json"]),
    "Cervus elaphus": (DEER, []),
    "Aedes communis": (MOSQUITO, []),
    "Ursus arctos": (BEAR, []),
    "Vulpes zerda": (FENNEC, []),
    "Rangifer tarandus fennicus": (FENNICUS, []),
    "Rangifer tarandus": (
        {"gbif_key": 5220114, "rank": "SPECIES", "name": "Rangifer tarandus"},
        [],
    ),
    "Apis mellifera": (BEE, []),
    "Aquila chrysaetos": (
        {"gbif_key": 2480506, "rank": "SPECIES", "name": "Aquila chrysaetos"},
        [],
    ),
    "Anguis fragilis": (
        {"gbif_key": 2468095, "rank": "SPECIES", "name": "Anguis fragilis"},
        [],
    ),
    "Eptesicus serotinus": (
        {"gbif_key": 2433599, "rank": "SPECIES", "name": "Eptesicus serotinus"},
        [],
    ),
}


def _world(tmp_path: Path) -> Path:
    world = tmp_path / "world"
    animals = world / "animals"
    animals.mkdir(parents=True)
    (animals / "fox.json").write_text(json.dumps({"taxon": FOX}))
    (animals / "perch.json").write_text(json.dumps({"taxon": PERCH}))
    (animals / "young_perch.json").write_text(
        json.dumps({"taxon": PERCH, "stage_of": "perch"})
    )
    (animals / "archetypes.json").write_text("{}")
    (world / "models").mkdir(parents=True)
    (world / "models" / "README.md").write_text(
        "# Models\n\n"
        "## Reference models: where to start\n\n"
        "| New animal | Start from | Change |\n"
        "|---|---|---|\n"
        "| Deer: red deer, roe deer | `moose` | Lighter body |\n"
        "| Birds of prey, owls | `kestrel` | Size, beak |\n"
        "| Bats | none | Wings from the arms |\n"
    )
    return world


def _inputs(
    tmp_path: Path,
    survey_species: list[dict],
    region: dict | None = None,
) -> None:
    (tmp_path / "survey.json").write_text(
        json.dumps({"area_id": "xx_test", "facts": [], "species": survey_species})
    )
    regions = tmp_path / "regions"
    regions.mkdir(parents=True)
    (regions / "xx_test.json").write_text(
        json.dumps(
            region
            if region is not None
            else {
                "id": "xx_test",
                "animals": {
                    "playable": ["red_deer"],
                    "predators": ["red_fox"],
                    "prey": ["common_mosquito"],
                },
                "fish": [{"kind": "european_perch", "count": 5}],
                "food": [],
                "places": [],
            }
        )
    )


def _species(
    species: str, latin: str, role: str = "playable", sensitive: bool = False
) -> dict:
    return {
        "species": species,
        "latin": latin,
        "role": role,
        "evidence": "Seen.",
        "sources": ["https://example.org/x"],
        "sensitive": sensitive,
    }


def _ctx(tmp_path: Path, client: Any, world: Path) -> JobContext:
    return JobContext(
        job_id="job-1",
        job_dir=tmp_path,
        client=client,
        tracker=BudgetTracker(budget_usd=8.0),
        world_path=world,
    )


def _fake_match(world: Path, latin: str) -> dict[str, Any]:
    taxon, files = MATCH[latin]
    return {"taxon": taxon, "files": files}


PHYLA = {
    5219243: "Chordata",
    8140485: "Chordata",
    2440958: "Chordata",
    1651255: "Arthropoda",
    2433433: "Chordata",
    5219244: "Chordata",
    5220116: "Chordata",
    5220114: "Chordata",
    1340298: "Arthropoda",
    2480506: "Chordata",
    2468095: "Chordata",
    2433599: "Chordata",
}


def _fake_classify(gbif_key: int) -> str:
    return PHYLA[gbif_key]


class ScriptClient:
    model = "fake-test-model"

    def __init__(self, plans: list[dict], auto_pick: bool = True) -> None:
        self.plans = list(plans)
        self.auto_pick = auto_pick
        self.calls = 0

    def chat(self, messages: list[dict], **kwargs: Any) -> SimpleNamespace:
        self.calls += 1
        content = messages[0]["content"]
        if self.auto_pick and "reference-table row" in content:
            answer: dict[str, Any] = {"row": 1, "kind": "mammal"}
        else:
            answer = self.plans.pop(0)
        message = SimpleNamespace(content=json.dumps(answer), tool_calls=None)
        return SimpleNamespace(
            model="fake-test-model",
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=10, prompt_tokens_details=None
            ),
        )


def _plan(
    entries: list[tuple[str, str]],
    food_web_ok: bool = True,
    sensitive: list[str] | None = None,
    wild: dict[str, bool] | None = None,
) -> dict:
    wild = wild or {}
    return {
        "species": [
            {
                "species": species,
                "decision": decision,
                "reason": "Because.",
                "wild": wild.get(species, True),
            }
            for species, decision in entries
        ],
        "food_web_ok": food_web_ok,
        "missing_food": [],
        "sensitive_species": sensitive or [],
    }


def _surveyed() -> list[dict]:
    return [
        _species("red_fox", "Vulpes vulpes", "predator"),
        _species("european_perch", "Perca fluviatilis", "prey"),
        _species("red_deer", "Cervus elaphus", "playable"),
        _species("common_mosquito", "Aedes communis", "prey"),
    ]


def _no_revalidate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "builder.stages.species_plan.check_draft", lambda *args: {"results": []}
    )
    monkeypatch.setattr(
        "builder.stages.species_plan.draft_region_errors", lambda *args: []
    )


def _stage(**kwargs: Any) -> SpeciesPlanStage:
    kwargs.setdefault("match", _fake_match)
    kwargs.setdefault("classify", _fake_classify)
    kwargs.setdefault("recorded", lambda lat, lon, key: False)
    return SpeciesPlanStage(**kwargs)


def test_match_rewrite_and_plan(tmp_path: Path, monkeypatch: Any) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path, _surveyed())
    _no_revalidate(monkeypatch)
    client = ScriptClient(
        [
            _plan(
                [
                    ("red_fox", "reuse"),
                    ("european_perch", "reuse"),
                    ("red_deer", "new"),
                    ("common_mosquito", "food"),
                ]
            )
        ]
    )
    result = _stage().run(_ctx(tmp_path, client, world))
    assert result == {
        "species_plan_file": "species-plan.json",
        "reuse": 2,
        "new": 1,
        "food": 1,
        "candidate": 0,
        "needs_code": 0,
        "added_young": [],
        "backfilled": [],
        "backfill_unchecked": [],
    }
    written = json.loads((tmp_path / "species-plan.json").read_text())
    validate("species-plan", written)
    assert written["food_web_ok"] is True
    by_id = {entry["species"]: entry for entry in written["species"]}
    assert by_id["red_fox"]["taxon"]["gbif_key"] == 5219243
    assert by_id["common_mosquito"]["food_kind"] == "water_bugs"
    region = json.loads((tmp_path / "regions" / "xx_test.json").read_text())
    assert region["animals"]["predators"] == ["fox"]
    assert region["animals"]["playable"] == ["red_deer"]
    assert region["animals"]["prey"] == []
    assert region["fish"] == [{"kind": "perch", "count": 5}]
    assert region["food"] == [
        {"kind": "water_bugs", "amount": 10, "names": ["common mosquito"]}
    ]


def test_over_cap_demotes_in_order(tmp_path: Path, monkeypatch: Any) -> None:
    world = _world(tmp_path)
    _inputs(
        tmp_path,
        [
            _species("red_deer", "Cervus elaphus", "playable"),
            _species("brown_bear", "Ursus arctos", "predator"),
        ],
    )
    _no_revalidate(monkeypatch)
    client = ScriptClient([_plan([("red_deer", "new"), ("brown_bear", "new")])])
    result = _stage(max_new_species=1).run(_ctx(tmp_path, client, world))
    assert client.calls == 2, "plan, then one reference pick"
    assert result["new"] == 1
    assert result["candidate"] == 1
    written = json.loads((tmp_path / "species-plan.json").read_text())
    by_id = {entry["species"]: entry for entry in written["species"]}
    assert by_id["red_deer"]["decision"] == "new"
    assert by_id["brown_bear"]["decision"] == "candidate"
    assert "over the cap of 1" in by_id["brown_bear"]["reason"]
    region = json.loads((tmp_path / "regions" / "xx_test.json").read_text())
    assert region["animals"]["playable"] == ["red_deer"]


def test_young_forms_lose_to_grown_ones(tmp_path: Path, monkeypatch: Any) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path, _surveyed())
    _no_revalidate(monkeypatch)
    client = ScriptClient(
        [
            _plan(
                [
                    ("red_fox", "reuse"),
                    ("european_perch", "reuse"),
                    ("red_deer", "new"),
                    ("common_mosquito", "food"),
                ]
            )
        ]
    )
    _stage().run(_ctx(tmp_path, client, world))
    region = json.loads((tmp_path / "regions" / "xx_test.json").read_text())
    assert region["fish"][0]["kind"] == "perch"


def test_playable_invertebrate_is_ranked(tmp_path: Path, monkeypatch: Any) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path, [_species("honey_bee", "Apis mellifera", "playable")])
    _no_revalidate(monkeypatch)
    client = ScriptClient([_plan([("honey_bee", "new")])])
    result = _stage().run(_ctx(tmp_path, client, world))
    assert result["new"] == 1
    assert result["food"] == 0


def test_wormy_vertebrate_is_ranked(tmp_path: Path, monkeypatch: Any) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path, [_species("slow_worm", "Anguis fragilis", "prey")])
    _no_revalidate(monkeypatch)
    client = ScriptClient([_plan([("slow_worm", "new")])])
    result = _stage().run(_ctx(tmp_path, client, world))
    assert result["new"] == 1
    assert result["food"] == 0


def test_model_must_keep_the_match(tmp_path: Path, monkeypatch: Any) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path, _surveyed())
    _no_revalidate(monkeypatch)
    wrong = _plan(
        [
            ("red_fox", "new"),
            ("european_perch", "reuse"),
            ("red_deer", "new"),
            ("common_mosquito", "food"),
        ]
    )
    right = _plan(
        [
            ("red_fox", "reuse"),
            ("european_perch", "reuse"),
            ("red_deer", "new"),
            ("common_mosquito", "food"),
        ]
    )
    client = ScriptClient([wrong, right])
    _stage().run(_ctx(tmp_path, client, world))
    assert client.calls == 3, "two plan tries, then one reference pick"


def test_food_web_failure_stops_with_plan_written(
    tmp_path: Path, monkeypatch: Any
) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path, _surveyed())
    _no_revalidate(monkeypatch)
    hungry = _plan(
        [
            ("red_fox", "reuse"),
            ("european_perch", "reuse"),
            ("red_deer", "new"),
            ("common_mosquito", "food"),
        ],
        food_web_ok=False,
    )
    hungry["missing_food"] = ["prey for red_fox"]
    client = ScriptClient([hungry])
    pipeline = Pipeline([_stage()], tmp_path / "state.json")
    with pytest.raises(StageFailedError, match="stage species_plan failed"):
        pipeline.run(_ctx(tmp_path, client, world))
    written = json.loads((tmp_path / "species-plan.json").read_text())
    assert written["food_web_ok"] is False
    state = json.loads((tmp_path / "state.json").read_text())
    assert "food first fails" in state["failed"]["error"]


def test_id_collision_needs_a_person(tmp_path: Path, monkeypatch: Any) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path, [_species("fox", "Vulpes zerda", "playable")])
    _no_revalidate(monkeypatch)
    client = ScriptClient([_plan([])])
    pipeline = Pipeline([_stage()], tmp_path / "state.json")
    with pytest.raises(StageFailedError, match="stage species_plan failed"):
        pipeline.run(_ctx(tmp_path, client, world))
    assert client.calls == 0
    state = json.loads((tmp_path / "state.json").read_text())
    assert "must rename one" in state["failed"]["error"]


def test_other_subspecies_needs_a_person(tmp_path: Path, monkeypatch: Any) -> None:
    world = _world(tmp_path)
    (world / "animals" / "mountain_reindeer.json").write_text(
        json.dumps({"taxon": TARANDUS})
    )
    _inputs(tmp_path, [_species("reindeer", "Rangifer tarandus fennicus", "playable")])
    _no_revalidate(monkeypatch)
    client = ScriptClient([_plan([])])
    pipeline = Pipeline([_stage()], tmp_path / "state.json")
    with pytest.raises(StageFailedError, match="stage species_plan failed"):
        pipeline.run(_ctx(tmp_path, client, world))
    assert client.calls == 0
    state = json.loads((tmp_path / "state.json").read_text())
    assert "different subspecies" in state["failed"]["error"]


def test_broken_rewrite_stops(tmp_path: Path, monkeypatch: Any) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path, _surveyed())
    monkeypatch.setattr(
        "builder.stages.species_plan.check_draft", lambda *args: {"results": []}
    )
    monkeypatch.setattr(
        "builder.stages.species_plan.draft_region_errors",
        lambda *args: ["the cast is empty"],
    )
    client = ScriptClient(
        [
            _plan(
                [
                    ("red_fox", "reuse"),
                    ("european_perch", "reuse"),
                    ("red_deer", "new"),
                    ("common_mosquito", "food"),
                ]
            )
        ]
    )
    pipeline = Pipeline([_stage()], tmp_path / "state.json")
    with pytest.raises(StageFailedError, match="stage species_plan failed"):
        pipeline.run(_ctx(tmp_path, client, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "rewritten region fails" in state["failed"]["error"]


def test_tame_species_saved_for_person(tmp_path: Path, monkeypatch: Any) -> None:
    world = _world(tmp_path)
    _inputs(
        tmp_path,
        [
            _species("reindeer", "Rangifer tarandus", "prey"),
            _species("red_deer", "Cervus elaphus", "playable"),
            _species("brown_bear", "Ursus arctos", "predator"),
            _species("european_perch", "Perca fluviatilis", "prey"),
        ],
    )
    _no_revalidate(monkeypatch)
    client = ScriptClient(
        [
            _plan(
                [
                    ("reindeer", "new"),
                    ("red_deer", "new"),
                    ("brown_bear", "candidate"),
                    ("european_perch", "reuse"),
                ],
                wild={"reindeer": False},
            )
        ]
    )
    result = _stage().run(_ctx(tmp_path, client, world))
    assert result["new"] == 2
    assert result["candidate"] == 1
    written = json.loads((tmp_path / "species-plan.json").read_text())
    by_id = {entry["species"]: entry for entry in written["species"]}
    assert by_id["reindeer"]["decision"] == "candidate"
    assert "not built as a wild species" in by_id["reindeer"]["reason"]
    assert by_id["brown_bear"]["decision"] == "new"
    assert "within the cap" in by_id["brown_bear"]["reason"]


def test_predator_without_prey_waits(tmp_path: Path, monkeypatch: Any) -> None:
    world = _world(tmp_path)
    _inputs(
        tmp_path,
        [
            _species("brown_bear", "Ursus arctos", "predator"),
            _species("red_deer", "Cervus elaphus", "playable"),
        ],
    )
    _no_revalidate(monkeypatch)
    client = ScriptClient([_plan([("brown_bear", "new"), ("red_deer", "candidate")])])
    result = _stage().run(_ctx(tmp_path, client, world))
    assert result["new"] == 1
    written = json.loads((tmp_path / "species-plan.json").read_text())
    by_id = {entry["species"]: entry for entry in written["species"]}
    assert by_id["brown_bear"]["decision"] == "candidate"
    assert "no prey in the cast" in by_id["brown_bear"]["reason"]
    assert by_id["red_deer"]["decision"] == "new"


def _hungry_world(tmp_path: Path) -> Path:
    world = _world(tmp_path)
    animals = world / "animals"
    (animals / "fox.json").write_text(
        json.dumps(
            {
                "taxon": FOX,
                "diet": ["berries", "clover"],
                "hunts": ["small_mammal"],
                "tags": ["predator"],
            }
        )
    )
    (animals / "perch.json").write_text(
        json.dumps(
            {
                "taxon": PERCH,
                "diet": ["water_bugs"],
                "hunts": ["fry"],
                "tags": ["fish"],
            }
        )
    )
    (animals / "young_perch.json").write_text(
        json.dumps(
            {
                "taxon": PERCH,
                "stage_of": "perch",
                "diet": ["water_bugs"],
                "tags": ["fry"],
            }
        )
    )
    return world


def test_heal_cast_adds_food_and_young(tmp_path: Path, monkeypatch: Any) -> None:
    world = _hungry_world(tmp_path)
    _inputs(
        tmp_path,
        [
            _species("red_fox", "Vulpes vulpes", "predator"),
            _species("european_perch", "Perca fluviatilis", "prey"),
        ],
        {
            "id": "xx_test",
            "animals": {"playable": [], "predators": ["red_fox"], "prey": []},
            "fish": [{"kind": "european_perch", "count": 5}],
            "food": [{"kind": "berries", "amount": 10, "names": ["berries"]}],
            "places": [],
        },
    )
    _no_revalidate(monkeypatch)
    client = ScriptClient([_plan([("red_fox", "reuse"), ("european_perch", "reuse")])])
    result = _stage().run(_ctx(tmp_path, client, world))
    assert result["added_young"] == ["young_perch"]
    region = json.loads((tmp_path / "regions" / "xx_test.json").read_text())
    kinds = sorted(food["kind"] for food in region["food"])
    assert kinds == ["berries", "clover", "water_bugs"]
    assert region["fish"] == [
        {"kind": "perch", "count": 5},
        {"kind": "young_perch", "count": 5},
    ]
    written = json.loads((tmp_path / "species-plan.json").read_text())
    validate("species-plan", written)
    assert written["added_young"] == ["young_perch"]


def _enum_world(tmp_path: Path) -> Path:
    world = _world(tmp_path)
    (world / "animals" / "fox.json").write_text(
        json.dumps({"taxon": FOX, "diet": ["berries", "mushrooms"]})
    )
    tools = world / "tools"
    tools.mkdir(exist_ok=True)
    (tools / "validate_region.py").write_text('FOOD_KINDS = {"berries"}\n')
    return world


def _fox_region(ground_cover: dict) -> dict:
    return {
        "id": "xx_test",
        "animals": {"playable": [], "predators": ["red_fox"], "prey": []},
        "fish": [],
        "food": [{"kind": "berries", "amount": 10, "names": ["berries"]}],
        "places": [],
        "ground_cover": ground_cover,
    }


def test_plant_food_counts_for_diets(tmp_path: Path, monkeypatch: Any) -> None:
    world = _enum_world(tmp_path)
    _inputs(
        tmp_path,
        [_species("red_fox", "Vulpes vulpes", "predator")],
        _fox_region({"plants": [{"model": "porcini", "food": "mushrooms"}]}),
    )
    _no_revalidate(monkeypatch)
    client = ScriptClient([_plan([("red_fox", "reuse")])])
    result = _stage().run(_ctx(tmp_path, client, world))
    assert result["reuse"] == 1
    region = json.loads((tmp_path / "regions" / "xx_test.json").read_text())
    assert [food["kind"] for food in region["food"]] == ["berries"]


def test_unprovidable_food_stops_for_person(tmp_path: Path, monkeypatch: Any) -> None:
    world = _enum_world(tmp_path)
    _inputs(
        tmp_path,
        [_species("red_fox", "Vulpes vulpes", "predator")],
        _fox_region({"plants": []}),
    )
    _no_revalidate(monkeypatch)
    client = ScriptClient([_plan([("red_fox", "reuse")])])
    pipeline = Pipeline([_stage()], tmp_path / "state.json")
    with pytest.raises(StageFailedError, match="stage species_plan failed"):
        pipeline.run(_ctx(tmp_path, client, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "no food or plant here provides it" in state["failed"]["error"]


def test_unresolvable_tag_waits_for_new_species(
    tmp_path: Path, monkeypatch: Any
) -> None:
    world = _world(tmp_path)
    (world / "animals" / "fox.json").write_text(
        json.dumps({"taxon": FOX, "hunts": ["small_bird"], "tags": ["predator"]})
    )
    _inputs(
        tmp_path,
        [
            _species("red_fox", "Vulpes vulpes", "predator"),
            _species("red_deer", "Cervus elaphus", "playable"),
        ],
        {
            "id": "xx_test",
            "animals": {
                "playable": ["red_deer"],
                "predators": ["red_fox"],
                "prey": [],
            },
            "fish": [],
            "food": [],
            "places": [],
        },
    )
    _no_revalidate(monkeypatch)
    client = ScriptClient([_plan([("red_fox", "reuse"), ("red_deer", "new")])])
    result = _stage().run(_ctx(tmp_path, client, world))
    assert result["added_young"] == []
    region = json.loads((tmp_path / "regions" / "xx_test.json").read_text())
    assert region["animals"]["predators"] == ["fox"]


def test_named_nest_site_stops(tmp_path: Path, monkeypatch: Any) -> None:
    world = _world(tmp_path)
    _inputs(
        tmp_path,
        [_species("golden_eagle", "Aquila chrysaetos", "predator")],
        {
            "id": "xx_test",
            "animals": {"playable": [], "predators": ["golden_eagle"], "prey": []},
            "fish": [],
            "food": [],
            "places": [{"name": "Eagle Crag nest ledge"}],
        },
    )
    _no_revalidate(monkeypatch)
    client = ScriptClient(
        [_plan([("golden_eagle", "new")], sensitive=["golden_eagle"])]
    )
    pipeline = Pipeline([_stage()], tmp_path / "state.json")
    with pytest.raises(StageFailedError, match="stage species_plan failed"):
        pipeline.run(_ctx(tmp_path, client, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "real sites for sensitive species" in state["failed"]["error"]


def _proc(returncode: int, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_taxon_retries_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def flaky(*args: Any, **kwargs: Any) -> SimpleNamespace:
        calls["n"] += 1
        if calls["n"] < 3:
            return _proc(1, "", "URLError: timed out")
        return _proc(0, json.dumps({"taxon": FOX, "files": []}))

    monkeypatch.setattr(subprocess, "run", flaky)
    monkeypatch.setattr(time, "sleep", sleeps.append)
    found = taxon_same(tmp_path, "Vulpes vulpes")
    assert found["taxon"]["gbif_key"] == 5219243
    assert calls["n"] == 3
    assert sleeps == [2.0, 4.0]


def test_taxon_stable_failure_is_short(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stderr = "Traceback (most recent call last):\n  File x\nURLError: timed out"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _proc(1, "", stderr))
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    from builder.stages.species_plan import _Stopped

    with pytest.raises(_Stopped, match="URLError: timed out"):
        taxon_same(tmp_path, "Vulpes vulpes")


def test_impossible_kind_is_needs_code(tmp_path: Path, monkeypatch: Any) -> None:
    world = _world(tmp_path)
    _inputs(
        tmp_path,
        [_species("serotine_bat", "Eptesicus serotinus", "playable")],
        region={
            "id": "xx_test",
            "animals": {"playable": ["serotine_bat"], "predators": [], "prey": []},
            "fish": [],
            "food": [],
            "places": [],
        },
    )
    _no_revalidate(monkeypatch)
    client = ScriptClient([_plan([("serotine_bat", "needs_code")])])
    result = _stage().run(_ctx(tmp_path, client, world))
    assert result["needs_code"] == 1
    written = json.loads((tmp_path / "species-plan.json").read_text())
    entry = written["species"][0]
    assert entry["decision"] == "needs_code"
    assert entry["reason"] == "Because.", "the model writes reasons, as for food"
    region = json.loads((tmp_path / "regions" / "xx_test.json").read_text())
    assert region["animals"]["playable"] == []


def test_new_species_gets_reference(tmp_path: Path, monkeypatch: Any) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path, [_species("red_deer", "Cervus elaphus", "playable")])
    _no_revalidate(monkeypatch)
    client = ScriptClient(
        [_plan([("red_deer", "new")]), {"row": 1, "kind": "mammal"}],
        auto_pick=False,
    )
    _stage().run(_ctx(tmp_path, client, world))
    written = json.loads((tmp_path / "species-plan.json").read_text())
    assert written["species"][0]["reference"] == {
        "row": "Deer: red deer, roe deer",
        "start": "moose",
        "change": "Lighter body",
        "kind": "mammal",
    }


def test_picker_backstop_saves_impossible(tmp_path: Path, monkeypatch: Any) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path, [_species("red_deer", "Cervus elaphus", "playable")])
    _no_revalidate(monkeypatch)
    client = ScriptClient(
        [_plan([("red_deer", "new")]), {"row": 3, "kind": "mammal"}],
        auto_pick=False,
    )
    result = _stage().run(_ctx(tmp_path, client, world))
    assert result["needs_code"] == 1
    written = json.loads((tmp_path / "species-plan.json").read_text())
    entry = written["species"][0]
    assert entry["decision"] == "needs_code"
    assert "Saved" in entry["reason"]
    assert "reference" not in entry


HARE = {"gbif_key": 2436756, "rank": "SPECIES", "name": "Lepus timidus"}


def _backfill_world(tmp_path: Path) -> Path:
    world = _world(tmp_path)
    animals = world / "animals"
    (animals / "fox.json").write_text(
        json.dumps(
            {
                "taxon": FOX,
                "tags": ["predator"],
                "playable": True,
                "hunts": ["small_mammal"],
            }
        )
    )
    (animals / "mountain_hare.json").write_text(
        json.dumps({"taxon": HARE, "tags": ["small_mammal"], "playable": True})
    )
    return world


def _backfill_region() -> dict:
    return {
        "id": "xx_test",
        "center": {"lat": 61.18, "lon": 26.88},
        "animals": {"playable": ["red_deer"], "predators": ["red_fox"], "prey": []},
        "fish": [],
        "food": [],
        "places": [],
    }


def test_backfill_reuses_a_recorded_missed_animal(
    tmp_path: Path, monkeypatch: Any
) -> None:
    world = _backfill_world(tmp_path)
    _inputs(
        tmp_path,
        [
            _species("red_fox", "Vulpes vulpes", "predator"),
            _species("red_deer", "Cervus elaphus", "playable"),
        ],
        _backfill_region(),
    )
    _no_revalidate(monkeypatch)
    client = ScriptClient(
        [
            _plan(
                [
                    ("red_fox", "reuse"),
                    ("red_deer", "new"),
                    ("mountain_hare", "reuse"),
                ]
            )
        ]
    )
    result = _stage(recorded=lambda lat, lon, key: key == 2436756).run(
        _ctx(tmp_path, client, world)
    )
    assert result["backfilled"] == ["mountain_hare"]
    written = json.loads((tmp_path / "species-plan.json").read_text())
    validate("species-plan", written)
    by_id = {entry["species"]: entry for entry in written["species"]}
    assert by_id["mountain_hare"]["decision"] == "reuse"
    assert by_id["mountain_hare"]["taxon"]["gbif_key"] == 2436756
    region = json.loads((tmp_path / "regions" / "xx_test.json").read_text())
    assert "mountain_hare" in region["animals"]["prey"]
    assert "mountain_hare" in region["animals"]["playable"]


def test_backfill_skips_without_a_sound(tmp_path: Path, monkeypatch: Any) -> None:
    world = _backfill_world(tmp_path)
    animals = world / "animals"
    (animals / "red_fox_twin.json").write_text(json.dumps({"taxon": FOX}))
    (animals / "zz_stone.json").write_text(
        json.dumps(
            {
                "taxon": {
                    "gbif_key": 9990001,
                    "rank": "SPECIES",
                    "name": "Lapis testis",
                },
                "playable": False,
            }
        )
    )
    _inputs(
        tmp_path,
        [
            _species("red_fox", "Vulpes vulpes", "predator"),
            _species("red_deer", "Cervus elaphus", "playable"),
        ],
        _backfill_region(),
    )
    _no_revalidate(monkeypatch)
    # Everything recorded: the young perch, the twin fox (same taxon as
    # red_fox), the unplayable unhunted stone, and the hare.
    client = ScriptClient([_plan([("red_fox", "reuse"), ("red_deer", "new")])])
    result = _stage(recorded=lambda lat, lon, key: key != 2436756).run(
        _ctx(tmp_path, client, world)
    )
    assert result["backfilled"] == []
    written = json.loads((tmp_path / "species-plan.json").read_text())
    assert {entry["species"] for entry in written["species"]} == {
        "red_fox",
        "red_deer",
    }


def test_backfilled_prey_keeps_a_new_predator(tmp_path: Path, monkeypatch: Any) -> None:
    world = _backfill_world(tmp_path)
    _inputs(
        tmp_path,
        [
            _species("red_fox", "Vulpes vulpes", "predator"),
            _species("brown_bear", "Ursus arctos", "predator"),
        ],
        _backfill_region(),
    )
    _no_revalidate(monkeypatch)
    client = ScriptClient(
        [
            _plan(
                [
                    ("red_fox", "reuse"),
                    ("brown_bear", "new"),
                    ("mountain_hare", "reuse"),
                ]
            )
        ]
    )
    result = _stage(recorded=lambda lat, lon, key: key == 2436756).run(
        _ctx(tmp_path, client, world)
    )
    assert result["backfilled"] == ["mountain_hare"]
    written = json.loads((tmp_path / "species-plan.json").read_text())
    by_id = {entry["species"]: entry for entry in written["species"]}
    assert by_id["brown_bear"]["decision"] == "new"


def test_backfill_reports_unchecked_animals(tmp_path: Path, monkeypatch: Any) -> None:
    world = _backfill_world(tmp_path)
    _inputs(
        tmp_path,
        [
            _species("red_fox", "Vulpes vulpes", "predator"),
            _species("red_deer", "Cervus elaphus", "playable"),
        ],
        _backfill_region(),
    )
    _no_revalidate(monkeypatch)
    client = ScriptClient([_plan([("red_fox", "reuse"), ("red_deer", "new")])])

    def flaky(lat: float, lon: float, key: int) -> bool:
        raise OSError("GBIF is down")

    result = _stage(recorded=flaky).run(_ctx(tmp_path, client, world))
    assert result["backfilled"] == []
    assert result["backfill_unchecked"] == ["mountain_hare"]
