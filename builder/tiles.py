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
    EQUATOR_M,
    TILE_M,
    N,
    center,
    lat_of,
    lon_of,
    parse_tile,
    tile_at,
    tile_id,
)
from .habitats import (
    REEF_SOURCE,
    REEF_TRACT_SHARE,
    HabitatGrid,
    habitat_for,
)
from .presence import animals_for_habitat, fetch_smoothed
from .schemas import validate
from .shore import shore_for_tile
from .species import MODEL as SPECIES_MODEL
from .species import Source as SpeciesSource
from .species import (
    SpeciesPlaceError,
    check_answer,
    check_survey,
    fetch_species,
    library_fingerprint,
    list_animals,
    list_library,
    rank_weights,
    survey_facts,
    survey_round,
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


def available_biomes(world: Path) -> dict[str, dict[str, Any]]:
    """Every biome in the world checkout, by id (#148.1).

    Biomes are the layered source of truth (habitat, ecoregions,
    land style); checkouts without `biomes/` read as their
    `homes/` files, which carry no land style.
    """
    folder = world / "biomes"
    if not folder.is_dir():
        return available_homes(world)
    biomes: dict[str, dict[str, Any]] = {}
    for path in sorted(folder.glob("*.json")):
        try:
            biomes[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return biomes


def land_style_for(world: Path, biome: int | None) -> str | None:
    """The cell's land style by code, or None without one (#148.2).

    The first `lands/index.json` entry whose RESOLVE biome number
    matches; entries naming realms never match, since a cell
    carries no realm. None when the checkout has no index or the
    biome has no style yet.
    """
    if biome is None:
        return None
    try:
        index = json.loads((world / "lands" / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for entry in index.get("styles", []):
        if entry.get("biome") == biome and not entry.get("realms"):
            land = entry.get("land")
            return str(land) if land else None
    return None


def _open_sea(world: Path, animal_id: str) -> bool:
    """Whether the animal file says it lives at sea (#177.3)."""
    try:
        data = json.loads((world / "animals" / f"{animal_id}.json").read_text())
    except (OSError, ValueError):
        return False
    return bool(data.get("open_sea"))


def open_sea_entries(
    world: Path, entries: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The open-sea candidates: sea animals with GBIF keys (#177.3)."""
    return [
        entry
        for entry in entries
        if entry.get("gbif_key") and _open_sea(world, entry["id"])
    ]


_SEAS_CELLS: dict[Path, dict[str, str]] = {}


def _open_sea_name(tile: str, builder_root: Path, world: Path) -> dict[str, Any] | None:
    """The cell's sea and names, or nothing when no sea covers it (#177.2).

    The baked `data/seas.json.gz` names the sea; `seas/names.json`
    gives the entry's own `en` (and `fi` where the table has it).
    """
    path = builder_root / "data" / "seas.json.gz"
    if path not in _SEAS_CELLS:
        if not path.is_file():
            return None
        from .seas import load_seas

        _SEAS_CELLS[path] = load_seas(path)
    try:
        table = json.loads((world / "seas" / "names.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    seas = table.get("seas", {})
    sea = _SEAS_CELLS[path].get(tile)
    if sea is None or sea not in seas:
        return None
    entry = seas[sea]
    name = {"en": entry["en"]}
    if entry.get("fi"):
        name["fi"] = entry["fi"]
    return {"sea": sea, "name": name, "source": table.get("source", "")}


def _nearest_land(
    tile: str, habitat_grid: HabitatGrid
) -> tuple[int | None, int | None]:
    """The nearest land cell's ecoregion and biome number (#177.1).

    Expanding rings around the cell, wrapping at the date
    line; the first cell with an ecoregion wins, so the Gulf
    of Finland takes the boreal coast's. Nothing when the
    grid holds no land cell.
    """
    col, row = parse_tile(tile)
    for radius in range(1, N + 1):
        for dcol in range(-radius, radius + 1):
            for drow in range(-radius, radius + 1):
                if max(abs(dcol), abs(drow)) != radius:
                    continue
                ncol, nrow = (col + dcol) % N, row + drow
                if not 0 <= nrow < N:
                    continue
                try:
                    cell = habitat_grid.cell_data(ncol, nrow)
                except KeyError:
                    continue
                if cell[0] is not None:
                    return cell[0], cell[4]
    return None, None


def near_made_or_noted(
    tile: str,
    made: set[str] | None = None,
    noted: set[str] | None = None,
) -> bool:
    """Whether building reaches within 3 cells of this tile (#177.6).

    Chebyshev distance, wrapping at the date line: a made
    tile or a tile with border notes. `made` carries the
    published ids the caller knows (the worker reads the
    groups); `noted` the tiles with notes. Around a noted
    tile counts the same 3 cells: players waiting there want
    the sea made.
    """
    col, row = parse_tile(tile)
    for dcol in range(-3, 4):
        for drow in range(-3, 4):
            ncol, nrow = (col + dcol) % N, row + drow
            if not 0 <= nrow < N:
                continue
            cell = tile_id(ncol, nrow)
            if made is not None and cell in made:
                return True
            if noted is not None and cell in noted:
                return True
    return False


def ring_cells(habitat_grid: Any, col: int, row: int) -> Any:
    """Neighbour cells in expanding rings, as (ecoregion, habitat)."""
    for radius in range(1, 6):
        for dcol in range(-radius, radius + 1):
            for drow in range(-radius, radius + 1):
                if max(abs(dcol), abs(drow)) != radius:
                    continue
                ncol = (col + dcol) % N
                nrow = min(max(row + drow, 0), N - 1)
                try:
                    yield (
                        habitat_grid.cell_data(ncol, nrow)[0],
                        habitat_grid.habitat(ncol, nrow),
                    )
                except KeyError:
                    continue


def default_bundle(
    tile: str,
    habitat_grid: HabitatGrid,
    biomes: dict[str, dict[str, Any]],
    habitat: str,
    style: str,
    eco: int | None = None,
) -> str | None:
    """The nearest biome of this habitat with this land style (#148.3).

    A cell whose ecoregion no biome lists builds from this default
    bundle, with its real plants from the survey; the neighbours
    come first in expanding rings, then the first anywhere. None
    when no filed biome pairs this habitat with this style. When
    the ecoregion is listed somewhere, the bundle must list it
    too: a home that doesn't list the cell's ecoregion fails
    the world's check (#198.2).
    """
    listed = eco is not None and any(
        eco in (data.get("ecoregions") or []) for data in biomes.values()
    )
    col, row = parse_tile(tile)
    rings = list(ring_cells(habitat_grid, col, row))
    for neighbour_eco, neighbour in rings:
        found = home_for_cell(neighbour_eco, neighbour, biomes)
        if (
            found is not None
            and biomes[found].get("habitat") == habitat
            and biomes[found].get("land") == style
            and (not listed or eco in (biomes[found].get("ecoregions") or []))
        ):
            return found
    same = sorted(
        biome_id
        for biome_id, data in biomes.items()
        if data.get("habitat") == habitat
        and data.get("land") == style
        and (not listed or eco in (data.get("ecoregions") or []))
    )
    return same[0] if same else None


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


SEA_BIOMES = frozenset({4, 5, 6, 8, 11})
"""RESOLVE biomes the generic `sea` home fits (#169.1).

Temperate broadleaf and conifer, boreal forest, temperate
grassland and tundra: the cold northern seas it was made
for. A tropical cell plans a home item or blocks `no_home`
instead of a Finnish-looking archipelago by the reef.
"""


def home_for_cell(
    eco: int | None,
    habitat: str,
    homes: dict[str, dict[str, Any]],
    biome: int | None = None,
) -> str | None:
    """The home covering this ecoregion and habitat, or None.

    Sea and open ocean build from a home of habitat `sea` that
    lists the cell's ecoregion, else the generic `sea` (#140.1),
    but only where it fits: a known tropical biome refuses it
    (#169.1). Open ocean with no ecoregion builds from the one
    `open_sea` home when the checkout has it (#177.1). The reef
    blend resolves through the `coral_reef` home the same way
    (#226.1). Boreal
    water picks the lake home through the tile's habitat: a
    boreal_lake cell builds from boreal_lakeland, a
    boreal_forest cell from boreal_forest. Retired biomes are
    never picked: no new or remade tile builds on one (#201.1).
    """
    if (
        habitat == "open_ocean"
        and "open_sea" in homes
        and not homes["open_sea"].get("retired")
    ):
        return "open_sea"
    if (
        habitat == "coral_reef"
        and "coral_reef" in homes
        and not homes["coral_reef"].get("retired")
    ):
        return "coral_reef"
    if eco is None:
        return None
    if habitat in ("sea", "open_ocean"):
        listed = sorted(
            home_id
            for home_id, data in homes.items()
            if data.get("habitat") == "sea"
            and eco in (data.get("ecoregions") or [])
            and not data.get("retired")
        )
        if listed:
            return listed[0]
        if (
            "sea" in homes
            and not homes["sea"].get("retired")
            and (biome is None or biome in SEA_BIOMES)
        ):
            return "sea"
        return None
    for home_id in sorted(homes):
        data = homes[home_id]
        if (
            eco in (data.get("ecoregions") or [])
            and data.get("habitat") == habitat
            and not data.get("retired")
        ):
            return home_id
    return None


def coast_habitat(
    eco: int | None,
    habitat: str,
    land: float,
    homes: dict[str, dict[str, Any]],
    biome: int | None = None,
) -> str:
    """The habitat a coast cell builds with: sea when its land side can't.

    A coast cell whose land habitat has no home for its
    ecoregion builds from the sea side when a listed sea home
    covers the ecoregion (a Queensland coast cell with marine
    eco 150 builds from coral_reef); the world's check wants
    the home to list the ecoregion AND match the habitat, so
    the tile must flip sides, not just homes (#198.2). The
    generic sea doesn't count: new land waits for its own
    home. Anything else keeps its grid habitat.
    """
    if habitat in ("sea", "open_ocean") or not (0.0 < land < 1.0):
        return habitat
    if home_for_cell(eco, habitat, homes, biome) is not None:
        return habitat
    listed_sea = any(
        data.get("habitat") == "sea" and eco in (data.get("ecoregions") or [])
        for data in homes.values()
    )
    if not listed_sea:
        return habitat
    return "sea"


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


def _level_at(col: int, row: int, habitat_grid: HabitatGrid) -> int:
    col = col % N
    row = min(max(row, 0), N - 1)
    habitat = habitat_grid.habitat(col, row)
    _e1, _s1, _e2, _s2, _b, _l, _w, elev = habitat_grid.cell_data(col, row)
    return cell_level(habitat, elev)


def _noise(x: float, y: float) -> float:
    """Gentle value noise, at most ±2 blocks, seeded by grid position."""
    gx = x / NOISE_LATTICE_M
    gy = y / NOISE_LATTICE_M
    x0, y0 = math.floor(gx), math.floor(gy)
    fx, fy = gx - x0, gy - y0

    def lattice(lx: int, ly: int) -> float:
        key = f"z10:{lx}:{ly}".encode()
        digest = hashlib.sha256(key).digest()
        return int.from_bytes(digest[:2], "big") / 65535 - 0.5

    top = lattice(x0, y0) * (1 - fx) + lattice(x0 + 1, y0) * fx
    bottom = lattice(x0, y0 + 1) * (1 - fx) + lattice(x0 + 1, y0 + 1) * fx
    return (top * (1 - fy) + bottom * fy) * 4


def _mercator_y(lat: float) -> float:
    """Web Mercator metres north of the equator for this latitude."""
    return math.asinh(math.tan(math.radians(lat))) / math.pi * EQUATOR_M / 2


def _mercator_x(lon: float) -> float:
    """Web Mercator metres east of Greenwich for this longitude."""
    return lon / 360.0 * EQUATOR_M


def _corners(col: int, row: int) -> tuple[float, float, float, float]:
    """The cell's west, east, north and south in Mercator metres."""
    west = _mercator_x(lon_of(col))
    east = _mercator_x(lon_of(col + 1))
    north = _mercator_y(lat_of(row))
    south = _mercator_y(lat_of(row + 1))
    return west, east, north, south


def _side_points(
    start: tuple[float, float], end: tuple[float, float]
) -> list[tuple[float, float]]:
    """The side's samples from corner to corner in Mercator metres.

    The shared corner tuples stand at both ends, so corners
    agree by construction; the middle interpolates, so a side
    shared with a neighbour computes identical positions.
    """
    points = [start]
    for index in range(1, EDGE_SAMPLES - 1):
        frac = index / (EDGE_SAMPLES - 1)
        points.append(
            (
                start[0] + frac * (end[0] - start[0]),
                start[1] + frac * (end[1] - start[1]),
            )
        )
    points.append(end)
    return points


def edge_height(
    x: float,
    y: float,
    habitat_grid: HabitatGrid,
) -> int:
    """Ground height for one global position: levels blended, noise on top."""
    # One spelling per meridian: 180°E is 180°W, so the two
    # tiles either side of the date line meet exactly.
    x = (x + EQUATOR_M / 2) % EQUATOR_M - EQUATOR_M / 2
    fx = (x + EQUATOR_M / 2) / TILE_M - 0.5
    fy = (EQUATOR_M / 2 - y) / TILE_M - 0.5
    c0, r0 = math.floor(fx), math.floor(fy)
    tx, ty = fx - c0, fy - r0
    top = (
        _level_at(c0, r0, habitat_grid) * (1 - tx)
        + _level_at(c0 + 1, r0, habitat_grid) * tx
    )
    bottom = (
        _level_at(c0, r0 + 1, habitat_grid) * (1 - tx)
        + _level_at(c0 + 1, r0 + 1, habitat_grid) * tx
    )
    return max(1, round(top * (1 - ty) + bottom * ty + _noise(x, y)))


def tile_edges(tile: str, habitat_grid: HabitatGrid) -> dict[str, list[int]]:
    """The 65 ground heights per side, agreeing with neighbours.

    Sides run n and s west to east, w and e south to north.
    """
    col, row = parse_tile(tile)
    west, east, north, south = _corners(col, row)
    nw, ne, sw, se = (west, north), (east, north), (west, south), (east, south)
    sides = {"n": (nw, ne), "e": (se, ne), "s": (sw, se), "w": (sw, nw)}
    edges: dict[str, list[int]] = {}
    for side, (start, end) in sides.items():
        edges[side] = [
            edge_height(x, y, habitat_grid) for x, y in _side_points(start, end)
        ]
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
            (
                ("seabed" in (p.get("on") or []) or "reef" in (p.get("on") or []))
                and not isinstance(water, list)
            )
            or (
                "seabed" not in (p.get("on") or [])
                and "reef" not in (p.get("on") or [])
                and not isinstance(cover, list)
            )
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


_EATS_ROUND = """
import json, sys
sys.path.insert(0, sys.argv[1])
import food
if not callable(getattr(food, "eats_here", None)) or not callable(
    getattr(food, "food_gaps", None)
):
    print("the food rule predates decision 0061", file=sys.stderr)
    sys.exit(2)
queries = json.load(sys.stdin)
out = []
for q in queries:
    diet = q.get("diet") or []
    hunts = q.get("hunts") or []
    kinds = q.get("food") or []
    tags = q.get("prey_tags") or []
    out.append(
        {
            "eats": food.eats_here(diet, hunts, kinds, tags),
            "gaps": food.food_gaps(diet, hunts, kinds, tags),
        }
    )
print(json.dumps(out))
"""


def world_eats(
    world: Path, queries: list[dict[str, Any]]
) -> list[dict[str, Any]] | None:
    """Eats and gaps per query from the world's own food rule (#241.3).

    One subprocess for the batch, like the tile check: the world
    owns the rule (decision 0061), the builder only reads its
    verdicts. None when the checkout has no food rule, so tests
    with fake worlds keep every animal and report no gaps.
    """
    tools = world / "tools"
    if not (tools / "food.py").is_file():
        return None
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _EATS_ROUND, str(tools)],
            input=json.dumps(queries),
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode == 2:
        raise RuntimeError(
            "the world's food rule predates decision 0061 "
            "(tools/food.py has no eats_here/food_gaps); update the checkout"
        )
    if proc.returncode != 0:
        return None
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        return None
    return out if isinstance(out, list) else None


def tile_gaps_for(
    world: Path,
    diet: list[str],
    hunts: list[str],
    home: dict[str, Any],
    greens: dict[str, Any] | None,
    living: list[str],
) -> dict[str, list[str]]:
    """What the diet/hunts can't find on this tile: gaps, never a filter (#241.2).

    The tile's worked-out food kinds and the other living
    animals' tags, judged by the world's rule. Empty gaps when
    the checkout has no food rule.
    """
    kinds = tile_food_kinds(world, home, greens)
    tags: set[str] = set()
    for other in living:
        try:
            tags.update(_animal(world / "animals" / f"{other}.json").get("tags") or [])
        except (OSError, ValueError):
            continue
    verdicts = world_eats(
        world,
        [
            {
                "diet": list(diet),
                "hunts": list(hunts),
                "food": sorted(kinds),
                "prey_tags": sorted(tags),
            }
        ],
    )
    if not verdicts:
        return {"diet": [], "hunts": []}
    gaps = verdicts[0].get("gaps") or {}
    return {
        "diet": [str(kind) for kind in gaps.get("diet", [])],
        "hunts": [str(tag) for tag in gaps.get("hunts", [])],
    }


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
    library now, never wanted (#147.3); outside nominations the
    survey confirmed come after the library ids as `gbif:<key>`
    (#196.3).
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
    keys = answer.get("outside_keys") or {}
    for latin in answer.get("outside_animals") or []:
        if keys.get(latin) is not None:
            drops.append(
                {"kind": "animal", "what": f"gbif:{keys[latin]}", "part": "wild"}
            )
    for latin in answer.get("outside_plants") or []:
        if keys.get(latin) is not None:
            drops.append(
                {"kind": "plant", "what": f"gbif:{keys[latin]}", "part": "wild"}
            )
    return drops[:WANTED_LIMIT]


def tile_plants(
    world: Path, answer: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    """The survey's picks as the tile's plants (#147.3).

    Cover or water by the spec's grows (seabed and reef grow in water);
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
        on = grows.get("on") or []
        part = water if "seabed" in on or "reef" in on else cover
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
    """Playable, prey and predators, most present first (#241.1).

    Food never removes an animal (decision 0061): every filed
    survey species with a `diet` or `hunts` stays, and only an
    animal with neither is left out. The home's fish hunt in the
    region as well, so they join the prey pool whatever they
    find. Playable is every kept animal whose file is playable;
    predators every kept hunter; prey every kept animal someone
    hunts, plus every kept animal in neither other list, so an
    animal can be both playable and someone's prey. With no kept
    animal all three lists are empty and the tile is blocked with
    `no_animal`. The fallback when the checkout has no converter;
    make_tile otherwise selects with the world's own food-web
    check (#154), which keeps the same animals since 0061.
    """
    animals_dir = world / "animals"
    ranked = sorted(counts, key=lambda s: (-counts[s], s))
    candidates: dict[str, dict[str, Any]] = {}
    for species in ranked:
        path = animals_dir / f"{species}.json"
        if path.is_file():
            candidates[species] = _animal(path)
    fish = home_fish(world, home)
    kept = {
        species
        for species, data in candidates.items()
        if (data.get("diet") or data.get("hunts"))
    }
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
    habitat_grid: HabitatGrid,
    world: Path,
    home: str,
    adapt: bool,
) -> dict[str, Any]:
    """Our own facts for one tile's survey, maker and planner alike."""
    col, row = parse_tile(tile)
    eco1, share1, eco2, share2, _biome, land, water, elev = habitat_grid.cell_data(
        col, row
    )
    habitat = habitat_grid.habitat(col, row)
    level = cell_level(habitat, elev)
    edges = tile_edges(tile, habitat_grid)
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


def live_place_for_tile(tile: str, world: Path) -> dict[str, Any] | None:
    """The live place listing this tile in its tiles, if any (#212.1)."""
    folder = world / "places"
    if not folder.is_dir():
        return None
    col, row = parse_tile(tile)
    for path in sorted(folder.glob("*.json")):
        try:
            place = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(place, dict) or place.get("live") is not True:
            continue
        cells = place.get("tiles") or []
        if [col, row] in [list(cell) for cell in cells if len(list(cell)) == 2]:
            return place
    return None


def _place_biome(
    place: dict[str, Any], world: Path, homes: dict[str, Any], biomes: dict[str, Any]
) -> str | None:
    """A live place's old region biome, when the world still files it (#212.1)."""
    pid = place.get("id")
    if not isinstance(pid, str) or not pid:
        return None
    try:
        region = json.loads((world / "regions" / f"{pid}.json").read_text())
    except (OSError, ValueError):
        return None
    biome = region.get("biome") if isinstance(region, dict) else None
    if not isinstance(biome, str) or (biome not in homes and biome not in biomes):
        return None
    return biome


def _all_open_sea(place: dict[str, Any], world: Path) -> bool:
    """Whether every species the place lays says it lives at sea (#212.2)."""
    species = place.get("species") or {}
    ids = set(
        (species.get("playable") or [])
        + (species.get("prey") or [])
        + (species.get("predators") or [])
    )
    return bool(ids) and all(_open_sea(world, sid) for sid in ids)


def _make_place_tile(
    tile: str,
    habitat_grid: HabitatGrid,
    world: Path,
    place: dict[str, Any],
    home: str,
    house: dict[str, Any],
    layered: dict[str, Any],
    builder_root: Path,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """A live place's tile on the place's biome, never open sea (#212.1).

    Habitat, ecoregion and plants come from the biome, the
    filed species from the place file (#207): the place's
    cast plays here or the tile blocks, and its unfiled
    species are wanted, never listed. Food and budgets
    check like any tile; only the seating is placed.
    """
    from .plan import plan_name

    col, row = parse_tile(tile)
    _eco1, _s1, _eco2, _s2, _biome, land, water, elev = habitat_grid.cell_data(col, row)
    habitat = str(house.get("habitat") or "sea")
    regions = house.get("ecoregions") or []
    eco1 = regions[0] if regions else None
    lat, lon = center(tile)
    level = cell_level(habitat, elev)
    edges = tile_edges(tile, habitat_grid)
    species = place.get("species") or {}
    animals_dir = world / "animals"

    def filed(ids: list[str]) -> list[str]:
        return [s for s in ids if (animals_dir / f"{s}.json").is_file()]

    def eats(ids: list[str]) -> list[str]:
        """Filed species with a diet or hunts; the rest stay out (#241.1)."""
        out = []
        for sid in filed(ids):
            try:
                data = _animal(animals_dir / f"{sid}.json")
            except (OSError, ValueError):
                continue
            if data.get("diet") or data.get("hunts"):
                out.append(sid)
        return out

    playable = eats(species.get("playable") or [])
    prey = eats(species.get("prey") or [])
    predators = eats(species.get("predators") or [])
    if not playable:
        return None, _blocked("no_animal", habitat, None)
    wanted = [
        {"kind": "animal", "what": sid, "part": habitat}
        for sid in dict.fromkeys(
            list(species.get("playable") or [])
            + list(species.get("prey") or [])
            + list(species.get("predators") or [])
        )
        if not (animals_dir / f"{sid}.json").is_file()
    ][:12]
    maker, commit = _versions(builder_root, world)
    grown = house.get("plants") or layered.get("plants") or {}
    waters = [
        {"id": str(p["id"]), "density": p["density"]}
        for p in grown.get("water") or []
        if isinstance(p, dict) and p.get("id")
    ]
    made_tile: dict[str, Any] = {
        "format": 1,
        "id": tile,
        "kind": "code",
        "version": 1,
        "center": {"lat": round(lat, 2), "lon": round(lon, 2)},
        "level": level,
        "height": tile_height(level, edges),
        "habitat": habitat,
        "home": home,
        "biome": home,
        "seed": tile_seed(tile),
        "water": round(water, 2),
        "species": {"playable": playable, "prey": prey, "predators": predators},
        "edges": edges,
        "landmarks": [],
        "requires": {
            "homes": [home],
            "biomes": [home],
            "animals": sorted(set(playable) | set(prey) | set(predators)),
            "plants": [p["id"] for p in waters],
            "trees": [],
        },
        "sources": {
            "habitat": f"place:{place.get('id')}",
            "elevation": "etopo2022:v1_60s_surface",
            "water": "hydrolakes:v1",
            "species": f"place:{place.get('id')}",
        },
        "made": {
            "by": "tile_maker",
            "maker": maker,
            "world": commit,
            "at": datetime.now(UTC).isoformat(),
        },
    }
    if eco1 is not None:
        made_tile["ecoregion"] = eco1
    if wanted:
        made_tile["wanted"] = wanted
    made_tile["name"] = plan_name(habitat, lat, lon, world)
    if waters:
        made_tile["plants"] = {"water": waters}
    shore = shore_for_tile(tile, habitat_grid.grid_dir)
    if shore is not None:
        made_tile["shore"] = shore
    validate("tile", made_tile)
    failures: list[tuple[str, list[str]]] = []
    for _ in range(3):
        failures = check_with_world(made_tile, world)
        if not failures:
            return made_tile, None
        if all(check == "food" for check, _ in failures):
            return None, _blocked("no_animal", habitat, None)
        if not _budget_failure(failures):
            break
        _thin_densities(made_tile)
    detail = "; ".join(
        f"{check}: {', '.join(problems) or 'failed'}" for check, problems in failures
    )
    raise RuntimeError(f"tile {tile} failed the world's check: {detail}")


def make_tile(
    tile: str,
    habitat_grid: HabitatGrid,
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

    col, row = parse_tile(tile)
    eco1, _share1, eco2, share2, biome, land, water, elev = habitat_grid.cell_data(
        col, row
    )
    habitat = habitat_grid.habitat(col, row)
    if tile in excluded_tiles(builder_root):
        return None, {"reason": "excluded", "habitat": habitat}
    homes = available_homes(world)
    biomes = available_biomes(world)
    habitat = coast_habitat(eco1, habitat, land, biomes, biome)
    if habitat == "sea" and land > 0:
        land_habitat = habitat_for(eco1, biome, 1.0, 0.0)
        if home_for_cell(eco1, land_habitat, biomes, biome) is not None:
            # A coast tile with land builds from its land habitat's
            # home (#232.1); the coast mask carries the sea. Without
            # a land home for the ecoregion it stays sea (#198.2).
            habitat = land_habitat
    home = home_for_cell(eco1, habitat, biomes, biome)
    if home is None:
        if eco1 is None or not (world / "lands" / "index.json").is_file():
            return None, {"reason": "no_home", "habitat": habitat}
        style = land_style_for(world, biome)
        if style is None:
            return None, {"reason": "no_land", "habitat": habitat}
        home = default_bundle(tile, habitat_grid, biomes, habitat, style, eco1)
        if home is None:
            return None, {"reason": "no_home", "habitat": habitat}
    house = homes.get(home, biomes[home])
    if habitat == "open_ocean":
        place = live_place_for_tile(tile, world)
        if place is not None:
            placed = _place_biome(place, world, homes, biomes)
            if placed is None:
                reef_claims = (
                    home in ("open_sea", "coral_reef")
                    and habitat_grid.reef_share(col, row) >= REEF_TRACT_SHARE
                    and "coral_reef" in biomes
                    and not biomes["coral_reef"].get("retired")
                )
                # The reef map decides a mostly-reef cell even under
                # a live place whose region names no biome (#233):
                # the flip below builds coral_reef, so the mixed
                # species need no person. Any other open-sea tile
                # without a biome still raises.
                if not _all_open_sea(place, world) and not reef_claims:
                    pid = place.get("id")
                    raise ValueError(
                        f"{tile}: live place {pid} names no biome for "
                        "its open-sea tile, and its species are not "
                        "all open-sea animals; a person decides"
                    )
            else:
                return _make_place_tile(
                    tile,
                    habitat_grid,
                    world,
                    place,
                    placed,
                    homes.get(placed, biomes[placed]),
                    biomes.get(placed, {}),
                    builder_root or BUILDER_ROOT,
                )
    if (
        habitat == "open_ocean"
        and home in ("open_sea", "coral_reef")
        and habitat_grid.reef_share(col, row) >= REEF_TRACT_SHARE
        and "coral_reef" in biomes
        and not biomes["coral_reef"].get("retired")
    ):
        # A mostly-reef cell with no live place builds from the
        # reef home, habitat sea like the placed reef tiles: the
        # same listed-home path a reef-sea cell takes (#226.1). A
        # cell another listed sea home covers keeps its home: the
        # reef home must list the ecoregion (#198.2).
        habitat = "sea"
        home = "coral_reef"
        house = homes.get(home, biomes[home])
        regions = house.get("ecoregions") or []
        if eco1 is None and regions:
            eco1 = regions[0]
        reef_flipped = True
    else:
        reef_flipped = False
    sea: dict[str, Any] | None = None
    land_style: str | None = None
    land_eco: int | None = None
    if habitat == "open_ocean":
        # Open sea resolves before any species work: unnamed water
        # stays open_water, and the preview land is the nearest
        # coast's land style, so the Gulf of Finland is boreal
        # (#177.1, #177.2).
        sea = _open_sea_name(tile, builder_root or BUILDER_ROOT, world)
        if sea is None:
            return None, {"reason": "open_water", "habitat": habitat}
        land_eco, land_biome = _nearest_land(tile, habitat_grid)
        if land_eco is None:
            return None, {"reason": "no_land", "habitat": habitat}
        land_style = land_style_for(world, land_biome)
        if land_style is None:
            return None, {"reason": "no_land", "habitat": habitat}
    blend: dict[str, Any] | None = None
    blend_home = home_for_cell(eco2, habitat, biomes) if eco2 is not None else None
    if blend_home is not None and blend_home != home and share2 >= 0.1:
        # Two keys only: the world's tile check takes no third (#233).
        blend = {"home": blend_home, "share": round(share2, 2)}
    if blend is None:
        reef = habitat_grid.reef_share(col, row)
        reef_home = home_for_cell(None, "coral_reef", biomes) if reef >= 0.1 else None
        if reef_home is not None and reef_home != home:
            # Blends ship at 0.1 and up like eco blends (#230):
            # tinier shares ride no tile.
            blend = {"home": reef_home, "share": round(reef, 2)}
    coast: list[str] | None = None
    if land > 0.0 and habitat != "open_ocean":
        # Lake water reads 0 in the mask like the sea (#202.2):
        # a cell part land and part lake gets a coast, and a
        # cell all under a lake gets all 0s, even at land 1.0.
        mask = habitat_grid.coast(col, row)
        if mask is not None and "0" in mask:
            coast = [mask[r * 5 : (r + 1) * 5] for r in range(5)]
    lat, lon = center(tile)
    animals = list_animals(world)
    candidates = []
    seen: set[str] = set()
    labels = _candidate_habitats(habitat, eco1, biome, land, water)
    if home == "coral_reef":
        # Reef-filed animals live on reef tiles, not plain sea ones.
        labels.add("reef")
    for label in sorted(labels):
        for entry in animals_for_habitat(animals, label):
            if entry["id"] not in seen:
                seen.add(entry["id"])
                candidates.append(entry)
    if habitat == "open_ocean":
        # Open sea: only animals that live at sea, by code (#177.3).
        candidates = open_sea_entries(world, animals)
    wanted: list[dict[str, str]] | None = None
    greens: dict[str, Any] | None = None
    if presence is not None:
        present = {s: n for s, n in presence.items() if n > 0}
        species_tag = f"gbif:search:{datetime.now(UTC).date().isoformat()}"
    elif habitat == "open_ocean":
        present = {s: n for s, n in fetch_smoothed(tile, candidates).items() if n > 0}
        species_tag = "gbif:density"
    else:
        ids = [a["id"] for a in animals]
        greens_list = list_library(world, "plants")
        trees_list = list_library(world, "trees")
        facts = tile_facts(tile, habitat_grid, world, home, False)
        if species is not None:
            recorded = {
                **species,
                "outside_animals": species.get("outside_animals") or [],
                "outside_plants": species.get("outside_plants") or [],
            }
            answer = check_answer(
                recorded,
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
                    survey_round(world),
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
    edges = tile_edges(tile, habitat_grid)
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
        playable, prey, predators = pick_species(present, world, house, greens)
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
            "water": "hydrolakes:v1",
            "species": species_tag,
        },
        "made": {
            "by": "tile_maker",
            "maker": maker,
            "world": commit,
            "at": datetime.now(UTC).isoformat(),
        },
    }
    if reef_flipped:
        # The reef map placed this tile, not the ecoregion grid.
        made_tile["sources"]["habitat"] = REEF_SOURCE
    if habitat != "open_ocean":
        # Every land tile carries its compass name, like the plan
        # (#195.1): the game reads it straight from the tile. The
        # import stays local: plan.py builds on this module.
        from .plan import plan_name

        made_tile["name"] = plan_name(habitat, lat, lon, world)
    if habitat == "open_ocean":
        assert sea is not None and land_style is not None and land_eco is not None
        del made_tile["ecoregion"]
        made_tile["sea"] = sea["sea"]
        made_tile["name"] = sea["name"]
        made_tile["land"] = land_style
        made_tile["land_share"] = round(land, 2)
        made_tile["sources"]["habitat"] = "spec04:open_ocean"
        made_tile["sources"]["name"] = sea["source"]
        made_tile["sources"]["land"] = f"lands/index.json:nearest_coast:{land_eco}"
    if blend is not None:
        made_tile["blend"] = blend
    if coast is not None:
        made_tile["coast"] = coast
    shore = shore_for_tile(tile, habitat_grid.grid_dir)
    if shore is not None:
        made_tile["shore"] = shore
    if greens is not None:
        made_tile["plants"] = greens
    if wanted:
        made_tile["wanted"] = wanted
    validate("tile", made_tile)
    failures: list[tuple[str, list[str]]] = []
    for _ in range(3):
        failures = check_with_world(made_tile, world)
        if not failures:
            return made_tile, None
        if not _budget_failure(failures):
            break
        _thin_densities(made_tile)
    detail = "; ".join(
        f"{check}: {', '.join(problems) or 'failed'}" for check, problems in failures
    )
    raise RuntimeError(f"tile {tile} failed the world's check: {detail}")


def _budget_failure(failures: list[tuple[str, list[str]]]) -> bool:
    """Whether every failure is a plant-density budget (#186).

    The world's ground, reef and bog budgets all end the same
    way; the maker thins its own densities and tries again.
    Anything else mixed in is a real error, raised at once.
    """
    return bool(failures) and all(
        "must add up to 1 or less" in problem
        for _, problems in failures
        for problem in problems
    )


def _thin_densities(made_tile: dict[str, Any]) -> None:
    """Halve the tile's own plant densities, in place (#186).

    The counts' ratios stand; the world fits what grows. Never
    below 0.001: the tile still grows what the survey saw.
    """
    for side in ("cover", "water"):
        for entry in (made_tile.get("plants") or {}).get(side, []) or []:
            density = entry.get("density")
            if isinstance(density, (int, float)):
                entry["density"] = max(0.001, round(density / 2, 3))


def write_group(
    out_dir: Path,
    tile: str,
    made: dict[str, Any] | None,
    blocked: dict[str, Any] | None,
) -> Path:
    """Write the tile or blocked cell into its group file, merged."""
    from .grid import NAME, group

    gcol, grow = group(tile)
    path = out_dir / NAME / f"{gcol:03d}_{grow:03d}.json"
    data: dict[str, Any]
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        data["version"] = int(data.get("version", 0)) + 1
    else:
        data = {
            "format": 1,
            "grid": NAME,
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
    habitat_grid: HabitatGrid,
    world: Path,
    out_dir: Path,
    builder_root: Path | None = None,
    species_source: SpeciesSource | None = None,
    history_store: Any | None = None,
) -> tuple[str, Path, str]:
    """Make the tile at this point and write its group file.

    Returns (tile id, group path, "ready" or the blocked reason).
    The tile's history row goes to the store when one is given,
    else to `tile-history.jsonl` beside the groups (#165.2).
    """
    from . import history as history_mod
    from .grid import NAME, group

    tile = tile_at(lat, lon)
    made, blocked = make_tile(
        tile,
        habitat_grid,
        world,
        builder_root=builder_root,
        species_source=species_source,
    )
    gcol, grow = group(tile)
    group_file = out_dir / NAME / f"{gcol:03d}_{grow:03d}.json"
    old: dict[str, Any] = {}
    if group_file.is_file():
        try:
            old = json.loads(group_file.read_text(encoding="utf-8"))
        except ValueError:
            old = {}
    path = write_group(out_dir, tile, made, blocked)
    if made is not None:
        event = (
            "remade"
            if tile in (old.get("tiles") or {}) or tile in (old.get("blocked") or {})
            else "made"
        )
        reason = None
    else:
        event = "blocked"
        reason = (blocked or {}).get("reason", "?")
    try:
        version = int(json.loads(path.read_text(encoding="utf-8")).get("version", 1))
    except ValueError:
        version = 1
    entry = history_mod.row(
        tile,
        event,
        job=None,
        kind="cli",
        world=history_mod.commit(world),
        version=version,
        reason=reason,
    )
    if history_store is not None:
        history_mod.record(history_store, entry)
    else:
        local = out_dir / "tile-history.jsonl"
        history_mod.record_local(local, entry)
        print(f"history: no store, appended to {local}")
    status = "ready" if made is not None else (blocked or {}).get("reason", "?")
    return tile, path, status
