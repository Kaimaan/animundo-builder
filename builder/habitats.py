"""The world habitat grid: which habitat each tile cell holds, with no AI.

RESOLVE Ecoregions 2017 gives the ecoregions and biome; Natural Earth
gives land and lakes; ETOPO 2022 gives elevation. `bake()` samples
5 by 5 points per cell and writes one compact file per grid: each
cell keeps `[eco1, share1, eco2, share2, biome, land, water,
elevation_m]`. `HabitatGrid` reads them back and `habitat_for()`
maps one cell to a habitat id.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import fiona
import numpy as np
import shapely
from shapely.geometry import shape
from shapely.strtree import STRtree

from .grid import CELL_M, CELLS, HALF_M, GridName, parse_tile

#: RESOLVE biome number to habitat id. Boreal forest becomes
#: `boreal_lake` when wet; see `habitat_for()`.
BIOME_TO_HABITAT: dict[int, str] = {
    1: "rainforest",
    2: "dry_forest",
    3: "dry_forest",
    4: "forest",
    5: "forest",
    6: "boreal_forest",
    7: "savanna",
    8: "grassland",
    9: "wetland",
    10: "mountain",
    11: "tundra",
    12: "mediterranean",
    13: "desert",
    14: "mangrove",
}

#: RESOLVE ecoregion id for Rock and Ice (its biome reads N/A).
ROCK_AND_ICE_ECO_ID = 0

#: Sample points per cell side.
SAMPLES = 5

#: Cells per bake batch (keeps memory flat over a million cells).
BATCH_CELLS = 50_000

#: Coarse mask step in degrees (about 11 km): points deep inside
#: one mask cell skip the exact polygon tests.
MASK_STEP = 0.1


def habitat_for(
    eco_id: int | None, biome: int | None, land: float, water: float
) -> str:
    """The habitat id for one cell's baked values."""
    if land < 0.1:
        return "open_ocean"
    if land < 0.5:
        return "sea"
    if eco_id == ROCK_AND_ICE_ECO_ID:
        return "ice"
    if biome is None:
        return "sea"
    habitat = BIOME_TO_HABITAT.get(biome)
    if habitat is None:
        return "sea"
    if habitat == "boreal_forest" and water >= 0.15:
        return "boreal_lake"
    if habitat != "boreal_forest" and habitat != "boreal_lake" and water >= 0.5:
        return "lake"
    return habitat


class HabitatGrid:
    """One baked grid file: cell data and habitats by tile."""

    def __init__(self, cells: dict[str, list[Any]], grid: GridName) -> None:
        self._cells = cells
        self.grid = grid

    @classmethod
    def load(cls, path: Path, grid: GridName) -> HabitatGrid:
        """Read a baked `ease2_*.json.gz` file."""
        raw = gzip.open(path, "rt", encoding="utf-8").read()
        data = json.loads(raw)
        return cls(data["cells"], grid)

    def cell_data(
        self, col: int, row: int
    ) -> tuple[Any, Any, Any, Any, Any, float, float, float]:
        """The baked `[eco1, share1, eco2, share2, biome, land, water, elevation_m]`."""
        eco1, share1, eco2, share2, biome, land, water, elevation = self._cells[
            f"{col},{row}"
        ]
        return (
            eco1,
            float(share1),
            eco2,
            float(share2),
            biome,
            float(land),
            float(water),
            float(elevation),
        )

    def habitat(self, col: int, row: int) -> str:
        """The habitat id for one cell."""
        eco1, _s1, _e2, _s2, biome, land, water, _elev = self.cell_data(col, row)
        return habitat_for(eco1, biome, land, water)

    def habitat_at(self, tile: str) -> str:
        """The habitat id for one tile id."""
        grid, col, row = parse_tile(tile)
        if grid != self.grid:
            raise ValueError(f"{tile} is not on {self.grid}")
        return self.habitat(col, row)

    def water_at(self, tile: str) -> float:
        """The lake-water share for one tile id."""
        grid, col, row = parse_tile(tile)
        if grid != self.grid:
            raise ValueError(f"{tile} is not on {self.grid}")
        return self.cell_data(col, row)[6]

    def ecoregions_at(self, tile: str) -> tuple[Any, float, Any, float]:
        """The (eco1, share1, eco2, share2) for one tile id."""
        grid, col, row = parse_tile(tile)
        if grid != self.grid:
            raise ValueError(f"{tile} is not on {self.grid}")
        eco1, share1, eco2, share2, _b, _l, _w, _e = self.cell_data(col, row)
        return eco1, share1, eco2, share2

    def elevation_at(self, tile: str) -> float:
        """The mean land elevation in metres for one tile id."""
        grid, col, row = parse_tile(tile)
        if grid != self.grid:
            raise ValueError(f"{tile} is not on {self.grid}")
        return self.cell_data(col, row)[7]


