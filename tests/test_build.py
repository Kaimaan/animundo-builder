"""Item builds, publishing, previews and take. No Firebase, no key."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fake_store import FakeStore

from builder import build as build_mod
from builder.schemas import validate


def _plan(tile: str = "n_413_478") -> dict[str, Any]:
    return {
        "format": 1,
        "tile": tile,
        "code_only": False,
        "items": [
            {
                "id": "home_774",
                "kind": "home",
                "what": "774",
                "from": "tundra",
                "estimate_usd": 0.02,
            }
        ],
        "improve": [],
        "estimate_usd": 0.02,
        "name": {
            "en": "Tundra north of Saimaa",
            "fi": "Tundra Saimaan pohjoispuolella",
        },
        "coming": [],
        "world": "abc123",
        "planned_at": "2026-10-28T00:00:00+00:00",
    }


def test_order_items_builds_home_food_animal() -> None:
    items = [
        {"id": "improve_x", "kind": "animal"},
        {"id": "food_grass", "kind": "food"},
        {"id": "animal_lion", "kind": "animal"},
        {"id": "home_774", "kind": "home"},
    ]
    assert [i["id"] for i in build_mod.order_items(items)] == [
        "home_774",
        "food_grass",
        "animal_lion",
        "improve_x",
    ]


def test_write_home_brief_carries_plan_item(tmp_path: Path) -> None:
    item = _plan()["items"][0]
    brief = build_mod.write_brief(tmp_path, item, "tundra", 68.4, 23.6, None)
    assert brief == {
        "home": "tundra",
        "home_id": "tundra_774",
        "ecoregion": 774,
        "base_home": "tundra",
        "based_on": "tundra",
        "needs_home": True,
        "center": {"lat": 68.4, "lon": 23.6},
    }
    assert json.loads((tmp_path / "home.json").read_text()) == brief


def test_write_place_home_brief_carries_the_example(tmp_path: Path) -> None:
    item = {
        "id": "home_reef",
        "kind": "home",
        "what": "reef",
        "from": "examples/reef",
    }
    brief = build_mod.write_brief(tmp_path, item, "sea", -16.6, 146.0, None)
    assert brief == {
        "home": "sea",
        "home_id": "reef",
        "ecoregion": None,
        "base_home": "examples/reef",
        "based_on": "examples/reef",
        "needs_home": True,
        "center": {"lat": -16.6, "lon": 146.0},
    }
    assert json.loads((tmp_path / "home.json").read_text()) == brief


def test_write_animal_brief_carries_list_entry(tmp_path: Path) -> None:
    item = {"id": "animal_lion", "kind": "animal", "what": "lion"}
    entry = {"id": "lion", "gbif_key": 5219404, "latin": "Panthera leo", "name": "Lion"}
    brief = build_mod.write_brief(tmp_path, item, "savanna", -2.3, 34.8, entry)
    assert brief["species_key"] == 5219404
    assert brief["needs_home"] is False
    assert brief["home"] == "savanna"


def test_write_scaffold_uses_home_food(tmp_path: Path) -> None:
    home = {
        "food": [{"kind": "grass"}],
        "fish": [{"kind": "roach"}],
        "sources": ["https://example.com/meadow"],
    }
    path = build_mod.write_scaffold(tmp_path, home, lat=68.4, lon=23.6)
    scaffold = json.loads(path.read_text())
    assert scaffold["food"] == home["food"]
    assert scaffold["fish"] == home["fish"]
    assert scaffold["animals"] == {"playable": [], "prey": [], "predators": []}
    assert "welcome" not in scaffold
    assert path.name == "zz_work.json"
    assert scaffold["id"] == "zz_work"
    assert scaffold["center"] == {"lat": 68.4, "lon": 23.6}
    assert scaffold["sources"] == ["https://example.com/meadow"]


def test_write_scaffold_seats_the_tiles_cast(tmp_path: Path) -> None:
    """#150.1: given the tile's species, the scaffold seats them."""
    home = {"food": [], "fish": []}
    path = build_mod.write_scaffold(
        tmp_path,
        home,
        lat=68.4,
        lon=23.6,
        species={"playable": ["mountain_hare"], "prey": ["vole"], "predators": []},
    )
    scaffold = json.loads(path.read_text())
    assert scaffold["animals"] == {
        "playable": ["mountain_hare"],
        "prey": ["vole"],
        "predators": [],
    }


