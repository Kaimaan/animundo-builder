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
    check_additions,
    check_pack,
    next_pack_version,
    publish_pack,
)


class _Store:
    """Storage enough for packs: blobs under string names."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def list_blobs(self, prefix: str) -> list[str]:
        return sorted(n for n in self.blobs if n.startswith(prefix))

    def write_blob(self, name: str, data: bytes) -> None:
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
    assert check_additions({"format": 1, "animals": ["testfish"]}) == [
        "an addition may only add fish, playable, predators and prey, not animals"
    ]
    assert check_additions({"format": 1, "food": []}) == [
        "an addition may only add fish, playable, predators and prey, not food"
    ]
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
