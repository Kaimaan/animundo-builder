"""Enrich checks in real target tiles, through the world's converter (#161).

The hand-built `zz_work` region is gone: for an enrich job each
target tile becomes its place exactly as the game builds it
(`tools/tile_region.py`), the new animal or plant is seated, the
food is worked out again, and the world's checks run there. A
tile inside a hand-made region instead checks in that region
file, which has no tile home to convert from (#168.1). The
world's code runs in its own process, as for every world tool;
the builder only feeds it the tile and reads back the place.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .world import mentions, problems_by_file

GAME_ONLY = ("level", "edges", "coast", "ecoregions", "habitat", "land", "plants")

_CONVERT = """
import copy, json, sys
tools, tile_path, what, kind = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
sys.path.insert(0, tools)
import food, layers, tile_region
tile = json.load(open(tile_path, encoding="utf-8"))
made = tile_region.place(tile)
region = {"id": "tl_" + tile["id"], "name": "Tile " + tile["id"],
          "country": "(tile)",
          "author": tile_region.read_biome(tile["home"]).get("author", "Animundo team")}
region.update({k: v for k, v in made.items() if k not in __game_only__})
if what:
    if kind == "plant":
        spec = layers.spec("plants", what) or {}
        seabed = "seabed" in (spec.get("grows", {}).get("on") or [])
        side = "water" if seabed else "cover"
        plants = copy.deepcopy(tile.get("plants") or {})
        grown = plants.get(side)
        if not isinstance(grown, list):
            grown = []
            plants[side] = grown
        if what not in [p.get("id") for p in grown if isinstance(p, dict)]:
            grown.append({"id": what, "density": 0.1})
        layers.use_plants(region, plants)
    else:
        lists = region.setdefault("animals", {}).setdefault("playable", [])
        if what not in lists:
            lists.append(what)
    region["food"] = food.worked_out(region)
print(json.dumps(region))
""".replace("__game_only__", repr(GAME_ONLY))


class ConvertError(RuntimeError):
    """The world's converter refused the tile."""


def tile_place(
    tile: dict[str, Any],
    world: Path,
    what: str = "",
    kind: str = "",
) -> dict[str, Any]:
    """One tile as its checkable place, the new thing seated (#161.1).

    `tile` is a tile doc (spec 02 §2); `what`/`kind` seat the new
    animal or plant and rework the food. Needs a world with
    `tools/tile_region.py`.
    """
    tools = world / "tools"
    if not (tools / "tile_region.py").is_file():
        raise ConvertError(f"no tile converter in this checkout: {tools}")
    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as handle:
        json.dump(tile, handle)
        tile_path = handle.name
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _CONVERT, str(tools), tile_path, what, kind],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        Path(tile_path).unlink(missing_ok=True)
    if proc.returncode != 0:
        raise ConvertError(
            f"tile_region.py refused {tile.get('id')}: "
            f"{(proc.stdout + proc.stderr).strip()[:300]}"
        )
    try:
        return json.loads(proc.stdout)
    except ValueError:
        raise ConvertError(
            f"tile_region.py gave no JSON for {tile.get('id')}: "
            f"{proc.stdout.strip()[:200]}"
        )