def test_write_state_params_merges_retry_safely(tmp_path: Path) -> None:
    """#150.2: labels merge into the item state, stages untouched."""
    (tmp_path / "state.json").write_text(
        json.dumps({"completed": ["research"], "params": {"area": "old"}})
    )
    build_mod.write_state_params(
        tmp_path, {"area": "n_413_478 (Forest)", "kind": "enrich"}
    )
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["completed"] == ["research"]
    assert state["params"] == {"area": "n_413_478 (Forest)", "kind": "enrich"}
    build_mod.write_state_params(tmp_path, {"kind": "enrich"})
    again = json.loads((tmp_path / "state.json").read_text())
    assert again["params"]["area"] == "n_413_478 (Forest)"


def test_write_scaffold_falls_back_to_its_own_source(tmp_path: Path) -> None:
    home = {"food": [], "fish": []}
    path = build_mod.write_scaffold(tmp_path, home, lat=68.4, lon=23.6)
    scaffold = json.loads(path.read_text())
    assert scaffold["sources"] == ["Scaffolding for one item's checks."]


def test_write_plant_brief_carries_names(tmp_path: Path) -> None:
    item = {"id": "plant_arctic_willow", "kind": "plant", "what": "arctic_willow"}
    brief = build_mod.write_brief(
        tmp_path, item, "tundra", 68.4, 23.6, None, ["Arctic Willow"]
    )
    assert brief["plant"] == "arctic_willow"
    assert brief["names"] == ["Arctic Willow"]
    assert brief["needs_home"] is False
    assert json.loads((tmp_path / "home.json").read_text()) == brief


def test_write_scaffold_names_ground_cover_plants(tmp_path: Path) -> None:
    home: dict[str, Any] = {"food": [], "fish": []}
    path = build_mod.write_scaffold(
        tmp_path,
        home,
        [{"model": "arctic_willow", "names": ["Arctic Willow"]}],
        lat=68.4,
        lon=23.6,
    )
    scaffold = json.loads(path.read_text())
    assert scaffold["ground_cover"]["plants"] == [
        {"model": "arctic_willow", "names": ["Arctic Willow"]}
    ]


def test_find_plant_names_matches_slug() -> None:
    home = {"food": [{"kind": "browse", "names": ["Arctic Willow", "dwarf birch"]}]}
    assert build_mod.find_plant_names(home, "arctic_willow") == ["Arctic Willow"]
    assert build_mod.find_plant_names(home, "cloudberry") == ["cloudberry"]


def test_write_flora_survey_lists_facts(tmp_path: Path) -> None:
    path = build_mod.write_flora_survey(tmp_path, ["Arctic Willow"])
    facts = json.loads(path.read_text())["facts"]
    assert facts == [{"topic": "plant", "text": "Arctic Willow grows here"}]


def test_run_item_stages_retries_once(tmp_path: Path) -> None:
    from builder.pipeline import BudgetTracker, Pipeline

    calls: list[str] = []

    class Flaky:
        name = "flaky"

        def run(self, ctx: Any) -> dict[str, Any]:
            calls.append("run")
            if len(calls) < 2:
                raise ValueError("boom")
            return {"ok": True}

    ctx = SimpleNamespace(
        job_dir=tmp_path,
        tracker=BudgetTracker(budget_usd=8.0),
        check_budget=lambda: None,
        client=None,
        open_client=None,
    )
    pipeline = Pipeline([Flaky()], state_path=tmp_path / "state.json")  # type: ignore[list-item]
    result = build_mod.run_item_stages(ctx, tmp_path, pipeline)
    assert result == {"flaky": {"ok": True}}
    assert calls == ["run", "run"]


