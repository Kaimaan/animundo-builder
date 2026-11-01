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
from .grid import N, bounds, lat_of, lon_of, neighbours, parse_tile

GBIF_SEARCH = "https://api.gbif.org/v1/occurrence/search"
GBIF_MATCH = "https://api.gbif.org/v1/species/match"
GBIF_TRIES = 3
GBIF_PAUSE = 1.0

WILD = {
    "basisOfRecord": ["HUMAN_OBSERVATION", "MACHINE_OBSERVATION"],
    "year": "1996,2026",
    "hasCoordinate": "true",
    "occurrenceStatus": "PRESENT",
}


def cell_wkt(tile: str) -> str:
    """The tile's box as WKT for GBIF's geometry filter (#182.2)."""
    south, north, west, east = bounds(tile)
    # Counter-clockwise, as GBIF's geometry filter requires.
    corners = [
        (west, north),
        (west, south),
        (east, south),
        (east, north),
        (west, north),
    ]
    ring = ", ".join(f"{lon:.6f} {lat:.6f}" for lon, lat in corners)
    return f"POLYGON (({ring}))"


def _gbif(params: dict[str, Any], base: str = GBIF_SEARCH) -> dict[str, Any]:
    """One GBIF call, retried; raises when it stays down."""
    query = urllib.parse.urlencode(params, doseq=True)
    last_error = ""
    for _ in range(GBIF_TRIES):
        try:
            request = urllib.request.Request(
                f"{base}?{query}",
                headers={"User-Agent": "animundo-builder-presence/0.1"},
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as error:  # noqa: BLE001 - retried, then raised
            last_error = str(error)
            time.sleep(GBIF_PAUSE)
    raise ConnectionError(f"GBIF stayed down: {last_error}")


def match_kingdom(name: str) -> str | None:
    """The GBIF kingdom a Latin name matches, or nothing (#146.2)."""
    data = _gbif({"name": name}, GBIF_MATCH)
    if data.get("matchType", "NONE") == "NONE":
        return None
    return data.get("kingdom")


def match_key(name: str) -> int | None:
    """The GBIF species key a Latin name matches, or nothing (#196.3)."""
    data = _gbif({"name": name}, GBIF_MATCH)
    if data.get("matchType", "NONE") == "NONE":
        return None
    key = data.get("speciesKey", data.get("usageKey"))
    return int(key) if key is not None else None


def near_wkt(tile: str, span: int = 2) -> str:
    """The tile's box grown by `span` cells each way, as WKT (#196.3).

    Clamped at the map's edges (and the antimeridian), where a
    grown box would wrap.
    """
    x, y = parse_tile(tile)
    south = lat_of(min(y + 1 + span, N))
    north = lat_of(max(y - span, 0))
    west = lon_of(max(x - span, 0))
    east = lon_of(min(x + 1 + span, N))
    corners = [
        (west, north),
        (west, south),
        (east, south),
        (east, north),
        (west, north),
    ]
    ring = ", ".join(f"{lon:.6f} {lat:.6f}" for lon, lat in corners)
    return f"POLYGON (({ring}))"


def has_records_near(key: int, tile: str, span: int = 2) -> bool:
    """A wild GBIF record for this key near the tile, or not (#196.3)."""
    data = _gbif(
        {
            **WILD,
            "taxon_key": str(key),
            "geometry": near_wkt(tile, span),
            "limit": 0,
        }
    )
    return int(data.get("count", 0)) > 0


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
