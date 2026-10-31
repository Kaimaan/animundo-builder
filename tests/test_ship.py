"""The ship stage: Finnish, safety, the gates, and the pack. No network."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from builder.packs import check_additions, check_pack
from builder.stages.ship import ShipStage, _Stopped, gate_checks

TILE = "s_530_612"
REGION = "xx_reef"


def _check_draft(results: list[dict[str, Any]]) -> str:
    body = json.dumps({"results": results})
    return f"import json, sys\nprint({body!r})\n"


def _world(tmp_path: Path, results: list[dict[str, Any]] | None = None) -> Path:
    world = tmp_path / "world"
    (world / "animals").mkdir(parents=True)
    (world / "animals" / "clownfish.json").write_text(
        json.dumps({"id": "clownfish", "tags": ["fish", "small_fish"]})
    )
    (world / "animals" / "reef_shark.json").write_text(
        json.dumps(
            {"id": "reef_shark", "tags": ["fish", "big_fish"], "hunts": ["small_fish"]}
        )
    )
    (world / "regions").mkdir()
    (world / "regions" / f"{REGION}.json").write_text(json.dumps(_region()))
    (world / "locale").mkdir()
    (world / "locale" / "languages.json").write_text(
        json.dumps({"en": "English", "fi": "Finnish"})
    )
    (world / "tools").mkdir()
    (world / "tools" / "check_draft.py").write_text(
        _check_draft(results if results is not None else [])
    )
    return world


def _region() -> dict[str, Any]:
    return {
        "id": REGION,
        "name": "Test Reef",
        "description": "A reef for the ship tests.",
        "center": {"lat": -16.7, "lon": 146.0},
        "grid": {"grid": "ease2_s25", "tiles": [[530, 612]]},
        "terrain": {"water_level": 40, "sea_depth": 35},
        "food": [{"kind": "water_plants", "names": ["seagrass"]}],
        "fish": [{"kind": "clownfish", "count": 5}],
        "animals": {"playable": ["clownfish"], "prey": [], "predators": ["reef_shark"]},
    }


def _animal(**over: Any) -> dict[str, Any]:
    animal = {
        "id": "hammerhead",
        "text": {"name": "Scalloped hammerhead", "intro": "A shark with a wide head."},
        "model": {"archetype": "fish", "length": 3.5},
        "tags": ["fish", "big_fish"],
        "hunts": ["small_fish"],
        "year": {"winter": "active"},
        "cam_dist": 4.0,
        "cam_height": 0.5,
    }
    animal.update(over)
    return animal


def _research(**over: Any) -> dict[str, Any]:
    research = {
        "size": {"length_cm": 350},
        "year": {"winter": "active"},
        "main_food": {"name": "small fish", "kind": "prey"},
        "other_foods": [],
    }
    research.update(over)
    return research


def _item(tmp_path: Path, animal: dict[str, Any], research: dict[str, Any]) -> Path:
    job_dir = tmp_path / "item"
    job_dir.mkdir(exist_ok=True)
    (job_dir / "home.json").write_text(
        json.dumps(
            {
                "species_key": 1,
                "id": "hammerhead",
                "latin": "Sphyrna lewini",
                "home": "reef",
                "enrich": {"tiles": [TILE]},
            }
        )
    )
    (job_dir / "animals").mkdir(exist_ok=True)
    (job_dir / "animals" / "hammerhead.json").write_text(json.dumps(animal))
    (job_dir / "research.json").write_text(json.dumps(research))
    laid = _region()
    laid["fish"].append({"kind": "hammerhead", "count": 2})
    laid["animals"]["playable"].append("hammerhead")
    (job_dir / "regions").mkdir(exist_ok=True)
    (job_dir / "regions" / f"{REGION}.json").write_text(json.dumps(laid))
    review = job_dir / "tries" / "hammerhead" / "edits" / "1"
    review.mkdir(parents=True)
    (review / "marks_review.json").write_text(
        json.dumps({"shown": ["wide hammer head"], "missing": []})
    )
    (job_dir / "checks").mkdir(exist_ok=True)
    (job_dir / "checks" / "oneshot.json").write_text(
        json.dumps({"made": ["hammerhead"], "unmakable": {}})
    )
    (job_dir / "models").mkdir(exist_ok=True)
    (job_dir / "models" / "hammerhead.glb").write_bytes(b"glb-bytes")
    (job_dir / "models" / "hammerhead.png").write_bytes(b"png-bytes")
    texts = animal.get("text", {}) or {}
    (job_dir / "locale" / "en").mkdir(parents=True)
    (job_dir / "locale" / "en" / "animals.json").write_text(
        json.dumps({f"animal.hammerhead.{k}": v for k, v in texts.items()})
    )
    (job_dir / "locale" / "fi").mkdir(parents=True)
    (job_dir / "locale" / "fi" / "animals.json").write_text(
        json.dumps({f"animal.hammerhead.{k}": f"{v} (fi)" for k, v in texts.items()})
    )
    return job_dir


class _Chat:
    """Plays back canned answers in order."""

    def __init__(self, answers: list[str]) -> None:
        self._answers = [
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=a))],
            )
            for a in answers
        ]

    def __call__(self, messages: Any, **kwargs: Any) -> Any:
        return self._answers.pop(0)


class _Store:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def list_blobs(self, prefix: str) -> list[str]:
        return sorted(n for n in self.blobs if n.startswith(prefix))

    def write_blob(self, name: str, data: bytes, content_type: str = "") -> None:
        self.blobs[name] = data


def _ctx(job_dir: Path, world: Path, answers: list[str]) -> SimpleNamespace:
    from builder.stages.safety import collect_texts

    keys = [item["key"] for item in collect_texts(job_dir, world)]
    verdicts = {
        "verdicts": [
            {"key": key, "verdict": "pass", "reasons": [], "rule": "ok"} for key in keys
        ]
    }
    return SimpleNamespace(
        job_id="enrich_x/animal_hammerhead",
        job_dir=job_dir,
        world_path=world,
        check_budget=lambda: None,
        chat=_Chat(answers + [json.dumps(verdicts)]),
    )


def _passing_run(tmp_path: Path, store: _Store) -> tuple[Path, Path, dict[str, Any]]:
    world = _world(tmp_path)
    job_dir = _item(tmp_path, _animal(), _research())
    translated = {"name": "Kampavasarahai", "intro": "Hai, jolla on leveä pää."}
    result = ShipStage(store=store).run(_ctx(job_dir, world, [json.dumps(translated)]))
    return world, job_dir, result


def test_ship_publishes_a_passing_pack(tmp_path: Path) -> None:
    """#174.1, #176.1: a clean item ships `packs/animal_hammerhead/1/`."""
    store = _Store()
    _world, job_dir, result = _passing_run(tmp_path, store)
    assert result["pack"] == "packs/animal_hammerhead/1/"
    assert result["version"] == 1
    ship = json.loads((job_dir / "checks" / "ship.json").read_text())
    assert ship == {"status": "pass", "problems": []}
    pack = {
        name.removeprefix("packs/animal_hammerhead/1/"): data
        for name, data in store.blobs.items()
    }
    assert check_pack(pack) == []
    assert set(pack) == {
        "animals/hammerhead.json",
        "models/hammerhead.glb",
        "models/previews/hammerhead.png",
        "locale/en/animals.json",
        "locale/fi/animals.json",
        f"additions/{REGION}.json",
        "manifest.json",
    }
    additions = json.loads(pack[f"additions/{REGION}.json"].decode("utf-8"))
    assert check_additions(additions) == []
    assert additions == {
        "format": 1,
        "fish": [{"kind": "hammerhead", "count": 2}],
        "playable": ["hammerhead"],
    }
    en = json.loads(pack["locale/en/animals.json"].decode("utf-8"))
    fi = json.loads(pack["locale/fi/animals.json"].decode("utf-8"))
    assert en == {
        "animal.hammerhead.name": "Scalloped hammerhead",
        "animal.hammerhead.intro": "A shark with a wide head.",
    }
    assert fi["animal.hammerhead.name"] == "Kampavasarahai"
    assert (job_dir / "pack" / "manifest.json").is_file()