def _load_shapes(
    path: Path, simplify: float = 0.0
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Geometries and properties from a shapefile.

    `simplify` trims vertices within that many degrees; 0.02 (about
    2 km) keeps 25 km cells honest while making the bake 30 times
    faster. Topology is not preserved: slivers may appear, but a
    sample point only needs the covering polygon.
    """
    geoms: list[Any] = []
    props: list[dict[str, Any]] = []
    with fiona.open(path) as src:
        for feature in src:
            geom = feature["geometry"]
            if geom is None:
                continue
            parsed = shape(geom)
            if simplify > 0:
                parsed = shapely.simplify(parsed, simplify, preserve_topology=False)
            shapely.prepare(parsed)
            geoms.append(parsed)
            props.append(dict(feature["properties"]))
    return geoms, props


def _sample_points(
    grid: GridName, cols: list[int], rows: list[int]
) -> tuple[Any, list[tuple[int, int]]]:
    """The 5 by 5 sample points per cell, as a shapely point array."""
    from pyproj import Transformer

    epsg = 6931 if grid == "ease2_n25" else 6932
    back = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    fracs = [(i + 0.5) / SAMPLES for i in range(SAMPLES)]
    xs: list[float] = []
    ys: list[float] = []
    owners: list[tuple[int, int]] = []
    for col, row in zip(cols, rows):
        x0 = -HALF_M + col * CELL_M
        y1 = HALF_M - row * CELL_M
        for fx in fracs:
            for fy in fracs:
                xs.append(x0 + fx * CELL_M)
                ys.append(y1 - fy * CELL_M)
                owners.append((col, row))
    lons, lats = back.transform(np.array(xs), np.array(ys))
    return shapely.points(lons, lats), owners


def bake(
    resolve_path: Path,
    land_path: Path,
    lakes_path: Path,
    dem_path: Path | None,
    out_dir: Path,
    grids: tuple[GridName, ...] = ("ease2_n25", "ease2_s25"),
    bbox: tuple[int, int, int, int] | None = None,
) -> dict[str, int]:
    """Bake the habitat grid from the sources and the DEM.

    `bbox` is (col0, row0, col1, row1), baked instead of the whole
    grid, for tests. Without a DEM every cell's elevation is 0.
    Returns counts per grid. The raw downloads are read from their
    staging paths and never committed.
    """
    import rasterio

    resolve_geoms, resolve_props = _load_shapes(resolve_path, simplify=0.02)
    land_geoms, _ = _load_shapes(land_path, simplify=0.02)
    lakes_geoms, _ = _load_shapes(lakes_path, simplify=0.005)
    resolve_tree = STRtree(resolve_geoms)
    land_tree = STRtree(land_geoms)
    lakes_tree = STRtree(lakes_geoms)
    masks = (
        _build_masks(land_tree, resolve_tree, resolve_props) if bbox is None else None
    )
    dem = rasterio.open(dem_path) if dem_path is not None else None
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        counts: dict[str, int] = {}
        for grid in grids:
            cells = _bake_grid(
                grid,
                resolve_geoms,
                resolve_props,
                resolve_tree,
                land_tree,
                lakes_tree,
                bbox,
                masks,
                dem,
            )
            out_path = out_dir / f"{grid}.json.gz"
            with gzip.open(out_path, "wt", encoding="utf-8") as handle:
                json.dump({"grid": grid, "cells": cells}, handle)
            counts[grid] = len(cells)
    finally:
        if dem is not None:
            dem.close()
    return counts


def _build_masks(
    land_tree: STRtree, resolve_tree: STRtree, resolve_props: list[dict[str, Any]]
) -> tuple[Any, Any, Any]:
    """Coarse global masks: land, ecoregion id and biome at 0.1 degrees."""
    lons = np.arange(-180 + MASK_STEP / 2, 180, MASK_STEP)
    lats = np.arange(-90 + MASK_STEP / 2, 90, MASK_STEP)
    llons, llats = np.meshgrid(lons, lats)
    points = shapely.points(llons.ravel(), llats.ravel())
    land = np.zeros(len(points), dtype=bool)
    for _point_idx, _tree_idx in zip(*land_tree.query(points, predicate="intersects")):
        land[int(_point_idx)] = True
    eco = np.full(len(points), -1, dtype=np.int16)
    biome = np.zeros(len(points), dtype=np.uint8)
    land_points = points[land]
    land_idx = np.nonzero(land)[0]
    pairs = resolve_tree.query(land_points, predicate="intersects")
    seen: set[int] = set()
    for point_pos, tree_idx in zip(pairs[0], pairs[1]):
        if int(point_pos) in seen:
            continue
        seen.add(int(point_pos))
        props = resolve_props[int(tree_idx)]
        flat = land_idx[int(point_pos)]
        eco_value = props.get("ECO_ID")
        biome_value = props.get("BIOME_NUM")
        eco[flat] = -1 if eco_value is None else int(eco_value)
        biome[flat] = 0 if biome_value is None else int(biome_value)
    shape2d = (len(lats), len(lons))
    return land.reshape(shape2d), eco.reshape(shape2d), biome.reshape(shape2d)


def _mask_lookup(
    masks: tuple[Any, Any, Any] | None, lon: float, lat: float
) -> tuple[bool, int | None, int | None, bool]:
    """Coarse (land, eco, biome, interior) for one lon/lat.

    `interior` means all eight neighbouring mask cells agree, so no
    exact test is needed. Without masks, nothing is interior.
    """
    if masks is None:
        return False, None, None, False
    land_mask, eco_mask, biome_mask = masks
    rows, cols = land_mask.shape
    col = int((lon + 180) // MASK_STEP)
    row = int((lat + 90) // MASK_STEP)
    col = min(max(col, 0), cols - 1)
    row = min(max(row, 0), rows - 1)
    land = bool(land_mask[row, col])
    eco_value = int(eco_mask[row, col])
    biome_value = int(biome_mask[row, col])
    eco = eco_value if eco_value >= 0 else None
    biome = biome_value if biome_value > 0 else None
    r0, r1 = max(row - 1, 0), min(row + 2, rows)
    c0, c1 = max(col - 1, 0), min(col + 2, cols)
    neighbourhood = land_mask[r0:r1, c0:c1]
    if land:
        interior = bool(np.all(neighbourhood)) and bool(
            np.all(eco_mask[r0:r1, c0:c1] == eco_value)
        )
    else:
        interior = not bool(np.any(neighbourhood))
    return land, eco, biome, interior


def _batches(
    bbox: tuple[int, int, int, int] | None,
) -> list[tuple[list[int], list[int]]]:
    if bbox is None:
        cols = list(range(CELLS))
        rows = list(range(CELLS))
    else:
        c0, r0, c1, r1 = bbox
        cols = list(range(c0, c1 + 1))
        rows = list(range(r0, r1 + 1))
    pairs = [(c, r) for r in rows for c in cols]
    out: list[tuple[list[int], list[int]]] = []
    for start in range(0, len(pairs), BATCH_CELLS):
        chunk = pairs[start : start + BATCH_CELLS]
        out.append(([c for c, _r in chunk], [r for _c, r in chunk]))
    return out


def _bake_grid(
    grid: GridName,
    resolve_geoms: list[Any],
    resolve_props: list[dict[str, Any]],
    resolve_tree: STRtree,
    land_tree: STRtree,
    lakes_tree: STRtree,
    bbox: tuple[int, int, int, int] | None,
    masks: tuple[Any, Any, Any] | None,
    dem: Any | None,
) -> dict[str, list[Any]]:
    cells: dict[str, list[Any]] = {}
    for cols, rows in _batches(bbox):
        points, owners = _sample_points(grid, cols, rows)
        lons = shapely.get_x(points)
        lats = shapely.get_y(points)
        exact_idx: list[int] = []
        coarse: dict[int, tuple[bool, int | None, int | None]] = {}
        for idx, (lon, lat) in enumerate(zip(lons, lats)):
            land, eco, biome, interior = _mask_lookup(masks, lon, lat)
            if interior:
                coarse[idx] = (land, eco, biome)
            else:
                exact_idx.append(idx)
        exact_points = points[exact_idx] if exact_idx else points[:0]
        exact_pos = {idx: pos for pos, idx in enumerate(exact_idx)}
        land_hits = _covering(land_tree, exact_points) if exact_idx else []
        exact_land = [i for pos, i in enumerate(exact_idx) if land_hits[pos]]
        exact_land_points = points[exact_land] if exact_land else points[:0]
        lake_hits = _covering(lakes_tree, exact_land_points) if exact_land else []
        eco_hits = _covering(resolve_tree, exact_land_points) if exact_land else []
        lake_by_point = dict(zip(exact_land, lake_hits))
        eco_by_point = dict(zip(exact_land, eco_hits))
        lake_coarse_idx = [i for i in coarse if coarse[i][0]]
        lake_coarse_points = points[lake_coarse_idx] if lake_coarse_idx else points[:0]
        lake_coarse_hits = (
            _covering(lakes_tree, lake_coarse_points) if lake_coarse_idx else []
        )
        for i, hits in zip(lake_coarse_idx, lake_coarse_hits):
            lake_by_point[i] = hits
        by_cell: dict[tuple[int, int], dict[str, Any]] = {}
        land_points: dict[tuple[int, int], list[tuple[float, float]]] = {}
        for idx, owner in enumerate(owners):
            slot = by_cell.setdefault(owner, {"land": 0, "lake": 0, "ecos": [], "n": 0})
            slot["n"] += 1
            if idx in coarse:
                land, eco, biome = coarse[idx]
                if not land:
                    continue
                slot["land"] += 1
                if lake_by_point.get(idx):
                    slot["lake"] += 1
                slot["ecos"].append((eco, biome))
                land_points.setdefault(owner, []).append(
                    (float(lons[idx]), float(lats[idx]))
                )
                continue
            if not land_hits[exact_pos[idx]]:
                continue
            slot["land"] += 1
            if lake_by_point.get(idx):
                slot["lake"] += 1
            for hit in eco_by_point.get(idx, []):
                props = resolve_props[hit]
                eco = props.get("ECO_ID")
                biome = props.get("BIOME_NUM")
                slot["ecos"].append((eco, biome))
                break
            land_points.setdefault(owner, []).append(
                (float(lons[idx]), float(lats[idx]))
            )
        elevations = _elevations(dem, land_points)
        for (col, row), slot in by_cell.items():
            n = slot["n"]
            land = slot["land"] / n if n else 0.0
            water = slot["lake"] / slot["land"] if slot["land"] else 0.0
            (eco1, biome1, share1), (eco2, _biome2, share2) = _top_two(slot["ecos"])
            biome = int(biome1) if biome1 is not None else None
            eco1 = int(eco1) if eco1 is not None else None
            eco2 = int(eco2) if eco2 is not None else None
            cells[f"{col},{row}"] = [
                eco1,
                round(share1, 4),
                eco2,
                round(share2, 4),
                biome,
                round(land, 4),
                round(water, 4),
                round(elevations.get((col, row), 0.0), 1),
            ]
    return cells


def _elevations(
    dem: Any | None, land_points: dict[tuple[int, int], list[tuple[float, float]]]
) -> dict[tuple[int, int], float]:
    """Mean elevation over each cell's land points, or 0 without a DEM."""
    if dem is None:
        return {}
    out: dict[tuple[int, int], float] = {}
    for cell, coords in land_points.items():
        try:
            samples = list(dem.sample(coords))
        except Exception:
            continue
        values = [float(s[0]) for s in samples if s[0] is not None]
        if values:
            out[cell] = sum(values) / len(values)
    return out


def _covering(tree: STRtree, points: Any) -> list[list[int]]:
    """Polygon indexes covering each point, checked exactly (vectorized)."""
    pairs = tree.query(points, predicate="intersects")
    out: list[list[int]] = [[] for _ in range(len(points))]
    for point_idx, tree_idx in zip(pairs[0], pairs[1]):
        out[int(point_idx)].append(int(tree_idx))
    return out


def _top_two(
    pairs: list[tuple[Any, Any]],
) -> tuple[tuple[Any, Any, float], tuple[Any, Any, float]]:
    """The two most common ecoregions with their land shares."""
    counts: dict[tuple[Any, Any], int] = {}
    for pair in pairs:
        counts[pair] = counts.get(pair, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], str(item[0][0])))
    total = len(pairs)
    first: tuple[Any, Any, float] = (None, None, 0.0)
    second: tuple[Any, Any, float] = (None, None, 0.0)
    if ranked:
        (eco, biome), n = ranked[0]
        first = (eco, biome, n / total if total else 0.0)
    if len(ranked) > 1:
        (eco, biome), n = ranked[1]
        second = (eco, biome, n / total if total else 0.0)
    return first, second
