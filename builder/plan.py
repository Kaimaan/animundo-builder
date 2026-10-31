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

from .grid import GridName, center, parse_tile
from .habitats import HabitatGrid
from .presence import animals_for_habitat, fetch_smoothed, library_animals
from .prices import ITEM_ESTIMATES
from .schemas import ROOT as BUILDER_ROOT
from .schemas import validate
from .species import (
    Source as SpeciesSource,
)
from .species import (
    SpeciesPlaceError,
    fetch_species,
    library_fingerprint,
    list_animals,
    list_fingerprint,
    list_library,
)
from .tiles import (
    available_biomes,
    available_homes,
    default_bundle,
    excluded_tiles,
    home_for_cell,
    land_style_for,
    make_tile,
    pick_species,
    place_cells,
    ring_cells,
    tile_facts,
)

#: Hand-made places the plan name points from: id, English and
#: genitive Finnish names, and the region centre.
PLACES: tuple[tuple[str, str, str, float, float], ...] = (
    ("nuuksio", "Nuuksio", "Nuuksion", 60.3007, 24.5263),
    ("saimaa", "Saimaa", "Saimaan", 62.115, 28.508),
)

#: Finnish vowels for the genitive rule: a base ending in one
#: takes `-n`, in `-e` takes `-en`, else `-in` (#169.2).
_FI_VOWELS = frozenset("aeiouyäöåAEIOUYÄÖÅ")


def _fi_genitive(base: str) -> str:
    """The Finnish genitive of a place name, by rule (#169.2).

    The world's region files carry no genitive field yet, so a
    compass fallback derives it: `Nuuksio` → `Nuuksion`,
    `Green Island` → `Green Islandin`. A surveyed name always
    wins over this guess.
    """
    word = base.strip()
    if not word:
        return word
    if word[-1] in _FI_VOWELS:
        return word + ("en" if word[-1] in ("e", "E") else "n")
    return word + "in"


def region_places(world: Path) -> list[tuple[str, str, str, float, float]]:
    """The world's hand-made regions as plan-name places (#169.2).

    Id, English and genitive Finnish names, and the centre, as
    `PLACES`. A region without a centre or a name is skipped;
    a name dict takes its `en`/`fi`, else the name is both.
    """
    out: list[tuple[str, str, str, float, float]] = []
    folder = world / "regions"
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("*.json")):
        try:
            region = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(region, dict):
            continue
        center = region.get("center")
        lat = center.get("lat") if isinstance(center, dict) else None
        lon = center.get("lon") if isinstance(center, dict) else None
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        name = region.get("name")
        if isinstance(name, dict):
            en = name.get("en")
            fi = name.get("fi") or en
        else:
            en = fi = name
        if not isinstance(en, str) or not en.strip():
            continue
        if not isinstance(fi, str) or not fi.strip():
            fi = en
        rid = region.get("id")
        out.append(
            (
                str(rid) if isinstance(rid, str) and rid else path.stem,
                en.strip(),
                _fi_genitive(fi),
                float(lat),
                float(lon),
            )
        )
    return out


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


