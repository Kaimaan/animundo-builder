"""Content packs: assembly, the game's rules, versions. No network."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from builder.packs import (
    MANIFEST_MAX,
    MAX_GLB_BYTES,
    MAX_PACK_BYTES,
    PackExists,
    assemble_enrich_pack,
    build_tiles_overlay,
    check_additions,
    check_pack,
    check_tiles_overlay,
    next_pack_version,
    publish_pack,
)


class _Store:
    """Storage enough for packs: blobs under string names."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def list_blobs(self, prefix: str) -> list[str]:
        return sorted(n for n in self.blobs if n.startswith(prefix))

    def write_blob(self, name: str, data: bytes, content_type: str = "") -> None:
        self.blobs[name] = data


def _files() -> dict[str, bytes]:
    return {
        "animals/hammerhead.json": b'{"id": "hammerhead"}',
        "models/hammerhead.glb": b"glb-bytes",
        "models/previews/hammerhead.png": b"png-bytes",
        "locale/en/animals.json": b"{}",
        "locale/fi/animals.json": b"{}",
        "additions/au_great_barrier_reef.json": b'{"format": 1}',
    }


def _manifest(pack: dict[str, bytes]) -> dict[str, Any]:
    return json.loads(pack["manifest.json"].decode("utf-8"))


def test_assembled_pack_verifies() -> None:
    """#176.1: the manifest's sizes and hashes match its files."""
    pack = assemble_enrich_pack("animal_hammerhead", 1, _files())
    assert check_pack(pack) == []
    manifest = _manifest(pack)
    assert manifest == {
        "format": 1,
        "version": 1,
        "min_game": 1,
        "kind": "enrich",
        "files": manifest["files"],
    }
    assert {e["path"] for e in manifest["files"]} == set(_files())


def test_pack_id_must_be_a_game_id() -> None:
    """#179: the folder names the pack, `^[a-z0-9_]{1,64}$`."""
    with pytest.raises(ValueError, match="no pack id"):
        assemble_enrich_pack("Animal-Hammerhead!", 1, _files())
    with pytest.raises(ValueError, match="no pack id"):
        assemble_enrich_pack("a" * 65, 1, _files())


def test_tampered_file_fails_its_size_and_hash() -> None:
    pack = assemble_enrich_pack("animal_hammerhead", 1, _files())
    pack["models/hammerhead.glb"] = b"other-bytes"
    assert check_pack(pack) == ["models/hammerhead.glb fails its size and hash"]


def test_pack_rules_mirror_the_game() -> None:
    """#176.3: paths, extensions, sizes, hashes, as preview.gd reads."""

    def _with(entry: dict[str, Any]) -> dict[str, bytes]:
        pack = assemble_enrich_pack("animal_hammerhead", 1, _files())
        manifest = _manifest(pack)
        manifest["files"].append(entry)
        pack["manifest.json"] = json.dumps(manifest).encode("utf-8")
        return pack

    bad = _with(
        {
            "path": "../escape.json",
            "sha256": hashlib.sha256(b"x").hexdigest(),
            "bytes": 1,
        }
    )
    assert check_pack(bad) == ["bad path ../escape.json"]
    outside = _with(
        {
            "path": "scripts/run.gd",
            "sha256": hashlib.sha256(b"x").hexdigest(),
            "bytes": 1,
        }
    )
    assert check_pack(outside) == ["file outside the pack folders: scripts/run.gd"]
    regions = _with(
        {
            "path": "regions/reef.json",
            "sha256": hashlib.sha256(b"x").hexdigest(),
            "bytes": 1,
        }
    )
    assert check_pack(regions) == ["file outside the pack folders: regions/reef.json"]
    pack = dict(_files())
    pack["animals/notes.txt"] = b"a note"
    pack = assemble_enrich_pack("animal_hammerhead", 1, pack)
    assert check_pack(pack) == ["not data: animals/notes.txt"]


