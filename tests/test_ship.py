"""The ship stage: Finnish, safety, the gates, and the pack. No network."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from builder.packs import check_pack
from builder.stages.ship import ShipStage, _Stopped, gate_checks

TILE = "z10_0927_0560"
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
        "grid": {"grid": "z10", "tiles": [[927, 560]]},
        "terrain": {"water_level": 40, "sea_depth": 35},
        "food": [{"kind": "water_plants", "names": ["seagrass"]}],
        "fish": [{"kind": "clownfish", "count": 5}],
        "animals": {"playable": ["clownfish"], "prey": [], "predators": ["reef_shark"]},
    }


def _animal(**over: Any) -> dict[str, Any]:
    animal = {
        "id": "hammerhead",
        "name": "Scalloped hammerhead",
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
        "size": {"length_cm": 350, "largest_cm": 400},
        "year": {"winter": "active"},
        "main_food": {"name": "small fish", "kind": "prey"},
        "other_foods": [],
        "looks": {"marks": [{"mark": "wide hammer head", "source": "test"}]},
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
    review = job_dir / "tries" / "hammerhead" / "edits" / "1"
    review.mkdir(parents=True)
    (review / "marks_review.json").write_text(
        json.dumps({"shown": ["wide hammer head"], "missing": []})
    )
    (job_dir / "checks").mkdir(exist_ok=True)
    (job_dir / "checks" / "oneshot.json").write_text(
        json.dumps({"made": ["hammerhead"], "unmakable": {}})
    )
    (job_dir / "checks" / "marks_ship.json").write_text(
        json.dumps({"shown": ["wide hammer head"], "missing": [], "skipped": None})
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


def _look() -> str:
    return json.dumps({"shown": ["wide hammer head"], "missing": []})


def _ctx(
    job_dir: Path,
    world: Path,
    answers: list[str],
    job_id: str | None = None,
    look: str | None = None,
) -> SimpleNamespace:
    from builder.stages.safety import collect_texts

    keys = [item["key"] for item in collect_texts(job_dir, world)]
    verdicts = {
        "verdicts": [
            {"key": key, "verdict": "pass", "reasons": [], "rule": "ok"} for key in keys
        ]
    }
    replies = answers + [json.dumps(verdicts)]
    if look is not None:
        replies.append(look)
    return SimpleNamespace(
        job_id=job_id or "enrich_x/enrich_hammerhead",
        job_dir=job_dir,
        world_path=world,
        check_budget=lambda: None,
        chat=_Chat(replies),
    )


def _passing_run(tmp_path: Path, store: _Store) -> tuple[Path, Path, dict[str, Any]]:
    world = _world(tmp_path)
    job_dir = _item(tmp_path, _animal(), _research())
    translated = {
        "map_name": "Kampavasarahai",
        "name": "Kampavasarahai",
        "intro": "Hai, jolla on leveä pää.",
    }
    result = ShipStage(store=store).run(
        _ctx(job_dir, world, [json.dumps(translated)], look=_look())
    )
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
        "manifest.json",
    }
    en = json.loads(pack["locale/en/animals.json"].decode("utf-8"))
    fi = json.loads(pack["locale/fi/animals.json"].decode("utf-8"))
    assert en == {
        "animal.hammerhead.name": "Scalloped hammerhead",
        "animal.hammerhead.intro": "A shark with a wide head.",
    }
    assert fi["animal.hammerhead.name"] == "Kampavasarahai"
    assert (job_dir / "pack" / "manifest.json").is_file()


def test_ship_writes_the_finnish_map_name(tmp_path: Path) -> None:
    """#197.2: the Finnish file names the animal on the map too."""
    _world, job_dir, _result = _passing_run(tmp_path, _Store())
    fi = json.loads((job_dir / "locale" / "fi" / "animals.json").read_text())
    assert fi["animal.hammerhead.map_name"] == "Kampavasarahai"
    en = json.loads((job_dir / "locale" / "en" / "animals.json").read_text())
    assert "animal.hammerhead.map_name" not in en


