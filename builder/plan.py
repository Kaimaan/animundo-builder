"""The planner (T74): what a border tile needs and what that costs.

`plan_tile()` writes no Firestore; the worker stores its result in
`plans/{tile}`, and `builder request plan <tile>` prints it. By code
with no model call. A tile code can make is `code_only` with no
items; anything missing becomes one item per new thing, each with
an id, a kind, what it is, what it is adapted from, and its
measured cost. At most one home, one animal, one food and one
thing to find expand the area; at most 2 improve species.
"""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .grid import CELLS, GridName, center, parse_tile
from .habitats import HabitatGrid
from .presence import animals_for_habitat, fetch_smoothed, library_animals
from .prices import ITEM_ESTIMATES
from .schemas import validate
from .tiles import (
    available_homes,
    excluded_tiles,
    home_for_cell,
    make_tile,
    pick_species,
)

#: Hand-made places the plan name points from: id, English and
#: genitive Finnish names, and the region centre.
PLACES: tuple[tuple[str, str, str, float, float], ...] = (
    ("nuuksio", "Nuuksio", "Nuuksion", 60.3007, 24.5263),
    ("saimaa", "Saimaa", "Saimaan", 62.115, 28.508),
)

#: Compass winds for the plan name, clockwise from north.
WINDS: tuple[tuple[str, str], ...] = (
    ("north", "pohjois"),
    ("northeast", "koillis"),
    ("east", "itä"),
    ("southeast", "kaakkois"),
    ("south", "etelä"),
    ("southwest", "lounais"),
    ("west", "länsi"),
    ("northwest", "luoteis"),
)

#: Habitat words for the plan name, English and Finnish.
HABITATS: dict[str, tuple[str, str]] = {
    "boreal_forest": ("Boreal forest", "Havumetsä"),
    "boreal_lake": ("Boreal lake", "Erämaajärvi"),
    "desert": ("Desert", "Aavikko"),
    "forest": ("Forest", "Metsä"),
    "grassland": ("Grassland", "Aro"),
    "ice": ("Ice", "Jää"),
    "lake": ("Lake", "Järvi"),
    "mangrove": ("Mangrove", "Mangrove"),
    "mountain": ("Mountains", "Vuoristo"),
    "rainforest": ("Rainforest", "Sademetsä"),
    "reef": ("Reef", "Riutta"),
    "river": ("River", "Joki"),
    "savanna": ("Savanna", "Savanni"),
    "sea": ("Sea", "Meri"),
    "tundra": ("Tundra", "Tundra"),
    "wetland": ("Wetland", "Kosteikko"),
    "dry_forest": ("Dry forest", "Kuiva metsä"),
    "mediterranean": ("Mediterranean forest", "Välimeren metsä"),
    "open_ocean": ("Open ocean", "Avomeri"),
}


