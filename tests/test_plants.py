"""Plants stage: draft, GBIF check, build, review, fix. No network."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from builder.pipeline import BudgetTracker, JobContext, Pipeline, StageFailedError
from builder.stages.plants import PlantsStage, _Stopped

SPEC = {
    "id": "wood_sorrel",
    "name": "Wood sorrel",
    "author": "Animundo Builder",
    "latin": "Oxalis acetosella",
    "kind": "ground",
    "colors": {"leaf": "#3f7a34"},
    "parts": [{"name": "leaves"}],
    "year": {"leaves": "deciduous", "flowers": "May.", "fruit": "none"},
    "sources": ["https://example.org/wood-sorrel"],
}
TAXON = {"gbif_key": 123, "rank": "SPECIES", "name": "Oxalis acetosella"}
REVIEW_CLEAN = {"species": "wood_sorrel", "round": 1, "issues": []}

DRAFT_STUB = """\
import json
print(json.dumps({
    "ok": True,
    "results": [{"check": "models", "status": "pass", "problems": []}],
}))
"""


def _png() -> bytes:
    canvas = Image.new("RGB", (64, 64), (63, 122, 52))
    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()


def _world(tmp_path: Path) -> Path:
    world = tmp_path / "world"
    (world / "models" / "plants").mkdir(parents=True)
    (world / "models" / "plants" / "bilberry.json").write_text(
        json.dumps({"id": "bilberry", "name": "Bilberry", "kind": "bush"})
    )
    (world / "models" / "plants" / "wood_anemone.json").write_text(
        json.dumps({"id": "wood_anemone", "name": "Wood anemone", "kind": "ground"})
    )
    (world / "models" / "previews" / "plants").mkdir(parents=True)
    (world / "models" / "previews" / "plants" / "bilberry.png").write_bytes(b"png")
    (world / "models" / "previews" / "plants" / "wood_anemone.png").write_bytes(b"png")
    (world / "models" / "README.md").write_text(
        "# Models\n\n## Plants\n\nParts take shapes.\n"
    )
    (world / "CONTRIBUTING.md").write_text(
        "# World\n\n## Adding plants\n\n### The rules\n\nReal species only.\n\n"
        "> Add a **[PLANT]** to the game.\n"
    )
    (world / "tools").mkdir(parents=True)
    (world / "tools" / "check_draft.py").write_text(DRAFT_STUB)
    (world / "schemas").mkdir(parents=True)
    (world / "schemas" / "model.schema.json").write_text(
        json.dumps(
            {
                "if": {"required": ["kind"]},
                "then": {"$ref": "#/$defs/plant_model"},
                "$defs": {
                    "plant_model": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            key: {}
                            for key in (
                                "id",
                                "name",
                                "author",
                                "latin",
                                "kind",
                                "colors",
                                "parts",
                                "year",
                                "sources",
                                "taxon",
                            )
                        },
                    }
                },
            }
        )
    )
    (world / "poisonous-plants.json").write_text(
        json.dumps({"plants": [{"gbif_key": 999, "name": "Deadly one"}]})
    )
    return world


def _inputs(tmp_path: Path, plants: list[dict] | None = None) -> None:
    (tmp_path / "survey.json").write_text(
        json.dumps(
            {
                "area_id": "xx_test",
                "facts": [
                    {"topic": "vegetation", "text": "Sorrel carpets the forest."}
                ],
                "species": [],
            }
        )
    )
    regions = tmp_path / "regions"
    regions.mkdir(parents=True)
    (regions / "xx_test.json").write_text(
        json.dumps(
            {
                "id": "xx_test",
                "name": "Test forest",
                "country": "FI",
                "ground_cover": {
                    "plants": plants
                    if plants is not None
                    else [
                        {
                            "model": "wood_sorrel",
                            "names": ["wood sorrel"],
                            "density": 0.2,
                        }
                    ]
                },
            }
        )
    )


def _ctx(tmp_path: Path, world: Path) -> JobContext:
    return JobContext(
        job_id="job-1",
        job_dir=tmp_path,
        client=None,
        tracker=BudgetTracker(budget_usd=8.0),
        world_path=world,
    )


class ScriptChat:
    """Plays back canned answers, recording prompts."""

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, messages: list[dict], **kwargs: Any) -> SimpleNamespace:
        content = messages[-1]["content"]
        text = content if isinstance(content, str) else content[0]["text"]
        self.prompts.append(text)
        message = SimpleNamespace(content=self.answers.pop(0), tool_calls=None)
        return SimpleNamespace(
            model="fake-test-model", choices=[SimpleNamespace(message=message)]
        )


def _match_new(world: Path, latin: str) -> dict[str, Any]:
    assert latin == "Oxalis acetosella"
    return {"taxon": TAXON, "files": []}


def _build_ok(sandbox: Path, plant_id: str) -> dict[str, Any]:
    previews = sandbox / "models" / "previews" / "plants"
    previews.mkdir(parents=True, exist_ok=True)
    (previews / f"{plant_id}.png").write_bytes(_png())
    assets = sandbox / "assets" / "plants"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / f"{plant_id}.glb").write_bytes(b"glb-bytes")
    return {"ok": True, "summary": "wood_sorrel: 40 triangles", "log": "BUILT"}


def test_skips_when_no_region_names_plants(tmp_path: Path) -> None:
    """Items that name no plants skip survey, sandbox and calls."""
    world = _world(tmp_path)
    result = PlantsStage().run(_ctx(tmp_path, world))
    assert result == {
        "plant_files": [],
        "rounds": {},
        "reused": {},
        "expert_review": [],
    }
    assert not (tmp_path / "sandbox").exists()

    _inputs(tmp_path, plants=[])
    result = PlantsStage().run(_ctx(tmp_path, world))
    assert result["plant_files"] == []


def test_animal_item_builds_no_plants(tmp_path: Path) -> None:
    """#180.3: an animal item makes no plant call, even naming one."""
    world = _world(tmp_path)
    _inputs(tmp_path)
    (tmp_path / "home.json").write_text(
        json.dumps({"species_key": 1234, "id": "whale", "latin": "X"})
    )
    chat = ScriptChat([])
    result = PlantsStage(chat=chat).run(_ctx(tmp_path, world))
    assert result == {
        "plant_files": [],
        "rounds": {},
        "reused": {},
        "expert_review": [],
    }
    assert chat.prompts == []
    assert not (tmp_path / "sandbox").exists()


