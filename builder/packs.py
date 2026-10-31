"""Content packs to Storage: preview-shaped, game-checked, never rewritten.

An enrich item that passes every check ships as a public pack (#176,
#179): `packs/<item id>/<version>/` with the animal file, its built
model and preview, its English and Finnish texts, one `additions/`
file per hand-made place, and a manifest. Spec 02 §8b is the
contract; the rules mirror the game's own `scripts/preview.gd`
(`files_error`, `file_error`: paths, extensions, sizes, hashes) and
`scripts/content_packs.gd` (`addition_error`), enforced at the §8b
shape (folders, manifest keys, 64 KB manifest). A pack is never
rewritten: a later publish is a later version.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .schemas import validate

PACK_FORMAT = 1
MIN_GAME = 1
MAX_GLB_BYTES = 2 * 1024 * 1024
MAX_PACK_BYTES = 8 * 1024 * 1024
MANIFEST_MAX = 65536

#: Where an enrich pack's files may sit (spec 02 §8b).
DATA_DIRS = ("animals/", "models/", "locale/", "additions/")
DATA_EXTS = (".json", ".glb", ".png")

#: A pack id, and an additions id (live_packs.gd, content_packs.gd).
PACK_ID = re.compile(r"^[a-z0-9_]{1,64}$")
GAME_ID = re.compile(r"^[a-z0-9_]+$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

#: An additions file's only keys (content_packs.gd).
ADDITION_KEYS = ("format", "fish", "playable", "predators", "prey")
ADDITION_LISTS = ("playable", "predators", "prey")
MAX_ADDED_FISH = 50


class PackExists(Exception):
    """The pack version is already published; publish a later one."""


def assemble_enrich_pack(
    pack_id: str, version: int, files: dict[str, bytes]
) -> dict[str, bytes]:
    """The pack's files plus its manifest, validated, unjudged.

    The manifest is spec 02 §8b's (`format`, `version`, `min_game`,
    `kind`, every file's size and SHA-256); the folder names the
    pack. Whether the game would take it is `check_pack`'s call.
    """
    if not files:
        raise ValueError("a pack needs at least one file")
    if not PACK_ID.match(pack_id):
        raise ValueError(f"{pack_id} is no pack id")
    manifest = {
        "format": PACK_FORMAT,
        "version": version,
        "min_game": MIN_GAME,
        "kind": "enrich",
        "files": [
            {
                "path": rel,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
            for rel, data in sorted(files.items())
        ],
    }
    validate("pack-manifest", manifest)
    packed = dict(files)
    packed["manifest.json"] = json.dumps(manifest, indent=2).encode("utf-8")
    return packed


def check_pack(files: dict[str, bytes]) -> list[str]:
    """Why the game would refuse the pack, or nothing when it ships.

    Mirrors `scripts/preview.gd` (`files_error`, `file_error`, and
    the download's size-and-hash check on every file) at spec 02
    §8b's shape: the enrich folders, `kind: enrich`, and a manifest
    of at most 64 KB.
    """
    raw = files.get("manifest.json")
    if raw is None:
        return ["no manifest.json"]
    if len(raw) > MANIFEST_MAX:
        return ["manifest.json is bigger than 64 KB"]
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except ValueError:
        return ["manifest.json is no JSON object"]
    if not isinstance(manifest, dict):
        return ["manifest.json is no JSON object"]
    problems = _manifest_problems(manifest)
    if problems:
        return problems
    entries = manifest["files"]
    total = 0
    for entry in entries:
        problems.extend(_entry_problems(entry, files))
        size = entry.get("bytes") if isinstance(entry, dict) else None
        if _is_int(size):
            total += int(size)
    if total > MAX_PACK_BYTES:
        problems.append("bigger than 8 MB")
    return problems


def check_additions(additions: Any) -> list[str]:
    """Why the game would refuse an additions file, or nothing (#179).

    Mirrors `scripts/content_packs.gd` (`addition_error`): only the
    five keys, lists of game ids, fish exactly `kind` and `count`.
    """
    if not isinstance(additions, dict):
        return ["not an object"]
    for key in additions:
        if str(key) not in ADDITION_KEYS:
            return [
                "an addition may only add fish, playable, predators and prey, "
                f"not {key}"
            ]
    if "format" in additions and not (
        _is_int(additions["format"]) and int(additions["format"]) == 1
    ):
        return ["format isn't 1"]
    for key in ADDITION_LISTS:
        if key not in additions:
            continue
        if not isinstance(additions[key], list):
            return [f"{key} isn't a list"]
        for animal_id in additions[key]:
            if not isinstance(animal_id, str) or not GAME_ID.match(animal_id):
                return [f"{key} has a bad id"]
    if "fish" in additions:
        if not isinstance(additions["fish"], list):
            return ["fish isn't a list"]
        for entry in additions["fish"]:
            if (
                not isinstance(entry, dict)
                or len(entry) != 2
                or not isinstance(entry.get("kind"), str)
                or not GAME_ID.match(entry["kind"])
                or not _is_int(entry.get("count"))
                or not 1 <= int(entry["count"]) <= MAX_ADDED_FISH
            ):
                return ["a fish isn't {kind, count} (1 to 50)"]
    return []


def next_pack_version(store: Any, pack_id: str) -> int:
    """One past the pack's newest published version, or 1 when new."""
    prefix = f"packs/{pack_id}/"
    versions = []
    for name in store.list_blobs(prefix):
        head = name.removeprefix(prefix).split("/", 1)[0]
        if head.isdigit():
            versions.append(int(head))
    return max(versions, default=0) + 1


def publish_pack(
    store: Any, pack_id: str, version: int, files: dict[str, bytes]
) -> str:
    """The pack's files to Storage under its version; never rewritten.

    Returns the pack prefix (`packs/<id>/<version>/`). A version that
    already holds files refuses, so a republish must take a later one.
    """
    prefix = f"packs/{pack_id}/{version}/"
    if store.list_blobs(prefix):
        raise PackExists(f"{prefix} is already published")
    for rel in sorted(files):
        store.write_blob(prefix + rel, files[rel], _content_type(rel))
    return prefix


def _content_type(rel: str) -> str:
    if rel.endswith(".png"):
        return "image/png"
    if rel.endswith(".glb"):
        return "model/gltf-binary"
    return "application/json"


def _is_int(value: Any) -> bool:
    """An integer, as JSON reads one (preview.gd `_is_int`)."""
    return (isinstance(value, int) and not isinstance(value, bool)) or (
        isinstance(value, float) and value.is_integer()
    )


def _manifest_problems(manifest: dict[str, Any]) -> list[str]:
    for key in ("version", "format", "kind", "min_game", "files"):
        if key not in manifest:
            return [f"manifest misses {key}"]
    if not _is_int(manifest["version"]) or int(manifest["version"]) < 1:
        return ["version is not a positive integer"]
    if not _is_int(manifest["format"]) or int(manifest["format"]) < 1:
        return ["format is not a positive integer"]
    if int(manifest["format"]) > PACK_FORMAT:
        return ["format is newer than this game reads"]
    if manifest["kind"] != "enrich":
        return ["kind is not enrich"]
    min_game = manifest["min_game"]
    if not _is_int(min_game) or int(min_game) > MIN_GAME:
        shown = str(int(min_game)) if _is_int(min_game) else str(min_game)
        return [f"it needs a newer game (min_game {shown})"]
    if not isinstance(manifest["files"], list) or not manifest["files"]:
        return ["files is not a non-empty list"]
    return []


def _entry_problems(entry: Any, files: dict[str, bytes]) -> list[str]:
    if not isinstance(entry, dict):
        return ["a file entry is no object"]
    for key in ("path", "sha256", "bytes"):
        if key not in entry:
            return ["a file entry misses path, sha256 or bytes"]
    path = entry["path"]
    if not isinstance(path, str):
        return ["a file entry is no object"]
    if path == "" or path.startswith("/") or ".." in path or path == "manifest.json":
        return [f"bad path {path}"]
    if not path.startswith(DATA_DIRS):
        return [f"file outside the pack folders: {path}"]
    if not path.endswith(DATA_EXTS):
        return [f"not data: {path}"]
    size = entry["bytes"]
    if not _is_int(size):
        return [f"bad size for {path}"]
    size = int(size)
    if size < 0:
        return [f"negative size: {path}"]
    if path.endswith(".glb") and size > MAX_GLB_BYTES:
        return [f".glb bigger than 2 MB: {path}"]
    if not isinstance(entry["sha256"], str) or not _HEX64.match(entry["sha256"]):
        return [f"bad sha256 for {path}"]
    data = files.get(path)
    if data is None:
        return [f"manifest lists {path}, which is not in the pack"]
    if len(data) != size or hashlib.sha256(data).hexdigest() != entry["sha256"]:
        return [f"{path} fails its size and hash"]
    return []