def test_ship_second_publish_makes_version_2(tmp_path: Path) -> None:
    """#176.4: a republish is version 2; version 1 stays as it was."""
    store = _Store()
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    _world, _job, first = _passing_run(tmp_path / "one", store)
    assert first["version"] == 1
    _world2, _job2, second = _passing_run(tmp_path / "two", store)
    assert second["version"] == 2
    assert second["pack"] == "packs/animal_hammerhead/2/"
    assert (
        store.blobs["packs/animal_hammerhead/1/manifest.json"]
        != store.blobs["packs/animal_hammerhead/2/manifest.json"]
    )


def test_ship_refuses_a_plant_item(tmp_path: Path) -> None:
    """Plant packs have no shape yet; they still ship by hand."""
    world = _world(tmp_path)
    job_dir = tmp_path / "item"
    job_dir.mkdir()
    (job_dir / "home.json").write_text(
        json.dumps({"plant": "kelp", "home": "reef", "enrich": {"tiles": [TILE]}})
    )
    ctx = SimpleNamespace(
        job_id="enrich_x/plant_kelp",
        job_dir=job_dir,
        world_path=world,
        check_budget=lambda: None,
        chat=_Chat([]),
    )
    with pytest.raises(_Stopped, match="ships by hand"):
        ShipStage(store=_Store()).run(ctx)