def region_place(
    region_id: str,
    world: Path,
    what: str = "",
    kind: str = "",
) -> dict[str, Any]:
    """One hand-made region with the new thing seated (#168.1).

    An enrich item only adds: the animal to `playable` (its
    `fish` seat comes from the maker, which knows its file), a
    plant to its grown side. Food, terrain and everything else
    stay exactly as they are: reworking food rewrote the reef's
    (#173.1).
    """
    region_path = world / "regions" / f"{region_id}.json"
    try:
        region = json.loads(region_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ConvertError(f"the region file {region_id} won't read: {error}")
    if not what:
        return region
    if kind == "plant":
        side = "water" if _seabed_plant(world, what) else "cover"
        plants = region.setdefault("plants", {})
        grown = plants.get(side)
        if not isinstance(grown, list):
            grown = next((v for v in plants.values() if isinstance(v, list)), None)
            if grown is None:
                grown = []
                plants[side] = grown
        if what not in [p.get("id") for p in grown if isinstance(p, dict)]:
            grown.append({"id": what, "density": 0.1})
    else:
        animals = region.setdefault("animals", {})
        if isinstance(animals, dict):
            lists = animals.setdefault("playable", [])
        else:
            lists = animals
        if what not in lists:
            lists.append(what)
    return region


def _seabed_plant(world: Path, what: str) -> bool:
    """Whether the plant file grows on the seabed (#173.1)."""
    try:
        spec = json.loads((world / "models" / "plants" / f"{what}.json").read_text())
    except (OSError, ValueError):
        return False
    return "seabed" in ((spec.get("grows") or {}).get("on") or [])


MANIFEST = "enrich_places.json"


def _manifest(job_dir: Path) -> list[str] | None:
    """The intake's place stems, or None before #168.1 wrote one."""
    path = job_dir / "checks" / MANIFEST
    if not path.is_file():
        return None
    try:
        names = json.loads(path.read_text(encoding="utf-8")).get("places")
    except ValueError:
        return None
    if not isinstance(names, list):
        return None
    if not all(isinstance(name, str) for name in names):
        return None
    return names


def enrich_places(job_dir: Path) -> list[Path]:
    """The job's checked places, sorted; empty for a non-enrich job.

    The intake manifests its files: converted tiles
    (`tl_<tile>.json`) and hand-made regions (`<region id>.json`)
    alike (#168.1). Without a manifest only converted tiles
    count, so a build scaffold is never read as a place.
    """
    names = _manifest(job_dir)
    if names is None:
        return sorted((job_dir / "regions").glob("tl_*.json"))
    regions = job_dir / "regions"
    paths = [regions / f"{name}.json" for name in names]
    return sorted(path for path in paths if path.is_file())


def place_tiles(job_dir: Path) -> dict[str, str]:
    """Each place's region id (and file stem) to its tile (#161.2).

    A hand-made region maps to itself: its verdict line names
    the region, not one of its tiles (#168.1).
    """
    out: dict[str, str] = {}
    for path in enrich_places(job_dir):
        tile = path.stem[3:] if path.stem.startswith("tl_") else path.stem
        try:
            rid = json.loads(path.read_text(encoding="utf-8")).get("id")
        except ValueError:
            rid = None
        out[path.stem] = tile
        if isinstance(rid, str) and rid:
            out[rid] = tile
    return out


_IN_PLACE = re.compile(r"\bin ([A-Za-z0-9_]+)")
_REGION_OF = re.compile(r"region ([A-Za-z0-9_]+):")


def sort_problems(
    problems: list[str], what: str, rid_to_tile: dict[str, str]
) -> tuple[list[str], dict[str, list[str]], list[str]]:
    """Sort check problems: the model's, per tile's, the place's (#161.2).

    A failure in the new file is the model's and feeds the fix
    round. A failure in a place (food, cast) lands on its tile,
    for the report, with no model call. Anything else names
    neither the new file nor a known place and is the place's in
    general, never the model's.
    """
    model: list[str] = []
    tiled: dict[str, list[str]] = {}
    general: list[str] = []
    for line in problems:
        match = _IN_PLACE.search(line) or _REGION_OF.search(line)
        rid = match.group(1) if match else None
        if rid is not None:
            tile = rid_to_tile.get(rid)
            if tile is None:
                general.append(line)
            else:
                tiled.setdefault(tile, []).append(line)
        elif mentions(line, what):
            model.append(line)
        else:
            general.append(line)
    return model, tiled, general


NOT_BUILT = "build it first with tools/build_model.sh"


def split_not_built(problems: list[str]) -> tuple[list[str], list[str]]:
    """Real region problems, and models only not built here (#173.3).

    A fresh clone has no `.glb` (git-ignored), so "build it
    first" verdicts are unknown, not failures; the caller notes
    the models instead of failing their tiles. Returns the real
    problems and the model names, each in order, de-duplicated.
    """
    real: list[str] = []
    models: list[str] = []
    for line in problems:
        if NOT_BUILT not in line:
            real.append(line)
            continue
        rest = line.split(NOT_BUILT, 1)[1].strip().split()
        model = rest[0].rsplit("/", 1)[-1] if rest else ""
        model = model[:-5] if model.endswith(".json") else model
        if model and model not in models:
            models.append(model)
    return real, models


def sort_region_problems(
    problems: list[str], rid_to_tile: dict[str, str]
) -> tuple[dict[str, list[str]], list[str]]:
    """Sort region-verdict problems per tile, by their file (#161.2).

    Each failing place file lands its details on its tile; lines
    under no known file are the place's in general.
    """
    tiled: dict[str, list[str]] = {}
    general: list[str] = []
    for file, lines in problems_by_file(problems).items():
        cleaned = [
            line[2:].strip() if line.startswith("- ") else line.strip()
            for line in lines
        ]
        tile = None
        if file:
            stem = file.rsplit("/", 1)[-1]
            if stem.endswith(".json"):
                stem = stem[: -len(".json")]
            tile = rid_to_tile.get(stem)
        if tile is None:
            general.extend(cleaned)
        else:
            tiled.setdefault(tile, []).extend(cleaned)
    return tiled, general


def write_places(
    job_dir: Path,
    rid_to_tile: dict[str, str],
    tiled: dict[str, list[str]],
    general: list[str],
    region_note: str | None = None,
) -> None:
    """Each tile's place verdict, where the report finds it (#161.2).

    `checks/places.json` lists every converted tile as passed or
    failed with its reasons, plus place-general failures and the
    region check's skip note. The review ships the passed tiles
    and lists the rest instead of failing the whole job.
    """
    results: list[dict[str, Any]] = []
    for tile in sorted(set(rid_to_tile.values())):
        reasons = tiled.get(tile, [])
        results.append(
            {
                "check": f"place {tile}",
                "status": "fail" if reasons else "pass",
                "problems": reasons,
            }
        )
    if general:
        results.append({"check": "place", "status": "fail", "problems": general})
    if region_note:
        results.append(
            {
                "check": "region",
                "status": "skip",
                "reason": region_note,
                "problems": [],
            }
        )
    checks = job_dir / "checks"
    checks.mkdir(parents=True, exist_ok=True)
    (checks / "places.json").write_text(
        json.dumps(
            {
                "ok": all(r["status"] != "fail" for r in results),
                "results": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
