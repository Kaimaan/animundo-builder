"""Place requests as files (D20, #137): survey a place, plan it, build it.

`builder place survey "<place>"` researches one place with a single
Muse Spark call and writes `survey.json`; `builder place plan`
turns the survey into a §7 `plan.json`. No Firestore, no admin
console: agents run these rarely from the command line.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .grid import GridName, parse_tile, tile_at
from .habitats import HabitatGrid
from .plan import plant_slug
from .presence import fetch_raw
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
                "description": "The start's tile id, like s_530_612.",
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
                "properties": {
                    "en": {"type": "string"},
                    "fi": {"type": "string"},
                },
                "required": ["en", "fi"],
                "additionalProperties": False,
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
    """The tile and its cells out to `radius`, clamped to the grid."""
    from .grid import CELLS

    grid, col, row = parse_tile(tile)
    prefix = "n" if grid == "ease2_n25" else "s"
    out = []
    for dc in range(-radius, radius + 1):
        for dr in range(-radius, radius + 1):
            c, r = col + dc, row + dr
            if 0 <= c < CELLS and 0 <= r < CELLS:
                out.append(f"{prefix}_{c:03d}_{r:03d}")
    return out


def gbif_near(tile: str, animals: list[dict[str, Any]]) -> dict[str, int]:
    """GBIF records per animal id within 2 tiles of the surveyed tile."""
    totals: dict[str, int] = {}
    for cell in cells_within(tile):
        for animal_id, count in fetch_raw(cell, animals).items():
            totals[animal_id] = totals.get(animal_id, 0) + count
    return totals


def check_survey(
    data: Any,
    place: str,
    grids: dict[GridName, HabitatGrid],
    world: Path,
    animals: list[dict[str, Any]],
    want: list[str],
    budget_usd: float,
    improve: str | None,
) -> dict[str, Any]:
    """A checked survey in §6a shape, or why not (#137).

    The tile must be a real cell (the improved place's own with
    `--improve`); `home_from` a home file; every animal on the
    list with a source and GBIF records within 2 tiles (the
    wanted stay first, records or not); plants and food named
    with sources.
    """
    if not isinstance(data, dict):
        raise SurveyError("the survey is not an object")
    for key in ("tile", "habitat", "home_from", "name", "animals", "plants", "food"):
        if key not in data:
            raise SurveyError(f"the survey has no {key}")
    tile = data["tile"]
    try:
        grid, col, row = parse_tile(tile)
    except ValueError:
        raise SurveyError(f"{tile} is no tile id") from None
    if grid not in grids:
        raise SurveyError(f"{tile} is on no baked grid")
    try:
        habitat = grids[grid].habitat(col, row)
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
    counts = gbif_near(tile, animals)
    kept: list[dict[str, str]] = []
    for entry in data["animals"]:
        if entry["id"] in want or counts.get(entry["id"], 0) > 0:
            kept.append({"id": entry["id"], "source": entry["source"]})
    kept.sort(key=lambda e: 0 if e["id"] in want else 1)
    return {
        "place": place,
        "tile": tile,
        "habitat": data["habitat"],
        "grid_habitat": habitat,
        "home_from": home_from,
        "name": {"en": name["en"], "fi": name["fi"]},
        "animals": kept,
        "plants": [{"name": e["name"], "source": e["source"]} for e in data["plants"]],
        "food": [{"name": e["name"], "source": e["source"]} for e in data["food"]],
        "wanted": [w for w in want if w in by_id],
        "budget_usd": budget_usd,
    }


def write_survey(out_dir: Path, survey: dict[str, Any]) -> Path:
    """`survey.json` in §6a shape for `place plan` and the job."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "survey.json"
    path.write_text(json.dumps(survey, indent=2) + "\n", encoding="utf-8")
    return path


def plan_place(
    survey: dict[str, Any], world: Path, world_commit: str
) -> dict[str, Any]:
    """The §7 plan from a place survey (T98), not from the grid.

    A home item when `home_from` is an example (examples are not
    buildable); each unfiled survey animal with its food, the
    children's animals first; one findable plant per kind the
    home names. Items stop at the budget. An animal with no
    `start_from` and no `"shape": "new"` is `blocked` with
    `needs_reference`, never built; one the list can't make is
    `not_makeable`. Filed animals need nothing: with
    `--improve` the plan holds only what the place lacks.
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
    if home_from.startswith("examples/"):
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
    for slug, grounds in _missing_plants(home, survey, have):
        estimate = ITEM_ESTIMATES["plant"]
        if not afford(estimate):
            break
        items.append(
            {
                "id": f"plant_{slug}",
                "kind": "plant",
                "what": slug,
                "from": _plant_from(slug, grounds, home, have),
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
        "coming": coming,
        "world": world_commit,
        "planned_at": datetime.now(UTC).isoformat(),
    }
    validate("plan", plan)
    return plan


def _home_plant_entries(home: dict[str, Any]) -> list[dict[str, Any]]:
    """The home's named plants in either shape: an example's
    `plants` or a filed home's `ground_cover.plants`."""
    if isinstance(home.get("plants"), list):
        return [p for p in home["plants"] if isinstance(p, dict)]
    return [
        p for p in home.get("ground_cover", {}).get("plants", []) if isinstance(p, dict)
    ]


def _missing_plants(
    home: dict[str, Any], survey: dict[str, Any], have: list[str]
) -> list[tuple[str, list[str]]]:
    """Named plants with no model yet: the home's, then the survey's.

    Food names stay food (#144.1): only `plants[].model` and the
    survey's `plants` become items, each with the grounds it
    grows on when the home says.
    """
    missing: list[tuple[str, list[str]]] = []
    seen: set[str] = set()

    def want(slug: str, grounds: list[str]) -> None:
        if slug and slug not in have and slug not in seen:
            seen.add(slug)
            missing.append((slug, grounds))

    for entry in _home_plant_entries(home):
        model = entry.get("model") or ""
        on = entry.get("on", [])
        want(model, [on] if isinstance(on, str) else [g for g in on if g])
    for entry in survey.get("plants", []):
        want(plant_slug(entry.get("name", "")), [])
    return missing


def _plant_from(
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
    for entry in _home_plant_entries(home):
        model = entry.get("model") or ""
        if model not in have:
            continue
        on = entry.get("on", [])
        grounds_here = [on] if isinstance(on, str) else [g for g in on if g]
        if grounds and set(grounds) & set(grounds_here):
            return model
    return have[0]


def write_plan(out_dir: Path, plan: dict[str, Any]) -> Path:
    """`plan.json` next to the survey it was planned from."""
    path = out_dir / "plan.json"
    path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    return path


def improve_tile(improve: str, world: Path) -> str:
    """The surveyed tile for `--improve`: a tile id, or a region's own."""
    try:
        parse_tile(improve)
        return improve
    except ValueError:
        pass
    path = world / "regions" / f"{improve}.json"
    if not path.is_file():
        raise SurveyError(f"{improve} is neither a tile nor a region")
    region = json.loads(path.read_text(encoding="utf-8"))
    center = region.get("center", {})
    return tile_at(float(center["lat"]), float(center["lon"]))