def test_run_item_stages_fails_twice(tmp_path: Path) -> None:
    from builder.pipeline import BudgetTracker, Pipeline, StageFailedError

    class Broken:
        name = "broken"

        def run(self, ctx: Any) -> dict[str, Any]:
            raise ValueError("always")

    ctx = SimpleNamespace(
        job_dir=tmp_path,
        tracker=BudgetTracker(budget_usd=8.0),
        check_budget=lambda: None,
        client=None,
        open_client=None,
    )
    pipeline = Pipeline([Broken()], state_path=tmp_path / "state.json")  # type: ignore[list-item]
    with pytest.raises(StageFailedError):
        build_mod.run_item_stages(ctx, tmp_path, pipeline)


def test_build_food_item_patches_home_copy(tmp_path: Path) -> None:
    home = {"id": "tundra", "food": [{"kind": "grass", "names": []}]}
    item = {"id": "food_berries", "kind": "food", "what": "berries", "from": "tundra"}
    out = build_mod.build_food_item(tmp_path, item, home)
    assert out == tmp_path / "homes" / "tundra.json"
    patched = json.loads(out.read_text())
    assert {f["kind"] for f in patched["food"]} == {"grass", "berries"}
    assert home["food"] == [{"kind": "grass", "names": []}]


def test_upload_bundle_keeps_world_shape_only(tmp_path: Path) -> None:
    store = FakeStore()
    (tmp_path / "homes").mkdir()
    (tmp_path / "homes" / "t.json").write_text("{}")
    (tmp_path / "tries" / "t").mkdir(parents=True)
    (tmp_path / "tries" / "t" / "x.json").write_text("{}")
    (tmp_path / "report.html").write_text("<html></html>")
    report = build_mod.upload_bundle(store, "review/j1/home_1", tmp_path)
    assert report == "review/j1/home_1/report.html"
    assert sorted(store.blobs) == [
        "review/j1/home_1/homes/t.json",
        "review/j1/home_1/report.html",
    ]


def _made_tile(tile: str = "n_413_478") -> dict[str, Any]:
    return {
        "format": 1,
        "id": tile,
        "kind": "code",
        "grid": "ease2_n25",
        "grid_north": 0.0,
        "level": 12,
        "height": 32,
        "edges": {"n": [12], "e": [12], "s": [12], "w": [12]},
        "requires": {"homes": ["boreal_forest"], "animals": ["fox"]},
        "habitat": "boreal_forest",
        "ecoregion": 717,
        "home": "boreal_forest",
        "seed": 1,
        "water": 0.0,
        "species": {"playable": ["fox"], "prey": ["vole"], "predators": ["fox"]},
        "landmarks": [],
        "sources": {"habitat": "g", "water": "g", "species": "p"},
        "made": {"by": "b", "maker": "m", "world": "w", "at": "t"},
    }


def test_publish_group_writes_group_and_index() -> None:
    store = FakeStore()
    path = build_mod.publish_group("n_413_478", _made_tile(), store, "abc123")
    assert path == "tiles/v1/ease2_n25/51_59.json"
    group = json.loads(store.blobs[path].decode("utf-8"))
    assert group["tiles"]["n_413_478"]["home"] == "boreal_forest"
    assert group["version"] == 1
    index = json.loads(store.blobs["tiles/v1/index.json"].decode("utf-8"))
    validate("tile-index", index)
    assert index["groups"] == {"ease2_n25/51_59": 1}
    assert index["counts"] == {"ready": 1, "blocked": {}}
    assert index["world"] == "abc123"