def plan_name(
    habitat: str,
    lat: float,
    lon: float,
    species: dict[str, Any] | None = None,
    world: Path | None = None,
) -> dict[str, str]:
    """The area name: the species call's own, else the compass (#135.5).

    "Helsinki shore" / "Helsingin ranta" when the answer names the
    square; without names, "Forest north of Nuuksio" / "Metsä
    Nuuksion pohjoispuolella" by code as before. With a world,
    the hand-made regions join the places and the nearest wins;
    a tie keeps the curated name (#169.2).
    """
    if species is not None:
        en, fi = species.get("name_en"), species.get("name_fi")
        if isinstance(en, str) and en.strip() and isinstance(fi, str) and fi.strip():
            return {"en": en.strip(), "fi": fi.strip()}
    words = HABITATS.get(habitat, (habitat.replace("_", " ").title(), habitat))
    scored = []
    candidates: list[tuple[tuple[str, str, str, float, float], int]] = [
        (place, 0) for place in PLACES
    ]
    if world is not None:
        candidates += [(place, 1) for place in region_places(world)]
    for (_pid, en, gen, plat, plon), order in candidates:
        east = (lon - plon) * math.cos(math.radians(lat))
        north = lat - plat
        dist = math.hypot(east, north)
        angle = (math.degrees(math.atan2(east, north)) + 360) % 360
        wind = WINDS[int((angle + 22.5) // 45) % 8]
        scored.append((dist, order, en, gen, wind))
    _dist, _order, en, gen, wind = sorted(scored)[0]
    return {
        "en": f"{words[0]} {wind[0]} of {en}",
        "fi": f"{words[1]} {gen} {wind[1]}puolella",
    }


#: Habitats that are water: a new water home adapts from water.
WATER_HABITATS = frozenset({"sea", "open_ocean"})


def nearest_home(
    tile: str,
    grids: dict[GridName, HabitatGrid],
    homes: dict[str, dict[str, Any]],
    habitat: str,
) -> str:
    """The nearest existing home to adapt a new home from.

    The same habitat first: the nearest around the tile in
    expanding rings, else the first anywhere. Then the nearest
    of the same kind (land or water) the same way: a forest
    border adapts from the forest behind it, not the sea
    beside it (#141.4). Only with no home of the kind at all,
    the nearest of any habitat. Nearness never beats the
    habitat (#139.2), then never the kind. A world with no
    homes at all is broken, said loudly.
    """
    if not homes:
        raise ValueError("the world has no homes to adapt from")
    grid, col, row = parse_tile(tile)
    habitat_grid = grids[grid]
    rings = list(ring_cells(habitat_grid, col, row))
    for eco, neighbour in rings:
        found = home_for_cell(eco, neighbour, homes)
        if found is not None and homes[found].get("habitat") == habitat:
            return found
    same = sorted(h for h, d in homes.items() if d.get("habitat") == habitat)
    if same:
        return same[0]
    want_water = habitat in WATER_HABITATS

    def kind_matches(home_id: str) -> bool:
        return (homes[home_id].get("habitat") in WATER_HABITATS) == want_water

    for eco, neighbour in rings:
        found = home_for_cell(eco, neighbour, homes)
        if found is not None and kind_matches(found):
            return found
    kind = sorted(h for h in homes if kind_matches(h))
    if kind:
        return kind[0]
    for eco, neighbour in rings:
        found = home_for_cell(eco, neighbour, homes)
        if found is not None:
            return found
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
    from .tiles import tile_food_kinds

    kinds = tile_food_kinds(world, home)
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


def _missing_find(
    world: Path, home: dict[str, Any], picks: list[dict[str, Any]] | None = None
) -> tuple[str, str] | None:
    """The plan's thing to find, and the plant it adapts from.

    The survey's first unfiled pick goes first (#147.3: the
    plant that really grows there); then the home's first named
    plant without a plant file (`food[].names`, say "arctic
    willow"). The pick adapts from the first plant there is. A
    pick that is already filed, and a home naming nothing
    unfiled, need nothing.
    """
    have = sorted(p.stem for p in (world / "models" / "plants").glob("*.json"))
    for pick in picks or []:
        slug = pick.get("id", "")
        if slug and slug not in have:
            if not have:
                raise ValueError("the world has no plants to adapt from")
            return slug, have[0]
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


def home_plant_entries(home: dict[str, Any]) -> list[dict[str, Any]]:
    """The home's named plants in either shape: an example's
    `plants` or a filed home's `ground_cover.plants`."""
    if isinstance(home.get("plants"), list):
        return [p for p in home["plants"] if isinstance(p, dict)]
    return [
        p for p in home.get("ground_cover", {}).get("plants", []) if isinstance(p, dict)
    ]


def closest_plant(
    slug: str, grounds: list[str], home: dict[str, Any], have: list[str]
) -> str:
    """A filed plant of the same kind to start from (#144.2).

    The name's words first (a coral from a coral); then a filed
    plant off the same grounds (seabed grows corals); then the
    first file, when nothing says what it is like.
    """
    words = set(slug.split("_"))
    shared = [
        (len(words & set(filed.split("_"))), filed)
        for filed in have
        if words & set(filed.split("_"))
    ]
    if shared:
        best = max(count for count, _ in shared)
        return min(filed for count, filed in shared if count == best)
    for entry in home_plant_entries(home):
        model = entry.get("model") or ""
        if model not in have:
            continue
        on = entry.get("on", [])
        grounds_here = [on] if isinstance(on, str) else [g for g in on if g]
        if grounds and set(grounds) & set(grounds_here):
            return model
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
    species: dict[str, Any] | None = None,
    species_source: Any | None = None,
) -> dict[str, Any]:
    """The border plan for one tile, valid against the plan contract.

    `presence` and `unfiled_presence` override the live GBIF fetch
    (tests and replays). The plan is made again when the world
    commit moves; a new home can turn an AI plan into `code_only`.
    An excluded cell gets no items: it is never built. The
    code-only probe makes the tile the worker would make: a recorded
    `species` answer, else the cache or one model call.
    """
    grid, col, row = parse_tile(tile)
    habitat_grid = grids[grid]
    eco1 = habitat_grid.cell_data(col, row)[0]
    biome_number = habitat_grid.cell_data(col, row)[4]
    habitat = habitat_grid.habitat(col, row)
    lat, lon = center(tile)
    if tile in excluded_tiles(builder_root):
        return _finish(
            tile,
            False,
            [],
            [],
            {},
            request,
            habitat,
            lat,
            lon,
            world_commit,
            world=world,
        )
    placed = place_cells(world).get(tile)
    if placed is not None:
        # A place cell is the region's, never a code tile's (#147.1).
        return _finish(
            tile,
            False,
            [],
            [],
            {},
            request,
            habitat,
            lat,
            lon,
            world_commit,
            None,
            f"place:{placed[0]}",
            world=world,
        )
    homes = available_homes(world)
    biomes = available_biomes(world)
    home = home_for_cell(eco1, habitat, biomes, biome_number)
    entries = library_animals(world)
    unfiled = _unfiled(world, habitat, entries)
    if unfiled_presence is None:
        fetched = fetch_smoothed(tile, unfiled) if unfiled else {}
        unfiled_counts = {s: n for s, n in fetched.items() if n > 0}
    else:
        unfiled_counts = {s: n for s, n in unfiled_presence.items() if n > 0}
    items: list[dict[str, Any]] = []
    if home is None and eco1 is None:
        # Open water has no ecoregion and no home to plan: nobody
        # can go there yet, said plainly, not an empty AI plan. No
        # species call either: the compass name is enough where
        # nobody will open (#141.3).
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
            None,
            "open_water",
            world=world,
        )
    bundle: str | None = None
    if home is None:
        assert eco1 is not None  # narrowed above; mypy can't see it
        if not (world / "lands" / "index.json").is_file():
            home_id = nearest_home(tile, grids, biomes, habitat)
            adapt = True
        else:
            style = land_style_for(world, biome_number)
            if style is None:
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
                    None,
                    "no_land",
                    world=world,
                )
            bundle = default_bundle(tile, grids, biomes, habitat, style)
            if bundle is None:
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
                    None,
                    "no_home",
                    world=world,
                )
            home_id = bundle
            adapt = False
    else:
        home_id = home
        adapt = False
    probe_species, answer, place_blocked = _resolve_species(
        tile,
        world,
        habitat,
        lat,
        lon,
        builder_root,
        species,
        species_source,
        tile_facts(tile, grids, world, home_id, adapt),
    )
    if home is None and bundle is None:
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
        if place_blocked:
            made = None
        else:
            made, _blocked = make_tile(
                tile,
                grids,
                world,
                presence=presence,
                builder_root=builder_root,
                species=probe_species,
                species_source=species_source,
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
                answer,
                world=world,
            )
    # Expanding: each missing thing on its own (spec 04, minimum).
    # The home below is real, or the one the new home adapts from.
    house = homes.get(home_id, biomes[home_id])
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
    picks = answer.get("plants") if answer else None
    find = _missing_find(world, house, picks if isinstance(picks, list) else None)
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
        answer,
        world=world,
    )