def test_finnish_gate_allows_a_lone_map_name(tmp_path: Path) -> None:
    """#197.2: a Finnish map_name with no English key is the world's rule."""
    from builder.stages.ship import _gate_finnish

    job_dir = _item(tmp_path, _animal(), _research())
    fi_path = job_dir / "locale" / "fi" / "animals.json"
    fi = json.loads(fi_path.read_text())
    fi["animal.hammerhead.map_name"] = "Kampavasarahai"
    fi_path.write_text(json.dumps(fi))
    assert _gate_finnish(job_dir, "hammerhead") == []
    fi["animal.hammerhead.stray"] = "Kulkuri"
    fi_path.write_text(json.dumps(fi))
    assert _gate_finnish(job_dir, "hammerhead") == [
        "finnish: animal.hammerhead.stray is not in the English"
    ]


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


PLANT = "table_coral"

_ABOUT = {
    "about_looks": "A flat plate on a stalk.",
    "about_grows": "Grows on the coral rock.",
    "about_fact": "A colony of tiny polyps.",
    "about_year": "Grows all year.",
}


def _plant_world(
    tmp_path: Path,
    results: list[dict[str, Any]] | None = None,
) -> Path:
    world = _world(tmp_path, results)
    (world / "locale" / "en").mkdir(exist_ok=True)
    (world / "locale" / "en" / "plants.json").write_text(
        json.dumps({"plant.bilberry.title": "Bilberry"})
    )
    (world / "locale" / "fi").mkdir(exist_ok=True)
    (world / "locale" / "fi" / "plants.json").write_text(
        json.dumps({"plant.bilberry.title": "Mustikka"})
    )
    return world


def _plant_item(tmp_path: Path) -> Path:
    job_dir = tmp_path / "plant_item"
    job_dir.mkdir(exist_ok=True)
    (job_dir / "home.json").write_text(
        json.dumps(
            {
                "plant": PLANT,
                "from": "closed_brain_coral",
                "home": "reef",
                "enrich": {"tiles": [TILE]},
            }
        )
    )
    greens = job_dir / "models" / "plants"
    greens.mkdir(parents=True, exist_ok=True)
    (greens / f"{PLANT}.json").write_text(
        json.dumps(
            {
                "id": PLANT,
                "name": "Table coral",
                "latin": "Acropora hyacinthus",
                "kind": "bush",
                "grows": {"on": ["reef"], "density": 0.2},
                "colors": {"main": "#c9a06a"},
                "parts": [
                    {
                        "name": "plate",
                        "shape": "disc",
                        "size": [0.9, 0.08, 0.9],
                        "at": [0, 0.4, 0],
                        "color": "main",
                    }
                ],
            }
        )
    )
    (greens / f"{PLANT}.glb").write_bytes(b"glb-bytes")
    (greens / f"{PLANT}.png").write_bytes(b"png-bytes")
    (job_dir / "reviews").mkdir(exist_ok=True)
    (job_dir / "reviews" / f"{PLANT}-1.json").write_text(
        json.dumps({"species": PLANT, "round": 1, "issues": []})
    )
    texts = {
        f"plant.{PLANT}.title": "Table coral",
        f"plant.{PLANT}.latin": "Acropora hyacinthus",
        **{f"plant.{PLANT}.{key}": text for key, text in _ABOUT.items()},
    }
    (job_dir / "locale" / "en").mkdir(parents=True)
    (job_dir / "locale" / "en" / "plants.json").write_text(json.dumps(texts))
    (job_dir / "locale" / "fi").mkdir(parents=True)
    (job_dir / "locale" / "fi" / "plants.json").write_text(
        json.dumps({key: f"{text} (fi)" for key, text in texts.items()})
    )
    return job_dir


def _plant_answers() -> list[str]:
    fi = {
        f"plant.{PLANT}.title": "Pöytäkoralli",
        **{f"plant.{PLANT}.{key}": f"{text} (fi)" for key, text in _ABOUT.items()},
    }
    return [json.dumps(_ABOUT), json.dumps(fi)]


