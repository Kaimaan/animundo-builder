"""Enrich place checks: sorting the model's failures from the place's. No key."""

from __future__ import annotations

import json
from pathlib import Path

from builder.enrich import (
    place_tiles,
    sort_problems,
    sort_region_problems,
    write_places,
)


def _places(job_dir: Path) -> None:
    regions = job_dir / "regions"
    regions.mkdir(parents=True, exist_ok=True)
    (regions / "tl_n_413_478.json").write_text(
        json.dumps({"id": "tl_n_413_478", "animals": {}}), encoding="utf-8"
    )
    (regions / "tl_n_413_479.json").write_text(
        json.dumps({"id": "tl_n_413_479", "animals": {}}), encoding="utf-8"
    )


def test_place_tiles_maps_ids_and_stems(tmp_path: Path) -> None:
    """#161.2: food-web lines name region ids; both spellings land."""
    _places(tmp_path)
    mapping = place_tiles(tmp_path)
    assert mapping["tl_n_413_478"] == "n_413_478"
    assert mapping["tl_n_413_479"] == "n_413_479"


def test_sort_problems_splits_model_tile_and_place(tmp_path: Path) -> None:
    """#161.2: the new file's failures are the model's; food is the tile's."""
    _places(tmp_path)
    mapping = place_tiles(tmp_path)
    model, tiled, general = sort_problems(
        [
            'ERROR: lynx: needs text "intro"',
            'ERROR: squirrel in tl_n_413_479 eats "mushrooms", '
            "but the region has no such food",
            'ERROR: lynx in tl_n_413_479 hunts "small_mammal", '
            "but no animal there has that tag",
            'ERROR: region tl_n_413_479: playable animal "bee" has no file',
            "ERROR: fox: model file not found: res://assets/animals/fox.glb",
        ],
        "lynx",
        mapping,
    )
    assert model == ['ERROR: lynx: needs text "intro"']
    assert sorted(tiled) == ["n_413_479"]
    assert len(tiled["n_413_479"]) == 3
    assert general == ["ERROR: fox: model file not found: res://assets/animals/fox.glb"]


def test_sort_problems_unknown_place_is_general(tmp_path: Path) -> None:
    """#161.2: a place line for no known tile stays loud, never the model's."""
    _places(tmp_path)
    model, tiled, general = sort_problems(
        ['ERROR: squirrel in tl_n_999_999 eats "mushrooms"'],
        "lynx",
        place_tiles(tmp_path),
    )
    assert model == []
    assert tiled == {}
    assert general == ['ERROR: squirrel in tl_n_999_999 eats "mushrooms"']


def test_sort_region_problems_groups_by_place_file(tmp_path: Path) -> None:
    """#161.2: a failing place file lands its details on its tile."""
    _places(tmp_path)
    tiled, general = sort_region_problems(
        [
            "FAIL regions/tl_n_413_479.json",
            '- "center.lat" must be a number between -90 and 90',
            "FAIL regions/fi_nuuksio.json",
            '- "welcome" must be a non-empty text',
        ],
        place_tiles(tmp_path),
    )
    assert tiled == {"n_413_479": ['"center.lat" must be a number between -90 and 90']}
    assert general == ['"welcome" must be a non-empty text']


def test_write_places_lists_every_tile(tmp_path: Path) -> None:
    """#161.2: checks/places.json passes quiet tiles, fails loud ones."""
    _places(tmp_path)
    mapping = place_tiles(tmp_path)
    write_places(
        tmp_path,
        mapping,
        {"n_413_479": ["ERROR: squirrel in tl_n_413_479 eats mushrooms"]},
        ["ERROR: fox: something broke"],
        region_note="models not built: run tools/build_model.sh first",
    )
    data = json.loads((tmp_path / "checks" / "places.json").read_text())
    assert data["ok"] is False
    by_check = {entry["check"]: entry for entry in data["results"]}
    assert by_check["place n_413_478"]["status"] == "pass"
    failed = by_check["place n_413_479"]
    assert failed["status"] == "fail"
    assert failed["problems"] == ["ERROR: squirrel in tl_n_413_479 eats mushrooms"]
    assert by_check["place"]["status"] == "fail"
    assert by_check["region"]["status"] == "skip"