def test_publish_group_moves_counts_by_delta() -> None:
    store = FakeStore()
    store.write_blob(
        "tiles/v1/ease2_n25/51_59.json",
        json.dumps(
            {
                "format": 1,
                "grid": "ease2_n25",
                "group": [51, 59],
                "version": 1,
                "tiles": {},
                "blocked": {"n_413_478": {"reason": "no_home", "habitat": "tundra"}},
            }
        ).encode("utf-8"),
    )
    store.write_blob(
        "tiles/v1/index.json",
        json.dumps(
            {
                "format": 1,
                "made_at": "t",
                "world": "old",
                "groups": {"ease2_n25/51_59": 1},
                "counts": {"ready": 4, "blocked": {"no_home": 2}},
            }
        ).encode("utf-8"),
    )
    build_mod.publish_group("n_413_478", _made_tile(), store, "abc123")
    group = json.loads(store.blobs["tiles/v1/ease2_n25/51_59.json"].decode("utf-8"))
    assert group["version"] == 2
    index = json.loads(store.blobs["tiles/v1/index.json"].decode("utf-8"))
    assert index["groups"] == {"ease2_n25/51_59": 2}
    assert index["counts"] == {"ready": 5, "blocked": {"no_home": 1}}


def test_assemble_preview_validates_and_hashes() -> None:
    home = b'{"id": "tundra_774"}'
    pack = build_mod.assemble_preview(
        "j1",
        "n_401_434",
        2,
        {"home_774": {"homes/tundra_774.json": home, "tries/x": b"no"}},
    )
    assert sorted(pack) == ["homes/tundra_774.json", "manifest.json"]
    manifest = json.loads(pack["manifest.json"].decode("utf-8"))
    validate("pack-manifest", manifest)
    assert manifest["draft_id"] == "j1"
    assert manifest["place_id"] == "n_401_434"
    assert manifest["version"] == 2
    assert manifest["files"] == [
        {
            "path": "homes/tundra_774.json",
            "sha256": __import__("hashlib").sha256(home).hexdigest(),
            "bytes": len(home),
        }
    ]


def test_assemble_preview_needs_a_file() -> None:
    with pytest.raises(ValueError, match="at least one file"):
        build_mod.assemble_preview("j1", "n_401_434", 1, {"home_774": {}})


def test_next_preview_version_bumps() -> None:
    store = FakeStore()
    assert build_mod.next_preview_version(store, "j1") == 1
    store.write_blob("previews/j1/1/manifest.json", b"{}")
    store.write_blob("previews/j1/2/manifest.json", b"{}")
    assert build_mod.next_preview_version(store, "j1") == 3


def test_take_bundle_lays_out_world_shape(tmp_path: Path) -> None:
    store = FakeStore()
    store.write_blob("review/j1/home_1/homes/t.json", b"{}")
    store.write_blob("review/j1/home_1/report.html", b"<html></html>")
    dest = build_mod.take_bundle(store, "j1", tmp_path)
    assert dest == tmp_path / "j1"
    assert (dest / "home_1" / "homes" / "t.json").is_file()
    assert (dest / "home_1" / "report.html").is_file()


def test_take_bundle_empty_errors(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="has nothing"):
        build_mod.take_bundle(FakeStore(), "j1", tmp_path)


def _real_world() -> Path | None:
    world = Path(__file__).resolve().parent.parent.parent / "animundo-world"
    if not (world / "tools" / "check_draft.py").is_file():
        return None
    return world


def _fake_glb() -> bytes:
    """A .glb the checks can read: skeleton, mesh, clips, no geometry."""
    import struct

    gltf = json.dumps(
        {
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
            "accessors": [{"count": 300}],
            "nodes": [{"mesh": 0}],
            "skins": [{"joints": [0]}],
            "animations": [
                {"name": clip} for clip in ("idle", "walk", "run", "eat", "sleep")
            ],
        }
    ).encode()
    return (
        b"glTF"
        + struct.pack("<II", 2, 12 + 8 + len(gltf))
        + struct.pack("<I", len(gltf))
        + b"JSON"
        + gltf
    )


