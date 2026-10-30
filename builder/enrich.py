"""Enrich checks in real target tiles, through the world's converter (#161).

The hand-built `zz_work` region is gone: for an enrich job each
target tile becomes its place exactly as the game builds it
(`tools/tile_region.py`), the new animal or plant is seated, the
food is worked out again, and the world's checks run there. The
world's code runs in its own process, as for every world tool;
the builder only feeds it the tile and reads back the place.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

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
