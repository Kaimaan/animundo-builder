"""A shipped plant joins its biome, then its biome's tiles (#214)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from builder.biome import join_plant, remake_biome_tiles

REEF = [
    ("branching_staghorn_coral", 0.34, ["reef"]),
    ("closed_brain_coral", 0.30, ["reef"]),
    ("giant_sea_fan", 0.26, ["reef"]),
    ("halimeda", 0.08, ["reef", "seabed"]),
    ("dugong_grass", 0.25, ["seabed"]),
    ("paddle_grass", 0.25, ["seabed"]),
]


def _world(tmp_path: Path, entries: list[tuple[str, float, list[str]]] = REEF) -> Path:
    world = tmp_path / "world"
    (world / "biomes").mkdir(parents=True)
    (world / "models" / "plants").mkdir(parents=True)
    (world / "biomes" / "coral_reef.json").write_text(
        json.dumps(
            {
                "id": "coral_reef",
                "plants": {
                    "trees": [],
                    "cover": [],
                    "water": [
                        {"id": pid, "density": density} for pid, density, _ in entries
                    ],
                },
            }
        )
        + "\n"
    )
    for pid, _, on in entries:
        (world / "models" / "plants" / f"{pid}.json").write_text(
            json.dumps({"grows": {"on": on}})
        )
    (world / "models" / "plants" / "table_coral.json").write_text(
        json.dumps({"grows": {"on": ["reef"]}})
    )
    (world / "models" / "plants" / "eel_grass.json").write_text(
        json.dumps({"grows": {"on": ["seabed"]}})
    )
    return world


def test_join_scales_a_full_reef_to_fit_the_coral(tmp_path: Path) -> None:
    """#214.1: table_coral joins at the average share; the reef stays ≤ 1.0."""
    world = _world(tmp_path)
    report = join_plant(world, "coral_reef", "table_coral", run_flatten=False)
    assert report["ground"] == "reef"
    assert report["share"] == 0.245
    assert report["total"] == 1.0
    water = json.loads((world / "biomes" / "coral_reef.json").read_text())["plants"][
        "water"
    ]
    by_id = {e["id"]: e["density"] for e in water}
    assert by_id == {
        "branching_staghorn_coral": 0.262,
        "closed_brain_coral": 0.231,
        "giant_sea_fan": 0.2,
        "halimeda": 0.062,
        "dugong_grass": 0.25,
        "paddle_grass": 0.25,
        "table_coral": 0.245,
    }


def test_join_on_another_ground_leaves_the_reef_untouched(
    tmp_path: Path,
) -> None:
    """#214.1: a seabed grass seats without touching a coral's density."""
    world = _world(tmp_path)
    before = json.loads((world / "biomes" / "coral_reef.json").read_text())["plants"][
        "water"
    ][:4]
    report = join_plant(world, "coral_reef", "eel_grass", run_flatten=False)
    assert report["scaled"] == {}
    after = json.loads((world / "biomes" / "coral_reef.json").read_text())["plants"][
        "water"
    ]
    assert after[:4] == before
    assert after[-1]["id"] == "eel_grass"


def test_join_without_a_full_ground_scales_nothing(tmp_path: Path) -> None:
    """#214.1: room on the rock seats the coral at the average, as is."""
    world = _world(
        tmp_path,
        [
            ("branching_staghorn_coral", 0.3, ["reef"]),
            ("closed_brain_coral", 0.2, ["reef"]),
        ],
    )
    report = join_plant(world, "coral_reef", "table_coral", run_flatten=False)
    assert report["scaled"] == {}
    assert report["share"] == 0.25
    assert report["total"] == 0.75


def test_join_refuses_twice_and_unknown(tmp_path: Path) -> None:
    world = _world(tmp_path)
    join_plant(world, "coral_reef", "table_coral", run_flatten=False)
    with pytest.raises(ValueError, match="already seated"):
        join_plant(world, "coral_reef", "table_coral", run_flatten=False)
    with pytest.raises(ValueError, match="no plant spec"):
        join_plant(world, "coral_reef", "no_such_plant", run_flatten=False)


def _tile(tile_id: str, home: str, version: int) -> dict[str, Any]:
    base = json.loads(
        (
            Path(__file__).resolve().parent / "examples" / "valid" / "tile.json"
        ).read_text()
    )
    base["id"] = tile_id
    base["home"] = home
    base["biome"] = home
    base["version"] = version
    return base


def test_remake_seats_the_newcomer_on_its_biome_tiles(
    tmp_path: Path,
) -> None:
    """#214.2: the biome's published tiles grow the joined plant, $0."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from fake_store import FakeStore

    world = _world(tmp_path)
    join_plant(world, "coral_reef", "table_coral", run_flatten=False)
    store = FakeStore()
    store.blobs["tiles/v2/index.json"] = json.dumps(
        {
            "format": 1,
            "made_at": "2026-11-02",
            "world": "abc",
            "groups": {"z10/115_070": 7},
            "counts": {"ready": 2, "blocked": {}},
        }
    ).encode("utf-8")
    store.blobs["tiles/v2/z10/115_070.json"] = json.dumps(
        {
            "format": 1,
            "grid": "z10",
            "group": [115, 70],
            "version": 7,
            "tiles": {
                "z10_0927_0560": _tile("z10_0927_0560", "coral_reef", 2),
                "z10_0927_0561": _tile("z10_0927_0561", "boreal_taiga", 5),
            },
            "blocked": {},
        }
    ).encode("utf-8")
    out = remake_biome_tiles(store, world, "coral_reef", "table_coral", "abc123")
    assert out["held"] == []
    assert out["versions"] == {"z10_0927_0560": 3}
    data = json.loads(store.read_blob("tiles/v2/z10/115_070.json")[0].decode("utf-8"))
    grown = data["tiles"]["z10_0927_0560"]["plants"]["water"]
    assert {"id": "table_coral", "density": 0.245} in grown
    assert "table_coral" in data["tiles"]["z10_0927_0560"]["requires"]["plants"]
    assert data["tiles"]["z10_0927_0561"]["version"] == 5
    assert len(store.build_jobs) == 1
