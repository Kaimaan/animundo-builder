"""Place requests as files (D20, #137): survey a place, plan it, build it.

`builder place survey "<place>"` researches one place with a single
Muse Spark call and writes `survey.json`; `builder place plan`
turns the survey into a §7 `plan.json`. No Firestore, no admin
console: agents run these rarely from the command line.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from pathlib import Path
from typing import Any

from .grid import N, bounds, parse_tile, tile_at, tile_id
from .habitats import HabitatGrid
from .plan import closest_plant, home_plant_entries, plant_slug
from .presence import fetch_raw, match_kingdom
from .prompts import load_prompt

#: Headroom past the round, as research: the model doesn't keep
#: to the count, so the API caps the runaway (#51.2).
SEARCH_SLACK = 5
SEARCHES = 5

#: How far around the surveyed tile an animal must be GBIF-recorded.
GBIF_RADIUS = 2


def list_homes(world: Path) -> list[str]:
    """Every `home_from` the survey may name: homes, then examples."""
    names = sorted(p.stem for p in (world / "homes").glob("*.json"))
    examples = sorted((world / "homes" / "examples").glob("*.json"))
    names.extend(f"examples/{p.stem}" for p in examples)
    return names


def survey_schema(ids: list[str]) -> dict[str, Any]:
    """The strict survey shape: tile, home, names, animals, plants, food."""
    return {
        "type": "object",
        "properties": {
            "tile": {
                "type": "string",
                "description": "The start's tile id, like z10_0927_0560.",
            },
            "habitat": {
                "type": "string",
                "description": "The habitat there, in a few words.",
            },
            "home_from": {
                "type": "string",
                "description": "The home file to adapt from.",
            },
            "name": {
                "type": "object",
                "description": (
                    "The short child-friendly name in each language, "
                    "written as in that language."
                ),
                "properties": {
                    "en": {"type": "string"},
                    "fi": {"type": "string"},
                },
                "required": ["en", "fi"],
                "additionalProperties": False,
            },
            "country": {
                "type": "string",
                "description": "The country it is in, in English.",
            },
            "region_id": {
                "type": "string",
                "description": "The place file's id, like au_great_barrier_reef.",
            },
            "animals": {
                "type": "array",
                "description": "The animals living there, best known first.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "enum": ids},
                        "source": {"type": "string"},
                    },
                    "required": ["id", "source"],
                    "additionalProperties": False,
                },
            },
            "plants": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "source": {"type": "string"},
                    },
                    "required": ["name", "source"],
                    "additionalProperties": False,
                },
            },
            "food": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "source": {"type": "string"},
                    },
                    "required": ["name", "source"],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "tile",
            "habitat",
            "home_from",
            "name",
            "country",
            "region_id",
            "animals",
            "plants",
            "food",
        ],
        "additionalProperties": False,
    }


def survey_format(ids: list[str]) -> dict[str, Any]:
    """The survey answer as a Responses strict format."""
    return {
        "type": "json_schema",
        "name": "PlaceSurvey",
        "schema": survey_schema(ids),
        "strict": True,
    }


def survey_prompt(
    place: str,
    animals: list[dict[str, Any]],
    homes: list[str],
    want: list[str],
    improve: str | None,
) -> str:
    """The survey prompt: the place, the homes, the wanted, the list."""
    listing = "\n".join(f"- {a['id']}: {a['name']} ({a['latin']})" for a in animals)
    if want:
        want_line = f"Keep {', '.join(want)} first."
    else:
        want_line = "No animal is wanted in particular."
    if improve is None:
        improve_line = "Pick the spot the place starts from."
        tile_line = "It must be a real cell of the grid."
    else:
        improve_line = f"Survey the existing place {improve}, not a new spot."
        tile_line = f"It must be {improve}."
    return load_prompt(
        "place_survey",
        {
            "PLACE": place,
            "IMPROVE_LINE": improve_line,
            "TILE_LINE": tile_line,
            "WANT_LINE": want_line,
            "HOMES": "\n".join(f"- {h}" for h in homes),
            "ANIMALS": listing,
        },
    )


def ask_survey(
    ctx: Any,
    place: str,
    animals: list[dict[str, Any]],
    homes: list[str],
    want: list[str],
    improve: str | None,
) -> tuple[dict[str, Any], int]:
    """One surveyed place: the call, the parsed answer, its searches."""
    from .meta import count_search_calls, is_cut_off
    from .stages.survey import SEARCH_TOOL, final_text

    ids = [a["id"] for a in animals]
    prompt = survey_prompt(place, animals, homes, want, improve)
    response = ctx.respond(
        prompt,
        tools=SEARCH_TOOL,
        timeout=180,
        max_tool_calls=SEARCHES + SEARCH_SLACK,
        text_format=survey_format(ids),
    )
    searches = count_search_calls(response)
    text = final_text(response)
    if is_cut_off(response, 0) and not text.strip():
        raise SurveyError("the survey came back empty")
    try:
        data = json.loads(text)
    except ValueError as error:
        raise SurveyError(f"the survey came back unparsable: {error}") from error
    return data, searches


class SurveyError(Exception):
    """The survey cannot be used as it came back."""


def cells_within(tile: str, radius: int = GBIF_RADIUS) -> list[str]:
    """The tile and its cells out to `radius`, wrapping the date line."""
    from .grid import tile_id

    col, row = parse_tile(tile)
    out = []
    for dc in range(-radius, radius + 1):
        for dr in range(-radius, radius + 1):
            c, r = (col + dc) % N, row + dr
            if 0 <= r < N:
                out.append(tile_id(c, r))
    return out


#: A place tile is wild below 2 % built-up and below 25 %
#: built-up plus cropland (#238).
WILD_BUILT_MAX = 0.02
WILD_CROP_SUM_MAX = 0.25

#: How far a picked tile moves to find wild land: 3 tiles (#238).
WILD_MOVE_RANGE = 3


class NoWildTile(Exception):
    """No wild tile near the surveyed one: the job fails naming it."""


def tile_wild(tile: str, grid: HabitatGrid) -> bool | None:
    """Whether the tile is wild land, None without land-cover data (#238).

    Sea cells have no data and pass through unchecked: nothing
    is built on water.
    """
    col, row = parse_tile(tile)
    shares = grid.landcover_share(col, row)
    if shares is None:
        return None
    built, crop = shares
    return built < WILD_BUILT_MAX and built + crop < WILD_CROP_SUM_MAX


def ensure_wild_tiles(
    tiles: list[str], grid: HabitatGrid
) -> tuple[list[str], list[dict[str, str]]]:
    """Wild tiles for the surveyed ones, with the moves made (#238).

    Each tile that is not wild moves to the nearest tile within
    3 that is wild and keeps the habitat (the same habitat and
    biome number); each move is recorded as {from, to, why}. A
    tile with nothing wild nearby raises NoWildTile. Tiles
    without land-cover data stay as surveyed. No two tiles land
    on the same target, and none lands on a surveyed tile:
    repeats collapse and moves skip them (#246).
    """
    out: list[str] = []
    moved: list[dict[str, str]] = []
    used = set(tiles)
    for tile in tiles:
        if tile_wild(tile, grid) is not False:
            if tile not in out:
                out.append(tile)
            continue
        target = _nearest_wild(tile, grid, skip=used)
        if target is None:
            raise NoWildTile(tile)
        used.add(target)
        out.append(target)
        moved.append({"from": tile, "to": target, "why": _wild_why(tile, grid)})
    return out, moved


def _nearest_wild(
    tile: str, grid: HabitatGrid, skip: Collection[str] = ()
) -> str | None:
    """The nearest wild tile within range keeping the habitat."""
    col, row = parse_tile(tile)
    habitat = grid.habitat(col, row)
    biome = grid.cell_data(col, row)[4]
    for ring in range(1, WILD_MOVE_RANGE + 1):
        for dr in range(-ring, ring + 1):
            for dc in range(-ring, ring + 1):
                if max(abs(dc), abs(dr)) != ring:
                    continue
                c, r = (col + dc) % N, row + dr
                if not (0 <= r < N):
                    continue
                candidate = tile_id(c, r)
                if candidate in skip:
                    continue
                if tile_wild(candidate, grid) is not True:
                    continue
                if grid.habitat(c, r) != habitat:
                    continue
                if grid.cell_data(c, r)[4] != biome:
                    continue
                return candidate
    return None


def _wild_why(tile: str, grid: HabitatGrid) -> str:
    """Why the tile moved: its built-up and cropland shares."""
    col, row = parse_tile(tile)
    built, crop = grid.landcover_share(col, row) or (0.0, 0.0)
    text = f"{tile} is {_pct(built)} % built-up"
    if crop >= 0.01:
        text += f" and {_pct(crop)} % cropland"
    return text


def _pct(share: float) -> str:
    """The share as a short percent: `2`, `12.5`."""
    rounded = round(share * 100, 1)
    return str(int(rounded)) if rounded == int(rounded) else str(rounded)


def wild_spot(
    tile: str, lat: float, lon: float, grid: HabitatGrid
) -> tuple[float, float]:
    """The start spot on a wild part of the tile (#238).

    The nearest wild-land mask cell's centre; the point as
    given when the tile has no mask or no wild land in it.
    """
    col, row = parse_tile(tile)
    mask = grid.wild_mask(col, row)
    if mask is None or "l" not in mask:
        return lat, lon
    mnla, mxla, mnlo, mxlo = bounds(tile)
    best: tuple[float, float, float] | None = None
    for r in range(5):
        for s in range(5):
            if mask[r * 5 + s] != "l":
                continue
            cla = mxla - (r + 0.5) / 5 * (mxla - mnla)
            clo = mnlo + (s + 0.5) / 5 * (mxlo - mnlo)
            dist = (cla - lat) ** 2 + (clo - lon) ** 2
            if best is None or dist < best[0]:
                best = (dist, cla, clo)
    if best is None:
        return lat, lon
    return best[1], best[2]


def gbif_near(tile: str, animals: list[dict[str, Any]]) -> dict[str, int]:
    """GBIF records per animal id within 2 tiles of the surveyed tile."""
    totals: dict[str, int] = {}
    for cell in cells_within(tile):
        for animal_id, count in fetch_raw(cell, animals).items():
            totals[animal_id] = totals.get(animal_id, 0) + count
    return totals


def _latin(name: str) -> str | None:
    """The Latin name in trailing parentheses, if the survey gave one."""
    match = re.search(r"\(([^()]*)\)\s*$", name)
    return match.group(1).strip() or None if match else None


def check_survey(
    data: Any,
    place: str,
    habitat_grid: HabitatGrid,
    world: Path,
    animals: list[dict[str, Any]],
    want: list[str],
    budget_usd: float,
    improve: str | None,
    recorded: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A checked survey in §6a shape, or why not (#137).

    The tile must be a real cell (the improved place's own with
    `--improve`); `home_from` a home file; every animal on the
    list with a source and GBIF records within 2 tiles (the
    wanted stay first, records or not); plants and food named
    with sources. Each plant keeps its GBIF kingdom, matched from
    the Latin name (#146.2). The counts ride in the survey, so a
    `--replay` passes the prior survey's back and stays fast,
    offline and identical instead of calling GBIF live (#167.3).
    """
    if not isinstance(data, dict):
        raise SurveyError("the survey is not an object")
    for key in (
        "tile",
        "habitat",
        "home_from",
        "name",
        "country",
        "region_id",
        "animals",
        "plants",
        "food",
    ):
        if key not in data:
            raise SurveyError(f"the survey has no {key}")
    if not isinstance(data["country"], str) or not data["country"].strip():
        raise SurveyError("the survey names no country")
    if not isinstance(data["region_id"], str) or not re.fullmatch(
        r"[a-z]{2}_[a-z0-9_]+", data["region_id"]
    ):
        raise SurveyError(f"{data['region_id']} is no place id")
    tile = data["tile"]
    try:
        col, row = parse_tile(tile)
    except ValueError:
        raise SurveyError(f"{tile} is no tile id") from None
    try:
        habitat = habitat_grid.habitat(col, row)
    except KeyError:
        raise SurveyError(f"{tile} is no baked cell") from None
    if improve is not None and tile != improve:
        raise SurveyError(f"the survey picked {tile}, not {improve}")
    home_from = data["home_from"]
    home_path = world / "homes" / f"{home_from}.json"
    if not isinstance(home_from, str) or not home_path.is_file():
        raise SurveyError(f"{home_from} is no home file")
    name = data["name"]
    if not isinstance(name, dict) or not name.get("en") or not name.get("fi"):
        raise SurveyError("the survey names nothing in English and Finnish")
    if not isinstance(data["habitat"], str) or not data["habitat"].strip():
        raise SurveyError("the survey names no habitat")
    by_id = {a["id"]: a for a in animals}
    for entry in data["animals"]:
        if entry.get("id") not in by_id:
            raise SurveyError(f"{entry.get('id')} is not on the animal list")
        if not entry.get("source"):
            raise SurveyError(f"{entry['id']} has no source")
    for key in ("plants", "food"):
        for entry in data[key]:
            if not entry.get("name") or not entry.get("source"):
                raise SurveyError(f"a {key} entry has no name or source")
    recorded_counts = (recorded or {}).get("counts")
    if recorded_counts is not None:
        counts = recorded_counts
    else:
        counts = gbif_near(tile, animals)
    recorded_kingdoms = (recorded or {}).get("kingdoms") or {}
    kept: list[dict[str, str]] = []
    for entry in data["animals"]:
        if entry["id"] in want or counts.get(entry["id"], 0) > 0:
            kept.append({"id": entry["id"], "source": entry["source"]})
    kept.sort(key=lambda e: 0 if e["id"] in want else 1)
    plants: list[dict[str, Any]] = []
    for entry in data["plants"]:
        latin = _latin(entry["name"])
        if entry["name"] in recorded_kingdoms:
            kingdom = recorded_kingdoms[entry["name"]]
        else:
            kingdom = match_kingdom(latin) if latin else None
        plants.append(
            {
                "name": entry["name"],
                "source": entry["source"],
                "kingdom": kingdom,
            }
        )
    return {
        "place": place,
        "tile": tile,
        "habitat": data["habitat"],
        "grid_habitat": habitat,
        "home_from": home_from,
        "name": {"en": name["en"], "fi": name["fi"]},
        "country": data["country"].strip(),
        "region_id": data["region_id"],
        "animals": kept,
        "plants": plants,
        "food": [{"name": e["name"], "source": e["source"]} for e in data["food"]],
        "wanted": [w for w in want if w in by_id],
        "missing": _survey_missing(kept, plants, world, habitat),
        "surveyed_at": (recorded or {}).get("surveyed_at") or _today(),
        "budget_usd": budget_usd,
        "gbif_counts": counts,
    }


def _today() -> str:
    """Today's UTC date: a survey's `surveyed_at` (#170.2)."""
    from datetime import UTC, datetime

    return datetime.now(UTC).date().isoformat()


def _survey_missing(
    kept: list[dict[str, str]],
    plants: list[dict[str, Any]],
    world: Path,
    part: str,
) -> list[dict[str, str]]:
    """What the survey found that the world doesn't have yet (#170.1).

    Kept animals with no animal file, then survey plants with no
    model, in the tile `wanted` shape, so a place and a tile
    agree. The survey keeps it; the plan doesn't make it; filed
    at ship time, enrich reads it later.
    """
    from .tiles import WANTED_LIMIT

    drops: list[dict[str, str]] = []
    animals_dir = world / "animals"
    for entry in kept:
        sid = entry["id"]
        if (animals_dir / f"{sid}.json").is_file():
            continue
        drops.append({"kind": "animal", "what": sid, "part": part})
    have = sorted(p.stem for p in (world / "models" / "plants").glob("*.json"))
    slugs, _blocked = _missing_plants(
        {},
        {"plants": plants},
        have,
        latin_have=_plant_latins(world),
    )
    for slug, _grounds in slugs:
        drops.append({"kind": "plant", "what": slug, "part": part})
    return drops[:WANTED_LIMIT]


def missing_species(
    animals: list[dict[str, Any]],
    plants: list[dict[str, Any]],
    world: Path,
    biome: str,
    swaps: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """What the research found that the world lacks right now (#239.2).

    The re-price runs this against the current world: species
    filed since the survey cost nothing to build. `swaps` maps
    a researched plant slug to the species to build instead
    (a human correction, kept on the job's research, #264.3):
    the swapped name runs the same filed checks as a surveyed
    one, so an already-filed correction costs nothing.
    """
    kept = [
        {"id": entry["id"], "source": entry.get("source", "")}
        for entry in animals
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    ]
    greens = []
    for entry in plants:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            continue
        name = entry["name"]
        if swaps and plant_slug(name) in swaps:
            name = swaps[plant_slug(name)]
        greens.append({"name": name, "source": entry.get("source", "")})
    return _survey_missing(kept, greens, world, biome)


def write_survey(out_dir: Path, survey: dict[str, Any]) -> Path:
    """`survey.json` in §6a shape for `place plan` and the job."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "survey.json"
    path.write_text(json.dumps(survey, indent=2) + "\n", encoding="utf-8")
    return path


def job_survey_format() -> dict[str, Any]:
    """A place job's survey answer as a Responses strict format (#222.2)."""
    entry = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "source": {"type": "string"}},
        "required": ["id", "source"],
        "additionalProperties": False,
    }
    named = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "source": {"type": "string"}},
        "required": ["name", "source"],
        "additionalProperties": False,
    }
    return {
        "type": "json_schema",
        "name": "PlaceJobSurvey",
        "schema": {
            "type": "object",
            "properties": {
                "centre": {
                    "type": "object",
                    "properties": {
                        "lat": {"type": "number"},
                        "lon": {"type": "number"},
                    },
                    "required": ["lat", "lon"],
                    "additionalProperties": False,
                },
                "tiles": {"type": "array", "items": {"type": "string"}},
                "tiles_why": {"type": "string"},
                "biome": {"type": "string"},
                "name": {
                    "type": "object",
                    "properties": {
                        "en": {"type": "string"},
                        "fi": {"type": "string"},
                        "fi_gen": {"type": "string"},
                    },
                    "required": ["en", "fi", "fi_gen"],
                    "additionalProperties": False,
                },
                "short": {
                    "type": "object",
                    "properties": {
                        "en": {"type": "string"},
                        "fi": {"type": "string"},
                        "fi_gen": {"type": "string"},
                    },
                    "required": ["en", "fi", "fi_gen"],
                    "additionalProperties": False,
                },
                "country": {"type": "string"},
                "wikidata": {"type": "string"},
                "animals": {"type": "array", "items": entry},
                "plants": {"type": "array", "items": named},
            },
            "required": [
                "centre",
                "tiles",
                "tiles_why",
                "biome",
                "name",
                "short",
                "country",
                "wikidata",
                "animals",
                "plants",
            ],
            "additionalProperties": False,
        },
        "strict": True,
    }


def job_survey_prompt(
    place: str, animals: list[dict[str, Any]], biomes: list[str], want: list[str]
) -> str:
    """The place-job survey prompt: the place, the wanted, the lists."""
    if want:
        want_line = f"Keep {', '.join(want)} first."
    else:
        want_line = "No animal is wanted in particular."
    listing = "\n".join(f"- {a['id']}: {a['name']} ({a['latin']})" for a in animals)
    return load_prompt(
        "place_job_survey",
        {
            "PLACE": place,
            "WANT_LINE": want_line,
            "BIOMES": "\n".join(f"- {b}" for b in biomes),
            "ANIMALS": listing,
        },
    )


def ask_job_survey(
    ctx: Any,
    place: str,
    animals: list[dict[str, Any]],
    biomes: list[str],
    want: list[str],
) -> tuple[dict[str, Any], int]:
    """One surveyed place job: the call, the parsed answer, its searches."""
    from .meta import count_search_calls, is_cut_off
    from .stages.survey import SEARCH_TOOL, final_text

    prompt = job_survey_prompt(place, animals, biomes, want)
    response = ctx.respond(
        prompt,
        tools=SEARCH_TOOL,
        timeout=180,
        max_tool_calls=SEARCHES + SEARCH_SLACK,
        text_format=job_survey_format(),
    )
    searches = count_search_calls(response)
    text = final_text(response)
    if is_cut_off(response, 0) and not text.strip():
        raise SurveyError("the survey came back empty")
    try:
        data = json.loads(text)
    except ValueError as error:
        raise SurveyError(f"the survey came back unparsable: {error}") from error
    return data, searches


def check_job_survey(
    data: Any,
    place: str,
    world: Path,
    animals: list[dict[str, Any]],
    want: list[str],
) -> dict[str, Any]:
    """A checked place-job survey, or why not (#222.2).

    The tiles form a covered rectangle of at most 2 by 2,
    one tile by default; more than one needs its reason in
    a sentence (#228.2). Every animal is on the list with a
    source; plants and names as answered. Whether the biome
    fits is the worker's call: a miss stops the job naming
    its land.
    """
    if not isinstance(data, dict):
        raise SurveyError("the survey is not an object")
    for key in ("centre", "tiles", "biome", "name", "wikidata", "animals", "plants"):
        if key not in data:
            raise SurveyError(f"the survey has no {key}")
    centre = data["centre"]
    if (
        not isinstance(centre, dict)
        or not isinstance(centre.get("lat"), (int, float))
        or not isinstance(centre.get("lon"), (int, float))
    ):
        raise SurveyError("the survey centres on nothing")
    tiles = _survey_rectangle(data["tiles"])
    why = data.get("tiles_why")
    if why is None:
        why = ""
    if not isinstance(why, str):
        raise SurveyError("the survey's tiles_why is no sentence")
    if len(tiles) > 1 and not why.strip():
        raise SurveyError(f"the survey covers {len(tiles)} tiles with no tiles_why")
    name = data["name"]
    if (
        not isinstance(name, dict)
        or not name.get("en")
        or not name.get("fi")
        or not name.get("fi_gen")
    ):
        raise SurveyError("the survey names nothing in English, Finnish and genitive")
    short = data.get("short")
    if short is not None and (
        not isinstance(short, dict)
        or not short.get("en")
        or not short.get("fi")
        or not short.get("fi_gen")
    ):
        raise SurveyError("the survey's short name wants en, fi and fi_gen")
    country = data.get("country")
    if country is not None and (
        not isinstance(country, str) or len(country.strip()) != 2
    ):
        raise SurveyError("the survey's country is no 2-letter code")
    if not isinstance(data["wikidata"], str) or not data["wikidata"].strip():
        raise SurveyError("the survey names no Wikidata id")
    by_id = {a["id"]: a for a in animals}
    kept: list[dict[str, str]] = []
    for entry in data["animals"]:
        if entry.get("id") not in by_id:
            raise SurveyError(f"{entry.get('id')} is not on the animal list")
        if not entry.get("source"):
            raise SurveyError(f"{entry['id']} has no source")
        kept.append({"id": entry["id"], "source": entry["source"]})
    kept.sort(key=lambda e: 0 if e["id"] in want else 1)
    plants: list[dict[str, str]] = []
    for entry in data["plants"]:
        if not entry.get("name") or not entry.get("source"):
            raise SurveyError("a plant entry has no name or source")
        plants.append({"name": entry["name"], "source": entry["source"]})
    survey: dict[str, Any] = {
        "place": place,
        "centre": {"lat": centre["lat"], "lon": centre["lon"]},
        "tiles": tiles,
        "tiles_why": why.strip() if len(tiles) > 1 else "",
        "biome": data["biome"],
        "name": {"en": name["en"], "fi": name["fi"], "fi_gen": name["fi_gen"]},
        "wikidata": data["wikidata"].strip(),
        "animals": kept,
        "plants": plants,
        "wanted": [w for w in want if w in by_id],
        "missing": _survey_missing(kept, plants, world, str(data["biome"])),
        "surveyed_at": _today(),
    }
    if short is not None:
        assert isinstance(short, dict)
        survey["short"] = {
            "en": short["en"],
            "fi": short["fi"],
            "fi_gen": short["fi_gen"],
        }
    if country is not None:
        assert isinstance(country, str)
        survey["country"] = country.strip().upper()
    return survey


def _survey_rectangle(tiles: Any) -> list[str]:
    """The tiles as a covered rectangle of at most 2 by 2, or why not."""
    if not isinstance(tiles, list) or not tiles:
        raise SurveyError("the survey covers no tiles")
    try:
        cells = [parse_tile(tile) for tile in tiles]
    except (ValueError, TypeError) as error:
        raise SurveyError(f"a surveyed tile is no tile id: {error}") from None
    cols = [c for c, _ in cells]
    rows = [r for _, r in cells]
    wide, high = max(cols) - min(cols) + 1, max(rows) - min(rows) + 1
    if wide > 2 or high > 2 or len(set(cells)) != wide * high:
        raise SurveyError("the surveyed tiles are no covered 2 by 2 rectangle")
    return sorted(set(tiles))


def biome_thinness(biome: dict[str, Any]) -> str | None:
    """None when the biome can build; else why it is thin (#239.1, #259.1).

    Plants count as ids, written-out `trees.mix` entries, or
    worked-out food (`small_life` lists, mix `food` fields): the
    várzea has no ids yet but builds fine on its mix and food.
    Only a biome with none of the three is thin.
    """
    plants = biome.get("plants") or {}
    ids = any(
        isinstance(entry, dict) and entry.get("id")
        for side in ("trees", "cover", "water")
        for entry in (plants.get(side) or [])
    )
    mix = [
        entry
        for entry in ((biome.get("trees") or {}).get("mix") or [])
        if isinstance(entry, dict)
    ]
    small = biome.get("small_life") or {}
    food = any(small.values()) if isinstance(small, dict) else False
    food = food or any(entry.get("food") for entry in mix)
    if not ids and not mix and not food:
        return "the biome has no plants, written-out trees or food"
    return None


def price_place_build(
    tiles: list[str],
    missing: list[dict[str, Any]],
    estimates: dict[str, tuple[float, str]],
    world: Path,
) -> dict[str, Any]:
    """The build's quote: tiles plus each species it builds (#239.2).

    Every number says where it came from: the cost log's rows
    or today's fixed hand numbers (#259.4). Missing animals
    with no `start_from` to adapt cost nothing: stage C leaves
    them on wanted instead of building them (#264.4).
    """
    from .animal_list import find_list, load_list
    from .prices import ITEM_ESTIMATES, PLACE_BUILD_USD_PER_TILE

    listed = {
        entry["id"]: entry
        for entry in load_list(find_list(world))["animals"]
        if isinstance(entry.get("id"), str)
    }
    tile_usd, tile_from = estimates.get("tile", (PLACE_BUILD_USD_PER_TILE, "fixed"))
    animal_usd, animal_from = estimates.get(
        "animal", (ITEM_ESTIMATES["animal"], "fixed")
    )
    plant_usd, plant_from = estimates.get("plant", (ITEM_ESTIMATES["plant"], "fixed"))
    animals = [
        {"id": entry["what"], "usd": animal_usd, "from": animal_from}
        for entry in missing
        if entry.get("kind") == "animal"
        and isinstance(entry.get("what"), str)
        and (listed.get(entry["what"]) or {}).get("start_from")
    ]
    plants = [
        {"id": entry["what"], "usd": plant_usd, "from": plant_from}
        for entry in missing
        if entry.get("kind") == "plant" and isinstance(entry.get("what"), str)
    ]
    total = round(
        len(tiles) * tile_usd + len(animals) * animal_usd + len(plants) * plant_usd, 2
    )
    return {
        "tiles": {
            "n": len(tiles),
            "usd": round(len(tiles) * tile_usd, 2),
            "from": tile_from,
        },
        "animals": animals,
        "plants": plants,
        "total": total,
    }


def plan_place(
    survey: dict[str, Any], world: Path, world_commit: str
) -> dict[str, Any]:
    """The §7 plan from a place survey (T98), not from the grid.

    A home item when `home_from` is an example (examples are not
    buildable) — never for an existing place, where `--improve`
    adds animals and plants only, no home and no terrain change
    (#167.2). Each unfiled survey animal with its food, the
    children's animals first; one findable plant per kind the
    home names. Items stop at the budget. An animal with no
    `start_from` and no `"shape": "new"` is `blocked` with
    `needs_reference`, never built; one the list can't make is
    `not_makeable`. Filed animals need nothing: with
    `--improve` the plan holds only what the place lacks.
    `looked_for` names the habitat-fitting library animals
    asked about but in neither the answer nor the place, so
    an enrich round finds something new without `--want`
    (#167.4).
    """
    from datetime import UTC, datetime

    from .animal_list import find_list, load_list
    from .prices import ITEM_ESTIMATES
    from .schemas import validate

    animals = load_list(find_list(world))["animals"]
    by_id = {a["id"]: a for a in animals}
    budget = float(survey["budget_usd"])
    items: list[dict[str, Any]] = []
    blocked: list[dict[str, str]] = []
    spent = 0.0

    def afford(estimate: float) -> bool:
        return spent + estimate <= budget

    home_from = survey["home_from"]
    existing = (world / "places" / f"{survey['region_id']}.json").is_file()
    if home_from.startswith("examples/") and not existing:
        stem = home_from.split("/")[-1]
        estimate = ITEM_ESTIMATES["home"]
        if afford(estimate):
            items.append(
                {
                    "id": f"home_{stem}",
                    "kind": "home",
                    "what": stem,
                    "from": home_from,
                    "estimate_usd": estimate,
                }
            )
            spent += estimate
    home = json.loads((world / "homes" / f"{home_from}.json").read_text())
    made: set[str] = set()
    for entry in survey["animals"]:
        animal_id = entry["id"]
        if (world / "animals" / f"{animal_id}.json").is_file():
            continue
        listed = by_id[animal_id]
        if not listed.get("can_make", True):
            blocked.append({"what": animal_id, "reason": "not_makeable"})
            continue
        if not listed.get("start_from") and listed.get("shape") != "new":
            blocked.append({"what": animal_id, "reason": "needs_reference"})
            continue
        estimate = ITEM_ESTIMATES["animal"]
        if not afford(estimate):
            continue
        items.append(
            {
                "id": f"animal_{animal_id}",
                "kind": "animal",
                "what": animal_id,
                "from": str(listed.get("start_from") or listed["id"]),
                "estimate_usd": estimate,
            }
        )
        made.add(animal_id)
        spent += estimate
    have = sorted(p.stem for p in (world / "models" / "plants").glob("*.json"))
    missing, not_plants = _missing_plants(
        home,
        survey,
        have,
        latin_have=_plant_latins(world),
    )
    blocked.extend(not_plants)
    for slug, grounds in missing:
        estimate = ITEM_ESTIMATES["plant"]
        if not afford(estimate):
            break
        items.append(
            {
                "id": f"plant_{slug}",
                "kind": "plant",
                "what": slug,
                "from": closest_plant(slug, grounds, home, have),
                "estimate_usd": estimate,
            }
        )
        spent += estimate
    unplanned = {b["what"] for b in blocked}
    coming = [
        e["id"]
        for e in survey["animals"]
        if e["id"] not in made
        and e["id"] not in unplanned
        and not (world / "animals" / f"{e['id']}.json").is_file()
    ]
    residents = place_residents(world, survey["region_id"])
    looked_for: list[str] = []
    if residents:
        habitats: set[str] = set()
        for resident in residents:
            habitats |= set(by_id.get(resident, {}).get("habitats") or [])
        kept = {e["id"] for e in survey["animals"]}
        for candidate in animals:
            if (
                set(candidate.get("habitats") or []) & habitats
                and candidate["id"] not in kept
                and candidate["id"] not in residents
                and candidate.get("can_make", True)
                and (candidate.get("start_from") or candidate.get("shape") == "new")
            ):
                looked_for.append(candidate["id"])
    plan = {
        "format": 1,
        "tile": survey["tile"],
        "code_only": not items,
        "items": items,
        "improve": [],
        "blocked": blocked,
        "survey": "survey.json",
        "estimate_usd": round(spent, 2),
        "name": survey["name"],
        "country": survey["country"],
        "region_id": survey["region_id"],
        "coming": coming,
        "looked_for": looked_for,
        "world": world_commit,
        "planned_at": datetime.now(UTC).isoformat(),
    }
    validate("plan", plan)
    return plan


#: Kingdoms a survey plant may be built from (#146.2).
PLANT_KINGDOMS = ("Plantae", "Fungi")


def _common_slug(name: str) -> str:
    """The slug without a trailing Latin parenthetical (#167.1)."""
    return plant_slug(re.sub(r"\s*\([^()]*\)\s*$", "", name))


def _plant_latins(world: Path) -> dict[str, str]:
    """Lower Latin name to plant file stem, for survey matching (#167.1)."""
    latins: dict[str, str] = {}
    for path in (world / "models" / "plants").glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        latin = data.get("latin")
        if isinstance(latin, str) and latin.strip():
            latins[latin.strip().lower()] = path.stem
    return latins


def _missing_plants(
    home: dict[str, Any],
    survey: dict[str, Any],
    have: list[str],
    *,
    latin_have: dict[str, str] | None = None,
) -> tuple[list[tuple[str, list[str]]], list[dict[str, str]]]:
    """Named plants with no model yet: the home's, then the survey's.

    Food names stay food (#144.1): only `plants[].model` and the
    survey's `plants` become items, each with the grounds it
    grows on when the home says. A survey plant whose GBIF
    kingdom is outside Plantae and Fungi is blocked, never
    built (#146.2). A survey plant the place already has is
    skipped: the common name without parentheses, and the Latin
    against the world's plant files (or as a stem) (#167.1).
    """
    have_all = set(have)
    latin_have = latin_have or {}
    missing: list[tuple[str, list[str]]] = []
    blocked: list[dict[str, str]] = []
    seen: set[str] = set()

    def want(slug: str, grounds: list[str]) -> None:
        if slug and slug not in have_all and slug not in seen:
            seen.add(slug)
            missing.append((slug, grounds))

    def known(name: str) -> bool:
        latin = _latin(name)
        return (
            plant_slug(name) in have_all
            or _common_slug(name) in have_all
            or (
                latin is not None
                and (latin.lower() in latin_have or plant_slug(latin) in have_all)
            )
        )

    for entry in home_plant_entries(home):
        model = entry.get("model") or ""
        on = entry.get("on", [])
        want(model, [on] if isinstance(on, str) else [g for g in on if g])
    for entry in survey.get("plants", []):
        name = entry.get("name", "")
        if known(name):
            continue
        slug = plant_slug(name)
        kingdom = entry.get("kingdom")
        if kingdom is not None and kingdom not in PLANT_KINGDOMS:
            if slug and slug not in seen:
                seen.add(slug)
                blocked.append({"what": slug, "reason": "not_a_plant"})
            continue
        want(slug, [])
    return missing, blocked


def write_plan(out_dir: Path, plan: dict[str, Any]) -> Path:
    """`plan.json` next to the survey it was planned from."""
    path = out_dir / "plan.json"
    path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    return path


def place_tiles(world: Path, place_id: str, survey_tile: str) -> list[str]:
    """The tiles a place's wanted list is filed under (#170.2).

    The place file's own tiles; its centre's tile when it has
    none; the surveyed tile when the place isn't filed yet.
    """
    try:
        place = json.loads(
            (world / "places" / f"{place_id}.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return [survey_tile]
    listed = place.get("tiles") if isinstance(place, dict) else None
    if isinstance(listed, list) and listed:
        found = sorted(
            tile_id(cell[0], cell[1])
            for cell in listed
            if isinstance(cell, list)
            and len(cell) == 2
            and all(isinstance(v, int) and not isinstance(v, bool) for v in cell)
        )
        return found or [survey_tile]
    center = place.get("center") if isinstance(place, dict) else None
    lat = center.get("lat") if isinstance(center, dict) else None
    lon = center.get("lon") if isinstance(center, dict) else None
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        return [tile_at(float(lat), float(lon))]
    return [survey_tile]


def country_name(code: str, builder_root: Path | None) -> str | None:
    """The English country name for a 2-letter code, else nothing (#239.3).

    Without a root the checkout's own data answers (#266.1):
    the worker runs with no root, and a place publish must not
    need one.
    """
    from .schemas import ROOT as _ROOT

    root = builder_root if builder_root is not None else _ROOT
    try:
        names = json.loads(
            (root / "data" / "country-names.json").read_text(encoding="utf-8")
        ).get("names", {})
    except (OSError, ValueError):
        return None
    name = names.get(code.strip().upper())
    return name if isinstance(name, str) and name else None


def place_id_of(research: dict[str, Any]) -> str | None:
    """The job place's id: the survey's, else country plus short (#275.1).

    Stage-C research predates `region_id` surveys, so the id
    falls back to the same `country_short` the place file
    takes. Nothing when neither names it.
    """
    region = research.get("region_id")
    if isinstance(region, str):
        return region
    short = research.get("short")
    country = research.get("country")
    if not isinstance(short, dict) or not isinstance(country, str):
        return None
    slug = place_slug(str(short.get("en", "")))
    if not slug or not country.strip():
        return None
    return f"{country.strip().lower()}_{slug}"


def place_slug(short_en: str) -> str:
    """`Mamirauá` to `mamiraua`: the place id's short part (#239.3)."""
    import re
    import unicodedata

    ascii_name = unicodedata.normalize("NFKD", short_en).encode("ascii", "ignore")
    words = re.findall(r"[a-z0-9]+", ascii_name.decode("ascii").lower())
    return "_".join(words)


def _animal_swims(animal: dict[str, Any] | None, habitats: list[str]) -> bool:
    """Water start: spawn, sea habitats, or dive without walk or fly (#239)."""

    def num(value: Any) -> float:
        return value if isinstance(value, (int, float)) else 0.0

    if animal is not None and animal.get("spawn") == "water":
        return True
    if set(habitats) & {"sea", "reef", "open_sea", "open_ocean"}:
        return True
    if animal is None:
        return False
    return bool(
        num(animal.get("dive"))
        and not num(animal.get("walk"))
        and not num(animal.get("fly"))
    )


def place_start(
    first: str | None,
    tiles: list[str],
    made: dict[str, dict[str, Any]],
    centre: dict[str, float],
    grid: Any,
    world: Path,
    job_dir: Path,
) -> tuple[float, float]:
    """The start point on ground suiting the first playable animal (#239).

    Land for walkers (the wild spot), water for swimmers (a
    water-majority tile's centre); the surveyed centre when
    nothing suits better. The world's start check is the backstop.
    """
    from .grid import center

    try:
        lat = float(centre.get("lat", 0.0))
        lon = float(centre.get("lon", 0.0))
    except (TypeError, ValueError):
        lat, lon = 0.0, 0.0
    if first is None:
        return lat, lon
    animal = _animal_file(world, job_dir, first)
    habitats: list[str] = []
    try:
        listed = json.loads(
            (world / "animal-list.json").read_text(encoding="utf-8")
        ).get("animals", [])
        habitats = next(
            (e.get("habitats", []) for e in listed if e.get("id") == first), []
        )
    except (OSError, ValueError):
        pass
    if not _animal_swims(animal, habitats if isinstance(habitats, list) else []):
        return wild_spot(tiles[0], lat, lon, grid)
    for tile in tiles:
        if float((made.get(tile) or {}).get("water", 0.0)) >= 0.5:
            cla, clo = center(tile)
            return cla, clo
    return lat, lon


def file_places(
    store: Any, world: Path, survey_dir: Path, world_commit: str
) -> list[str]:
    """A surveyed place's missing list into its tiles' groups (#170.2).

    `survey.json`'s `missing` lands under each of the place's own
    tiles as `places[tile]`; the tiles themselves are untouched.
    An old survey without `surveyed_at` takes the plan's date,
    else today. Returns the group paths written.
    """
    from .build import publish_places

    survey = json.loads((survey_dir / "survey.json").read_text(encoding="utf-8"))
    place_id = survey["region_id"]
    surveyed_at = survey.get("surveyed_at")
    if not surveyed_at:
        plan_path = survey_dir / "plan.json"
        if plan_path.is_file():
            try:
                planned = json.loads(plan_path.read_text(encoding="utf-8"))
                surveyed_at = str(planned.get("planned_at", ""))[:10]
            except ValueError:
                surveyed_at = ""
        else:
            surveyed_at = ""
    if not surveyed_at:
        surveyed_at = _today()
    entry = {
        "region": place_id,
        "wanted": survey.get("missing", []),
        "surveyed_at": surveyed_at,
    }
    paths = []
    for tile in place_tiles(world, place_id, survey["tile"]):
        paths.append(publish_places(tile, entry, store, world_commit))
    return paths


def _animal_file(world: Path, job_dir: Path, animal_id: str) -> dict[str, Any] | None:
    """The animal's file: the world's, else the job's made one."""
    filed = world / "animals" / f"{animal_id}.json"
    if filed.is_file():
        return json.loads(filed.read_text(encoding="utf-8"))
    for path in sorted(job_dir.glob(f"*/animals/{animal_id}.json")):
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def write_place(
    survey: dict[str, Any],
    job_dir: Path,
    world: Path,
) -> dict[str, Any]:
    """The draft place from the survey, by code (#202.3).

    A name and points over ordinary tiles, with no land: id,
    both names, country, centre, the surveyed tile, the cast,
    and the start at the centre. Playable is every surveyed
    animal already filed or made by this job, in survey order;
    predators hunt a place animal, prey are hunted by one.
    `live` is false: the game plays the region file until a
    person has played the place (#205).
    """
    from .grid import center

    col, row = parse_tile(survey["tile"])
    lat, lon = center(survey["tile"])
    playable: list[str] = []
    files: dict[str, dict[str, Any]] = {}
    for entry in survey.get("animals", []):
        data = _animal_file(world, job_dir, entry["id"])
        if data is None:
            continue
        playable.append(entry["id"])
        files[entry["id"]] = data
    return {
        "id": survey["region_id"],
        "name": {"en": survey["name"]["en"], "fi": survey["name"]["fi"]},
        "country": survey["country"],
        "center": {"lat": lat, "lon": lon},
        "tiles": [[col, row]],
        "species": {
            "playable": playable,
            "predators": sorted(
                a for a, d in files.items() if set(d.get("hunts", [])) & set(playable)
            ),
            "prey": sorted(
                {p for d in files.values() for p in d.get("hunts", [])} & set(playable)
            ),
        },
        "points": [
            {
                "kind": "start",
                "id": "default",
                "lat": lat,
                "lon": lon,
                "facing": "north",
            }
        ],
        "live": False,
    }


def write_job_place(
    research: dict[str, Any],
    job_dir: Path,
    world: Path,
    made: dict[str, dict[str, Any]],
    grid: Any,
    builder_root: Path | None,
) -> dict[str, Any]:
    """The live place file from stage C's build, by code (#239.3).

    The id is `<country>_<short slug>`; `name` is the short
    child-friendly triple, `name_full` the long official one;
    playable is every surveyed animal filed or made by this job,
    in survey order; the start suits the first playable animal;
    `live` is true. Needs `research.short` and `research.country`:
    without them the build stops naming the backfill, it never
    guesses child-facing names.
    """
    from .grid import parse_tile

    short = research.get("short")
    if not isinstance(short, dict) or not short.get("en"):
        raise ValueError("place needs research.short {en, fi, fi_gen}")
    country = research.get("country")
    if not isinstance(country, str) or not country.strip():
        raise ValueError("place needs research.country (2-letter code)")
    slug = place_slug(str(short["en"]))
    if not slug:
        raise ValueError("place needs a non-empty short English name")
    country_full = country_name(country, builder_root)
    if country_full is None:
        raise ValueError(f"place needs a known country for {country!r}")
    tiles = [t for t in research.get("tiles", []) if isinstance(t, str)]
    playable: list[str] = []
    files: dict[str, dict[str, Any]] = {}
    for entry in research.get("animals", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            continue
        data = _animal_file(world, job_dir, entry["id"])
        if data is None:
            continue
        playable.append(entry["id"])
        files[entry["id"]] = data
    centre = research.get("centre")
    centre = centre if isinstance(centre, dict) else {}
    start_lat, start_lon = place_start(
        playable[0] if playable else None,
        tiles,
        made,
        centre,
        grid,
        world,
        job_dir,
    )
    cells = []
    for tile in tiles:
        col, row = parse_tile(tile)
        cells.append([col, row])
    name = research.get("name")
    name = name if isinstance(name, dict) else {}

    def num(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    return {
        "id": f"{country.strip().lower()}_{slug}",
        "name": {"en": short["en"], "fi": short["fi"], "fi_gen": short["fi_gen"]},
        "name_full": {
            "en": name.get("en", ""),
            "fi": name.get("fi", ""),
            "fi_gen": name.get("fi_gen", ""),
        },
        "wikidata": str(research.get("wikidata", "")),
        "country": country_full,
        "center": {
            "lat": num(centre.get("lat", 0.0)),
            "lon": num(centre.get("lon", 0.0)),
        },
        "tiles": cells,
        "species": {
            "playable": playable,
            "predators": sorted(
                a for a, d in files.items() if set(d.get("hunts", [])) & set(playable)
            ),
            "prey": sorted(
                {p for d in files.values() for p in d.get("hunts", [])} & set(playable)
            ),
        },
        "points": [
            {
                "kind": "start",
                "id": "default",
                "lat": start_lat,
                "lon": start_lon,
                "facing": "north",
            }
        ],
        "live": True,
    }


def check_place_file(path: Path, world: Path) -> list[str]:
    """The world's place schema over a draft file; [] when clean."""
    import jsonschema

    schema = json.loads(
        (world / "schemas" / "place.schema.json").read_text(encoding="utf-8")
    )
    draft = json.loads(path.read_text(encoding="utf-8"))
    try:
        jsonschema.validate(draft, schema)
    except jsonschema.ValidationError as error:
        return [f"the draft breaks the place schema: {error.message}"]
    return []


def write_replay_place(
    place_dir: Path, job_dir: Path, world: Path
) -> tuple[Path, list[str]]:
    """The job's draft place, and the world's verdict on it.

    Writes `places/<id>.json` under the job dir; the problems
    are [] when the draft passes. Take lays it out.
    """
    survey = json.loads((place_dir / "survey.json").read_text(encoding="utf-8"))
    place = write_place(survey, job_dir, world)
    out = job_dir / "places" / f"{survey['region_id']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(place, indent=2) + "\n", encoding="utf-8")
    return out, check_place_file(out, world)


def replay_place(
    place_dir: Path,
    out_dir: Path,
    world: Path,
    grid_dir: Path | None,
    store: Any | None = None,
    world_commit: str = "unknown",
) -> tuple[dict[str, Path], Path]:
    """Rebuild a place job from its own answers: no key, no cost.

    The plan and survey come from the place folder, the answers
    from its `items/` records; then the job writes its place.
    Anything failing — an item, the place — raises instead of
    reaching review. With a store, the shipped job files its
    survey's `missing` as #170.2 says, with no extra step (#188.4).
    """
    from .replay import JOB_ID, replay_request

    plan = json.loads((place_dir / "plan.json").read_text(encoding="utf-8"))
    reports = replay_request(plan["tile"], place_dir, out_dir, world, grid_dir)
    place_path, problems = write_replay_place(place_dir, out_dir / JOB_ID, world)
    if problems:
        raise ValueError(f"the draft place fails the world's checks: {problems[0]}")
    if store is not None:
        file_places(store, world, place_dir, world_commit)
    return reports, place_path


def record_place(
    place_dir: Path,
    out_dir: Path,
    world: Path,
    grid_dir: Path | None,
    config: Any,
    store: Any | None = None,
    world_commit: str = "unknown",
) -> tuple[dict[str, Path], Path]:
    """Run a place job live, saving its answers under the place folder.

    With a store, the shipped job files its survey's `missing`
    as #170.2 says, with no extra step (#188.4).
    """
    from .jobs import make_context
    from .meta import MetaAuthError
    from .records import RecordingClient
    from .replay import JOB_ID, run_job_id, run_request

    if not config.model_api_key:
        raise MetaAuthError("MODEL_API_KEY is not set")
    plan = json.loads((place_dir / "plan.json").read_text(encoding="utf-8"))
    survey_path = place_dir / "survey.json"
    survey = (
        json.loads(survey_path.read_text(encoding="utf-8"))
        if survey_path.is_file()
        else {}
    )
    place = place_of(plan["region_id"], survey) if plan.get("region_id") else None

    def make_ctx(item: dict[str, Any], item_dir: Path) -> Any:
        records = place_dir / "items" / item["id"] / "records"
        ctx = make_context(config, run_job_id(item["id"]), item_dir, world)
        ctx.client = RecordingClient(ctx.client, records)
        if ctx.open_client is not None:
            ctx.open_client = RecordingClient(ctx.open_client, records)
        return ctx

    reports = run_request(plan["tile"], plan, out_dir, world, grid_dir, make_ctx, place)
    place_path, problems = write_replay_place(place_dir, out_dir / JOB_ID, world)
    if problems:
        raise ValueError(f"the draft place fails the world's checks: {problems[0]}")
    if store is not None:
        file_places(store, world, place_dir, world_commit)
    return reports, place_path


def take_place_job(job_dir: Path, out_dir: Path, world: Path) -> Path:
    """A place job's checked files, laid out like animundo-world.

    Each item's take dirs plus the job's draft place; every
    item passes the world's draft check and the place its own,
    or the take stops with the first problems.
    """
    from .build import TAKE_DIRS, check_draft, take_report

    dest = out_dir / job_dir.name
    if dest.exists():
        raise ValueError(f"{dest} is already taken; clear it first")
    for item_dir in sorted(p for p in job_dir.iterdir() if p.is_dir()):
        if item_dir.name == "places":
            continue
        for name in TAKE_DIRS:
            root = item_dir / name
            if not root.is_dir():
                continue
            for path in sorted(p for p in root.rglob("*") if p.is_file()):
                target = dest / name / path.relative_to(root)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())
    places = job_dir / "places"
    if places.is_dir():
        for path in sorted(places.glob("*.json")):
            target = dest / "places" / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
            problems = check_place_file(target, world)
            if problems:
                raise ValueError(f"{path.name} fails the place checks: {problems[0]}")
    if _has_draft_content(dest):
        problems = check_draft(dest, world)
        if problems:
            raise ValueError(f"the take fails the draft check: {problems[0]}")
    take_report(dest)
    return dest


#: Take dirs the world's draft check reads (its CONTENT, minus
#: homes/ which no take checks that way). A take of only a place
#: and homes skips it: the place passed its schema above, the
#: home its build checks. When the tool reads places/, list it.
_DRAFT_CONTENT_DIRS = ("regions", "animals", "models", "assets", "locale")


def _has_draft_content(dest: Path) -> bool:
    """Whether the take holds files the world's draft check reads."""
    return any(
        path.is_file()
        for name in _DRAFT_CONTENT_DIRS
        for path in (dest / name).rglob("*")
        if (dest / name).is_dir()
    )


def place_of(place_id: str, survey: dict[str, Any]) -> dict[str, str]:
    """The intake's place: its place id and its own home (#175.2).

    The home is the `home_from` stem (`examples/reef` is the
    `reef` home); empty when the survey names none, and the
    intake then keeps the grid habitat.
    """
    home_from = str(survey.get("home_from") or "")
    home = home_from.split("/")[-1] if home_from else ""
    return {"id": place_id, "home": home}


def place_residents(world: Path, place_id: str) -> set[str]:
    """Animal ids an existing place already carries (#167.4)."""
    try:
        place = json.loads(
            (world / "places" / f"{place_id}.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return set()
    residents: set[str] = set()
    species = place.get("species") or {}
    groups = species.values() if isinstance(species, dict) else [species]
    for group in groups:
        for entry in group or []:
            if isinstance(entry, str):
                residents.add(entry)
            elif isinstance(entry, dict) and entry.get("id"):
                residents.add(str(entry["id"]))
    return residents


#: A pack's species delta for one place: the lists it adds to.
#: Fish counts don't ride along: places carry no fish (#207.1).
PACK_DELTA_LISTS = ("playable", "predators", "prey")


def pack_animals(pack_dir: Path) -> dict[str, dict[str, Any]]:
    """The pack's animal files by id (plants are no place species)."""
    animals: dict[str, dict[str, Any]] = {}
    folder = pack_dir / "animals"
    if not folder.is_dir():
        return animals
    for path in sorted(folder.glob("*.json")):
        try:
            animal = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if isinstance(animal, dict):
            animals[path.stem] = animal
    return animals


def pack_additions(pack_dir: Path) -> dict[str, dict[str, list[str]]]:
    """A pre-conversion pack's additions by place (#207.1).

    Only the species lists; `format` and `fish` don't go into
    a place file.
    """
    out: dict[str, dict[str, list[str]]] = {}
    folder = pack_dir / "additions"
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("*.json")):
        try:
            additions = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not isinstance(additions, dict):
            continue
        out[path.stem] = {
            key: [a for a in (additions.get(key) or []) if isinstance(a, str)]
            for key in PACK_DELTA_LISTS
        }
    return out


def animal_delta(
    animal_id: str,
    animal: dict[str, Any],
    species: dict[str, Any],
    world: Path,
) -> dict[str, list[str]]:
    """One animal's species delta for a place's cast (#207.1).

    Playable always; a predator when it hunts a resident's
    tag, prey when a resident hunts one of its tags. Featured
    never: the research carries no featured signal, so the
    hand keeps curating it.
    """
    residents: set[str] = set()
    for key in ("playable", "predators", "prey", "featured"):
        residents.update(s for s in (species.get(key) or []) if isinstance(s, str))
    tags = set(animal.get("tags", []) or [])
    hunts = set(animal.get("hunts", []) or [])
    hunts_tags: set[str] = set()
    resident_tags: set[str] = set()
    for resident in residents:
        try:
            data = json.loads(
                (world / "animals" / f"{resident}.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            continue
        hunts_tags.update(data.get("hunts", []) or [])
        resident_tags.update(data.get("tags", []) or [])
    return {
        "playable": [animal_id],
        "predators": [animal_id] if hunts & resident_tags else [],
        "prey": [animal_id] if hunts_tags & tags else [],
        "featured": [],
    }


def pack_delta(
    pack_dir: Path, place_ids: list[str], world: Path
) -> dict[str, dict[str, list[str]]]:
    """The pack's species delta per place (#207.1).

    Additions win when the pack carries them (packs from
    before conversion 2 name their own places); otherwise the
    pack's animals go to the given places by the tag rule.
    """
    delta = pack_additions(pack_dir)
    animals = pack_animals(pack_dir)
    if not animals:
        return delta
    for place_id in place_ids:
        if place_id in delta:
            continue
        try:
            species = (
                json.loads(
                    (world / "places" / f"{place_id}.json").read_text(encoding="utf-8")
                ).get("species")
                or {}
            )
        except (OSError, ValueError):
            continue
        merged: dict[str, list[str]] = {key: [] for key in PACK_DELTA_LISTS}
        for animal_id, animal in animals.items():
            one = animal_delta(animal_id, animal, species, world)
            for key in PACK_DELTA_LISTS:
                merged[key].extend(a for a in one[key] if a not in merged[key])
        delta[place_id] = merged
    return delta


def missing_pack_species(
    places_dir: Path,
    packs: list[tuple[Path, list[str]]],
    world: Path,
) -> dict[str, dict[str, list[str]]]:
    """Per place, the pack species its file still lacks (#207.3).

    Empty when every place's live species include everything
    its packs added. A place file missing entirely counts all
    of the delta missing.
    """
    missing: dict[str, dict[str, list[str]]] = {}
    for pack_dir, place_ids in packs:
        for place_id, delta in pack_delta(pack_dir, place_ids, world).items():
            try:
                species = (
                    json.loads(
                        (places_dir / f"{place_id}.json").read_text(encoding="utf-8")
                    ).get("species")
                    or {}
                )
            except (OSError, ValueError):
                species = {}
            for key in PACK_DELTA_LISTS:
                have = {s for s in (species.get(key) or []) if isinstance(s, str)}
                lacking = [a for a in delta[key] if a not in have]
                if lacking:
                    missing.setdefault(place_id, {}).setdefault(key, []).extend(
                        a for a in lacking if a not in missing[place_id].get(key, [])
                    )
    return missing


def file_pack_places(
    pack_dir: Path, place_ids: list[str], world: Path
) -> dict[str, list[str]]:
    """Merge the pack's species delta into the world's places (#207.1).

    New ids append at each list's end; featured and every other
    key stay untouched. Returns the filed ids per place. The
    merged files must pass the place schema.
    """
    filed: dict[str, list[str]] = {}
    for place_id, delta in pack_delta(pack_dir, place_ids, world).items():
        path = world / "places" / f"{place_id}.json"
        place = json.loads(path.read_text(encoding="utf-8"))
        species = place.setdefault("species", {})
        for key in PACK_DELTA_LISTS:
            listed = species.setdefault(key, [])
            for animal_id in delta[key]:
                if animal_id not in listed:
                    listed.append(animal_id)
                    filed.setdefault(place_id, []).append(f"{key}:{animal_id}")
        # The place files' own style: one-space indent, UTF-8
        # names as written, so a filing only touches its lines.
        path.write_text(
            json.dumps(place, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        problems = check_place_file(path, world)
        if problems:
            raise ValueError(f"{place_id} fails the place schema: {problems[0]}")
    return filed


def improve_habitats(
    animals: list[dict[str, Any]],
    world: Path,
    improve_arg: str,
    habitat_grid: HabitatGrid,
) -> set[str]:
    """The place's habitats for `--improve` asks (#167.4).

    A place's own: its residents' habitats, so the survey asks
    for more of what lives there. A bare tile's: the cell's grid
    habitat, the only signal before the answer.
    """
    try:
        col, row = parse_tile(improve_arg)
        return {habitat_grid.habitat(col, row)}
    except (ValueError, KeyError):
        pass
    residents = place_residents(world, improve_arg)
    by_id = {animal["id"]: animal for animal in animals}
    habitats: set[str] = set()
    for resident in residents:
        habitats |= set((by_id.get(resident) or {}).get("habitats") or [])
    return habitats


def habitat_first(
    animals: list[dict[str, Any]], habitats: set[str]
) -> list[dict[str, Any]]:
    """The library's animals with these habitats first, stable (#167.4)."""
    return sorted(
        animals, key=lambda a: 0 if set(a.get("habitats") or []) & habitats else 1
    )


def improve_tile(improve: str, world: Path) -> str:
    """The surveyed tile for `--improve`: a tile id, or a place's own."""
    try:
        parse_tile(improve)
        return improve
    except ValueError:
        pass
    path = world / "places" / f"{improve}.json"
    if not path.is_file():
        raise SurveyError(f"{improve} is neither a tile nor a place")
    place = json.loads(path.read_text(encoding="utf-8"))
    center = place.get("center", {})
    return tile_at(float(center["lat"]), float(center["lon"]))
