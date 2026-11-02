"""Content packs to Storage: preview-shaped, game-checked, never rewritten.

An enrich item that passes every check ships as a public pack (#176,
#179): `packs/<item id>/<version>/` with the animal file, its built
model and preview, its English and Finnish texts, and a manifest;
no additions since #202.3 (old packs keep theirs, and the game
still merges them). Spec 02 §8b is the
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
#: The newest game release the checks mirror: level 2 reads
#: `additions/tiles.json`, so a pack may ask for up to this (#220).
GAME_LEVEL = 2
MAX_GLB_BYTES = 2 * 1024 * 1024
MAX_PACK_BYTES = 8 * 1024 * 1024
MANIFEST_MAX = 65536
#: Most tiles one overlay names (spec 02 §8b).
MAX_OVERLAY_TILES = 5000

#: Cover and water plants a tile may hold after its overlays
#: (`ContentPacks.MAX_TILE_PLANTS`, 0043's limit).
MAX_TILE_PLANTS = 24

#: Where an enrich pack's files may sit (spec 02 §8b).
DATA_DIRS = ("animals/", "models/", "locale/", "additions/")
DATA_EXTS = (".json", ".glb", ".png")

#: A pack id, and an additions id (live_packs.gd, content_packs.gd).
PACK_ID = re.compile(r"^[a-z0-9_]{1,64}$")
GAME_ID = re.compile(r"^[a-z0-9_]+$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

#: An additions file's only keys (content_packs.gd).
ADDITION_KEYS = ("format", "fish", "playable", "predators", "prey", "plants")
ADDITION_LISTS = ("playable", "predators", "prey")
MAX_ADDED_FISH = 50

#: A plants addition's only sides, and most entries a side takes (#188.2).
PLANT_SIDES = ("cover", "water")
MAX_ADDED_PLANTS = 20

#: An overlay's only keys: an additions file's, plus its tiles (#220).
OVERLAY_KEYS = ADDITION_KEYS + ("tiles",)


class PackExists(Exception):
    """The pack version is already published; publish a later one."""


def assemble_enrich_pack(
    pack_id: str,
    version: int,
    files: dict[str, bytes],
    min_game: int = MIN_GAME,
) -> dict[str, bytes]:
    """The pack's files plus its manifest, validated, unjudged.

    The manifest is spec 02 §8b's (`format`, `version`, `min_game`,
    `kind`, every file's size and SHA-256); the folder names the
    pack. A pack with `additions/tiles.json` asks for game 2 (#220).
    Whether the game would take it is `check_pack`'s call.
    """
    if not files:
        raise ValueError("a pack needs at least one file")
    if not PACK_ID.match(pack_id):
        raise ValueError(f"{pack_id} is no pack id")
    manifest = {
        "format": PACK_FORMAT,
        "version": version,
        "min_game": min_game,
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
    six keys, lists of game ids, fish exactly `kind` and `count`,
    plants exactly `id` and `density` on `cover` or `water` (#188.2).
    """
    if not isinstance(additions, dict):
        return ["not an object"]
    for key in additions:
        if str(key) not in ADDITION_KEYS:
            return [
                "an addition may only add fish, playable, predators, prey "
                f"and plants, not {key}"
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
    if "plants" in additions:
        return _plants_problems(additions["plants"])
    return []


def _plants_problems(plants: Any) -> list[str]:
    """Why the game would refuse a plants addition, or nothing (#188.2)."""
    if not isinstance(plants, dict):
        return ["plants isn't {cover, water}"]
    for side in plants:
        if str(side) not in PLANT_SIDES:
            return [f"plants adds no {side} side"]
    for side in PLANT_SIDES:
        if side not in plants:
            continue
        entries = plants[side]
        if not isinstance(entries, list):
            return [f"plants.{side} isn't a list"]
        if len(entries) > MAX_ADDED_PLANTS:
            return [f"plants.{side} adds more than 20"]
        for entry in entries:
            if (
                not isinstance(entry, dict)
                or set(entry) != {"id", "density"}
                or not isinstance(entry.get("id"), str)
                or not GAME_ID.match(entry["id"])
                or isinstance(entry.get("density"), bool)
                or not isinstance(entry.get("density"), (int, float))
                or not 0 < float(entry["density"]) <= 1
            ):
                return ["a plant isn't {id, density} (above 0, at most 1)"]
    return []


def build_tiles_overlay(
    kind: str,
    what: str,
    world: Any,
    made_tiles: list[dict[str, Any]],
    tiles: list[str],
    plants: dict[str, list[dict[str, Any]]] | None = None,
) -> bytes:
    """`additions/tiles.json`: one species onto its passing tiles (#220).

    The species sits in the same lists the remakes put it in (their
    union over the tiles); a fish also swims with its count (#178.3:
    5 small, else 2), a plant with its first tile's seating. Carried
    food plants ride along for an animal (#231.2). The game merges
    it into each listed tile on load. Refuses loud when no tile
    passed or the file breaks the game's rules.
    """
    if not tiles:
        raise ValueError("an overlay needs at least one tile")
    if len(tiles) > MAX_OVERLAY_TILES:
        raise ValueError(f"an overlay holds {MAX_OVERLAY_TILES} tiles at most")
    doc: dict[str, Any] = {"format": 1}
    if kind == "plant":
        side, density = _overlay_plant_seating(made_tiles, what)
        doc["plants"] = {side: [{"id": what, "density": density}]}
    else:
        for name in ADDITION_LISTS:
            seated = (
                what in (made.get("species") or {}).get(name, []) for made in made_tiles
            )
            if any(seated):
                doc[name] = [what]
        fish = _overlay_fish(world, what)
        if fish is not None:
            doc["fish"] = [fish]
        if plants:
            doc["plants"] = plants
    doc["tiles"] = list(tiles)
    problems = check_tiles_overlay(doc)
    if problems:
        raise ValueError(f"bad overlay: {problems[0]}")
    return json.dumps(doc, separators=(",", ":")).encode("utf-8")


def check_tiles_overlay(overlay: Any) -> list[str]:
    """Why the game would refuse `additions/tiles.json`, or nothing (#220).

    An additions file's keys and rules, plus `tiles`: 1 to 5,000
    grid tile ids, and at least one species to add.
    """
    from .grid import parse_tile

    if not isinstance(overlay, dict):
        return ["not an object"]
    for key in overlay:
        if str(key) not in OVERLAY_KEYS:
            return [f"an overlay adds no {key}"]
    if overlay.get("format") != 1:
        return ["format isn't 1"]
    rest = {key: value for key, value in overlay.items() if key != "tiles"}
    problems = check_additions(rest)
    if problems:
        return problems
    adds = ("fish", "playable", "predators", "prey", "plants")
    if not any(key in overlay for key in adds):
        return ["an overlay adds nothing"]
    tiles = overlay.get("tiles")
    if not isinstance(tiles, list) or not tiles:
        return ["tiles isn't a non-empty list"]
    if len(tiles) > MAX_OVERLAY_TILES:
        return [f"tiles holds more than {MAX_OVERLAY_TILES}"]
    for tile in tiles:
        try:
            parse_tile(tile)
        except (ValueError, TypeError):
            return ["tiles has a bad tile id"]
    return []


def _overlay_fish(world: Any, what: str) -> dict[str, Any] | None:
    """The fish entry, or nothing when it doesn't swim (#178.3)."""
    try:
        raw = (world / "animals" / f"{what}.json").read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    tags = data.get("tags") or []
    model = data.get("model") or {}
    if (
        data.get("archetype") != "fish"
        and model.get("archetype") != "fish"
        and "fish" not in tags
    ):
        return None
    return {"kind": what, "count": 5 if "small_fish" in tags else 2}


def _overlay_plant_seating(
    made_tiles: list[dict[str, Any]], what: str
) -> tuple[str, float]:
    """The first tile's side and density for the plant (#220)."""
    for made in made_tiles:
        plants = made.get("plants") or {}
        for side in PLANT_SIDES:
            for entry in plants.get(side, []) or []:
                if isinstance(entry, dict) and entry.get("id") == what:
                    return side, entry["density"]
    raise ValueError(f"{what} is seated on no passing tile")


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
    if not _is_int(min_game) or int(min_game) > GAME_LEVEL:
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