def plan_name(habitat: str, lat: float, lon: float) -> dict[str, str]:
    """The area name from its habitat and nearest hand-made place.

    "Forest north of Nuuksio" / "Metsä Nuuksion pohjoispuolella",
    by code: the nearest place, the 8-wind compass, both languages.
    """
    words = HABITATS.get(habitat, (habitat.replace("_", " ").title(), habitat))
    scored = []
    for _pid, en, gen, plat, plon in PLACES:
        east = (lon - plon) * math.cos(math.radians(lat))
        north = lat - plat
        dist = math.hypot(east, north)
        angle = (math.degrees(math.atan2(east, north)) + 360) % 360
        wind = WINDS[int((angle + 22.5) // 45) % 8]
        scored.append((dist, en, gen, wind))
    _dist, en, gen, wind = sorted(scored)[0]
    return {
        "en": f"{words[0]} {wind[0]} of {en}",
        "fi": f"{words[1]} {gen} {wind[1]}puolella",
    }


def nearest_home(
    tile: str,
    grids: dict[GridName, HabitatGrid],
    homes: dict[str, dict[str, Any]],
    habitat: str,
) -> str:
    """The nearest existing home to adapt a new home from.

    The first home around the tile in expanding rings: the
    ecoregion the child came from. Without one nearby, the first
    home of the same habitat; without that, the first home there
    is. A world with no homes at all is broken, said loudly.
    """
    if not homes:
        raise ValueError("the world has no homes to adapt from")
    grid, col, row = parse_tile(tile)
    habitat_grid = grids[grid]
    for radius in range(1, 6):
        for dcol in range(-radius, radius + 1):
            for drow in range(-radius, radius + 1):
                if max(abs(dcol), abs(drow)) != radius:
                    continue
                ncol = min(max(col + dcol, 0), CELLS - 1)
                nrow = min(max(row + drow, 0), CELLS - 1)
                try:
                    eco = habitat_grid.cell_data(ncol, nrow)[0]
                    neighbour = habitat_grid.habitat(ncol, nrow)
                except KeyError:
                    continue
                found = home_for_cell(eco, neighbour, homes)
                if found is not None:
                    return found
    same = sorted(h for h, d in homes.items() if d.get("habitat") == habitat)
    if same:
        return same[0]
    return sorted(homes)[0]


def _unfiled(
    world: Path, habitat: str, entries: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Listed animals with GBIF keys but no animal files, for a habitat."""
    animals = animals_for_habitat(entries, habitat)
    if habitat in ("sea", "open_ocean"):
        other = "open_ocean" if habitat == "sea" else "sea"
        animals += [e for e in animals_for_habitat(entries, other) if e not in animals]
    return [
        e
        for e in animals
        if e.get("can_make", True)
        and not (world / "animals" / f"{e['id']}.json").is_file()
    ]


def _pick_animal(
    unfiled: list[dict[str, Any]],
    counts: dict[str, float],
    request: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """The unfiled animal to make: the explorer's own, else most recorded.

    When the explorer plays an animal the library lacks and GBIF
    recorded it near the cell, that animal serves them directly.
    Otherwise the most recorded one; ties break by id. Animals
    without a `start_from` are left out: the one-shot maker cannot
    adapt from a home, so they get no item until D19's `newshape`.
    """
    buildable = [e for e in unfiled if e.get("start_from")]
    recorded = {e["id"]: counts.get(e["id"], 0.0) for e in buildable}
    recorded = {s: n for s, n in recorded.items() if n > 0}
    if not recorded:
        return None
    played = (request or {}).get("species_played") or {}
    ranked_played = sorted(played, key=lambda s: (-played[s], s))
    for species in ranked_played:
        if species in recorded:
            return next(e for e in buildable if e["id"] == species)
    winner = sorted(recorded, key=lambda s: (-recorded[s], s))[0]
    return next(e for e in buildable if e["id"] == winner)


def _missing_food(
    present: dict[str, float],
    world: Path,
    home: dict[str, Any],
) -> str | None:
    """The first food kind a present filed animal needs and lacks."""
    from .tiles import home_food_kinds

    kinds = home_food_kinds(home)
    for species in sorted(present, key=lambda s: (-present[s], s)):
        path = world / "animals" / f"{species}.json"
        if not path.is_file():
            continue
        diet = json.loads(path.read_text(encoding="utf-8")).get("diet") or []
        for kind in diet:
            if kind not in kinds:
                return kind
    return None


def plant_slug(name: str) -> str:
    """A food name as a plant id: "Arctic Willow" -> "arctic_willow"."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _missing_find(world: Path, home: dict[str, Any]) -> tuple[str, str] | None:
    """The home's first named plant without a plant file, and its reference.

    Homes name the plants children find (`food[].names`, say
    "arctic willow"); the first name with no `models/plants/` file
    becomes the plan's thing to find, adapted from a filed plant of
    the same food kind, else the first plant there is. A home
    naming nothing unfiled needs nothing.
    """
    have = sorted(p.stem for p in (world / "models" / "plants").glob("*.json"))
    for entry in home.get("food", []) or []:
        for name in entry.get("names", []) or []:
            slug = plant_slug(name)
            if slug and slug not in have:
                if not have:
                    raise ValueError("the world has no plants to adapt from")
                return slug, _plant_ref(home, entry.get("kind"), have)
    return None


def _plant_ref(home: dict[str, Any], kind: str | None, have: list[str]) -> str:
    """A filed plant named under the same food kind, else the first filed."""
    filed = set(have)
    for entry in home.get("food", []) or []:
        if entry.get("kind") != kind:
            continue
        for name in entry.get("names", []) or []:
            if plant_slug(name) in filed:
                return plant_slug(name)
    return have[0]


def plan_tile(
    tile: str,
    request: dict[str, Any] | None,
    grids: dict[GridName, HabitatGrid],
    world: Path,
    world_commit: str,
    presence: dict[str, float] | None = None,
    unfiled_presence: dict[str, float] | None = None,
    builder_root: Path | None = None,
) -> dict[str, Any]:
    """The border plan for one tile, valid against the plan contract.

    `presence` and `unfiled_presence` override the live GBIF fetch
    (tests and replays). The plan is made again when the world
    commit moves; a new home can turn an AI plan into `code_only`.
    An excluded cell gets no items: it is never built.
    """
    grid, col, row = parse_tile(tile)
    habitat_grid = grids[grid]
    eco1 = habitat_grid.cell_data(col, row)[0]
    habitat = habitat_grid.habitat(col, row)
    lat, lon = center(tile)
    if tile in excluded_tiles(builder_root):
        return _finish(
            tile, False, [], [], {}, request, habitat, lat, lon, world_commit
        )
    homes = available_homes(world)
    home = home_for_cell(eco1, habitat, homes)
    entries = library_animals(world)
    unfiled = _unfiled(world, habitat, entries)
    if unfiled_presence is None:
        fetched = fetch_smoothed(tile, unfiled) if unfiled else {}
        unfiled_counts = {s: n for s, n in fetched.items() if n > 0}
    else:
        unfiled_counts = {s: n for s, n in unfiled_presence.items() if n > 0}

    items: list[dict[str, Any]] = []
    if home is None and eco1 is None:
        # Open water has no ecoregion and no home to plan: the plan
        # carries only its name and improve species, if any.
        return _finish(
            tile,
            False,
            [],
            unfiled,
            unfiled_counts,
            request,
            habitat,
            lat,
            lon,
            world_commit,
        )
    if home is None:
        assert eco1 is not None  # narrowed above; mypy can't see it
        home_id = nearest_home(tile, grids, homes, habitat)
        items.append(
            {
                "id": f"home_{eco1}",
                "kind": "home",
                "what": str(eco1),
                "from": home_id,
                "estimate_usd": ITEM_ESTIMATES["home"],
            }
        )
    else:
        home_id = home
        made, _blocked = make_tile(
            tile, grids, world, presence=presence, builder_root=builder_root
        )
        if made is not None:
            return _finish(
                tile,
                True,
                [],
                unfiled,
                unfiled_counts,
                request,
                habitat,
                lat,
                lon,
                world_commit,
            )
    # Expanding: each missing thing on its own (spec 04, minimum).
    # The home below is real, or the one the new home adapts from.
    house = homes[home_id]
    if presence is not None:
        present = {s: n for s, n in presence.items() if n > 0}
    else:
        candidates = animals_for_habitat(entries, habitat)
        if habitat in ("sea", "open_ocean"):
            other = "open_ocean" if habitat == "sea" else "sea"
            seen = {a["id"] for a in candidates}
            candidates += [
                a for a in animals_for_habitat(entries, other) if a["id"] not in seen
            ]
        fetched = fetch_smoothed(tile, candidates)
        present = {s: n for s, n in fetched.items() if n > 0}
    kind = _missing_food(present, world, house)
    if kind is not None:
        items.append(
            {
                "id": f"food_{kind}",
                "kind": "food",
                "what": kind,
                "from": home_id,
                "estimate_usd": ITEM_ESTIMATES["food"],
            }
        )
    if not pick_species(present, world, house)[0]:
        picked = _pick_animal(unfiled, unfiled_counts, request)
        if picked is not None:
            items.append(
                {
                    "id": f"animal_{picked['id']}",
                    "kind": "animal",
                    "what": picked["id"],
                    "from": picked["start_from"],
                    "estimate_usd": ITEM_ESTIMATES["animal"],
                }
            )
    find = _missing_find(world, house)
    if find is not None:
        slug, ref = find
        items.append(
            {
                "id": f"plant_{slug}",
                "kind": "plant",
                "what": slug,
                "from": ref,
                "estimate_usd": ITEM_ESTIMATES["plant"],
            }
        )
    return _finish(
        tile,
        False,
        items,
        unfiled,
        unfiled_counts,
        request,
        habitat,
        lat,
        lon,
        world_commit,
    )


def _finish(
    tile: str,
    code_only: bool,
    items: list[dict[str, Any]],
    unfiled: list[dict[str, Any]],
    unfiled_counts: dict[str, float],
    request: dict[str, Any] | None,
    habitat: str,
    lat: float,
    lon: float,
    world_commit: str,
) -> dict[str, Any]:
    """Improve species, names, estimates: the plan around the items."""
    picked_ids = {i["what"] for i in items if i["kind"] == "animal"}
    by_id = {e["id"]: e for e in unfiled}
    improve: list[dict[str, Any]] = []
    for species in sorted(unfiled_counts, key=lambda s: (-unfiled_counts[s], s)):
        if species in picked_ids or species not in by_id:
            continue
        entry = by_id[species]
        if not entry.get("start_from"):
            continue
        improve.append(
            {
                "id": f"improve_{species}",
                "kind": "animal",
                "what": species,
                "from": entry["start_from"],
                "estimate_usd": ITEM_ESTIMATES["animal"],
            }
        )
        if len(improve) >= 2:
            break
    coming = [item["what"] for item in [*items, *improve] if item["kind"] == "animal"]
    plan = {
        "format": 1,
        "tile": tile,
        "code_only": code_only,
        "items": items,
        "improve": improve,
        "estimate_usd": round(sum(i["estimate_usd"] for i in [*items, *improve]), 2),
        "name": plan_name(habitat, lat, lon),
        "coming": coming,
        "world": world_commit,
        "planned_at": datetime.now(UTC).isoformat(),
    }
    validate("plan", plan)
    return plan