def test_ship_publishes_a_passing_plant_pack(tmp_path: Path) -> None:
    """#188.3: a clean plant item ships `packs/plant_table_coral/1/`."""
    store = _Store()
    world = _plant_world(tmp_path)
    job_dir = _plant_item(tmp_path)
    result = ShipStage(store=store).run(
        _ctx(job_dir, world, _plant_answers(), f"enrich_x/enrich_{PLANT}")
    )
    assert result["pack"] == f"packs/plant_{PLANT}/1/"
    assert result["version"] == 1
    ship = json.loads((job_dir / "checks" / "ship.json").read_text())
    assert ship == {"status": "pass", "problems": []}
    pack = {
        name.removeprefix(f"packs/plant_{PLANT}/1/"): data
        for name, data in store.blobs.items()
    }
    assert check_pack(pack) == []
    assert set(pack) == {
        f"models/plants/{PLANT}.json",
        f"models/plants/{PLANT}.glb",
        f"models/previews/plants/{PLANT}.png",
        "locale/en/plants.json",
        "locale/fi/plants.json",
        "manifest.json",
    }
    en = json.loads(pack["locale/en/plants.json"].decode("utf-8"))
    assert set(en) == {
        f"plant.{PLANT}.title",
        f"plant.{PLANT}.latin",
        *(f"plant.{PLANT}.{key}" for key in _ABOUT),
    }
    fi = json.loads(pack["locale/fi/plants.json"].decode("utf-8"))
    assert fi[f"plant.{PLANT}.title"] == "Pöytäkoralli"
    assert (job_dir / "pack" / "manifest.json").is_file()


def test_ship_plant_fails_a_blocking_review(tmp_path: Path) -> None:
    """#184.4: a render that doesn't read as its species stops the ship."""
    world = _plant_world(tmp_path)
    job_dir = _plant_item(tmp_path)
    (job_dir / "reviews" / f"{PLANT}-1.json").write_text(
        json.dumps(
            {
                "species": PLANT,
                "round": 1,
                "issues": [
                    {
                        "part": "plate",
                        "problem": "no flat plate",
                        "severity": "blocking",
                        "fix": "grow the plate",
                    }
                ],
            }
        )
    )
    with pytest.raises(_Stopped, match="review blocks"):
        ShipStage(store=_Store()).run(_ctx(job_dir, world, _plant_answers()))


def test_ship_plant_finnish_gate(tmp_path: Path) -> None:
    """#184.4: every English plant key needs its Finnish, no strangers."""
    from builder.stages.ship import _gate_finnish_plant

    job_dir = tmp_path / "item"
    assert _gate_finnish_plant(job_dir, PLANT) == [
        "finnish: no locale/en/plants.json to ship",
        "finnish: no locale/fi/plants.json to ship",
    ]
    (job_dir / "locale" / "en").mkdir(parents=True)
    (job_dir / "locale" / "fi").mkdir(parents=True)
    en = {
        f"plant.{PLANT}.title": "Table coral",
        f"plant.{PLANT}.about_looks": "A plate.",
    }
    (job_dir / "locale" / "en" / "plants.json").write_text(json.dumps(en))
    (job_dir / "locale" / "fi" / "plants.json").write_text(
        json.dumps({f"plant.{PLANT}.title": "Pöytäkoralli"})
    )
    assert _gate_finnish_plant(job_dir, PLANT) == [
        f"finnish: the Finnish texts leave out ['plant.{PLANT}.about_looks']"
    ]
    fi = {
        f"plant.{PLANT}.title": "Pöytäkoralli",
        f"plant.{PLANT}.about_looks": "Levy.",
        "plant.bilberry.title": "Mustikka",
    }
    (job_dir / "locale" / "fi" / "plants.json").write_text(json.dumps(fi))
    assert _gate_finnish_plant(job_dir, PLANT) == [
        "finnish: plant.bilberry.title belongs to no shipped plant"
    ]


def test_ship_plant_spec_gate(tmp_path: Path) -> None:
    """#188.3: a spec without parts and colors stops the ship."""
    from builder.stages.ship import _gate_spec_plant

    assert _gate_spec_plant(PLANT, {"parts": [{}], "colors": {"main": "#fff"}}) == []
    assert _gate_spec_plant(PLANT, {"parts": [], "colors": {}}) == [
        f"spec: {PLANT} has no parts to build",
        f"spec: {PLANT} has no colors to paint",
    ]
    assert _gate_spec_plant(PLANT, []) == [f"spec: {PLANT} is no object"]
    world = _plant_world(tmp_path)
    job_dir = _plant_item(tmp_path)
    spec = json.loads((job_dir / "models" / "plants" / f"{PLANT}.json").read_text())
    del spec["parts"]
    (job_dir / "models" / "plants" / f"{PLANT}.json").write_text(json.dumps(spec))
    with pytest.raises(_Stopped, match="has no parts to build"):
        ShipStage(store=_Store()).run(
            _ctx(job_dir, world, _plant_answers(), f"enrich_x/enrich_{PLANT}")
        )