def _gate_problems(
    tmp_path: Path,
    animal: dict[str, Any] | None = None,
    research: dict[str, Any] | None = None,
    laid: dict[str, Any] | None = None,
    results: list[dict[str, Any]] | None = None,
) -> list[str]:
    world = _world(tmp_path, results)
    job_dir = _item(tmp_path, animal or _animal(), research or _research())
    if laid is not None:
        (job_dir / "regions" / f"{REGION}.json").write_text(json.dumps(laid))
    brief = json.loads((job_dir / "home.json").read_text())
    made = json.loads((job_dir / "animals" / "hammerhead.json").read_text())
    ctx = SimpleNamespace(job_dir=job_dir, world_path=world)
    return gate_checks(ctx, world, brief, "hammerhead", made)


def test_gate_passes_a_clean_item(tmp_path: Path) -> None:
    assert _gate_problems(tmp_path) == []


def test_gate_fails_a_region_that_rewrites_food(tmp_path: Path) -> None:
    """#174.5: the same job with food changed fails at the region check."""
    laid = _region()
    laid["fish"].append({"kind": "hammerhead", "count": 2})
    laid["animals"]["playable"].append("hammerhead")
    laid["food"] = [{"kind": "carrion", "names": ["leftovers"]}]
    problems = _gate_problems(tmp_path, laid=laid)
    assert problems == [f"region {REGION}: food changed"]


def test_gate_fails_the_world_checks(tmp_path: Path) -> None:
    results = [
        {"check": "animals", "status": "fail", "problems": ["hammerhead: no text"]}
    ]
    problems = _gate_problems(tmp_path, results=results)
    assert problems == ["world animals: hammerhead: no text"]


def test_gate_counts_not_built_as_unknown(tmp_path: Path) -> None:
    results = [
        {
            "check": "region",
            "status": "fail",
            "problems": ["staghorn: build it first with tools/build_model.sh"],
        }
    ]
    assert _gate_problems(tmp_path, results=results) == []


def test_gate_fails_a_record_length(tmp_path: Path) -> None:
    """#174.1: 4.97 m is the record; the researched adult is 3.5 m."""
    animal = _animal(model={"archetype": "fish", "length": 4.97}, cam_dist=6.0)
    problems = _gate_problems(tmp_path, animal=animal)
    assert problems == [
        f"{REGION}: length: 4.97 m is no typical adult (350 cm researched)"
    ]


def test_gate_fails_migration_against_the_research(tmp_path: Path) -> None:
    animal = _animal(year={"winter": "migrates"})
    problems = _gate_problems(tmp_path, animal=animal)
    assert problems == [
        f"{REGION}: year: the file says migrates, the research says active"
    ]


