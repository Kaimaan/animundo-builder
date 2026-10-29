"""The tile maker: one honest tile per cell, by code with no AI.

`make_tile()` reads the habitat grid, the world's homes and animals,
and GBIF presence, and returns either a tile or a blocked reason. A
cell the maker can't make honestly is blocked (`no_home`,
`no_animal`, `excluded`), never filled with the wrong land or
animals.
"""

from __future__ import annotations

import hashlib
import json
import math
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
)
from .habitats import HabitatGrid
from .presence import animals_for_habitat, fetch_smoothed, library_animals
from .schemas import validate

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

    Sea and open ocean both build from sea. Boreal water picks the
    lake home through the tile's habitat: a boreal_lake cell builds
    from boreal_lake, a boreal_forest cell from boreal_forest.
    """
    if eco is None:
        return None
    if habitat in ("sea", "open_ocean"):
        for home_id in sorted(homes):
            data = homes[home_id]
            if home_id == "sea" and eco in (data.get("ecoregions") or []):
                return home_id
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


def home_food_kinds(home: dict[str, Any]) -> set[str]:
    """Every food kind the home offers, as the world's check counts them.

    The region's `food` kinds, the ground cover's food plants, and
    nectar when flowers bloom. Mirrors `check_food_web`, which stays
    authoritative: the tile check still has the last word.
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


def pick_species(
    counts: dict[str, float],
    world: Path,
    home: dict[str, Any],
) -> tuple[list[str], list[str], list[str]]:
    """Playable, prey and predators, most present first, food first.

    An animal stays only if the home has every food kind it needs,
    and a hunter only if at least one of its prey stays too. The
    home's fish hunt in the region as well, so their prey must stay
    too. Playable is every kept animal whose file is playable;
    predators every kept hunter; prey every kept animal someone
    hunts, plus every kept animal in neither other list, so an
    animal can be both playable and someone's prey. When nothing
    can live here with its food, all three lists are empty and the
    tile is blocked with `no_animal`.
    """
    animals_dir = world / "animals"
    ranked = sorted(counts, key=lambda s: (-counts[s], s))
    candidates: dict[str, dict[str, Any]] = {}
    for species in ranked:
        path = animals_dir / f"{species}.json"
        if path.is_file():
            candidates[species] = _animal(path)
    food = home_food_kinds(home)
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
    playable = [s for s in ranked if s in kept and candidates[s].get("playable")]
    if not playable:
        return [], [], []
    predators = [s for s in ranked if s in kept and (candidates[s].get("hunts") or [])]
    hunted: set[str] = set()
    for data in [candidates[s] for s in kept] + list(fish.values()):
        hunted.update(data.get("hunts") or [])
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
    which owns it; the builder only reads its verdict.
    """
    tool = world / "tools" / "check_tile.py"
    if not tool.is_file():
        return []
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


def make_tile(
    tile: str,
    grids: dict[GridName, HabitatGrid],
    world: Path,
    presence: dict[str, float] | None = None,
    builder_root: Path | None = None,
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    """Make one tile, or the blocked reason when it can't be made.

    Returns (tile, None) or (None, {"reason": ..., "habitat": ...}).
    `presence` overrides the live GBIF fetch (tests and replays).
    """
    from .schemas import ROOT as BUILDER_ROOT

    grid, col, row = parse_tile(tile)
    habitat_grid = grids[grid]
    eco1, _share1, eco2, share2, _biome, _land, water, elev = habitat_grid.cell_data(
        col, row
    )
    habitat = habitat_grid.habitat(col, row)
    if tile in excluded_tiles(builder_root):
        return None, {"reason": "excluded", "habitat": habitat}
    homes = available_homes(world)
    home = home_for_cell(eco1, habitat, homes)
    if home is None:
        return None, {"reason": "no_home", "habitat": habitat}
    blend: dict[str, Any] | None = None
    blend_home = home_for_cell(eco2, habitat, homes) if eco2 is not None else None
    if blend_home is not None and blend_home != home and share2 >= 0.1:
        blend = {"home": blend_home, "share": round(share2, 2)}
    animals = library_animals(world)
    candidates = animals_for_habitat(animals, habitat)
    # Sea tiles also take open-ocean swimmers and vice versa.
    if habitat in ("sea", "open_ocean"):
        others = animals_for_habitat(
            animals, "open_ocean" if habitat == "sea" else "sea"
        )
        seen = {a["id"] for a in candidates}
        candidates += [a for a in others if a["id"] not in seen]
    counts = presence if presence is not None else fetch_smoothed(tile, candidates)
    present = {s: n for s, n in counts.items() if n > 0}
    playable, prey, predators = pick_species(present, world, homes[home])
    if not playable:
        return None, {"reason": "no_animal", "habitat": habitat}
    lat, lon = center(tile)
    level = cell_level(habitat, elev)
    edges = tile_edges(tile, grids)
    maker, commit = _versions(builder_root or BUILDER_ROOT, world)
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
        "seed": tile_seed(tile),
        "water": round(water, 2),
        "species": {"playable": playable, "prey": prey, "predators": predators},
        "edges": edges,
        "landmarks": [],
        "requires": {
            "homes": sorted({home} | ({blend["home"]} if blend else set())),
            "animals": sorted(set(playable) | set(prey) | set(predators)),
        },
        "sources": {
            "habitat": f"resolve2017:{eco1}",
            "elevation": "etopo2022:v1_60s_surface",
            "water": "naturalearth:lakes:5.0.0",
            "species": f"gbif:search:{datetime.now(UTC).date().isoformat()}",
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
    validate("tile", made_tile)
    failures = check_with_world(made_tile, world)
    if failures:
        if all(check == "food" for check, _ in failures):
            return None, {"reason": "no_animal", "habitat": habitat}
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
    blocked: dict[str, str] | None,
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
) -> tuple[str, Path, str]:
    """Make the tile at this point and write its group file.

    Returns (tile id, group path, "ready" or the blocked reason).
    """
    tile = tile_at(lat, lon)
    made, blocked = make_tile(tile, grids, world, builder_root=builder_root)
    path = write_group(out_dir, tile, made, blocked)
    status = "ready" if made is not None else (blocked or {}).get("reason", "?")
    return tile, path, status