def test_pack_size_limits() -> None:
    big = dict(_files())
    big["models/hammerhead.glb"] = b"g" * (MAX_GLB_BYTES + 1)
    pack = assemble_enrich_pack("animal_hammerhead", 1, big)
    assert check_pack(pack) == [".glb bigger than 2 MB: models/hammerhead.glb"]
    huge = dict(_files())
    huge["models/hammerhead.glb"] = b"g" * (MAX_PACK_BYTES + 1)
    pack = assemble_enrich_pack("animal_hammerhead", 1, huge)
    problems = check_pack(pack)
    assert ".glb bigger than 2 MB: models/hammerhead.glb" in problems
    assert "bigger than 8 MB" in problems
    fat = assemble_enrich_pack("animal_hammerhead", 1, _files())
    fat["manifest.json"] = b" " * (MANIFEST_MAX + 1)
    assert check_pack(fat) == ["manifest.json is bigger than 64 KB"]


def test_additions_mirror_the_game() -> None:
    """#179.1: top-level lists only, fish exactly kind and count."""
    assert check_additions({"format": 1}) == []
    assert (
        check_additions(
            {
                "format": 1,
                "fish": [{"kind": "testfish", "count": 3}],
                "playable": ["testfish"],
                "prey": ["testfish"],
            }
        )
        == []
    )
    long = "an addition may only add fish, playable, predators, prey and plants, "
    assert check_additions({"format": 1, "animals": ["testfish"]}) == [
        long + "not animals"
    ]
    assert check_additions({"format": 1, "food": []}) == [long + "not food"]
    assert check_additions({"format": 2}) == ["format isn't 1"]
    assert check_additions({"format": 1, "playable": ["Bad Name"]}) == [
        "playable has a bad id"
    ]
    assert check_additions({"format": 1, "fish": [{"kind": "testfish"}]}) == [
        "a fish isn't {kind, count} (1 to 50)"
    ]
    assert check_additions(
        {"format": 1, "fish": [{"kind": "testfish", "count": 0}]}
    ) == ["a fish isn't {kind, count} (1 to 50)"]
    assert check_additions(
        {"format": 1, "fish": [{"kind": "testfish", "count": 3, "extra": 1}]}
    ) == ["a fish isn't {kind, count} (1 to 50)"]


def test_additions_take_plants_on_cover_or_water() -> None:
    """#188.2: exactly id and density, above 0, at most 1, 20 a side."""
    assert (
        check_additions(
            {
                "format": 1,
                "plants": {"water": [{"id": "table_coral", "density": 0.2}]},
            }
        )
        == []
    )
    assert (
        check_additions(
            {
                "format": 1,
                "plants": {
                    "cover": [{"id": "bilberry", "density": 1}],
                    "water": [],
                },
            }
        )
        == []
    )
    long = "an addition may only add fish, playable, predators, prey and plants, "
    assert check_additions({"format": 1, "trees": []}) == [long + "not trees"]
    assert check_additions({"format": 1, "plants": {"trees": []}}) == [
        "plants adds no trees side"
    ]
    assert check_additions({"format": 1, "plants": []}) == [
        "plants isn't {cover, water}"
    ]
    assert check_additions(
        {"format": 1, "plants": {"water": [{"id": "table_coral", "density": 0}]}}
    ) == ["a plant isn't {id, density} (above 0, at most 1)"]
    assert check_additions(
        {"format": 1, "plants": {"water": [{"id": "table_coral", "density": 1.5}]}}
    ) == ["a plant isn't {id, density} (above 0, at most 1)"]
    assert check_additions(
        {
            "format": 1,
            "plants": {"water": [{"id": "table_coral", "density": 0.2, "extra": 1}]},
        }
    ) == ["a plant isn't {id, density} (above 0, at most 1)"]
    assert check_additions(
        {"format": 1, "plants": {"water": [{"id": "Table Coral", "density": 0.2}]}}
    ) == ["a plant isn't {id, density} (above 0, at most 1)"]
    many = [{"id": f"weed_{n}", "density": 0.01} for n in range(21)]
    assert check_additions({"format": 1, "plants": {"cover": many}}) == [
        "plants.cover adds more than 20"
    ]


