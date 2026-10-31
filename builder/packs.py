"""Content packs to Storage: preview-shaped, game-checked, never rewritten.

An enrich item that passes every check ships as a public pack (#176):
`packs/<item id>/<version>/` with the animal file, its built model
and preview, its English and Finnish texts, one `additions/` file per
hand-made place, and a manifest. The game rules mirror the game's own
`scripts/preview.gd` (paths, extensions, sizes, hashes), plus the live
packs' `additions/` folder, which the preview game code does not know
yet. A pack is never rewritten: a later publish is a later version.
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

#: Where pack files may sit: the game's folders plus `additions/`.
DATA_DIRS = (
    "regions/",
    "tiles/",
    "homes/",
    "plants/",
    "animals/",
    "models/",
    "locale/",
    "previews/",
    "additions/",
)
DATA_EXTS = (".json", ".glb", ".png")

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class PackExists(Exception):
    """The pack version is already published; publish a later one."""


def assemble_enrich_pack(
    pack_id: str, version: int, files: dict[str, bytes]
) -> dict[str, bytes]:
    """The pack's files plus its manifest, validated, unjudged.

    The manifest carries the preview pack's shape (`format`, every
    file's size and SHA-256) plus the pack id, `kind: enrich` and
    `min_game`. Whether the game would take it is `check_pack`'s call.
    """
    if not files:
        raise ValueError("a pack needs at least one file")
    manifest = {
        "pack": pack_id,
        "version": version,
        "format": PACK_FORMAT,
        "kind": "enrich",
        "min_game": MIN_GAME,
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


def check_pack(pack_id: str, files: dict[str, bytes]) -> list[str]:
    """Why the game would refuse the pack, or nothing when it ships.

    Mirrors `scripts/preview.gd` (`manifest_error`, `file_error`,
    and `open`'s size-and-hash check on every file), with `additions/`
    allowed beside the preview folders.
    """
    raw = files.get("manifest.json")
    if raw is None:
        return ["no manifest.json"]
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except ValueError:
        return ["manifest.json is no JSON object"]
    if not isinstance(manifest, dict):
        return ["manifest.json is no JSON object"]
    problems = _manifest_problems(manifest, pack_id)
    if problems:
        return problems
    entries = manifest["files"]
    total = 0
    for entry in entries:
        problems.extend(_entry_problems(entry, files))
        if isinstance(entry.get("bytes"), int):
            total += entry["bytes"]
    if total > MAX_PACK_BYTES:
        problems.append("bigger than 8 MB")
    return problems


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
        store.write_blob(prefix + rel, files[rel])
    return prefix


def _manifest_problems(manifest: dict[str, Any], pack_id: str) -> list[str]:
    for key in ("pack", "version", "format", "kind", "min_game", "files"):
        if key not in manifest:
            return [f"manifest misses {key}"]
    if manifest["pack"] != pack_id:
        return ["manifest is for another pack"]
    if (
        not isinstance(manifest["version"], int)
        or isinstance(manifest["version"], bool)
        or manifest["version"] < 1
    ):
        return ["version is not a positive integer"]
    if (
        not isinstance(manifest["format"], int)
        or isinstance(manifest["format"], bool)
        or manifest["format"] < 1
    ):
        return ["format is not a positive integer"]
    if manifest["format"] > PACK_FORMAT:
        return ["format is newer than this game reads"]
    if manifest["kind"] != "enrich":
        return ["kind is not enrich"]
    if (
        not isinstance(manifest["min_game"], int)
        or isinstance(manifest["min_game"], bool)
        or manifest["min_game"] < 1
    ):
        return ["min_game is not a positive integer"]
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
    if not isinstance(size, int) or isinstance(size, bool):
        return [f"bad size for {path}"]
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