def test_builds_one_new_plant(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    chat = ScriptChat([json.dumps(SPEC), json.dumps(REVIEW_CLEAN)])
    result = PlantsStage(match=_match_new, chat=chat, build=_build_ok).run(
        _ctx(tmp_path, world)
    )
    assert result["plant_files"] == ["models/plants/wood_sorrel.json"]
    assert result["rounds"] == {"wood_sorrel": 1}
    assert result["reused"] == {}
    assert result["expert_review"] == []
    saved = tmp_path / "models" / "plants" / "wood_sorrel.json"
    final = json.loads(saved.read_text())
    assert final["taxon"] == TAXON
    assert (tmp_path / "models" / "plants" / "wood_sorrel.glb").is_file()
    assert "Oxalis" not in chat.prompts[0], "latin comes from the draft, not in"
    assert "bilberry" in chat.prompts[0]
    assert "wood_anemone" in chat.prompts[0]
    assert "Wood anemone" in chat.prompts[1], "ground plants bar the anemone"
    bar = tmp_path / "references" / "wood_sorrel" / "closest-wood_anemone.png"
    assert bar.is_file()
    assert "Real species only" in chat.prompts[0]
    assert "40 triangles" in chat.prompts[1], "review sees the build stats"


def test_builds_a_layered_water_plant_keeping_its_grounds(tmp_path: Path) -> None:
    """#184.2: a layered water id builds, keeping the start plant's grounds."""
    from builder.stages.plants import _keep_grows_on

    world = _world(tmp_path)
    schema_path = world / "schemas" / "model.schema.json"
    schema = json.loads(schema_path.read_text())
    schema["$defs"]["plant_model"]["properties"]["grows"] = {}
    schema_path.write_text(json.dumps(schema))
    (world / "models" / "plants" / "closed_brain_coral.json").write_text(
        json.dumps({"grows": {"on": ["reef"], "density": 0.08}})
    )
    (tmp_path / "survey.json").write_text(json.dumps({"facts": [], "species": []}))
    regions = tmp_path / "regions"
    regions.mkdir(parents=True)
    (regions / "xx_test_reef.json").write_text(
        json.dumps(
            {
                "id": "xx_test_reef",
                "biome": "coral_reef",
                "plants": {"water": [{"id": "table_coral", "density": 0.2}]},
            }
        )
    )
    (tmp_path / "home.json").write_text(
        json.dumps({"plant": "table_coral", "from": "closed_brain_coral"})
    )
    draft = dict(
        SPEC,
        id="table_coral",
        name="Table coral",
        latin="Acropora hyacinthus",
        kind="bush",
        grows={"on": ["seabed"], "density": 0.2},
    )
    review = {"species": "table_coral", "round": 1, "issues": []}
    chat = ScriptChat([json.dumps(draft), json.dumps(review)])

    def match(world: Path, latin: str) -> dict[str, Any]:
        assert latin == "Acropora hyacinthus"
        return {"taxon": TAXON, "files": []}

    result = PlantsStage(match=match, chat=chat, build=_build_ok).run(
        _ctx(tmp_path, world)
    )
    assert result["plant_files"] == ["models/plants/table_coral.json"]
    final = json.loads(
        (tmp_path / "models" / "plants" / "table_coral.json").read_text()
    )
    assert final["grows"]["on"] == ["reef"]
    assert _keep_grows_on(world, None, {"grows": {"on": ["seabed"]}})["grows"][
        "on"
    ] == ["seabed"]
    assert _keep_grows_on(world, "missing_plant", {"id": "x"}) == {"id": "x"}


def test_known_species_reuses_its_spec(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    (world / "models" / "plants" / "sorrel.json").write_text(json.dumps(SPEC))

    def match(world: Path, latin: str) -> dict[str, Any]:
        return {"taxon": TAXON, "files": ["models/plants/sorrel.json"]}

    chat = ScriptChat([json.dumps(SPEC)])
    result = PlantsStage(match=match, chat=chat, build=_build_ok).run(
        _ctx(tmp_path, world)
    )
    assert result["plant_files"] == []
    assert result["reused"] == {"wood_sorrel": "sorrel"}
    region = json.loads((tmp_path / "regions" / "xx_test.json").read_text())
    models = [p["model"] for p in region["ground_cover"]["plants"]]
    assert models == ["sorrel"]
    assert not (tmp_path / "models").exists(), "no build for a reuse"


def test_plant_item_missing_from_region_fails_loud(tmp_path: Path) -> None:
    """#215.2: a plant item the region never names stops here, not at ship."""
    world = _world(tmp_path)
    _inputs(tmp_path, plants=[])
    (tmp_path / "home.json").write_text(
        json.dumps({"plant": "table_coral", "from": "closed_brain_coral"})
    )
    pipeline = Pipeline([PlantsStage(match=_match_new)], tmp_path / "state.json")
    with pytest.raises(StageFailedError, match="stage plants failed"):
        pipeline.run(_ctx(tmp_path, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "table_coral" in state["failed"]["error"]
    assert "nothing to start from" in state["failed"]["error"]


def test_fourth_new_plant_stops(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(
        tmp_path,
        [
            {"model": f"plant_{n}", "names": [f"plant {n}"], "density": 0.1}
            for n in range(4)
        ],
    )
    pipeline = Pipeline([PlantsStage(match=_match_new)], tmp_path / "state.json")
    with pytest.raises(StageFailedError, match="stage plants failed"):
        pipeline.run(_ctx(tmp_path, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "at most 3" in state["failed"]["error"]


def test_checker_error_fixed_next_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    monkeypatch.setenv("SEEN", str(tmp_path / "seen"))
    (world / "tools" / "check_draft.py").write_text(
        "import json, os\n"
        "seen = os.path.exists(os.environ.get('SEEN', '/none'))\n"
        "open(os.environ.get('SEEN', '/none'), 'w').write('x')\n"
        "problems = [] if seen else ['FAIL models/plants/wood_sorrel.json', "
        "'- \\'year.leaves\\' must be evergreen or deciduous']\n"
        "print(json.dumps({'ok': not problems, 'results': ["
        "{'check': 'models', 'status': 'fail' if problems else 'pass', "
        "'problems': problems}]}))"
    )
    chat = ScriptChat(
        [
            json.dumps(SPEC),
            json.dumps(REVIEW_CLEAN),
            json.dumps(SPEC),
            json.dumps({**REVIEW_CLEAN, "round": 2}),
        ]
    )
    result = PlantsStage(match=_match_new, chat=chat, build=_build_ok).run(
        _ctx(tmp_path, world)
    )
    assert result["rounds"] == {"wood_sorrel": 2}
    assert "year.leaves" in chat.prompts[2]


def test_review_blocking_fixed_next_round(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    blocking = {
        "species": "wood_sorrel",
        "round": 1,
        "issues": [
            {
                "part": "leaves",
                "problem": "Missing.",
                "severity": "blocking",
                "fix": "Add them.",
            }
        ],
    }
    chat = ScriptChat(
        [
            json.dumps(SPEC),
            json.dumps(blocking),
            json.dumps(SPEC),
            json.dumps({**REVIEW_CLEAN, "round": 2}),
        ]
    )
    result = PlantsStage(match=_match_new, chat=chat, build=_build_ok).run(
        _ctx(tmp_path, world)
    )
    assert result["rounds"] == {"wood_sorrel": 2}
    assert "Missing." in chat.prompts[2]
    assert "Simple, not realistic" in chat.prompts[1]
    assert "never `blocking`" in chat.prompts[1]


def test_review_survives_empty_answers(tmp_path: Path) -> None:
    """#275.5: an empty review answer retries instead of failing the plant."""
    world = _world(tmp_path)
    preview = tmp_path / "preview.png"
    preview.write_bytes(_png())
    chat = ScriptChat(["", "", json.dumps(REVIEW_CLEAN)])
    review = PlantsStage(chat=chat)._read_review(
        _ctx(tmp_path, world), "wood_sorrel", 1, "prompt", [preview]
    )
    assert review["issues"] == []
    assert len(chat.prompts) == 3


def test_review_gives_up_after_repeated_empties(tmp_path: Path) -> None:
    """#275.5: the reviewer still stops, naming the last failure."""
    world = _world(tmp_path)
    preview = tmp_path / "preview.png"
    preview.write_bytes(_png())
    chat = ScriptChat(["", "", "", ""])
    with pytest.raises(_Stopped, match="never wrote a valid review"):
        PlantsStage(chat=chat)._read_review(
            _ctx(tmp_path, world), "wood_sorrel", 1, "prompt", [preview]
        )
    assert len(chat.prompts) == 4


def test_rounds_cap(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    blocking = {
        "species": "wood_sorrel",
        "round": 1,
        "issues": [
            {
                "part": "leaves",
                "problem": "Missing.",
                "severity": "blocking",
                "fix": "Add them.",
            }
        ],
    }
    answers = []
    for round_no in range(1, 7):
        answers.append(json.dumps(SPEC))
        answers.append(json.dumps({**blocking, "round": round_no}))
    chat = ScriptChat(answers)
    pipeline = Pipeline(
        [PlantsStage(match=_match_new, chat=chat, build=_build_ok)],
        tmp_path / "state.json",
    )
    with pytest.raises(StageFailedError, match="stage plants failed"):
        pipeline.run(_ctx(tmp_path, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "still failing after 6 rounds" in state["failed"]["error"]


def test_silent_build_stops_at_once(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    chat = ScriptChat([json.dumps(SPEC)])

    def silent(sandbox: Path, plant_id: str) -> dict[str, Any]:
        return {"ok": False, "summary": "no build", "log": ""}

    pipeline = Pipeline(
        [PlantsStage(match=_match_new, chat=chat, build=silent)],
        tmp_path / "state.json",
    )
    with pytest.raises(StageFailedError, match="stage plants failed"):
        pipeline.run(_ctx(tmp_path, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "with no output" in state["failed"]["error"]
    assert len(chat.prompts) == 1, "no fix rounds on an environment failure"


def test_food_plant_flagged_for_expert(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(
        tmp_path,
        [
            {
                "model": "wood_sorrel",
                "names": ["wood sorrel"],
                "density": 0.2,
                "food": {"kind": "berries", "part": "leaf"},
            }
        ],
    )
    chat = ScriptChat([json.dumps(SPEC), json.dumps(REVIEW_CLEAN)])
    result = PlantsStage(match=_match_new, chat=chat, build=_build_ok).run(
        _ctx(tmp_path, world)
    )
    assert result["expert_review"] == ["wood_sorrel"]


def test_schema_break_fixed_next_round(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    warned = dict(SPEC, warning="Oxalic acid: don't eat much.")
    chat = ScriptChat(
        [
            json.dumps(warned),
            json.dumps(SPEC),
            json.dumps({**REVIEW_CLEAN, "round": 2}),
        ]
    )
    result = PlantsStage(match=_match_new, chat=chat, build=_build_ok).run(
        _ctx(tmp_path, world)
    )
    assert result["rounds"] == {"wood_sorrel": 2}
    assert "plant schema" in chat.prompts[1]


def test_empty_draft_retries_once(tmp_path: Path) -> None:
    """#140.4: an empty draft retries once, like a cut one."""
    world = _world(tmp_path)
    _inputs(tmp_path)
    chat = ScriptChat(["", json.dumps(SPEC), json.dumps(REVIEW_CLEAN)])
    result = PlantsStage(match=_match_new, chat=chat, build=_build_ok).run(
        _ctx(tmp_path, world)
    )
    assert result["plant_files"] == ["models/plants/wood_sorrel.json"]
    assert result["rounds"] == {"wood_sorrel": 1}
    assert len(chat.prompts) == 3


def test_empty_draft_twice_stops(tmp_path: Path) -> None:
    """#140.4: two empty drafts stop, as one bad draft did."""
    world = _world(tmp_path)
    _inputs(tmp_path)
    chat = ScriptChat(["", ""])
    with pytest.raises(_Stopped, match="was not JSON"):
        PlantsStage(match=_match_new, chat=chat, build=_build_ok).run(
            _ctx(tmp_path, world)
        )
    assert len(chat.prompts) == 2


def test_cut_draft_retries_with_doubled_room(tmp_path: Path) -> None:
    """#275: a length-cut draft retries once with room, like #166.1."""

    class CutChat:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def __call__(self, messages: list[dict], **kwargs: Any) -> SimpleNamespace:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                message = SimpleNamespace(content="", tool_calls=None)
                choice = SimpleNamespace(message=message, finish_reason="length")
                usage = SimpleNamespace(completion_tokens=8000, output_tokens=0)
                return SimpleNamespace(
                    model="fake-test-model", choices=[choice], usage=usage
                )
            text = (
                json.dumps(SPEC) if len(self.calls) == 2 else json.dumps(REVIEW_CLEAN)
            )
            message = SimpleNamespace(content=text, tool_calls=None)
            choice = SimpleNamespace(message=message, finish_reason="stop")
            return SimpleNamespace(model="fake-test-model", choices=[choice])

    world = _world(tmp_path)
    _inputs(tmp_path)
    chat = CutChat()
    result = PlantsStage(match=_match_new, chat=chat, build=_build_ok).run(
        _ctx(tmp_path, world)
    )
    assert result["plant_files"] == ["models/plants/wood_sorrel.json"]
    assert chat.calls[1]["max_tokens"] == 16384


def test_poison_notes_flagged_for_expert(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    noted = dict(SPEC, year={**SPEC["year"], "notes": "Poisonous in large amounts."})
    chat = ScriptChat([json.dumps(noted), json.dumps(REVIEW_CLEAN)])
    result = PlantsStage(match=_match_new, chat=chat, build=_build_ok).run(
        _ctx(tmp_path, world)
    )
    assert result["expert_review"] == ["wood_sorrel"]


def test_listed_plant_flagged_for_expert(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)

    def match(world: Path, latin: str) -> dict[str, Any]:
        return {
            "taxon": {"gbif_key": 999, "rank": "SPECIES", "name": latin},
            "files": [],
        }

    chat = ScriptChat([json.dumps(SPEC), json.dumps(REVIEW_CLEAN)])
    result = PlantsStage(match=match, chat=chat, build=_build_ok).run(
        _ctx(tmp_path, world)
    )
    assert result["expert_review"] == ["wood_sorrel"]


def test_missing_preview_line_skipped(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    (world / "tools" / "check_draft.py").write_text(
        "import json;print(json.dumps({'ok': False, 'results': ["
        "{'check': 'models', 'status': 'fail', 'problems': ["
        "'ERROR: wood_sorrel: the preview sheet is missing: run "
        "tools/build_model.sh models/plants/wood_sorrel.json']}]}))"
    )
    chat = ScriptChat([json.dumps(SPEC), json.dumps(REVIEW_CLEAN)])
    result = PlantsStage(match=_match_new, chat=chat, build=_build_ok).run(
        _ctx(tmp_path, world)
    )
    assert result["rounds"] == {"wood_sorrel": 1}


def test_unowned_problem_stops(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _inputs(tmp_path)
    (world / "tools" / "check_draft.py").write_text(
        "import json;print(json.dumps({'ok': False, 'results': ["
        "{'check': 'models', 'status': 'fail', "
        "'problems': ['ERROR: bilberry is on fire']}]}))"
    )
    chat = ScriptChat([json.dumps(SPEC)])
    pipeline = Pipeline(
        [PlantsStage(match=_match_new, chat=chat, build=_build_ok)],
        tmp_path / "state.json",
    )
    with pytest.raises(StageFailedError, match="stage plants failed"):
        pipeline.run(_ctx(tmp_path, world))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "outside the new plant" in state["failed"]["error"]


def _reef_tile() -> dict[str, Any]:
    """The reef tile's fields the converter reads, as the maker lays them."""
    return {
        "id": "z10_0927_0560",
        "kind": "code",
        "version": 30,
        "center": {"lat": -16.8, "lon": 146.07},
        "level": 5,
        "height": 32,
        "habitat": "sea",
        "home": "coral_reef",
        "biome": "coral_reef",
        "seed": 901855012,
        "water": 0.0,
        "species": {
            "playable": [
                "clownfish",
                "blue_tang",
                "butterflyfish",
                "lionfish",
                "green_sea_turtle",
                "reef_shark",
                "bottlenose_dolphin",
            ],
            "prey": ["clownfish", "blue_tang", "butterflyfish"],
            "predators": ["reef_shark", "lionfish"],
        },
        "plants": {
            "water": [
                {"id": "branching_staghorn_coral", "density": 0.34},
                {"id": "closed_brain_coral", "density": 0.3},
                {"id": "giant_sea_fan", "density": 0.26},
                {"id": "halimeda", "density": 0.08},
                {"id": "dugong_grass", "density": 0.25},
                {"id": "paddle_grass", "density": 0.25},
            ]
        },
    }


_CORAL_SPECS = (
    "branching_staghorn_coral",
    "closed_brain_coral",
    "giant_sea_fan",
    "halimeda",
    "dugong_grass",
    "paddle_grass",
)


def _write_reef_job(tmp_path: Path, checkout: Path) -> Path:
    """The table_coral enrich item's inputs, converted for real."""
    import shutil

    from builder.enrich import tile_place

    region = tile_place(
        _reef_tile(),
        checkout,
        "table_coral",
        "plant",
        start="branching_staghorn_coral",
    )
    assert region["biome"] == "coral_reef"
    regions = tmp_path / "regions"
    regions.mkdir(parents=True)
    (regions / "tl_z10_0927_0560.json").write_text(json.dumps(region))
    checks = tmp_path / "checks"
    checks.mkdir(parents=True)
    (checks / "enrich_places.json").write_text(
        json.dumps({"places": ["tl_z10_0927_0560"]})
    )
    (tmp_path / "survey.json").write_text(json.dumps({"facts": [], "species": []}))
    (tmp_path / "home.json").write_text(
        json.dumps(
            {
                "plant": "table_coral",
                "names": ["table coral"],
                "from": "branching_staghorn_coral",
            }
        )
    )
    world = _world(tmp_path)
    schema_path = world / "schemas" / "model.schema.json"
    schema = json.loads(schema_path.read_text())
    schema["$defs"]["plant_model"]["properties"]["grows"] = {}
    schema_path.write_text(json.dumps(schema))
    for stem in _CORAL_SPECS:
        shutil.copy(
            checkout / "models" / "plants" / f"{stem}.json",
            world / "models" / "plants" / f"{stem}.json",
        )
    return world


def _coral_draft() -> dict[str, Any]:
    return dict(
        SPEC,
        id="table_coral",
        name="Table coral",
        latin="Acropora hyacinthus",
        kind="bush",
        grows={"on": ["seabed"], "density": 0.2},
    )


#: What job _4 drew: a triangular plate in beige-grey (#221.1).
_TRIANGLE_PARTS = [
    {
        "name": "plate",
        "shape": "cone",
        "size": [0.5, 0.1, 0.5],
        "at": [0, 0.2, 0],
        "color": "plate",
        "detail": 0.4,
    },
    {
        "name": "stalk",
        "shape": "cylinder",
        "size": [0.09, 0.18, 0.09],
        "at": [0, 0.09, 0],
        "color": "stalk",
        "detail": 0.5,
    },
]
_TRIANGLE_COLORS = {"plate": "#a8a49c", "stalk": "#8a8578"}

#: The hand fix: a round plate in ochre with a lighter rim (#221.1-2).
_FIXED_PARTS = [
    {
        "name": "stalk",
        "shape": "cylinder",
        "size": [0.09, 0.18, 0.09],
        "at": [0, 0.09, 0],
        "color": "stalk",
        "detail": 0.5,
    },
    {
        "name": "plate",
        "shape": "cylinder",
        "size": [0.62, 0.06, 0.62],
        "at": [0, 0.21, 0],
        "color": "plate",
        "detail": 1.0,
    },
    {
        "name": "rim",
        "shape": "cylinder",
        "size": [0.66, 0.025, 0.66],
        "at": [0, 0.21, 0],
        "color": "rim",
        "detail": 1.0,
    },
]
_FIXED_COLORS = {"plate": "#a86f35", "rim": "#d9b77c", "stalk": "#6e4a2a"}

#: The surveyed shape the looks check holds the render to (#221.3).
_CORAL_LOOKS = "wide, flat, round or oval plates on a short stalk"


def _coral_triangle() -> dict[str, Any]:
    """The job's spec as made: names, sources and seating kept (#221.1)."""
    return dict(_coral_draft(), parts=_TRIANGLE_PARTS, colors=_TRIANGLE_COLORS)


def _coral_fixed() -> dict[str, Any]:
    """The hand fix: only parts and colors change (#221.1-2)."""
    return dict(_coral_draft(), parts=_FIXED_PARTS, colors=_FIXED_COLORS)


def _match_coral(world: Path, latin: str) -> dict[str, Any]:
    assert latin == "Acropora hyacinthus"
    return {"taxon": TAXON, "files": []}


def _file_into_reef(tmp_path: Path, checkout: Path, final: dict[str, Any]) -> Path:
    """File the built coral; the reef ground stays within budget."""
    import shutil

    from builder.biome import join_plant, plant_ground

    filed = tmp_path / "fileworld"
    (filed / "tools").mkdir(parents=True)
    for tool in ("biomes.py", "layers.py", "food.py"):
        shutil.copy(checkout / "tools" / tool, filed / "tools" / tool)
    shutil.copytree(checkout / "food", filed / "food")
    (filed / "animals").mkdir(parents=True)
    (filed / "models" / "trees").mkdir(parents=True)
    (filed / "models" / "plants").mkdir(parents=True)
    (filed / "lands").mkdir(parents=True)
    shutil.copy(
        checkout / "lands" / "tropical_moist.json",
        filed / "lands" / "tropical_moist.json",
    )
    for stem in _CORAL_SPECS:
        shutil.copy(
            checkout / "models" / "plants" / f"{stem}.json",
            filed / "models" / "plants" / f"{stem}.json",
        )
    (filed / "models" / "plants" / "table_coral.json").write_text(json.dumps(final))
    (filed / "biomes").mkdir(parents=True)
    shutil.copy(
        checkout / "biomes" / "coral_reef.json",
        filed / "biomes" / "coral_reef.json",
    )
    (filed / "homes").mkdir(parents=True)
    report = join_plant(filed, "coral_reef", "table_coral")
    assert report["total"] <= 1.0
    biome = json.loads((filed / "biomes" / "coral_reef.json").read_text())
    specs = {
        stem: json.loads((filed / "models" / "plants" / f"{stem}.json").read_text())
        for stem in (*_CORAL_SPECS, "table_coral")
    }
    reef = sum(
        entry["density"]
        for entry in biome["plants"]["water"]
        if plant_ground(specs[entry["id"]]["grows"]) == "reef"
    )
    assert reef <= 1.0
    assert "table_coral" in {entry["id"] for entry in biome["plants"]["water"]}
    return filed


def _checkout() -> Path:
    """The world checkout beside the builder, or skip the replay."""
    checkout = Path(__file__).resolve().parent.parent.parent / "animundo-world"
    if not (checkout / "tools" / "tile_region.py").is_file():
        pytest.skip("needs a world checkout beside the builder")
    return checkout


def test_replay_table_coral_on_reef_tile(tmp_path: Path) -> None:
    """#215.3: convert, build from a coral, file; the reef stays in budget."""
    checkout = _checkout()
    world = _write_reef_job(tmp_path, checkout)
    draft = _coral_draft()
    review = {"species": "table_coral", "round": 1, "issues": []}
    chat = ScriptChat([json.dumps(draft), json.dumps(review)])
    result = PlantsStage(match=_match_coral, chat=chat, build=_build_ok).run(
        _ctx(tmp_path, world)
    )
    assert result["plant_files"] == ["models/plants/table_coral.json"]
    final = json.loads(
        (tmp_path / "models" / "plants" / "table_coral.json").read_text()
    )
    assert final["grows"]["on"] == ["reef"]
    _file_into_reef(tmp_path, checkout, final)


def test_replay_table_coral_round_plate_and_seating(tmp_path: Path) -> None:
    """#221.5: the triangle fails looks; the fixed round plate ships seated."""
    from fake_store import FakeStore

    from builder.biome import plant_ground, remake_biome_tiles

    checkout = _checkout()
    world = _write_reef_job(tmp_path, checkout)
    (tmp_path / "survey.json").write_text(
        json.dumps(
            {
                "facts": [],
                "species": [
                    {"species": "table_coral", "description": _CORAL_LOOKS},
                ],
            }
        )
    )
    shape_issue = {
        "species": "table_coral",
        "round": 1,
        "issues": [
            {
                "part": "plate",
                "problem": (
                    "drawn as a triangle; the survey describes round, flat plates"
                ),
                "severity": "blocking",
                "fix": "rebuild the plate as a wide flat round cylinder",
            }
        ],
    }
    clean = {"species": "table_coral", "round": 2, "issues": []}
    chat = ScriptChat(
        [
            json.dumps(_coral_triangle()),
            json.dumps(shape_issue),
            json.dumps(_coral_fixed()),
            json.dumps(clean),
        ]
    )
    result = PlantsStage(match=_match_coral, chat=chat, build=_build_ok).run(
        _ctx(tmp_path, world)
    )
    assert result["plant_files"] == ["models/plants/table_coral.json"]
    assert result["rounds"] == {"table_coral": 2}
    assert _CORAL_LOOKS in chat.prompts[1]
    final = json.loads(
        (tmp_path / "models" / "plants" / "table_coral.json").read_text()
    )
    by_part = {part["name"]: part for part in final["parts"]}
    assert by_part["plate"]["shape"] == "cylinder"
    assert final["colors"] == _FIXED_COLORS
    assert final["name"] == "Table coral"
    assert final["sources"] == SPEC["sources"]
    assert final["grows"]["on"] == ["reef"]
    filed = _file_into_reef(tmp_path, checkout, final)
    store = FakeStore()
    tile = json.loads(
        (
            Path(__file__).resolve().parent / "examples" / "valid" / "tile.json"
        ).read_text()
    )
    tile["id"] = "z10_0927_0560"
    tile["home"] = "coral_reef"
    tile["biome"] = "coral_reef"
    pre = json.loads((checkout / "biomes" / "coral_reef.json").read_text())["plants"][
        "water"
    ]
    tile["plants"] = {"water": [dict(entry) for entry in pre]}
    store.blobs["tiles/v2/index.json"] = json.dumps(
        {
            "format": 1,
            "made_at": "2026-11-02",
            "world": "abc",
            "groups": {"z10/115_070": 7},
            "counts": {"ready": 1, "blocked": {}},
        }
    ).encode("utf-8")
    store.blobs["tiles/v2/z10/115_070.json"] = json.dumps(
        {
            "format": 1,
            "grid": "z10",
            "group": [115, 70],
            "version": 7,
            "tiles": {"z10_0927_0560": tile},
            "blocked": {},
        }
    ).encode("utf-8")
    out = remake_biome_tiles(store, filed, "coral_reef", "table_coral", "abc123")
    assert out["held"] == []
    data = json.loads(store.read_blob("tiles/v2/z10/115_070.json")[0].decode("utf-8"))
    grown = data["tiles"]["z10_0927_0560"]["plants"]["water"]
    joined = json.loads((filed / "biomes" / "coral_reef.json").read_text())["plants"][
        "water"
    ]
    assert grown == joined
    specs = {
        stem: json.loads((filed / "models" / "plants" / f"{stem}.json").read_text())
        for stem in (*_CORAL_SPECS, "table_coral")
    }
    reef = sum(
        entry["density"]
        for entry in grown
        if plant_ground(specs[entry["id"]]["grows"]) == "reef"
    )
    assert reef <= 1.0


#: What job _5 drew: a square plate in pale beige (#234.3).
_SQUARE_PARTS = [
    {
        "name": "stalk",
        "shape": "cylinder",
        "size": [0.09, 0.18, 0.09],
        "at": [0, 0.09, 0],
        "color": "base",
        "detail": 0.6,
    },
    {
        "name": "plate",
        "shape": "cylinder",
        "size": [0.68, 0.04, 0.68],
        "at": [0, 0.18, 0],
        "color": "plate",
        "detail": 0.5,
    },
    {
        "name": "plate_top",
        "shape": "cylinder",
        "size": [0.62, 0.028, 0.62],
        "at": [0, 0.202, 0],
        "color": "plate_top",
        "detail": 0.5,
    },
]
_SQUARE_COLORS = {"base": "#9f9180", "plate": "#c8bca6", "plate_top": "#d9ccb3"}


def _coral_square() -> dict[str, Any]:
    """The job's spec as made: names, sources and seating kept (#234.3)."""
    return dict(_coral_draft(), parts=_SQUARE_PARTS, colors=_SQUARE_COLORS)


def test_replay_table_coral_square_plate_gets_the_round_fix(tmp_path: Path) -> None:
    """#234.3: the square pale plate replays into the round ochre one."""
    checkout = _checkout()
    world = _write_reef_job(tmp_path, checkout)
    (tmp_path / "survey.json").write_text(
        json.dumps(
            {
                "facts": [],
                "species": [
                    {"species": "table_coral", "description": _CORAL_LOOKS},
                ],
            }
        )
    )
    shape_issue = {
        "species": "table_coral",
        "round": 1,
        "issues": [
            {
                "part": "plate",
                "problem": (
                    "drawn as a square; the survey describes round, flat plates"
                ),
                "severity": "blocking",
                "fix": "rebuild the plate as a wide flat round cylinder",
            },
            {
                "part": "plate",
                "problem": "pale beige, too close to the water's tint",
                "severity": "blocking",
                "fix": "warm ochre plate with a lighter rim",
            },
        ],
    }
    clean = {"species": "table_coral", "round": 2, "issues": []}
    chat = ScriptChat(
        [
            json.dumps(_coral_square()),
            json.dumps(shape_issue),
            json.dumps(_coral_fixed()),
            json.dumps(clean),
        ]
    )
    result = PlantsStage(match=_match_coral, chat=chat, build=_build_ok).run(
        _ctx(tmp_path, world)
    )
    assert result["plant_files"] == ["models/plants/table_coral.json"]
    assert result["rounds"] == {"table_coral": 2}
    assert _CORAL_LOOKS in chat.prompts[1]
    final = json.loads(
        (tmp_path / "models" / "plants" / "table_coral.json").read_text()
    )
    by_part = {part["name"]: part for part in final["parts"]}
    assert by_part["plate"]["shape"] == "cylinder"
    assert by_part["plate"]["detail"] == 1.0
    assert final["colors"] == _FIXED_COLORS
    assert final["name"] == "Table coral"
    assert final["sources"] == SPEC["sources"]
    assert final["grows"]["on"] == ["reef"]
    _file_into_reef(tmp_path, checkout, final)


def _big_png() -> bytes:
    """A 1280x960 PNG the size of a real preview, incompressible."""
    import os

    shot = Image.new("RGB", (1280, 960))
    shot.frombytes(os.urandom(1280 * 960 * 3))
    buf = io.BytesIO()
    shot.save(buf, format="PNG")
    return buf.getvalue()


def _build_big(sandbox: Path, plant_id: str) -> dict[str, Any]:
    """The build, leaving a full-size preview like the real one."""
    previews = sandbox / "models" / "previews" / "plants"
    previews.mkdir(parents=True, exist_ok=True)
    (previews / f"{plant_id}.png").write_bytes(_big_png())
    assets = sandbox / "assets" / "plants"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / f"{plant_id}.glb").write_bytes(b"glb-bytes")
    return {"ok": True, "summary": f"{plant_id}: 40 triangles", "log": "BUILT"}


class _Script:
    """Canned HTTP answers for the real client, recording requests."""

    def __init__(self, answers: list[tuple[int, dict[str, Any]]]) -> None:
        import httpx

        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: Any) -> Any:
        import httpx

        self.requests.append(request)
        status, body = self.answers.pop(0)
        return httpx.Response(status, json=body)


def _chat_body(content: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": "muse-spark-1.3",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 50, "completion_tokens": 60, "total_tokens": 110},
    }


def test_replay_table_coral_under_penny_cap(tmp_path: Path) -> None:
    """#217.4: the capped replay gets past plants and ships on the reef."""
    import httpx

    from builder.ledger import Caps, FileLedger
    from builder.meta import CostLogger, MetaClient

    checkout = _checkout()
    world = _write_reef_job(tmp_path, checkout)
    (world / "models" / "previews" / "plants" / "bilberry.png").write_bytes(_big_png())
    draft = _coral_draft()
    review = {"species": "table_coral", "round": 1, "issues": []}
    naive = (
        (len(json.dumps(draft)) + 2 * len(_big_png()) * 4 // 3) // 4 * 1.25 / 1_000_000
    )
    assert naive > 1.0
    script = _Script(
        [(200, _chat_body(json.dumps(draft))), (200, _chat_body(json.dumps(review)))]
    )
    ledger = FileLedger(tmp_path / "ledger.json")
    client = MetaClient(
        api_key="fake-test-key",
        model="muse-spark-1.3",
        logger=CostLogger(tmp_path / "cost.jsonl"),
        ledger=ledger,
        job_id="replay/plant_table_coral",
        caps=Caps(1.0, 20.0, 120.0),
        http_client=httpx.Client(transport=httpx.MockTransport(script)),
        sleeper=lambda seconds: None,
    )
    result = PlantsStage(match=_match_coral, chat=client.chat, build=_build_big).run(
        _ctx(tmp_path, world)
    )
    assert result["plant_files"] == ["models/plants/table_coral.json"]
    final = json.loads(
        (tmp_path / "models" / "plants" / "table_coral.json").read_text()
    )
    assert final["grows"]["on"] == ["reef"]
    assert ledger.job_spent("replay/plant_table_coral") < 0.01
    _file_into_reef(tmp_path, checkout, final)