def _resolve_species(
    tile: str,
    world: Path,
    habitat: str,
    lat: float,
    lon: float,
    builder_root: Path | None,
    species: dict[str, Any] | None,
    species_source: Any | None,
    facts: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, bool]:
    """The survey for the plan's name and probe (#141.1).

    A recorded answer passes through untouched; else the cache,
    else one call, like the tile maker. Anything failing leaves
    the name to the compass: a name never stops a plan. Only a
    twice-misread cell blocks the probe, as the maker would.
    Returns (probe_species, answer, place_blocked).
    """
    if species is not None:
        return species, species, False
    animals = list_animals(world)
    greens = list_library(world, "plants")
    trees = list_library(world, "trees")
    source = species_source or SpeciesSource(
        client=None,
        cache_dir=(builder_root or BUILDER_ROOT) / "data" / "species",
    )
    try:
        fetched = fetch_species(
            tile,
            animals,
            greens,
            trees,
            habitat,
            lat,
            lon,
            source,
            list_fingerprint(animals),
            library_fingerprint(greens, trees),
            facts,
        )
    except SpeciesPlaceError:
        return None, None, True
    except Exception:  # noqa: BLE001 - the compass names it instead
        return None, None, False
    if fetched.get("name_en") and fetched.get("name_fi"):
        probe = {
            key: fetched[key]
            for key in ("place", "parts", "plants", "trees", "name_en", "name_fi")
        }
    else:
        probe = None
    return probe, fetched, False


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
    answer: dict[str, Any] | None = None,
    blocked: str | None = None,
    world: Path | None = None,
) -> dict[str, Any]:
    """Improve species, names, estimates: the plan around the items.

    A blocked plan carries nothing to build and nothing coming,
    only the reason for the game to show (#141.2).
    """
    picked_ids = {i["what"] for i in items if i["kind"] == "animal"}
    by_id = {e["id"]: e for e in unfiled}
    improve: list[dict[str, Any]] = []
    if blocked is None:
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
        "name": plan_name(habitat, lat, lon, answer, world),
        "coming": coming,
        "world": world_commit,
        "planned_at": datetime.now(UTC).isoformat(),
    }
    if blocked is not None:
        plan["blocked"] = blocked
    validate("plan", plan)
    return plan