def _world_without_built_models(tmp_path: Path, real: Path) -> Path:
    """A fresh clone: everything committed, no .glb, built mode forced on."""

    def prune(path: str, names: list[str]) -> list[str]:
        return [
            name
            for name in names
            if name == ".git" or name == "__pycache__" or name.endswith(".glb")
        ]

    world = tmp_path / "world"
    shutil.copytree(real, world, ignore=prune)
    (world / "animals" / "herring_gull.json").unlink()
    (world / "models" / "herring_gull.json").unlink()
    other = world / "assets" / "animals" / "other.glb"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_bytes(_fake_glb())
    return world


def test_take_lays_the_built_model_for_the_check(tmp_path: Path) -> None:
    """#140.2: the bundle's .glb satisfies the check, not the world's."""
    real = _real_world()
    if real is None:
        pytest.skip("needs a real world checkout")
    if not (real / "animals" / "herring_gull.json").is_file():
        pytest.skip("needs world d009df2 with the reviewed gull")
    world = _world_without_built_models(tmp_path, real)
    store = FakeStore()
    store.write_blob(
        "review/j1/animal_1/animals/herring_gull.json",
        (real / "animals" / "herring_gull.json").read_bytes(),
    )
    store.write_blob(
        "review/j1/animal_1/models/herring_gull.json",
        (real / "models" / "herring_gull.json").read_bytes(),
    )
    store.write_blob("review/j1/animal_1/models/herring_gull.glb", _fake_glb())
    dest = build_mod.take_bundle(store, "j1", tmp_path)
    problems = build_mod.check_draft(dest / "animal_1", world)
    assert problems == []
    assert (dest / "animal_1" / "assets" / "animals" / "herring_gull.glb").is_file()


def test_take_files_pass_draft_check(tmp_path: Path) -> None:
    """#118.10: take's files pass the world's check_draft.py."""
    world = _real_world()
    if world is None:
        pytest.skip("needs a real world checkout")
    store = FakeStore()
    store.write_blob(
        "review/j1/home_1/homes/tundra.json",
        (world / "homes" / "tundra.json").read_bytes(),
    )
    store.write_blob(
        "review/j1/home_1/animals/fox.json",
        (world / "animals" / "fox.json").read_bytes(),
    )
    dest = build_mod.take_bundle(store, "j1", tmp_path)
    problems = build_mod.check_draft(dest / "home_1", world)
    assert problems == []
    report = build_mod.take_report(dest)
    assert report.is_file()


def test_broken_item_fails_draft_check(tmp_path: Path) -> None:
    """#118.10: take's check catches a broken item."""
    world = _real_world()
    if world is None:
        pytest.skip("needs a real world checkout")
    store = FakeStore()
    store.write_blob("review/j1/home_2/animals/broken.json", b"{}")
    dest = build_mod.take_bundle(store, "j1", tmp_path)
    problems = build_mod.check_draft(dest / "home_2", world)
    assert problems != []
    assert any("broken.json" in p for p in problems)


