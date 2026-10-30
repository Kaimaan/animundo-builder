"""The tile maker: one honest tile per cell, by code plus one AI call.

`make_tile()` reads the habitat grid, the world's homes and animals,
and Muse Spark's species for the cell, and returns either a tile or
a blocked reason. A cell the maker can't make honestly is blocked
(`no_home`, `no_animal`, `excluded`, `species_place`), never filled
with the wrong land or animals.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .grid import (
    CELL_M,
    CELLS,
    GridName,
    center,
    grid_north,
    parse_tile,
    tile_at,
    tile_id,
)
from .habitats import HabitatGrid, habitat_for
from .presence import animals_for_habitat
from .schemas import validate
from .species import MODEL as SPECIES_MODEL
from .species import Source as SpeciesSource
from .species import (
    SpeciesPlaceError,
    check_answer,
    check_survey,
    fetch_species,
    library_fingerprint,
    list_animals,
    list_fingerprint,
    list_library,
    rank_weights,
    survey_facts,
)

#: The shared water level every home builds to.
WATER_LEVEL = 8

#: Sea and ocean floors sit this far under the water.
SEA_DEPTH = 3

#: Edge samples per side.
EDGE_SAMPLES = 65

#: Noise lattice in real metres (256 m in the game, 25 m to 1).
NOISE_LATTICE_M = 6400


def available_homes(world: Path) -> dict[str, dict[str, Any]]:
    """Every home file in the world checkout, by home id."""
    homes: dict[str, dict[str, Any]] = {}
    folder = world / "homes"
    if not folder.is_dir():
        return homes
    for path in sorted(folder.glob("*.json")):
        if path.stem == "README":
            continue
        try:
            homes[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return homes


def excluded_tiles(builder_root: Path | None = None) -> set[str]:
    """Tile ids the core team blocked in `exclusions.json` (safety spec).

    The core team maintains that file by hand; agents never add to or
    remove from it. A missing file blocks nothing; a corrupt one fails
    loudly, so a broken safety file never silently unblocks a cell.
    """
    from .schemas import ROOT as BUILDER_ROOT

    path = (builder_root or BUILDER_ROOT) / "exclusions.json"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return set()
    try:
        data = json.loads(text)
    except ValueError as error:
        raise ValueError(f"cannot read {path}: {error}") from error
    tiles = data.get("tiles", [])
    return {t for t in tiles if isinstance(t, str)}


def home_for_cell(
    eco: int | None,
    habitat: str,
    homes: dict[str, dict[str, Any]],
) -> str | None:
    """The home covering this ecoregion and habitat, or None.

    Sea and open ocean build from a home of habitat `sea` that
    lists the cell's ecoregion, else the generic `sea` (#140.1).
    Boreal water picks the lake home through the tile's habitat:
    a boreal_lake cell builds from boreal_lake, a boreal_forest
    cell from boreal_forest.
    """
    if eco is None:
        return None
    if habitat in ("sea", "open_ocean"):
        listed = sorted(
            home_id
            for home_id, data in homes.items()
            if data.get("habitat") == "sea" and eco in (data.get("ecoregions") or [])
        )
        if listed:
            return listed[0]
        if "sea" in homes:
            return "sea"
        return None
    for home_id in sorted(homes):
        data = homes[home_id]
        if eco in (data.get("ecoregions") or []) and data.get("habitat") == habitat:
            return home_id
    return None


def tile_seed(tile: str) -> int:
    """The tile's deterministic 31-bit seed."""
    digest = hashlib.sha256(tile.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % 2**31


def cell_level(habitat: str, elevation_m: float) -> int:
    """The cell's ground level from its elevation.

    Sea and ocean floors sit 3 under the shared water level 8;
    land rises from 9 with elevation and flattens towards 59.
    """
    if habitat in ("sea", "open_ocean"):
        return WATER_LEVEL - SEA_DEPTH
    return 9 + round(50 * (1 - math.exp(-max(elevation_m, 0) / 1500)))


def _level_at(
    grid: GridName, col: int, row: int, grids: dict[GridName, HabitatGrid]
) -> int:
    col = min(max(col, 0), CELLS - 1)
    row = min(max(row, 0), CELLS - 1)
    habitat_grid = grids[grid]
    habitat = habitat_grid.habitat(col, row)
    _e1, _s1, _e2, _s2, _b, _l, _w, elev = habitat_grid.cell_data(col, row)
    return cell_level(habitat, elev)


def _noise(grid: GridName, x: float, y: float) -> float:
    """Gentle value noise, at most ±2 blocks, seeded by grid position."""
    gx = x / NOISE_LATTICE_M
    gy = y / NOISE_LATTICE_M
    x0, y0 = math.floor(gx), math.floor(gy)
    fx, fy = gx - x0, gy - y0

    def lattice(lx: int, ly: int) -> float:
        key = f"{grid}:{lx}:{ly}".encode()
        digest = hashlib.sha256(key).digest()
        return int.from_bytes(digest[:2], "big") / 65535 - 0.5

    top = lattice(x0, y0) * (1 - fx) + lattice(x0 + 1, y0) * fx
    bottom = lattice(x0, y0 + 1) * (1 - fx) + lattice(x0 + 1, y0 + 1) * fx
    return (top * (1 - fy) + bottom * fy) * 4


def _sample_xy(
    grid: GridName, col: int, row: int, side: str, index: int
) -> tuple[float, float]:
    """Global projected metres of one edge sample.

    Sides run n and s west to east, w and e south to north, so
    shared sides meet sample for sample and corners agree.
    """
    x0 = -9_000_000 + col * CELL_M
    y1 = 9_000_000 - row * CELL_M
    step = CELL_M / (EDGE_SAMPLES - 1)
    if side == "n":
        return x0 + index * step, y1
    if side == "s":
        return x0 + index * step, y1 - CELL_M
    if side == "w":
        return x0, y1 - (EDGE_SAMPLES - 1 - index) * step
    if side == "e":
        return x0 + CELL_M, y1 - (EDGE_SAMPLES - 1 - index) * step
    raise ValueError(f"bad side {side!r}")


def edge_height(
    grid: GridName,
    x: float,
    y: float,
    grids: dict[GridName, HabitatGrid],
) -> int:
    """Ground height for one global position: levels blended, noise on top."""
    fx = (x + 9_000_000) / CELL_M - 0.5
    fy = (9_000_000 - y) / CELL_M - 0.5
    c0, r0 = math.floor(fx), math.floor(fy)
    tx, ty = fx - c0, fy - r0
    top = (
        _level_at(grid, c0, r0, grids) * (1 - tx)
        + _level_at(grid, c0 + 1, r0, grids) * tx
    )
    bottom = (
        _level_at(grid, c0, r0 + 1, grids) * (1 - tx)
        + _level_at(grid, c0 + 1, r0 + 1, grids) * tx
    )
    return max(1, round(top * (1 - ty) + bottom * ty + _noise(grid, x, y)))


def tile_edges(tile: str, grids: dict[GridName, HabitatGrid]) -> dict[str, list[int]]:
    """The 65 ground heights per side, agreeing with neighbours."""
    grid, col, row = parse_tile(tile)
    edges: dict[str, list[int]] = {}
    for side in ("n", "e", "s", "w"):
        heights = []
        for index in range(EDGE_SAMPLES):
            x, y = _sample_xy(grid, col, row, side, index)
            heights.append(edge_height(grid, x, y, grids))
        edges[side] = heights
    return edges


def tile_height(level: int, edges: dict[str, list[int]]) -> int:
    """32, or 64 when the level or any edge sample tops 28."""
    top = max([level] + [h for side in edges.values() for h in side])
    return 64 if top > 28 else 32


def _animal(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _grows(world: Path, folder: str, stem: str | None) -> dict[str, Any]:
    """A plant or tree spec's grows, or nothing when unfiled."""
    if not stem:
        return {}
    path = world / "models" / folder / f"{stem}.json"
    if not path.is_file():
        return {}
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    grows = spec.get("grows", {})
    return grows if isinstance(grows, dict) else {}


def _food_kind(food: Any) -> str:
    """A food entry's kind, in the world's shape or a bare name."""
    if isinstance(food, dict):
        return str(food.get("kind", ""))
    return str(food or "")


def _tile_ground_plants(
    ground: dict[str, Any], greens: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """The ground plants the world's derive would read (#152).

    Without the tile's own cover or water lists, the home's. With
    them, the picks as models, keeping the home's plants only on
    a side the tile doesn't list — as `use_plants` does.
    """
    cover = (greens or {}).get("cover")
    water = (greens or {}).get("water")
    if not isinstance(cover, list) and not isinstance(water, list):
        return [p for p in ground.get("plants", []) or [] if isinstance(p, dict)]
    kept = [
        p
        for p in ground.get("plants", []) or []
        if isinstance(p, dict)
        and (
            ("seabed" in (p.get("on") or []) and not isinstance(water, list))
            or ("seabed" not in (p.get("on") or []) and not isinstance(cover, list))
        )
    ]
    sides = [
        cover if isinstance(cover, list) else [],
        water if isinstance(water, list) else [],
    ]
    for part in sides:
        for pick in part:
            if isinstance(pick, dict) and pick.get("id"):
                kept.append({"model": pick["id"]})
    return kept


def tile_food_kinds(
    world: Path, home: dict[str, Any], greens: dict[str, Any] | None = None
) -> set[str]:
    """Food kinds the 0043 rules work out for this tile (#147.4).

    Reads `food/rules.json`, the plants' and trees' own grows,
    the land and the water — never the home's written food list,
    which layered homes no longer keep. Mirrors the world's
    derive plus what its food check reads beside it (ground
    food entries, flowers nectar); the tile check still has the
    last word. A world without rules is pre-layer: the written
    list, as before.

    A tile with its own plants eats from those (#152): the game
    grows the tile's picks over the home (`use_plants`), so the
    food comes from the picks, with the home's plants kept only
    on a side the tile doesn't list.
    """
    rules_path = world / "food" / "rules.json"
    if not rules_path.is_file():
        return home_food_kinds(home)
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    kinds: set[str] = set()
    ground = home.get("ground_cover", {}) or {}
    if not isinstance(ground, dict):
        ground = {}
    for plant in _tile_ground_plants(ground, greens):
        if not isinstance(plant, dict):
            continue
        if "food" in plant:
            kind = _food_kind(plant["food"])
            if kind:
                kinds.add(kind)
        else:
            kind = _food_kind(_grows(world, "plants", plant.get("model")).get("food"))
            # No weight gate: derive counts every grown food, even
            # weightless ones like mushrooms (#152).
            if kind:
                kinds.add(kind)
    trees = home.get("trees", {}) or {}
    if not isinstance(trees, dict):
        trees = {}
    mix = trees.get("mix", []) or []
    if isinstance((greens or {}).get("trees"), list):
        mix = [{"plant": p["id"]} for p in greens["trees"] if isinstance(p, dict)]
    for entry in mix:
        if not isinstance(entry, dict):
            continue
        kind = _food_kind(entry.get("food"))
        if kind:
            kinds.add(kind)
        shape = entry.get("shape")
        sid = entry.get("plant")
        if not sid and shape:
            spec_path = world / "models" / "trees" / f"{shape}.json"
            if spec_path.is_file():
                try:
                    spec = json.loads(spec_path.read_text(encoding="utf-8"))
                except ValueError:
                    spec = {}
                if "game_shape" not in spec:
                    sid = shape
        grows = _grows(world, "trees", sid)
        kind = _food_kind(grows.get("food"))
        if kind:
            kinds.add(kind)
        for key in grows.get("more_food", {}) or {}:
            kinds.add(key)
        if grows.get("bark"):
            kinds.add("bark")
    life = home.get("lake_life", {}) or {}
    if isinstance(life, dict) and any(
        isinstance(life.get(key), (int, float)) and life.get(key, 0) > 0
        for key in ("lilies", "pondweed")
    ):
        kinds.add("water_plants")
    grass = ground.get("grass", 0)
    if isinstance(grass, (int, float)) and not isinstance(grass, bool) and grass > 0:
        kinds.add("grass")
    terrain = home.get("terrain", {}) or {}
    if not isinstance(terrain, dict):
        terrain = {}
    lakes = terrain.get("lakes", 0)
    wet = bool(home.get("lakes") or home.get("streams") or home.get("bogs"))
    wet = wet or (
        isinstance(lakes, (int, float)) and not isinstance(lakes, bool) and lakes > 0
    )
    fresh = home.get("habitat", "") != "sea" and wet
    water = fresh or home.get("habitat", "") == "sea" or terrain.get("edge") == "lake"
    when = {"land": True, "fresh_water": fresh, "water": water}
    for kind, rule in rules.get("kinds", {}).items():
        if rule.get("from") == "small_life" and when.get(rule.get("when"), False):
            kinds.add(kind)
    carrion = rules.get("kinds", {}).get("carrion", {})
    if carrion.get("or") == "small_life" and when.get(carrion.get("when"), False):
        kinds.add("carrion")
    flowers = ground.get("flowers", 0.0)
    if (
        isinstance(flowers, (int, float))
        and not isinstance(flowers, bool)
        and flowers > 0.0
        and ground.get("flower_colors")
    ):
        kinds.add("nectar")
    return kinds


def home_food_kinds(home: dict[str, Any]) -> set[str]:
    """Every food kind the home offers, as the world's check counts them.

    The region's `food` kinds, the ground cover's food plants, and
    nectar when flowers bloom. Only for worlds without rules now:
    layered homes keep no written food list (#147.4).
    """
    kinds: set[str] = set()
    for entry in home.get("food", []) or []:
        if isinstance(entry, dict) and entry.get("kind"):
            kinds.add(entry["kind"])
    ground = home.get("ground_cover", {}) or {}
    if isinstance(ground, dict):
        for plant in ground.get("plants", []) or []:
            food = plant.get("food") if isinstance(plant, dict) else None
            if isinstance(food, str) and food:
                kinds.add(food)
            elif isinstance(food, dict) and food.get("kind"):
                kinds.add(str(food["kind"]))
        flowers = ground.get("flowers", 0.0)
        if (
            isinstance(flowers, (int, float))
            and not isinstance(flowers, bool)
            and flowers > 0.0
            and ground.get("flower_colors")
        ):
            kinds.add("nectar")
    return kinds


def home_fish(world: Path, home: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The home's fish kinds that have animal files, by kind.

    Fish live in every region the home parents, so they eat and
    hunt there too. Kinds without files are skipped, as the
    world's check skips them.
    """
    fish: dict[str, dict[str, Any]] = {}
    for entry in home.get("fish", []) or []:
        kind = entry.get("kind", "") if isinstance(entry, dict) else ""
        if not kind or kind in fish:
            continue
        path = world / "animals" / f"{kind}.json"
        if path.is_file():
            fish[kind] = _animal(path)
    return fish


def _tag_covered(tag: str, hunter: str, pool: dict[str, dict[str, Any]]) -> bool:
    """Another animal in the pool carries this hunt tag."""
    return any(
        other != hunter and tag in (data.get("tags") or [])
        for other, data in pool.items()
    )


#: Wanted entries per tile: ids the game ranks by play (#143.1).
WANTED_LIMIT = 12


#: The phone's limits on a tile's own plants (spec 02 §2).
MAX_GROUND_PLANTS = 24
MAX_TILE_TREES = 6

#: Density for a picked plant whose spec names none.
TILE_PLANT_DENSITY = 0.05


def wanted_drops(
    answer: dict[str, Any], candidates: set[str], world: Path
) -> list[dict[str, str]]:
    """What the survey found that the world doesn't have yet (#143.1).

    Survey animals in the tile's habitat with no animal file, in
    the survey's order, each in its first part. Filed animals
    dropped for food stay out: building them again wouldn't help.
    Ids only, never model text. Plants are picked from the
    library now, never wanted (#147.3).
    """
    animals_dir = world / "animals"
    parts = answer["parts"]
    part_of: dict[str, str] = {}
    for part in parts:
        for species_id in part.get("species", []):
            part_of.setdefault(species_id, part["kind"])
    drops: list[dict[str, str]] = []
    for species_id in answer["present"]:
        if species_id not in candidates:
            continue
        if (animals_dir / f"{species_id}.json").is_file():
            continue
        if species_id not in part_of:
            continue
        drops.append(
            {"kind": "animal", "what": species_id, "part": part_of[species_id]}
        )
    return drops[:WANTED_LIMIT]


def tile_plants(
    world: Path, answer: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    """The survey's picks as the tile's plants (#147.3).

    Cover or water by the spec's grows (seabed grows in water);
    density is the spec's own scaled by the pick's count against
    the part's highest; tree shares split the picks' counts. The
    phone's limits cap each list in the survey's order. Returns
    the plants dict (nothing when nothing picked) with the
    requires lists.
    """
    cover: list[dict[str, Any]] = []
    water: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pick in answer.get("plants", []):
        if pick["id"] in seen:
            continue
        seen.add(pick["id"])
        grows = _grows(world, "plants", pick["id"])
        part = water if "seabed" in (grows.get("on") or []) else cover
        part.append(pick)
        if len(cover) + len(water) >= MAX_GROUND_PLANTS:
            break
    entries: dict[str, Any] = {}
    for part, key in ((cover, "cover"), (water, "water")):
        if not part:
            continue
        top = max(p["count"] for p in part)
        items = []
        for pick in part:
            density = _grows(world, "plants", pick["id"]).get("density")
            if not isinstance(density, (int, float)) or not 0 < density <= 1:
                density = TILE_PLANT_DENSITY
            amount = max(0.001, round(density * pick["count"] / top, 3))
            items.append({"id": pick["id"], "density": amount})
        entries[key] = items
    picked_trees = []
    seen = set()
    for pick in answer.get("trees", []):
        if pick["id"] in seen:
            continue
        seen.add(pick["id"])
        picked_trees.append(pick)
        if len(picked_trees) >= MAX_TILE_TREES:
            break
    if picked_trees:
        total = sum(p["count"] for p in picked_trees)
        entries["trees"] = [
            {"id": p["id"], "share": max(0.001, round(p["count"] / total, 3))}
            for p in picked_trees
        ]
    if not entries:
        return None, [], []
    needs = sorted({p["id"] for part in (cover, water) for p in part})
    wants = sorted(p["id"] for p in picked_trees)
    return entries, needs, wants


def _blocked(
    reason: str, habitat: str, wanted: list[dict[str, str]] | None
) -> dict[str, Any]:
    """A blocked entry, carrying `wanted` when the survey named any."""
    blocked: dict[str, Any] = {"reason": reason, "habitat": habitat}
    if wanted:
        blocked["wanted"] = wanted
    return blocked


_SELECT_ROUND = """
import json, os, sys
tools = sys.argv[1]
sys.path.insert(0, tools)
root = os.path.dirname(os.path.abspath(tools))
import check_animals, tile_region
tile = json.load(sys.stdin)
place = tile_region.place(tile)
content = check_animals.Content(root)
checker = check_animals.Checker(root, built_models=False)
checker.c = content
content.region = lambda rid: place
checker.check_food_web("tl_" + tile["id"])
fish = {}
for entry in place.get("fish", []) or []:
    kind = entry.get("kind", "") if isinstance(entry, dict) else ""
    if not kind or kind in fish:
        continue
    data = content.get_def(kind)
    if data:
        fish[kind] = [t for t in data.get("hunts", []) or [] if isinstance(t, str)]
print(json.dumps({"errors": checker.errors, "fish": fish}))
"""

_WEB_ANIMAL = re.compile(r"^([A-Za-z0-9_]+) in \S+")
_WEB_NOFILE = re.compile(r'playable animal "([^"]+)"')


def _web_failures(errors: list[str]) -> set[str]:
    """The animals a food-web round faults, by id."""
    out = set()
    for line in errors:
        match = _WEB_ANIMAL.match(line) or _WEB_NOFILE.search(line)
        if match is not None:
            out.add(match.group(1))
    return out


def select_species(
    tile: dict[str, Any], ranked: list[str], world: Path
) -> tuple[set[str], dict[str, list[str]]] | None:
    """The candidates that find food and prey here, the game's way (#154).

    `tile` is the tile so far (home, plants, land, water) with the
    candidates seated; the world's converter builds its place and
    the world's food-web check faults whoever can't eat or hunt
    there. The faults drop out and the round runs again, so a
    hunter follows its prey out and carrion follows its large
    animal. Returns the kept ids and the place's fish hunts, or
    None when the checkout has no converter and food-web check
    (tests fall back to the builder's own rules). An empty set
    blocks the tile: either nothing fits, or the place's own
    fish can't eat here.
    """
    tools = world / "tools"
    if not (tools / "tile_region.py").is_file():
        return None
    if not (tools / "check_animals.py").is_file():
        return None
    kept = set(ranked)
    while True:
        proc = subprocess.run(
            [sys.executable, "-c", _SELECT_ROUND, str(tools)],
            input=json.dumps(
                {
                    **tile,
                    "species": {
                        "playable": sorted(kept),
                        "prey": [],
                        "predators": [],
                    },
                }
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"the world's food-web check refused {tile.get('id')}: "
                f"{(proc.stdout + proc.stderr).strip()[:300]}"
            )
        try:
            verdict = json.loads(proc.stdout)
        except ValueError:
            raise RuntimeError(
                f"the world's food-web check gave no JSON for {tile.get('id')}: "
                f"{proc.stdout.strip()[:200]}"
            )
        fish = verdict.get("fish", {})
        failing = _web_failures(verdict.get("errors", [])) & kept
        if _web_failures(verdict.get("errors", [])) - kept:
            return set(), fish
        if not failing:
            return kept, fish
        kept -= failing


def _candidate_habitats(
    habitat: str, eco1: Any, biome: Any, land: float, water: float
) -> set[str]:
    """Habitats a tile takes candidates from: its own and its other side.

    A coast cell (any land share above 0) also takes its land's
    animals: the dry habitat its ecoregion's biome would give it
    with more land, and the wet one where the cell has lakes
    (#153). An inland cell with a coast or lakes also takes sea
    or lake animals. Sea tiles take open-ocean swimmers and vice
    versa, as before. The survey and the food check decide from
    there, as now.
    """
    out = {habitat}
    if habitat in ("sea", "open_ocean"):
        out.add("open_ocean" if habitat == "sea" else "sea")
        if land > 0:
            out.add(habitat_for(eco1, biome, 1.0, 0.0))
            if water > 0:
                out.add(habitat_for(eco1, biome, 1.0, 1.0))
    else:
        if land < 1.0:
            out.add("sea")
        if water > 0:
            out.add(habitat_for(eco1, biome, 1.0, 1.0))
    return out


def _split_species(
    ranked: list[str],
    kept: set[str],
    candidates: dict[str, dict[str, Any]],
    fish_hunts: dict[str, list[str]],
) -> tuple[list[str], list[str], list[str]]:
    """The kept ids as playable, prey and predators, most present first."""
    playable = [s for s in ranked if s in kept and candidates[s].get("playable")]
    if not playable:
        return [], [], []
    predators = [s for s in ranked if s in kept and (candidates[s].get("hunts") or [])]
    hunted: set[str] = set()
    for species in kept:
        hunted.update(candidates[species].get("hunts") or [])
    for hunts in fish_hunts.values():
        hunted.update(hunts)
    prey = [
        s
        for s in ranked
        if s in kept
        and (
            (s not in playable and s not in predators)
            or bool(set(candidates[s].get("tags") or []) & hunted)
        )
    ]
    return playable, prey, predators


def pick_species(
    counts: dict[str, float],
    world: Path,
    home: dict[str, Any],
    greens: dict[str, Any] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """Playable, prey and predators, most present first, food first.

    An animal stays only if the worked-out food has every kind it needs,
    and a hunter only if at least one of its prey stays too. The
    food is worked out from the tile's own plants when it has them
    (#152), else the home's. The home's fish hunt in the region as
    well, so their prey must stay too. Playable is every kept animal
    whose file is playable;
    predators every kept hunter; prey every kept animal someone
    hunts, plus every kept animal in neither other list, so an
    animal can be both playable and someone's prey. When nothing
    can live here with its food, all three lists are empty and the
    tile is blocked with `no_animal`. The fallback when the
    checkout has no converter; make_tile otherwise selects with
    the world's own food-web check (#154).
    """
    animals_dir = world / "animals"
    ranked = sorted(counts, key=lambda s: (-counts[s], s))
    candidates: dict[str, dict[str, Any]] = {}
    for species in ranked:
        path = animals_dir / f"{species}.json"
        if path.is_file():
            candidates[species] = _animal(path)
    food = tile_food_kinds(world, home, greens)
    fish = home_fish(world, home)
    for data in fish.values():
        if not (data.get("diet") or data.get("hunts")):
            return [], [], []
        if not set(data.get("diet") or []) <= food:
            return [], [], []
    kept = {
        species
        for species, data in candidates.items()
        if (data.get("diet") or data.get("hunts"))
        and set(data.get("diet") or []) <= food
    }
    changed = True
    while changed:
        changed = False
        pool = {**fish, **{s: candidates[s] for s in kept}}
        for species in sorted(kept):
            hunts = candidates[species].get("hunts") or []
            if any(not _tag_covered(tag, species, pool) for tag in hunts):
                kept.discard(species)
                changed = True
    pool = {**fish, **{s: candidates[s] for s in kept}}
    if any(
        not _tag_covered(tag, kind, pool)
        for kind, data in fish.items()
        for tag in data.get("hunts") or []
    ):
        return [], [], []
    return _split_species(
        ranked,
        kept,
        candidates,
        {kind: list(data.get("hunts") or []) for kind, data in fish.items()},
    )


def _versions(builder_root: Path, world: Path) -> tuple[str, str]:
    """The builder release and world commit for the tile's `made`."""

    def run(args: list[str], cwd: Path) -> str | None:
        try:
            out = subprocess.run(
                args, cwd=cwd, capture_output=True, text=True, check=False
            )
        except OSError:
            return None
        return out.stdout.strip() or None

    maker = run(["git", "describe", "--tags", "--always"], builder_root)
    commit = run(["git", "rev-parse", "--short", "HEAD"], world)
    return maker or "dev", commit or "unknown"


def check_with_world(
    made_tile: dict[str, Any], world: Path
) -> list[tuple[str, list[str]]]:
    """The world's tile check over one made tile, failing checks with problems.

    Empty when the tile passes, and when the world has no tile check
    (tests with fake worlds). The check runs in the world checkout,
    which owns it; the builder only reads its verdict. The checkout
    must carry the converter too: a tile check that predates it
    validates against an old copy, so the maker refuses it loudly
    instead of drifting from the game (#161.3).
    """
    tool = world / "tools" / "check_tile.py"
    if not tool.is_file():
        return []
    if not (world / "tools" / "tile_region.py").is_file():
        raise RuntimeError(
            "the world's tile check predates the converter "
            "(tools/tile_region.py is missing); update the checkout"
        )
    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as handle:
        json.dump(made_tile, handle)
        path = handle.name
    try:
        proc = subprocess.run(
            [sys.executable, str(tool), path],
            cwd=world,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        Path(path).unlink(missing_ok=True)
    if proc.returncode == 2:
        raise RuntimeError(
            f"the world's tile check can't read its input: {proc.stderr.strip()}"
        )
    try:
        report = json.loads(proc.stdout)
    except ValueError:
        raise RuntimeError(
            f"the world's tile check gave no JSON: {proc.stderr.strip()}"
        )
    return [
        (entry["check"], list(entry.get("problems", [])))
        for entry in report.get("results", [])
        if entry.get("status") == "fail"
    ]


def tile_facts(
    tile: str,
    grids: dict[GridName, HabitatGrid],
    world: Path,
    home: str,
    adapt: bool,
) -> dict[str, Any]:
    """Our own facts for one tile's survey, maker and planner alike."""
    grid, col, row = parse_tile(tile)
    habitat_grid = grids[grid]
    eco1, share1, eco2, share2, _biome, land, water, elev = habitat_grid.cell_data(
        col, row
    )
    habitat = habitat_grid.habitat(col, row)
    level = cell_level(habitat, elev)
    edges = tile_edges(tile, grids)
    samples = [level, *(s for side in edges.values() for s in side)]
    return survey_facts(
        habitat=habitat,
        eco1=eco1,
        share1=share1,
        eco2=eco2,
        share2=share2,
        land=land,
        water=water,
        elevation_m=elev,
        level=level,
        edge_lo=min(samples),
        edge_hi=max(samples),
        home=home,
        adapt=adapt,
    )


def place_cells(world: Path) -> dict[str, tuple[str, list[int]]]:
    """Place cells by tile id: the region and its `[i, j]` part (#147.1).

    From each region file's `grid` section; the part counts
    from the region's smallest col and row. Nothing else in
    the region is read: layered files keep colours, plants
    and food elsewhere now. An unreadable file is skipped; a
    malformed grid section, or two regions on one cell, fail
    loudly, so a code tile is never made in a place cell.
    """
    cells: dict[str, tuple[str, list[int]]] = {}
    folder = world / "regions"
    if not folder.is_dir():
        return cells
    for path in sorted(folder.glob("*.json")):
        try:
            region = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        grid = region.get("grid") if isinstance(region, dict) else None
        if grid is None:
            continue
        name = grid.get("grid") if isinstance(grid, dict) else None
        listed = grid.get("tiles") if isinstance(grid, dict) else None
        if (
            name not in ("ease2_n25", "ease2_s25")
            or not isinstance(listed, list)
            or not listed
            or any(
                not isinstance(cell, list)
                or len(cell) != 2
                or any(not isinstance(v, int) or isinstance(v, bool) for v in cell)
                for cell in listed
            )
        ):
            raise ValueError(f"{path.name} has a malformed grid section")
        region_id = region.get("id", path.stem)
        min_col = min(cell[0] for cell in listed)
        min_row = min(cell[1] for cell in listed)
        for col, row in listed:
            made = tile_id(name, col, row)
            if made in cells:
                raise ValueError(f"{made} is in two place regions")
            cells[made] = (region_id, [col - min_col, row - min_row])
    return cells


def make_place_tile(
    tile: str,
    grids: dict[GridName, HabitatGrid],
    world: Path,
    region_id: str,
    part: list[int],
    builder_root: Path | None = None,
) -> dict[str, Any]:
    """The place entry for a cell of a hand-made place (#147.1).

    `place` and `part` instead of the code tile's home and
    species; center, level, height and edges by the same fixed
    rules as the code tiles round it, so the place's open
    sides meet them. `requires` lists nothing: the game reads
    the place from its region file. The world's food check is
    the region file's, not the entry's (layered regions carry
    no written food), so only the other checks can fail it.
    """
    from .schemas import ROOT as BUILDER_ROOT

    grid, col, row = parse_tile(tile)
    habitat_grid = grids[grid]
    habitat = habitat_grid.habitat(col, row)
    elev = habitat_grid.cell_data(col, row)[7]
    lat, lon = center(tile)
    level = cell_level(habitat, elev)
    edges = tile_edges(tile, grids)
    maker, commit = _versions(builder_root or BUILDER_ROOT, world)
    entry: dict[str, Any] = {
        "format": 1,
        "id": tile,
        "kind": "place",
        "version": 1,
        "center": {"lat": round(lat, 2), "lon": round(lon, 2)},
        "grid_north": round(grid_north(tile), 1),
        "level": level,
        "height": tile_height(level, edges),
        "place": region_id,
        "part": part,
        "edges": edges,
        "requires": {"homes": [], "animals": []},
        "made": {
            "by": "tile_maker",
            "maker": maker,
            "world": commit,
            "at": datetime.now(UTC).isoformat(),
        },
    }
    validate("tile", entry)
    failures = [
        (check, problems)
        for check, problems in check_with_world(entry, world)
        if check != "food"
    ]
    if failures:
        detail = "; ".join(
            f"{check}: {', '.join(problems) or 'failed'}"
            for check, problems in failures
        )
        raise RuntimeError(f"place tile {tile} failed the world's check: {detail}")
    return entry


def make_tile(
    tile: str,
    grids: dict[GridName, HabitatGrid],
    world: Path,
    presence: dict[str, float] | None = None,
    builder_root: Path | None = None,
    species: dict[str, Any] | None = None,
    species_source: SpeciesSource | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Make one tile, or the blocked reason when it can't be made.

    Returns (tile, None) or (None, {"reason": ..., "habitat": ...}).
    `presence` overrides with GBIF-style counts (tests and replays);
    `species` overrides with a recorded Muse Spark answer. Otherwise
    the answer comes from the cache or one model call.
    """
    from .schemas import ROOT as BUILDER_ROOT

    grid, col, row = parse_tile(tile)
    habitat_grid = grids[grid]
    eco1, _share1, eco2, share2, biome, land, water, elev = habitat_grid.cell_data(
        col, row
    )
    habitat = habitat_grid.habitat(col, row)
    if tile in excluded_tiles(builder_root):
        return None, {"reason": "excluded", "habitat": habitat}
    placed = place_cells(world).get(tile)
    if placed is not None:
        region_id, part = placed
        return (
            make_place_tile(tile, grids, world, region_id, part, builder_root),
            None,
        )
    homes = available_homes(world)
    home = home_for_cell(eco1, habitat, homes)
    if home is None:
        return None, {"reason": "no_home", "habitat": habitat}
    blend: dict[str, Any] | None = None
    blend_home = home_for_cell(eco2, habitat, homes) if eco2 is not None else None
    if blend_home is not None and blend_home != home and share2 >= 0.1:
        blend = {
            "home": blend_home,
            "biome": blend_home,
            "share": round(share2, 2),
        }
    coast: list[str] | None = None
    if 0.0 < land < 1.0:
        mask = habitat_grid.coast(col, row)
        if mask is not None:
            coast = [mask[r * 5 : (r + 1) * 5] for r in range(5)]
    lat, lon = center(tile)
    animals = list_animals(world)
    candidates = []
    seen: set[str] = set()
    for label in sorted(_candidate_habitats(habitat, eco1, biome, land, water)):
        for entry in animals_for_habitat(animals, label):
            if entry["id"] not in seen:
                seen.add(entry["id"])
                candidates.append(entry)
    wanted: list[dict[str, str]] | None = None
    greens: dict[str, Any] | None = None
    if presence is not None:
        present = {s: n for s, n in presence.items() if n > 0}
        species_tag = f"gbif:search:{datetime.now(UTC).date().isoformat()}"
    else:
        ids = [a["id"] for a in animals]
        greens_list = list_library(world, "plants")
        trees_list = list_library(world, "trees")
        facts = tile_facts(tile, grids, world, home, False)
        if species is not None:
            answer = check_answer(
                species,
                ids,
                [p["id"] for p in greens_list],
                [t["id"] for t in trees_list],
            )
            problem = check_survey(answer, habitat, lat, lon, facts, animals)
            if problem is not None:
                return None, {"reason": "species_place", "habitat": habitat}
            date = datetime.now(UTC).date().isoformat()
        else:
            source = species_source or SpeciesSource(
                client=None,
                cache_dir=(builder_root or BUILDER_ROOT) / "data" / "species",
            )
            try:
                answer = fetch_species(
                    tile,
                    animals,
                    greens_list,
                    trees_list,
                    habitat,
                    lat,
                    lon,
                    source,
                    list_fingerprint(animals),
                    library_fingerprint(greens_list, trees_list),
                    facts,
                )
            except SpeciesPlaceError:
                return None, {"reason": "species_place", "habitat": habitat}
            date = answer["date"]
        ordered = [s for s in answer["present"] if s in {a["id"] for a in candidates}]
        present = rank_weights(ordered)
        species_tag = f"{SPECIES_MODEL}:{date}"
        wanted = wanted_drops(answer, {a["id"] for a in candidates}, world)
        greens, needs, wants = tile_plants(world, answer)
    level = cell_level(habitat, elev)
    edges = tile_edges(tile, grids)
    provisional: dict[str, Any] = {
        "id": tile,
        "home": home,
        "biome": home,
        "center": {"lat": round(lat, 2), "lon": round(lon, 2)},
        "seed": tile_seed(tile),
        "water": round(water, 2),
        "level": level,
        "height": tile_height(level, edges),
        "edges": edges,
    }
    if blend is not None:
        provisional["blend"] = blend
    if coast is not None:
        provisional["coast"] = coast
    if greens is not None:
        provisional["plants"] = greens
    ranked = sorted(present, key=lambda s: (-present[s], s))
    animals_dir = world / "animals"
    filed = [s for s in ranked if (animals_dir / f"{s}.json").is_file()]
    converted = select_species(provisional, filed, world)
    if converted is None:
        playable, prey, predators = pick_species(present, world, homes[home], greens)
    else:
        kept, fish_hunts = converted
        candidates = {s: _animal(animals_dir / f"{s}.json") for s in filed}
        playable, prey, predators = _split_species(ranked, kept, candidates, fish_hunts)
    if not playable:
        return None, _blocked("no_animal", habitat, wanted)
    maker, commit = _versions(builder_root or BUILDER_ROOT, world)
    needed = sorted({home} | ({blend["home"]} if blend else set()))
    requires: dict[str, Any] = {
        "homes": needed,
        "biomes": list(needed),
        "animals": sorted(set(playable) | set(prey) | set(predators)),
    }
    if greens is not None:
        requires["plants"] = needs
        requires["trees"] = wants
    made_tile: dict[str, Any] = {
        "format": 1,
        "id": tile,
        "kind": "code",
        "version": 1,
        "center": {"lat": round(lat, 2), "lon": round(lon, 2)},
        "grid_north": round(grid_north(tile), 1),
        "level": level,
        "height": tile_height(level, edges),
        "habitat": habitat,
        "ecoregion": eco1,
        "home": home,
        "biome": home,
        "seed": tile_seed(tile),
        "water": round(water, 2),
        "species": {"playable": playable, "prey": prey, "predators": predators},
        "edges": edges,
        "landmarks": [],
        "requires": requires,
        "sources": {
            "habitat": f"resolve2017:{eco1}",
            "elevation": "etopo2022:v1_60s_surface",
            "water": "naturalearth:lakes:5.0.0",
            "species": species_tag,
        },
        "made": {
            "by": "tile_maker",
            "maker": maker,
            "world": commit,
            "at": datetime.now(UTC).isoformat(),
        },
    }
    if blend is not None:
        made_tile["blend"] = blend
    if coast is not None:
        made_tile["coast"] = coast
    if greens is not None:
        made_tile["plants"] = greens
    if wanted:
        made_tile["wanted"] = wanted
    validate("tile", made_tile)
    failures = check_with_world(made_tile, world)
    if failures:
        if all(check == "food" for check, _ in failures):
            return None, _blocked("no_animal", habitat, wanted)
        detail = "; ".join(
            f"{check}: {', '.join(problems) or 'failed'}"
            for check, problems in failures
        )
        raise RuntimeError(f"tile {tile} failed the world's check: {detail}")
    return made_tile, None


def write_group(
    out_dir: Path,
    tile: str,
    made: dict[str, Any] | None,
    blocked: dict[str, Any] | None,
) -> Path:
    """Write the tile or blocked cell into its group file, merged."""
    from .grid import group

    grid, gcol, grow = group(tile)
    path = out_dir / grid / f"{gcol:02d}_{grow:02d}.json"
    data: dict[str, Any]
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        data["version"] = int(data.get("version", 0)) + 1
    else:
        data = {
            "format": 1,
            "grid": grid,
            "group": [gcol, grow],
            "version": 1,
            "tiles": {},
            "blocked": {},
        }
    if made is not None:
        # The stored copy's own version: the group file's (#143.5),
        # so a republished tile reads newer than the one before it.
        made["version"] = data["version"]
        data["tiles"][tile] = made
        data["blocked"].pop(tile, None)
    elif blocked is not None:
        data["blocked"][tile] = blocked
        data["tiles"].pop(tile, None)
    validate("tile-group", data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    return path


def make_at(
    lat: float,
    lon: float,
    grids: dict[GridName, HabitatGrid],
    world: Path,
    out_dir: Path,
    builder_root: Path | None = None,
    species_source: SpeciesSource | None = None,
) -> tuple[str, Path, str]:
    """Make the tile at this point and write its group file.

    Returns (tile id, group path, "ready" or the blocked reason).
    """
    tile = tile_at(lat, lon)
    made, blocked = make_tile(
        tile, grids, world, builder_root=builder_root, species_source=species_source
    )
    path = write_group(out_dir, tile, made, blocked)
    status = "ready" if made is not None else (blocked or {}).get("reason", "?")
    return tile, path, status