def _gate_problems(
    tmp_path: Path,
    animal: dict[str, Any] | None = None,
    research: dict[str, Any] | None = None,
    results: list[dict[str, Any]] | None = None,
) -> list[str]:
    world = _world(tmp_path, results)
    job_dir = _item(tmp_path, animal or _animal(), research or _research())
    brief = json.loads((job_dir / "home.json").read_text())
    made = json.loads((job_dir / "animals" / "hammerhead.json").read_text())
    ctx = SimpleNamespace(job_dir=job_dir, world_path=world)
    return gate_checks(ctx, world, brief, "hammerhead", made)


def test_gate_passes_a_clean_item(tmp_path: Path) -> None:
    assert _gate_problems(tmp_path) == []


def test_reviewed_prey_tags_come_from_shipped_hunters(tmp_path: Path) -> None:
    """#193.3: the gate and the maker read the same reviewed list."""
    from builder.stages.animals import _hunt_tags
    from builder.stages.ship import _world_hunts

    world = _world(tmp_path)
    assert _hunt_tags(world) == {"small_fish"}
    assert _world_hunts(world) == {"small_fish"}


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
    """#197.1: 4.97 m stands past the researched largest adult (400 cm)."""
    animal = _animal(model={"archetype": "fish", "length": 4.97}, cam_dist=6.0)
    problems = _gate_problems(tmp_path, animal=animal)
    assert problems == ["length: 4.97 m is past the largest adult (400 cm researched)"]


def test_gate_passes_the_six_reviewed_lengths(tmp_path: Path) -> None:
    """#197.1: 0.8 x typical to largest; small species read double."""
    from builder.stages.ship import _gate_file

    world = _world(tmp_path)
    cases = [
        ("reef_shark", 2.209, 150, 250),
        ("lionfish", 0.432, 30, 45),
        ("clownfish", 0.126, 8, 12),
        ("green_sea_turtle", 1.0, 100, 150),
        ("bottlenose_dolphin", 2.183, 250, 300),
        ("hammerhead", 3.5, 275, 400),
    ]
    for aid, length, typical, largest in cases:
        animal = {
            "id": aid,
            "model": {"length": length},
            "tags": [],
            "hunts": [],
            "year": {"winter": "active"},
            "cam_dist": max(length, 1.0),
            "cam_height": 0.5,
        }
        research = {
            "size": {"length_cm": typical, "largest_cm": largest},
            "year": {"winter": "active"},
        }
        assert _gate_file(animal, research, [], world) == [], aid
    oversized = {
        "id": "hammerhead",
        "model": {"length": 4.97},
        "tags": [],
        "hunts": [],
        "year": {"winter": "active"},
        "cam_dist": 6.0,
        "cam_height": 0.5,
    }
    research = {
        "size": {"length_cm": 275, "largest_cm": 400},
        "year": {"winter": "active"},
    }
    assert _gate_file(oversized, research, [], world) == [
        "length: 4.97 m is past the largest adult (400 cm researched)"
    ]
    no_largest = {"size": {"length_cm": 275}, "year": {"winter": "active"}}
    assert _gate_file(oversized, no_largest, [], world) == [
        "length: the research names no largest adult length"
    ]


def test_gate_fails_migration_against_the_research(tmp_path: Path) -> None:
    animal = _animal(year={"winter": "migrates"})
    problems = _gate_problems(tmp_path, animal=animal)
    assert problems == ["year: the file says migrates, the research says active"]


def test_gate_fails_a_departure_in_the_wrong_season(tmp_path: Path) -> None:
    migration = {"leaves": "11-01", "returns": "04-01"}
    research = _research(year={"winter": "migrates", "migration": migration})
    animal = _animal(year={"winter": "migrates", "migration": migration})
    problems = _gate_problems(tmp_path, animal=animal, research=research)
    assert problems == [
        f"year: {TILE} leaves in month 11, no southern autumn departure",
        f"year: {TILE} returns in month 4, no southern spring return",
    ]


def test_gate_fails_a_hunt_no_reviewed_animal_hunts(tmp_path: Path) -> None:
    """#174.1: `fish` would hunt sharks and turtle-sized fish."""
    animal = _animal(hunts=["small_fish", "fish"])
    problems = _gate_problems(tmp_path, animal=animal)
    assert problems == ["hunts: fish is no reviewed prey tag"]


def test_gate_fails_a_camera_inside_the_body(tmp_path: Path) -> None:
    animal = _animal(cam_dist=2.0)
    problems = _gate_problems(tmp_path, animal=animal)
    assert problems == ["camera: cam_dist 2.0 sits inside the body (3.5 m)"]


