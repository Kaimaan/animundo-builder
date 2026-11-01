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
        on = spec.get("grows", {}).get("on") or []
        side = "water" if "seabed" in on or "reef" in on else "cover"
        plants = copy.deepcopy(tile.get("plants") or {})
        grown = plants.get(side)
        if not isinstance(grown, list):
            grown = []
            plants[side] = grown
        if what not in [p.get("id") for p in grown if isinstance(p, dict)]:
            grown.append({"id": what, "density": 0.2 if "reef" in on else 0.1})
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

    An enrich item only adds: the animal to `playable`, a
    plant to its grown side. Food, terrain and everything else
    stay exactly as they are: reworking food rewrote the reef's
    (#173.1). A place on a `biome` writes the new way, as the
    game reads it (#178): the animal to `species.playable`,
    never `animals` (the region check refuses that); a plant
    or fish onto its side's biome list when the place writes
    none of its own, or the resolved place loses the biome's.
    """
    region_path = world / "regions" / f"{region_id}.json"
    try:
        region = json.loads(region_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ConvertError(f"the region file {region_id} won't read: {error}")
    if not what:
        return region
    biome_id = region.get("biome") if isinstance(region, dict) else None
    if kind == "plant":
        grows = _plant_grows(world, what)
        on = grows.get("on") or []
        side = "water" if "seabed" in on or "reef" in on else "cover"
        if biome_id is not None and not isinstance(
            (region.get("plants") or {}).get(side), list
        ):
            # No list of its own: the biome's, or resolve drops
            # every plant on this side (#178.2).
            region.setdefault("plants", {})[side] = _biome_list(
                world, biome_id, region_id, "plants", side
            )
        _seat_plant(region, what, side, plant_entry_density(grows))
    elif biome_id is not None:
        species = region.setdefault("species", {})
        if isinstance(species, dict):
            lists = species.setdefault("playable", [])
        else:
            lists = species
        if what not in lists:
            lists.append(what)
        small: bool | None = _fish_size(world, what)
        if small is not None:
            # A fish: the biome's fish first, like the game's
            # live packs, then the new one with its count (#178.3).
            if not isinstance(region.get("fish"), list):
                region["fish"] = _biome_list(world, biome_id, region_id, "fish")
            fish = region["fish"]
            if what not in [e.get("kind") for e in fish if isinstance(e, dict)]:
                fish.append({"kind": what, "count": 5 if small else 2})
    else:
        animals = region.setdefault("animals", {})
        if isinstance(animals, dict):
            lists = animals.setdefault("playable", [])
        else:
            lists = animals
        if what not in lists:
            lists.append(what)
    return region


def _seat_plant(
    region: dict[str, Any], what: str, side: str, density: float = 0.1
) -> None:
    """The plant onto its grown side, once (#168.1)."""
    plants = region.setdefault("plants", {})
    grown = plants.get(side)
    if not isinstance(grown, list):
        grown = next((v for v in plants.values() if isinstance(v, list)), None)
        if grown is None:
            grown = []
            plants[side] = grown
    if what not in [p.get("id") for p in grown if isinstance(p, dict)]:
        grown.append({"id": what, "density": density})


#: A coral's density on the rock when an enrich item adds it (#184.3).
CORAL_DENSITY = 0.2

#: Any other plant's density when seated (#168.1).
SEAT_DENSITY = 0.1


def plant_entry_density(grows: dict[str, Any]) -> float:
    """The density a new plant takes on its grown side (#184.3).

    A coral on the rock takes 0.2; everything else the 0.1 a
    seating has always used. Laid places and filed biomes use
    the same rule, so the checks run on what ships.
    """
    return CORAL_DENSITY if "reef" in (grows.get("on") or []) else SEAT_DENSITY


def biome_add_plant(
    biome: dict[str, Any], what: str, side: str, density: float
) -> dict[str, Any]:
    """A copy of the biome with the plant on its grown side (#184.3).

    The biome itself is never touched. Once, never twice: an
    id already listed stays as it is.
    """
    import copy

    biome = copy.deepcopy(biome)
    grown = (biome.get("plants") or {}).get(side)
    if not isinstance(grown, list):
        grown = []
        biome.setdefault("plants", {})[side] = grown
    if what not in [p.get("id") for p in grown if isinstance(p, dict)]:
        grown.append({"id": what, "density": density})
    return biome


def plant_region_additions_only(
    before: dict[str, Any], after: dict[str, Any], what: str
) -> list[str]:
    """Why the region change is not the one plant, or nothing (#184.4).

    An enrich plant on a region of its own only appends its id
    to one plants side. Anything else changed, removed, or
    added is one problem per offending key.
    """
    problems = []
    if not _plant_listed(after.get("plants"), what):
        return [f"{what} is not in the region's plants"]
    for key in after:
        if key not in before and key != "plants":
            problems.append(f"{key} was added")
            continue
        if key == "plants":
            problems.extend(_added_plant_side(before.get(key), after[key], what))
        elif after[key] != before.get(key):
            problems.append(f"{key} changed")
    for key in before:
        if key not in after:
            problems.append(f"{key} was removed")
    return problems


def biome_additions_only(
    before: dict[str, Any], after: dict[str, Any], what: str
) -> list[str]:
    """Why the biome change is not the one addition, or nothing (#184.3).

    An enrich plant on a biome-place only appends its id to one
    plants side. Anything else changed, removed, reordered, or
    added is one problem per offending key.
    """
    problems = []
    if not _plant_listed(after.get("plants"), what):
        return [f"{what} is not in the biome's plants"]
    for key in after:
        if key not in before and key != "plants":
            problems.append(f"{key} was added")
            continue
        if key == "plants":
            problems.extend(_added_plant_side(before.get(key), after[key], what))
        elif after[key] != before.get(key):
            problems.append(f"{key} changed")
    for key in before:
        if key not in after:
            problems.append(f"{key} was removed")
    return problems


def _plant_listed(plants: Any, what: str) -> bool:
    """Whether the cover or water lists name the plant."""
    return any(
        entry.get("id") == what
        for side in ("cover", "water")
        for entry in ((plants or {}).get(side, []) or [])
        if isinstance(entry, dict)
    )


def _added_plant_side(before: Any, after: Any, what: str) -> list[str]:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return ["plants changed shape"] if after != before else []
    problems = []
    for side in after:
        if side not in before and side not in ("cover", "water", "trees"):
            problems.append(f"plants.{side} was added")
            continue
        if side not in ("cover", "water"):
            if after[side] != before.get(side):
                problems.append(f"plants.{side} changed")
            continue
        first = before.get(side, [])
        second = after[side]
        if not isinstance(first, list) or not isinstance(second, list):
            if second != first:
                problems.append(f"plants.{side} changed shape")
            continue
        if len(second) == len(first) + 1 and second[:-1] == first:
            last = second[-1]
            if not isinstance(last, dict) or last.get("id") != what:
                problems.append(f"plants.{side} added no {what}")
            elif not isinstance(last.get("density"), (int, float)):
                problems.append(f"plants.{side} added {what} with no density")
        elif second != first:
            problems.append(f"plants.{side} changed")
    for side in before:
        if side not in after:
            problems.append(f"plants.{side} was removed")
    return problems


def run_flatten(world: Path) -> str:
    """The world's flatten in this checkout: biomes/ to homes/ (#184.3).

    Returns its output. A missing tool or a failing run stops
    loudly: homes/ is made from biomes/, never by hand.
    """
    tool = world / "tools" / "biomes.py"
    if not tool.is_file():
        raise ConvertError(f"no biome flattener in this checkout: {tool}")
    try:
        proc = subprocess.run(
            [sys.executable, str(tool), "flatten"],
            cwd=world,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ConvertError(f"biomes.py flatten did not run: {error}")
    if proc.returncode != 0:
        raise ConvertError(
            f"biomes.py flatten failed: {(proc.stdout + proc.stderr).strip()[:300]}"
        )
    return (proc.stdout or "").strip()


def _biome_list(
    world: Path, biome_id: str, region_id: str, key: str, side: str = ""
) -> list[Any]:
    """A copy of the biome's list for this side, or nothing (#178.2).

    The copy seats the new thing; the biome file itself is never
    touched. A biome that won't read stops the item loudly: seating
    on nothing would silently drop the whole side.
    """
    import copy

    try:
        biome = json.loads(
            (world / "biomes" / f"{biome_id}.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as error:
        raise ConvertError(f"the biome {biome_id} of {region_id} won't read: {error}")
    if key == "fish":
        found = biome.get("fish", [])
    else:
        found = (biome.get("plants") or {}).get(side, [])
    return copy.deepcopy(found) if isinstance(found, list) else []


def _fish_size(world: Path, what: str) -> bool | None:
    """Whether the new animal swims small, or nothing when it doesn't.

    The animal file says (tags, or a made file's archetype);
    before it is written the animal list's file example does:
    a fish adapts from a fish, a whale from a seal (#178.3).
    None when neither says fish, or nothing says anything.
    """
    data = _animal_data(world, what)
    if data is None:
        data = _animal_data(world, _file_example(world, what))
    if data is None:
        return None
    tags = data.get("tags") or []
    if (data.get("model") or {}).get("archetype") == "fish" or "fish" in tags:
        return "small_fish" in tags
    return None


def _animal_data(world: Path, what: str | None) -> dict[str, Any] | None:
    """The animal file's JSON, or nothing without one."""
    if not what:
        return None
    try:
        data = json.loads(
            (world / "animals" / f"{what}.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _file_example(world: Path, what: str) -> str | None:
    """The animal list's file example for this id, if it names one."""
    try:
        listed = json.loads((world / "animal-list.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    animals = listed.get("animals") if isinstance(listed, dict) else None
    if not isinstance(animals, list):
        return None
    for entry in animals:
        if isinstance(entry, dict) and entry.get("id") == what:
            return entry.get("file_from") or entry.get("start_from")
    return None


def region_additions_only(
    before: dict[str, Any],
    after: dict[str, Any],
    animal_id: str,
    world: Path | None = None,
) -> list[str]:
    """Why the region change is not additions-only, or nothing (#174.1).

    An enrich item only adds: the animal to `fish` (or `animals`)
    and to `animals.playable` / `predators` / `prey`. Anything
    else changed, removed, reordered, or added is one problem per
    offending key. A place on a `biome` adds to `species` instead
    of `animals`, and its `fish` starts from the biome's, so the
    biome's lists are the base the change is weighed against
    (#178): `world` must be given to read them.
    """
    problems = []
    biome_id = before.get("biome") if isinstance(before, dict) else None
    for key in after:
        if key not in before and key not in ("animals", "fish", "species"):
            problems.append(f"{key} was added")
            continue
        if key in ("animals", "species"):
            problems.extend(
                _added_lists(before.get(key, {}), after[key], animal_id, key)
            )
        elif key == "fish":
            if key not in before and biome_id is not None and world is not None:
                base = _gate_biome_fish(world, biome_id, before)
                if base is None:
                    problems.append("fish has no base: the biome won't read")
                    continue
            else:
                base = before.get(key, [])
            problems.extend(_added_fish(base, after[key], animal_id))
        elif after[key] != before[key]:
            problems.append(f"{key} changed")
    for key in before:
        if key not in after:
            problems.append(f"{key} was removed")
    return problems


def _gate_biome_fish(
    world: Path | None, biome_id: Any, before: dict[str, Any]
) -> list[Any] | None:
    """The fish the laid place should start from, or nothing unreadable."""
    if world is None or not isinstance(biome_id, str):
        return None
    try:
        return _biome_list(world, biome_id, str(before.get("id", "?")), "fish")
    except ConvertError:
        return None


def _added_lists(
    before: Any, after: Any, animal_id: str, key: str = "animals"
) -> list[str]:
    if not isinstance(before, dict) or not isinstance(after, dict):
        if after != before:
            return [f"{key} changed shape"]
        return []
    problems = []
    for group in ("playable", "predators", "prey"):
        old = before.get(group, [])
        new = after.get(group, [])
        if not isinstance(old, list) or not isinstance(new, list):
            problems.append(f"{key}.{group} changed shape")
            continue
        if [i for i in new if i != animal_id] != old:
            problems.append(f"{key}.{group} changed beyond {animal_id}")
        if new.count(animal_id) > 1:
            problems.append(f"{key}.{group} lists {animal_id} twice")
    for group in after:
        if group not in ("playable", "predators", "prey"):
            if group not in before:
                problems.append(f"{key}.{group} was added")
            elif after[group] != before[group]:
                problems.append(f"{key}.{group} changed")
    for group in before:
        if group not in after:
            problems.append(f"{key}.{group} was removed")
    return problems


def _added_fish(before: Any, after: Any, animal_id: str) -> list[str]:
    if not isinstance(before, list) or not isinstance(after, list):
        if after != before:
            return ["fish changed shape"]
        return []
    rest = [
        e for e in after if not (isinstance(e, dict) and e.get("kind") == animal_id)
    ]
    if rest != before:
        return [f"fish changed beyond {animal_id}"]
    if sum(1 for e in after if isinstance(e, dict) and e.get("kind") == animal_id) > 1:
        return [f"fish lists {animal_id} twice"]
    return []


def _plant_grows(world: Path, what: str) -> dict[str, Any]:
    """The plant file's grows, or nothing when it won't read."""
    try:
        spec = json.loads((world / "models" / "plants" / f"{what}.json").read_text())
    except (OSError, ValueError):
        return {}
    grows = spec.get("grows") or {}
    return grows if isinstance(grows, dict) else {}


def _water_plant(world: Path, what: str) -> bool:
    """Whether the plant file grows under the water (#173.1, #184.1).

    Seabed and reef rock both grow in water; everything else
    grows in cover.
    """
    on = _plant_grows(world, what).get("on") or []
    return "seabed" in on or "reef" in on


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