def test_second_publish_leaves_the_first_untouched() -> None:
    """#176.4: version 2 publishes beside version 1, never over it."""
    store = _Store()
    first = assemble_enrich_pack("animal_hammerhead", 1, _files())
    assert check_pack(first) == []
    assert next_pack_version(store, "animal_hammerhead") == 1
    prefix = publish_pack(store, "animal_hammerhead", 1, first)
    assert prefix == "packs/animal_hammerhead/1/"
    with pytest.raises(PackExists):
        publish_pack(store, "animal_hammerhead", 1, first)
    assert next_pack_version(store, "animal_hammerhead") == 2
    changed = dict(_files())
    changed["animals/hammerhead.json"] = b'{"id": "hammerhead", "v": 2}'
    second = assemble_enrich_pack("animal_hammerhead", 2, changed)
    publish_pack(store, "animal_hammerhead", 2, second)
    assert (
        store.blobs["packs/animal_hammerhead/1/animals/hammerhead.json"]
        == b'{"id": "hammerhead"}'
    )
    assert (
        store.blobs["packs/animal_hammerhead/2/animals/hammerhead.json"]
        == b'{"id": "hammerhead", "v": 2}'
    )


def _made(species: dict[str, list[str]], plants: dict | None = None) -> dict[str, Any]:
    made: dict[str, Any] = {"species": species}
    if plants is not None:
        made["plants"] = plants
    return made


def test_overlay_pack_asks_for_game_two(tmp_path: Any) -> None:
    """#220: a pack with the overlay names min_game 2 and still verifies."""
    overlay = build_tiles_overlay(
        "animal",
        "lynx",
        tmp_path,
        [_made({"playable": ["lynx"], "prey": [], "predators": []})],
        ["z10_0581_0294"],
    )
    pack = assemble_enrich_pack(
        "spread_20261102_1", 1, {"additions/tiles.json": overlay}, min_game=2
    )
    assert check_pack(pack) == []
    assert _manifest(pack)["min_game"] == 2


def test_overlay_lists_union_fish_and_plants(tmp_path: Any) -> None:
    """#220: the union of the remakes' lists, a fish count, a seating."""
    animals = tmp_path / "animals"
    animals.mkdir()
    (animals / "perch.json").write_text(json.dumps({"tags": ["fish", "small_fish"]}))
    (animals / "pike.json").write_text(json.dumps({"archetype": "fish", "tags": []}))
    small = build_tiles_overlay(
        "animal",
        "perch",
        tmp_path,
        [_made({"playable": ["perch"], "prey": [], "predators": []})],
        ["z10_0581_0294"],
    )
    assert json.loads(small)["fish"] == [{"kind": "perch", "count": 5}]
    big = build_tiles_overlay(
        "animal",
        "pike",
        tmp_path,
        [
            _made({"playable": [], "prey": [], "predators": ["pike"]}),
            _made({"playable": ["pike"], "prey": [], "predators": []}),
        ],
        ["z10_0581_0294", "z10_0580_0295"],
    )
    assert json.loads(big)["fish"] == [{"kind": "pike", "count": 2}]
    assert json.loads(big)["playable"] == ["pike"]
    assert json.loads(big)["predators"] == ["pike"]
    plant = build_tiles_overlay(
        "plant",
        "cloudberry",
        tmp_path,
        [_made({}, {"cover": [{"id": "cloudberry", "density": 0.1}]})],
        ["z10_0581_0294"],
    )
    assert json.loads(plant) == {
        "format": 1,
        "plants": {"cover": [{"id": "cloudberry", "density": 0.1}]},
        "tiles": ["z10_0581_0294"],
    }


def test_overlay_refuses_loud() -> None:
    """#220: no tile, too many, a bad id, or nothing added all refuse."""
    from pathlib import Path

    world = Path("/nonexistent")
    with pytest.raises(ValueError, match="at least one tile"):
        build_tiles_overlay("animal", "lynx", world, [], [])
    many = [f"z10_{c:04d}_0294" for c in range(5001)]
    with pytest.raises(ValueError, match="tiles at most"):
        build_tiles_overlay(
            "animal", "lynx", world, [_made({"playable": ["lynx"]})] * 5001, many
        )
    assert check_tiles_overlay({"format": 1, "playable": ["lynx"], "tiles": []}) == [
        "tiles isn't a non-empty list"
    ]
    assert check_tiles_overlay(
        {"format": 1, "playable": ["lynx"], "tiles": ["z10_0581_0294", "nope"]}
    ) == ["tiles has a bad tile id"]
    assert check_tiles_overlay({"format": 1, "tiles": ["z10_0581_0294"]}) == [
        "an overlay adds nothing"
    ]
    assert check_tiles_overlay(
        {"format": 1, "playable": ["lynx"], "tiles": ["z10_0581_0294"], "food": []}
    ) == ["an overlay adds no food"]