def test_gate_fails_missing_marks(tmp_path: Path) -> None:
    world = _world(tmp_path)
    job_dir = _item(tmp_path, _animal(), _research())
    review = job_dir / "tries" / "hammerhead" / "edits" / "1" / "marks_review.json"
    review.write_text(json.dumps({"shown": [], "missing": ["wide hammer head"]}))
    brief = json.loads((job_dir / "home.json").read_text())
    made = json.loads((job_dir / "animals" / "hammerhead.json").read_text())
    ctx = SimpleNamespace(job_dir=job_dir, world_path=world)
    assert gate_checks(ctx, world, brief, "hammerhead", made) == [
        "render: the looks disagree "
        "(edit round missing: wide hammer head; final look missing: none)"
    ]


def test_gate_fails_when_the_final_look_misses(tmp_path: Path) -> None:
    """#197.4: the ship's own look gates, with both verdicts on dispute."""
    world = _world(tmp_path)
    job_dir = _item(tmp_path, _animal(), _research())
    ship = job_dir / "checks" / "marks_ship.json"
    ship.write_text(
        json.dumps({"shown": [], "missing": ["wide hammer head"], "skipped": None})
    )
    brief = json.loads((job_dir / "home.json").read_text())
    made = json.loads((job_dir / "animals" / "hammerhead.json").read_text())
    ctx = SimpleNamespace(job_dir=job_dir, world_path=world)
    assert gate_checks(ctx, world, brief, "hammerhead", made) == [
        "render: the final look misses: wide hammer head",
        "render: the looks disagree "
        "(edit round missing: none; final look missing: wide hammer head)",
    ]
    review = job_dir / "tries" / "hammerhead" / "edits" / "1" / "marks_review.json"
    review.write_text(json.dumps({"shown": [], "missing": ["wide hammer head"]}))
    assert gate_checks(ctx, world, brief, "hammerhead", made) == [
        "render: the final look misses: wide hammer head"
    ]


def test_render_look_writes_the_ships_verdict(tmp_path: Path) -> None:
    """#197.4: the look judges the final preview's researched marks."""
    world = _world(tmp_path)
    job_dir = _item(tmp_path, _animal(), _research())
    (job_dir / "checks" / "marks_ship.json").unlink()
    ctx = SimpleNamespace(
        job_dir=job_dir,
        world_path=world,
        check_budget=lambda: None,
        chat=_Chat([_look()]),
    )
    brief = json.loads((job_dir / "home.json").read_text())
    made = json.loads((job_dir / "animals" / "hammerhead.json").read_text())
    ShipStage(store=_Store())._render_look(ctx, brief, "hammerhead", made)
    written = json.loads((job_dir / "checks" / "marks_ship.json").read_text())
    assert written == {
        "shown": ["wide hammer head"],
        "missing": [],
        "skipped": None,
    }


def test_render_look_skips_library_animals(tmp_path: Path) -> None:
    """#197.4: nothing to judge, no call spent."""
    world = _world(tmp_path)
    job_dir = _item(tmp_path, _animal(), _research())
    (job_dir / "checks" / "marks_ship.json").unlink()

    def _boom(messages: Any, **kwargs: Any) -> Any:
        raise AssertionError("must not call")

    ctx = SimpleNamespace(
        job_dir=job_dir,
        world_path=world,
        check_budget=lambda: None,
        chat=_boom,
    )
    brief = json.loads((job_dir / "home.json").read_text())
    brief["in_library"] = "hammerhead"
    made = json.loads((job_dir / "animals" / "hammerhead.json").read_text())
    ShipStage(store=_Store())._render_look(ctx, brief, "hammerhead", made)
    written = json.loads((job_dir / "checks" / "marks_ship.json").read_text())
    assert written["skipped"] == "in_library"


def test_ship_fails_unsafe_english_at_safety(tmp_path: Path) -> None:
    """#174.5: English made unsafe on purpose fails at the safety review."""
    from builder.stages.safety import _Stopped as _SafetyStopped
    from builder.stages.safety import collect_texts

    world = _world(tmp_path)
    animal = _animal(text={"name": "Scalloped hammerhead", "intro": "Bite them all."})
    job_dir = _item(tmp_path, animal, _research())
    translated = {
        "map_name": "Kampavasarahai",
        "name": "Kampavasarahai",
        "intro": "Pure kaikkia.",
    }
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
