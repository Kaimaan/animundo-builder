"""Content packs: assembly, the game's rules, versions. No network."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from builder.packs import (
    MAX_GLB_BYTES,
    MAX_PACK_BYTES,
    PackExists,
    assemble_enrich_pack,
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
    assert check_pack("animal_hammerhead", pack) == []
    manifest = _manifest(pack)
    assert manifest["pack"] == "animal_hammerhead"
    assert manifest["version"] == 1
    assert manifest["format"] == 1
    assert manifest["kind"] == "enrich"
    assert manifest["min_game"] == 1
    assert {e["path"] for e in manifest["files"]} == set(_files())


def test_tampered_file_fails_its_size_and_hash() -> None:
    pack = assemble_enrich_pack("animal_hammerhead", 1, _files())
    pack["models/hammerhead.glb"] = b"other-bytes"
    assert check_pack("animal_hammerhead", pack) == [
        "models/hammerhead.glb fails its size and hash"
    ]


def test_pack_rules_mirror_the_game() -> None:
    """#176.3: paths, extensions, sizes, hashes, as preview.gd reads."""
    pack = assemble_enrich_pack("animal_hammerhead", 1, _files())
    manifest = _manifest(pack)
    manifest["files"].append(
        {
            "path": "../escape.json",
            "sha256": hashlib.sha256(b"x").hexdigest(),
            "bytes": 1,
        }
    )
    pack["manifest.json"] = json.dumps(manifest).encode("utf-8")
    assert check_pack("animal_hammerhead", pack) == ["bad path ../escape.json"]
    pack = assemble_enrich_pack("animal_hammerhead", 1, _files())
    manifest = _manifest(pack)
    manifest["files"].append(
        {
            "path": "scripts/run.gd",
            "sha256": hashlib.sha256(b"x").hexdigest(),
            "bytes": 1,
        }
    )
    pack["manifest.json"] = json.dumps(manifest).encode("utf-8")
    assert check_pack("animal_hammerhead", pack) == [
        "file outside the pack folders: scripts/run.gd"
    ]
    pack = dict(_files())
    pack["animals/notes.txt"] = b"a note"
    pack = assemble_enrich_pack("animal_hammerhead", 1, pack)
    assert check_pack("animal_hammerhead", pack) == ["not data: animals/notes.txt"]


def test_pack_size_limits() -> None:
    big = dict(_files())
    big["models/hammerhead.glb"] = b"g" * (MAX_GLB_BYTES + 1)
    pack = assemble_enrich_pack("animal_hammerhead", 1, big)
    assert check_pack("animal_hammerhead", pack) == [
        ".glb bigger than 2 MB: models/hammerhead.glb"
    ]
    huge = dict(_files())
    huge["models/hammerhead.glb"] = b"g" * (MAX_PACK_BYTES + 1)
    pack = assemble_enrich_pack("animal_hammerhead", 1, huge)
    problems = check_pack("animal_hammerhead", pack)
    assert ".glb bigger than 2 MB: models/hammerhead.glb" in problems
    assert "bigger than 8 MB" in problems


def test_second_publish_leaves_the_first_untouched() -> None:
    """#176.4: version 2 publishes beside version 1, never over it."""
    store = _Store()
    first = assemble_enrich_pack("animal_hammerhead", 1, _files())
    assert check_pack("animal_hammerhead", first) == []
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