def test_draft_check_without_tool_errors(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no draft check"):
        build_mod.check_draft(tmp_path, tmp_path / "no-world")


def _old_tile(**over: Any) -> dict[str, Any]:
    tile: dict[str, Any] = {
        "id": "n_413_478",
        "version": 3,
        "made": {"at": "2026-01-01", "world": "aaa", "maker": "m"},
        "seed": 7,
        "home": "boreal_forest",
        "edges": {"n": [1]},
        "species": {"playable": ["fox"], "prey": [], "predators": []},
        "requires": {"animals": ["fox"]},
        "wanted": [{"kind": "animal", "what": "lynx", "part": "forest"}],
    }
    tile.update(over)
    return tile


def test_remake_decision_skips_an_identical_tile(tmp_path: Path) -> None:
    """#143.4: provenance alone never bumps a version."""
    old = _old_tile()
    del old["wanted"]
    made = _old_tile(version=1, made={"at": "2026-10-30", "world": "bbb"})
    del made["wanted"]
    verdict, no_food = build_mod.remake_decision(
        "n_413_478", old, None, made, None, tmp_path, "lynx"
    )
    assert verdict == ("skip", None)
    assert no_food is False


def test_remake_decision_publishes_an_addition(tmp_path: Path) -> None:
    """#143.4: the joined animal bumps the version; wanted drops it."""
    (tmp_path / "animals").mkdir()
    (tmp_path / "animals" / "lynx.json").write_text("{}")
    made = _old_tile(
        version=1,
        species={"playable": ["fox", "lynx"], "prey": [], "predators": []},
        requires={"animals": ["fox", "lynx"]},
    )
    del made["wanted"]
    verdict, no_food = build_mod.remake_decision(
        "n_413_478", _old_tile(), None, made, None, tmp_path, "lynx"
    )
    assert verdict[0] == "tile"
    assert verdict[1] is not None and verdict[1]["version"] == 4
    assert no_food is False


def test_remake_decision_flags_no_food_and_still_bumps(tmp_path: Path) -> None:
    """#143.4: the unfed animal stays out, but wanted changed."""
    (tmp_path / "animals").mkdir()
    (tmp_path / "animals" / "lynx.json").write_text("{}")
    made = _old_tile(version=1)
    del made["wanted"]
    verdict, no_food = build_mod.remake_decision(
        "n_413_478", _old_tile(), None, made, None, tmp_path, "lynx"
    )
    assert verdict[0] == "tile"
    assert no_food is True


def test_remake_decision_refuses_a_rebuilt_tile(tmp_path: Path) -> None:
    """#143.4: a moved home or a shrunk list fails loudly."""
    made = _old_tile(version=1, home="tundra")
    with pytest.raises(build_mod.EnrichRemakeError, match="moved home"):
        build_mod.remake_decision(
            "n_413_478", _old_tile(), None, made, None, tmp_path, "lynx"
        )
    made = _old_tile(version=1, species={"playable": [], "prey": [], "predators": []})
    with pytest.raises(build_mod.EnrichRemakeError, match="dropped playable"):
        build_mod.remake_decision(
            "n_413_478", _old_tile(), None, made, None, tmp_path, "lynx"
        )
    made = _old_tile(version=1)
    del made["wanted"]
    with pytest.raises(build_mod.EnrichRemakeError, match="unfiled"):
        build_mod.remake_decision(
            "n_413_478", _old_tile(), None, made, None, tmp_path, "lynx"
        )


def test_remake_decision_opens_a_blocked_tile(tmp_path: Path) -> None:
    """#143.4: the opener publishes the tile at version 1."""
    old_blocked = {
        "reason": "no_animal",
        "habitat": "boreal_forest",
        "wanted": [{"kind": "animal", "what": "lynx", "part": "forest"}],
    }
    made = _old_tile(
        version=1,
        species={"playable": ["lynx"], "prey": [], "predators": []},
        requires={"animals": ["lynx"]},
    )
    del made["wanted"]
    verdict, no_food = build_mod.remake_decision(
        "n_413_478", None, old_blocked, made, None, tmp_path, "lynx"
    )
    assert verdict[0] == "tile"
    assert verdict[1] is not None and verdict[1]["version"] == 1
    assert no_food is False


def test_remake_decision_keeps_a_hungry_block(tmp_path: Path) -> None:
    """#143.4: still nothing kept refreshes the block, flagged no_food."""
    old_blocked = {
        "reason": "no_animal",
        "habitat": "boreal_forest",
        "wanted": [{"kind": "animal", "what": "lynx", "part": "forest"}],
    }
    blocked = {"reason": "no_animal", "habitat": "boreal_forest"}
    verdict, no_food = build_mod.remake_decision(
        "n_413_478", None, old_blocked, None, blocked, tmp_path, "lynx"
    )
    assert verdict == ("blocked", blocked)
    assert no_food is True
    with pytest.raises(build_mod.EnrichRemakeError, match="remade blocked"):
        build_mod.remake_decision(
            "n_413_478", _old_tile(), None, None, blocked, tmp_path, "lynx"
        )


def test_publish_remakes_writes_once_per_group_and_index_once() -> None:
    """#143.4: two tiles in one group share its write and its bump."""
    store = FakeStore()
    writes: list[str] = []
    real_write = store.write_blob
    store.write_blob = lambda path, data, **k: (
        writes.append(path),
        real_write(path, data, **k),
    )  # type: ignore[method-assign]
    group_blob = "tiles/v1/ease2_n25/51_59.json"
    groups = {
        group_blob: {
            "format": 1,
            "grid": "ease2_n25",
            "group": [51, 59],
            "version": 5,
            "tiles": {
                "n_413_478": _old_tile(id="n_413_478", version=3),
                "n_413_479": _old_tile(id="n_413_479", version=2),
            },
            "blocked": {},
        }
    }
    store.blobs[group_blob] = json.dumps(groups[group_blob]).encode("utf-8")
    other_blob = "tiles/v1/ease2_n25/52_59.json"
    other = {
        "format": 1,
        "grid": "ease2_n25",
        "group": [52, 59],
        "version": 9,
        "tiles": {"n_421_478": _old_tile(id="n_421_478", version=1)},
        "blocked": {},
    }
    store.blobs[other_blob] = json.dumps(other).encode("utf-8")
    versions = build_mod.publish_remakes(
        store,
        groups,
        {
            "n_413_478": ("tile", _old_tile(id="n_413_478", version=4)),
            "n_413_479": ("tile", _old_tile(id="n_413_479", version=3)),
            "n_421_478": ("skip", None),
        },
        "abc",
    )
    assert versions == {"n_413_478": 4, "n_413_479": 3}
    assert writes.count(group_blob) == 1
    assert writes.count("tiles/v1/index.json") == 1
    assert writes.count(other_blob) == 0
    assert store.blobs[other_blob] == json.dumps(other).encode("utf-8")
    data = json.loads(store.blobs[group_blob].decode("utf-8"))
    assert data["version"] == 6
    index = json.loads(store.blobs["tiles/v1/index.json"].decode("utf-8"))
    assert index["groups"] == {"ease2_n25/51_59": 6}
    assert index["counts"]["ready"] == 0


def test_publish_remakes_moves_counts_on_an_opener() -> None:
    """#143.4: blocked to ready shifts the index by honest delta."""
    store = FakeStore()
    group_blob = "tiles/v1/ease2_n25/51_59.json"
    groups = {
        group_blob: {
            "format": 1,
            "grid": "ease2_n25",
            "group": [51, 59],
            "version": 1,
            "tiles": {},
            "blocked": {
                "n_413_478": {
                    "reason": "no_animal",
                    "habitat": "boreal_forest",
                }
            },
        }
    }
    store.blobs[group_blob] = json.dumps(groups[group_blob]).encode("utf-8")
    store.blobs["tiles/v1/index.json"] = json.dumps(
        {
            "format": 1,
            "made_at": "",
            "world": "",
            "groups": {},
            "counts": {"ready": 0, "blocked": {"no_animal": 1}},
        }
    ).encode("utf-8")
    versions = build_mod.publish_remakes(
        store,
        groups,
        {"n_413_478": ("tile", _old_tile(id="n_413_478", version=1))},
        "abc",
    )
    assert versions == {"n_413_478": 1}
    index = json.loads(store.blobs["tiles/v1/index.json"].decode("utf-8"))
    assert index["counts"] == {"ready": 1, "blocked": {}}
    data = json.loads(store.blobs[group_blob].decode("utf-8"))
    assert data["blocked"] == {}