def test_gate_fails_a_departure_in_the_wrong_season(tmp_path: Path) -> None:
    migration = {"leaves": "11-01", "returns": "04-01"}
    research = _research(year={"winter": "migrates", "migration": migration})
    animal = _animal(year={"winter": "migrates", "migration": migration})
    problems = _gate_problems(tmp_path, animal=animal, research=research)
    assert problems == [
        f"{REGION}: year: it leaves in month 11, no southern autumn departure"
    ]


def test_gate_fails_a_hunt_no_reviewed_animal_hunts(tmp_path: Path) -> None:
    """#174.1: `fish` would hunt sharks and turtle-sized fish."""
    animal = _animal(hunts=["small_fish", "fish"])
    problems = _gate_problems(tmp_path, animal=animal)
    assert problems == [f"{REGION}: hunts: fish is no reviewed prey tag"]


def test_gate_fails_a_shark_no_fewer_than_the_small_fish(tmp_path: Path) -> None:
    laid = _region()
    laid["fish"].append({"kind": "hammerhead", "count": 5})
    laid["animals"]["playable"].append("hammerhead")
    problems = _gate_problems(tmp_path, laid=laid)
    assert problems == [f"{REGION}: fish: count 5 is no fewer than the small fish (5)"]


def test_gate_fails_a_camera_inside_the_body(tmp_path: Path) -> None:
    animal = _animal(cam_dist=2.0)
    problems = _gate_problems(tmp_path, animal=animal)
    assert problems == [f"{REGION}: camera: cam_dist 2.0 sits inside the body (3.5 m)"]


def test_gate_fails_a_camera_above_the_water(tmp_path: Path) -> None:
    animal = _animal(cam_height=40.0)
    problems = _gate_problems(tmp_path, animal=animal)
    assert problems == [f"{REGION}: camera: cam_height 40.0 is above the water (35 m)"]


def test_gate_fails_missing_marks(tmp_path: Path) -> None:
    world = _world(tmp_path)
    job_dir = _item(tmp_path, _animal(), _research())
    review = job_dir / "tries" / "hammerhead" / "edits" / "1" / "marks_review.json"
    review.write_text(json.dumps({"shown": [], "missing": ["wide hammer head"]}))
    brief = json.loads((job_dir / "home.json").read_text())
    made = json.loads((job_dir / "animals" / "hammerhead.json").read_text())
    ctx = SimpleNamespace(job_dir=job_dir, world_path=world)
    assert gate_checks(ctx, world, brief, "hammerhead", made) == [
        "render: wide hammer head missing from the views"
    ]


def test_ship_fails_unsafe_english_at_safety(tmp_path: Path) -> None:
    """#174.5: English made unsafe on purpose fails at the safety review."""
    from builder.stages.safety import _Stopped as _SafetyStopped
    from builder.stages.safety import collect_texts

    world = _world(tmp_path)
    animal = _animal(text={"name": "Scalloped hammerhead", "intro": "Bite them all."})
    job_dir = _item(tmp_path, animal, _research())
    translated = {"name": "Kampavasarahai", "intro": "Pure kaikkia."}
    keys = [item["key"] for item in collect_texts(job_dir, world)]
    verdicts = {
        "verdicts": [
            {
                "key": key,
                "verdict": "fail" if key.endswith(".intro") else "pass",
                "reasons": ["urges harm"] if key.endswith(".intro") else [],
                "rule": "ok",
            }
            for key in keys
        ]
    }
    ctx = SimpleNamespace(
        job_id="enrich_x/animal_hammerhead",
        job_dir=job_dir,
        world_path=world,
        check_budget=lambda: None,
        chat=_Chat([json.dumps(translated), json.dumps(verdicts)]),
    )
    with pytest.raises(_SafetyStopped, match="child-safety review failed"):
        ShipStage(store=_Store()).run(ctx)
    safety = json.loads((job_dir / "checks" / "safety.json").read_text())
    assert safety["failed"] == ["animal.hammerhead.intro"]
