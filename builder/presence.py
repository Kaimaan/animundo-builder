"""Species presence: wild GBIF records per tile cell, smoothed.

Batched GBIF occurrence calls per cell, faceted by species, counts
only (`limit=0`). Counts are smoothed over the 3 by 3 neighbourhood
so a species just over a cell border still counts. Only wild,
recent, located records: human and machine observations since 1996
with coordinates and PRESENT status.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .animal_list import find_list, load_list
from .grid import CELL_M, HALF_M, neighbours, parse_tile

GBIF_SEARCH = "https://api.gbif.org/v1/occurrence/search"
GBIF_TRIES = 3
GBIF_PAUSE = 1.0

WILD = {
    "basisOfRecord": ["HUMAN_OBSERVATION", "MACHINE_OBSERVATION"],
    "year": "1996,2026",
    "hasCoordinate": "true",
    "occurrenceStatus": "PRESENT",
}


def cell_wkt(tile: str) -> str:
    """The tile cell corners as WKT for GBIF's geometry filter."""
    from pyproj import Transformer

    grid, col, row = parse_tile(tile)
    epsg = 6931 if grid == "ease2_n25" else 6932
    back = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    x0 = -HALF_M + col * CELL_M
    x1 = x0 + CELL_M
    y1 = HALF_M - row * CELL_M
    y0 = y1 - CELL_M
    # Counter-clockwise, as GBIF's geometry filter requires.
    corners = [(x0, y1), (x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    lonlats = [back.transform(x, y) for x, y in corners]
    ring = ", ".join(f"{lon:.6f} {lat:.6f}" for lon, lat in lonlats)
    return f"POLYGON (({ring}))"


def _gbif(params: dict[str, Any]) -> dict[str, Any]:
    """One GBIF occurrence call, retried; raises when it stays down."""
    query = urllib.parse.urlencode(params, doseq=True)
    last_error = ""
    for _ in range(GBIF_TRIES):
        try:
            request = urllib.request.Request(
                f"{GBIF_SEARCH}?{query}",
                headers={"User-Agent": "animundo-builder-presence/0.1"},
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as error:  # noqa: BLE001 - retried, then raised
            last_error = str(error)
            time.sleep(GBIF_PAUSE)
    raise ConnectionError(f"GBIF stayed down: {last_error}")


#: GBIF answers 50 taxon keys per call; more is a 400.
KEYS_PER_CALL = 50


def fetch_raw(tile: str, animals: list[dict[str, Any]]) -> dict[str, int]:
    """Wild record counts per animal id in this cell, batched GBIF calls."""
    keyed = [a for a in animals if a.get("gbif_key")]
    by_key = {str(a["gbif_key"]): a["id"] for a in keyed}
    counts: dict[str, int] = {}
    wkt = cell_wkt(tile)
    for start in range(0, len(keyed), KEYS_PER_CALL):
        batch = keyed[start : start + KEYS_PER_CALL]
        params: dict[str, Any] = {
            **WILD,
            "taxon_key": [str(a["gbif_key"]) for a in batch],
            "geometry": wkt,
            "facet": "speciesKey",
            "facetLimit": len(batch),
            "limit": 0,
        }
        data = _gbif(params)
        for facet in data.get("facets", []):
            if str(facet.get("field")).upper() != "SPECIES_KEY":
                continue
            for entry in facet.get("counts", []):
                animal_id = by_key.get(str(entry.get("name")))
                if animal_id is not None:
                    counts[animal_id] = int(entry.get("count", 0))
    return counts


def animals_for_habitat(
    animals: list[dict[str, Any]], habitat: str
) -> list[dict[str, Any]]:
    """The library animals that live in this habitat."""
    return [a for a in animals if habitat in (a.get("habitats") or [])]


def fetch_neighbourhood(
    tile: str, animals: list[dict[str, Any]]
) -> dict[str, dict[str, int]]:
    """Raw counts for the cell and its existing neighbours."""
    tiles = [tile] + [n for n in neighbours(tile).values() if n is not None]
    return {t: fetch_raw(t, animals) for t in tiles}


def smooth(centre: dict[str, int], nearby: list[dict[str, int]]) -> dict[str, float]:
    """Blend the cell with its neighbours: centre counts triple.

    A species just over the border still counts here. A species only
    next door counts at a third of the weight, so it takes a strong
    neighbour signal to matter.
    """
    out: dict[str, float] = {}
    for species, count in centre.items():
        total = 3 * count
        total += sum(n.get(species, 0) for n in nearby)
        out[species] = total / (3 + len(nearby))
    for near in nearby:
        for species, count in near.items():
            if species not in out:
                out[species] = count / (3 + len(nearby))
    return out


def fetch_smoothed(tile: str, animals: list[dict[str, Any]]) -> dict[str, float]:
    """Smoothed wild counts per animal id for this cell."""
    hoods = fetch_neighbourhood(tile, animals)
    centre = hoods.pop(tile)
    return smooth(centre, list(hoods.values()))


def library_animals(world: Path) -> list[dict[str, Any]]:
    """The library animals with GBIF keys, from the world checkout."""
    data = load_list(find_list(world))
    return [e for e in data["animals"] if e.get("gbif_key")]
