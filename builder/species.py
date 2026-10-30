"""The tile survey from Muse Spark (D18, #141.1): one call per tile, cached.

The tile maker asks Muse Spark what is in the tile's cell: its
parts, the animals per part, the plant children will find, and
both names. The call carries our own surveyed facts first
(habitat and blend, land and water, heights, the home), so the
answer describes our square instead of guessing one. The answer
is checked against the facts afterwards and cached per tile; a
re-run calls the model only when the animal list or the facts
change. Every call goes through MetaClient with the Meta caps.
GBIF stays for the T40 eval and the planner's improve list,
never for a tile's species.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema

from .ledger import Caps, FileLedger

#: The species call, as the lab ran it: Chat Completions on Muse
#: Spark, low effort (default effort spent its whole reasoning
#: budget and returned nothing), room for the list.
MODEL = "muse-spark-1.3"
EFFORT = "low"
MAX_TOKENS = 16000

SYSTEM = (
    "You are a field biologist surveying one 25 km square of land or sea. "
    "You are given the square's surveyed facts (habitat, land and water, "
    "heights, the home the game builds it from) and a list of animals. "
    "Name what's in the square in parts, and in each part every animal on "
    "the list with a wild population living there today: resident, "
    "breeding, or a regular seasonal visitor. Leave out zoo, farm and pet "
    "animals, rare strays, and animals whose range stops short of the "
    "square. The list names exact species (see the Latin names), not "
    "their relatives. Name the plant children will find there: one of "
    "ours when one grows there, else a new one that really grows there. "
    "Say what the square is in a few words. Give the square a short "
    "child-friendly name in English and in Finnish, describing what a "
    "child will find there, without naming animals that aren't on the "
    "tile. If no listed animal lives there, name none."
)

#: What a part of a square can be. The checks know the water
#: ones; the rest are labels only.
PART_KINDS = (
    "sea",
    "skerries",
    "shore",
    "forest",
    "lake",
    "town",
    "river",
    "farmland",
    "mountain",
    "tundra",
    "savanna",
    "grassland",
    "desert",
    "wetland",
    "glacier",
    "reef",
    "mangrove",
    "ice",
)

#: Part kinds that need open water in the square, and the one
#: that needs lake water. A shore is unchecked: rivers aren't in
#: our facts, so a riverbank reads as shore too.
SEA_PARTS = frozenset({"sea", "skerries", "reef"})
LAKE_PARTS = frozenset({"lake"})


class SpeciesPlaceError(ValueError):
    """The species answer named somewhere else, twice. The tile blocks."""

    def __init__(self, tile: str, place: str, reason: str) -> None:
        super().__init__(f"{tile} answered as {place!r}: {reason}")
        self.tile = tile
        self.place = place
        self.reason = reason


def _picks_schema(ids: list[str], what: str) -> dict[str, Any]:
    """One picked-plants shape: library ids with how many grow there."""
    return {
        "type": "array",
        "description": f"The square's {what} from the list, with counts.",
        "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "enum": ids},
                "count": {"type": "integer", "minimum": 1},
            },
            "required": ["id", "count"],
            "additionalProperties": False,
        },
    }


def species_schema(
    ids: list[str], plant_ids: list[str], tree_ids: list[str]
) -> dict[str, Any]:
    """The strict survey shape: parts with ids, plants, trees, both names."""
    return {
        "type": "object",
        "properties": {
            "place": {
                "type": "string",
                "description": "What this square is, in a few words.",
            },
            "parts": {
                "type": "array",
                "description": (
                    "What's in the square, in parts, with each part's "
                    "animals from the list."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": list(PART_KINDS)},
                        "species": {
                            "type": "array",
                            "items": {"type": "string", "enum": ids},
                        },
                    },
                    "required": ["kind", "species"],
                    "additionalProperties": False,
                },
            },
            "plants": _picks_schema(plant_ids, "plants"),
            "trees": _picks_schema(tree_ids, "trees"),
            "name_en": {
                "type": "string",
                "description": (
                    "A short child-friendly name for this square in English."
                ),
            },
            "name_fi": {
                "type": "string",
                "description": (
                    "A short child-friendly name for this square in Finnish."
                ),
            },
        },
        "required": ["place", "parts", "plants", "trees", "name_en", "name_fi"],
        "additionalProperties": False,
    }


def survey_facts(
    *,
    habitat: str,
    eco1: int | None,
    share1: float,
    eco2: int | None,
    share2: float,
    land: float,
    water: float,
    elevation_m: float,
    level: int,
    edge_lo: int,
    edge_hi: int,
    home: str,
    adapt: bool,
) -> dict[str, Any]:
    """Our own facts for the survey: what the prompt states, keyed as one.

    `land` is the share of the 25 sample points on land, `water`
    the share of those that is lake; `sea` is what is not land.
    Rounded, so the cache key is stable.
    """
    land = round(land, 2)
    water = round(water, 2)
    return {
        "habitat": habitat,
        "eco1": eco1,
        "share1": round(share1, 2),
        "eco2": eco2,
        "share2": round(share2, 2),
        "land": land,
        "lake": round(land * water, 2),
        "sea": round(max(0.0, 1.0 - land), 2),
        "elevation_m": round(elevation_m, 1),
        "level": level,
        "edge_lo": edge_lo,
        "edge_hi": edge_hi,
        "home": home,
        "adapt": adapt,
    }


def facts_fingerprint(facts: dict[str, Any]) -> str:
    """The facts' short hash: with the list's, it keys the cache."""
    text = json.dumps(facts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def list_library(world: Path, folder: str) -> list[dict[str, str]]:
    """The plant or tree library: id, name and Latin, by id (#147.3)."""

    def entry(path: Path) -> dict[str, str] | None:
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return None
        if not spec.get("id") or not spec.get("name") or not spec.get("latin"):
            return None
        return {"id": spec["id"], "name": spec["name"], "latin": spec["latin"]}

    listed = []
    for path in sorted((world / "models" / folder).glob("*.json")):
        found = entry(path)
        if found is not None:
            listed.append(found)
    return listed


def library_fingerprint(
    plants: list[dict[str, str]], trees: list[dict[str, str]]
) -> str:
    """The plants' short hash: a re-run calls only on change (#147.3)."""
    text = "\n".join(
        f"{e['id']}:{e.get('name', '')}:{e.get('latin', '')}" for e in [*plants, *trees]
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def user_message(
    lat: float,
    lon: float,
    animals: list[dict[str, Any]],
    plants: list[dict[str, str]],
    trees: list[dict[str, str]],
    facts: dict[str, Any],
) -> str:
    """The square's centre both ways, our facts, the lists.

    Decimals alone once read 120 W as 120 E, so the hemisphere
    letters ride along.
    """
    ns, ew = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
    listing = "\n".join(f"- {a['id']}: {a['name']} ({a['latin']})" for a in animals)
    greens = "\n".join(f"- {p['id']}: {p['name']} ({p['latin']})" for p in plants)
    woods = "\n".join(f"- {t['id']}: {t['name']} ({t['latin']})" for t in trees)
    eco = f"ecoregion {facts['eco1']} over all of it"
    if facts["eco2"] is not None:
        eco = (
            f"ecoregion {facts['eco1']} over {facts['share1']} of it, "
            f"ecoregion {facts['eco2']} over {facts['share2']}"
        )
    land_pts = round(facts["land"] * 25)
    lake_pts = round(facts["lake"] * 25)
    sea_pts = round(facts["sea"] * 25)
    home_line = (
        f"The tile's new home adapts from the home {facts['home']}."
        if facts["adapt"]
        else f"The tile builds from the home {facts['home']}."
    )
    kinds = ", ".join(PART_KINDS)
    return (
        f"The square is centred at {abs(lat):.2f}° {ns}, {abs(lon):.2f}° {ew} "
        f"(latitude {lat}, longitude {lon} in decimals; about 25 km across). "
        f"Our survey of the square: habitat {facts['habitat']}, {eco}. "
        f"Land on {land_pts} of 25 sample points, of which lake on "
        f"{lake_pts}; sea on {sea_pts}. Mean elevation "
        f"{facts['elevation_m']} m; the game builds it at level "
        f"{facts['level']}, edges {facts['edge_lo']} to {facts['edge_hi']} "
        f"blocks. {home_line} "
        f"Name what's in the square in parts ({kinds}), the animals of "
        f"each part from the animal list, the square's plants and trees "
        f"from the plant and tree lists with how many of each grow "
        f"there, and the square's name in English and Finnish. Only "
        f"list plants and trees that really grow in the square; every "
        f"id must come from the lists.\n\n"
        f"Animals:\n{listing}\n\n"
        f"Plants:\n{greens or 'none'}\n\n"
        f"Trees:\n{woods or 'none'}"
    )


def _picks(picks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Survey picks in the model's order: ranking follows it, as animals."""
    return [{"id": p["id"], "count": p["count"]} for p in picks]


def decode_answer(
    content: str, ids: list[str], plant_ids: list[str], tree_ids: list[str]
) -> dict[str, Any]:
    """The answer off the wire, refused unless the schema takes it.

    The API enforces the id enums; this refuses them again, so a
    recording with an unknown id fails here, never in a tile. The
    model's order stands: ranking follows it.
    """
    try:
        data = json.loads(content)
    except ValueError as error:
        raise ValueError(f"the species answer is not JSON: {error}") from error
    return check_answer(data, ids, plant_ids, tree_ids)


def check_answer(
    data: Any, ids: list[str], plant_ids: list[str], tree_ids: list[str]
) -> dict[str, Any]:
    """A decoded survey, refused unless the schema takes it.

    The id enums refuse anything off the lists, live or recorded.
    `present` flattens the parts in the model's order: ranking
    follows it, as before.
    """
    try:
        jsonschema.validate(data, species_schema(ids, plant_ids, tree_ids))
    except jsonschema.ValidationError as error:
        raise ValueError(f"the species answer is refused: {error.message}") from error
    parts = data["parts"]
    if not parts:
        raise ValueError("the species answer is refused: no parts")
    present = list(dict.fromkeys(s for p in parts for s in p["species"]))
    return {
        "place": data["place"],
        "parts": [{"kind": p["kind"], "species": list(p["species"])} for p in parts],
        "plants": _picks(data["plants"]),
        "trees": _picks(data["trees"]),
        "present": present,
        "name_en": data["name_en"],
        "name_fi": data["name_fi"],
    }


def list_animals(world: Path) -> list[dict[str, Any]]:
    """Every animal on the list, by id: the model goes by id, not GBIF key."""
    from .animal_list import find_list, load_list

    return [e for e in load_list(find_list(world))["animals"] if e.get("id")]


def ask(
    client: Any,
    lat: float,
    lon: float,
    animals: list[dict[str, Any]],
    plants: list[dict[str, str]],
    trees: list[dict[str, str]],
    facts: dict[str, Any],
    note: str | None = None,
) -> dict[str, Any]:
    """One survey call through MetaClient, with its caps and logging.

    `note` carries a refusal back on the retry, so the second
    answer fixes the first one's fault instead of repeating it.
    """
    ids = [a["id"] for a in animals]
    plant_ids = [p["id"] for p in plants]
    tree_ids = [t["id"] for t in trees]
    prompt = user_message(lat, lon, animals, plants, trees, facts)
    if note is not None:
        prompt += (
            f"\n\nYour last answer was refused: {note}. Survey the "
            "square again, keeping to our facts."
        )
    response = client.chat(
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "species",
                "schema": species_schema(ids, plant_ids, tree_ids),
                "strict": True,
            },
        },
        max_tokens=MAX_TOKENS,
        reasoning_effort=EFFORT,
    )
    return decode_answer(response.choices[0].message.content, ids, plant_ids, tree_ids)


#: Words that read as sea, coast (either way), or land in a `place`.
SEA_WORDS = frozenset(
    {
        "sea",
        "seas",
        "ocean",
        "oceans",
        "bay",
        "bays",
        "gulf",
        "gulfs",
        "atlantic",
        "pacific",
        "indian",
        "arctic",
        "mediterranean",
        "caribbean",
        "baltic",
        "channel",
        "strait",
        "straits",
    }
)
COAST_WORDS = frozenset(
    {
        "coast",
        "coastal",
        "shore",
        "shores",
        "beach",
        "beaches",
        "peninsula",
        "peninsulas",
        "estuary",
        "island",
        "islands",
        "isle",
        "archipelago",
        "lagoon",
        "reef",
        "reefs",
        "atoll",
        "fjord",
        "delta",
    }
)
LAND_WORDS = frozenset(
    {
        "forest",
        "forests",
        "jungle",
        "rainforest",
        "desert",
        "deserts",
        "savanna",
        "savannah",
        "tundra",
        "taiga",
        "mountain",
        "mountains",
        "prairie",
        "prairies",
        "steppe",
        "farmland",
        "farm",
        "farms",
        "inland",
        "lake",
        "lakes",
        "river",
        "rivers",
        "marsh",
        "marshes",
        "swamp",
        "wetland",
        "wetlands",
        "grassland",
        "grasslands",
        "woodland",
        "plateau",
        "valley",
        "canyon",
        "ice",
        "sheet",
        "glacier",
        "park",
        "reserve",
        "city",
        "town",
        "village",
    }
)

#: Explicit continent phrases in a `place`, to the boxes they allow.
CONTINENT_PHRASES: tuple[tuple[str, frozenset[str]], ...] = (
    ("north america", frozenset({"north_america"})),
    ("south america", frozenset({"south_america"})),
    ("europe", frozenset({"europe"})),
    ("european", frozenset({"europe"})),
    ("africa", frozenset({"africa"})),
    ("african", frozenset({"africa"})),
    ("asia", frozenset({"asia"})),
    ("asian", frozenset({"asia"})),
    ("australia", frozenset({"australia"})),
    ("australian", frozenset({"australia"})),
    ("oceania", frozenset({"australia"})),
    ("antarctica", frozenset({"antarctica"})),
    ("antarctic", frozenset({"antarctica"})),
    ("america", frozenset({"north_america", "south_america"})),
    ("american", frozenset({"north_america", "south_america"})),
    ("americas", frozenset({"north_america", "south_america"})),
)

#: Coarse continent boxes (name, lat0, lat1, lon0, lon1), generous at
#: the seams: a border cell allows both sides. Mid-ocean matches
#: nothing, and the continent half then passes.
CONTINENT_BOXES: tuple[tuple[str, float, float, float, float], ...] = (
    ("north_america", 5.0, 73.0, -170.0, -50.0),
    ("south_america", -57.0, 13.0, -83.0, -33.0),
    ("europe", 35.0, 72.0, -26.0, 46.0),
    ("africa", -36.0, 38.0, -19.0, 53.0),
    ("asia", 4.0, 79.0, 25.0, 180.0),
    ("australia", -44.0, -9.0, 110.0, 155.0),
    ("antarctica", -90.0, -62.0, -180.0, 180.0),
)


def _words(place: str) -> set[str]:
    return set(re.findall(r"[a-z]+", place.lower()))


def check_place(place: str, habitat: str, lat: float, lon: float) -> str | None:
    """The answer's `place` against the grid: sea or land, the continent.

    Returns None when it reads as the same square, else why not. A
    cheap backstop for misread coordinates, not a gazetteer: a place
    naming no continent passes that half, and a land-to-land flip
    told in local names slips through.
    """
    words = _words(place)
    sea = bool(words & SEA_WORDS)
    coast = bool(words & COAST_WORDS)
    land = bool(words & LAND_WORDS)
    if habitat in ("sea", "open_ocean"):
        if not sea and not coast:
            return "a sea cell answered as land"
    elif sea and not land and not coast:
        return "a land cell answered as sea"
    lowered = place.lower()
    found: set[str] = set()
    for phrase, names in CONTINENT_PHRASES:
        if re.search(rf"\b{re.escape(phrase)}\b", lowered):
            found |= names
    if found:
        expected = {
            name
            for name, lat0, lat1, lon0, lon1 in CONTINENT_BOXES
            if lat0 <= lat <= lat1 and lon0 <= lon <= lon1
        }
        if expected and found.isdisjoint(expected):
            return "the continent is not this cell's"
    return None


def check_survey(
    answer: dict[str, Any],
    habitat: str,
    lat: float,
    lon: float,
    facts: dict[str, Any],
    animals: list[dict[str, Any]],
) -> str | None:
    """A checked survey against our facts: the place, parts, the name.

    Returns None when it reads as the same square, else why not.
    Water parts need water in the facts; the English name must not
    name an animal that isn't on the tile ("Seal and Swan Shores"
    with no seal or swan). The Finnish name is unchecked: the
    list has no Finnish names.
    """
    problem = check_place(answer["place"], habitat, lat, lon)
    if problem is not None:
        return problem
    for part in answer["parts"]:
        if part["kind"] in SEA_PARTS and facts["sea"] <= 0:
            return f"a {part['kind']} part with no sea in the square"
        if part["kind"] in LAKE_PARTS and facts["lake"] <= 0:
            return "a lake part with no lake in the square"
    return check_name(answer["name_en"], answer["present"], animals)


def _head_words(name: str) -> set[str]:
    """An animal name's kind: its last word ("Grey seal" -> seal).

    Modifiers ("sea" in "sea trout", "red" in "red fox") also
    paint seas and sunsets, so only the kind counts: a name may
    paint, never name a missing kind.
    """
    order = re.findall(r"[a-z]+", name.lower())
    return {order[-1]} if order else set()


def check_name(
    name_en: str, present: list[str], animals: list[dict[str, Any]]
) -> str | None:
    """The English name against the tile's animals: none named missing.

    Returns None when no absent animal's kind reads in the name,
    else the first missing one ("Seal and Swan Shores" with no
    seal). Word for word, so "seaside" never trips on "sea".
    """
    on_tile = set(present)
    present_words = {
        word
        for a in animals
        if a["id"] in on_tile
        for word in _head_words(str(a.get("name", "")))
    }
    named = _words(name_en)
    for a in animals:
        if a["id"] in on_tile:
            continue
        for word in _head_words(str(a.get("name", ""))):
            if len(word) >= 3 and word not in present_words and word in named:
                return f"names {word}, which is not on the tile"
    return None


def list_fingerprint(animals: list[dict[str, Any]]) -> str:
    """The animal list's short hash: a re-run calls only on change."""
    text = "\n".join(
        f"{a['id']}:{a.get('name', '')}:{a.get('latin', '')}" for a in animals
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class Source:
    """How a tile gets its species answer: cache, and a client for misses."""

    client: Any | None
    cache_dir: Path


def source_from_config(config: Any, builder_root: Path | None = None) -> Source:
    """The tile species source: the Meta client, the cache on disk.

    Without a key the client stays None: cached tiles still make,
    and a miss fails clearly instead of calling. The cache defaults
    under `data/` (git-ignored); the live worker keeps it on the
    jobs Volume.
    """
    from .schemas import ROOT as BUILDER_ROOT

    client = None
    if config.model_api_key:
        from .meta import MetaClient

        client = MetaClient(
            api_key=config.model_api_key,
            model=config.model,
            ledger=FileLedger(config.ledger_path),
            job_id="species",
            caps=Caps(
                job_usd=config.job_budget_usd,
                day_usd=config.daily_budget_usd,
                total_usd=config.total_budget_usd,
            ),
        )
    if config.species_cache_dir:
        cache_dir = Path(config.species_cache_dir)
    else:
        cache_dir = (builder_root or BUILDER_ROOT) / "data" / "species"
    return Source(client=client, cache_dir=cache_dir)


def cache_path(cache_dir: Path, tile: str) -> Path:
    """Where one tile's species answer lives."""
    return cache_dir / f"{tile}.json"


def read_cache(cache_dir: Path, tile: str) -> dict[str, Any] | None:
    """One cached survey, or None when there is none worth reading.

    Answers from before the survey (no `parts`) read as misses:
    the tile is surveyed again.
    """
    try:
        data = json.loads(cache_path(cache_dir, tile).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("place"), str) or not isinstance(
        data.get("parts"), list
    ):
        return None
    return data


def write_cache(
    cache_dir: Path,
    tile: str,
    answer: dict[str, Any],
    fingerprint: str,
    plants_print: str,
    facts_print: str,
    date: str,
) -> None:
    """Keep one checked survey: only checked surveys are kept."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path(cache_dir, tile).write_text(
        json.dumps(
            {
                "tile": tile,
                "place": answer["place"],
                "parts": answer["parts"],
                "plants": answer.get("plants", []),
                "trees": answer.get("trees", []),
                "name_en": answer.get("name_en"),
                "name_fi": answer.get("name_fi"),
                "model": MODEL,
                "date": date,
                "list": fingerprint,
                "plants_list": plants_print,
                "facts": facts_print,
            },
            indent=1,
        ),
        encoding="utf-8",
    )


def fetch_species(
    tile: str,
    animals: list[dict[str, Any]],
    plants: list[dict[str, str]],
    trees: list[dict[str, str]],
    habitat: str,
    lat: float,
    lon: float,
    source: Source,
    fingerprint: str,
    plants_print: str,
    facts: dict[str, Any],
) -> dict[str, Any]:
    """One tile's survey: the cache, else one call, else a retry.

    A cached survey is checked again (cheap, no network) and used
    only when the lists and the facts are unchanged. On a mismatch
    the call goes out once more, carrying the refusal; a second
    mismatch blocks the tile.
    """
    facts_print = facts_fingerprint(facts)
    cached = read_cache(source.cache_dir, tile)
    if (
        cached is not None
        and cached.get("list") == fingerprint
        and cached.get("plants_list") == plants_print
        and cached.get("facts") == facts_print
    ):
        try:
            answer = {
                "place": cached["place"],
                "parts": cached["parts"],
                "plants": cached.get("plants", []),
                "trees": cached.get("trees", []),
                "name_en": cached.get("name_en"),
                "name_fi": cached.get("name_fi"),
            }
            answer["present"] = list(
                dict.fromkeys(s for p in answer["parts"] for s in p["species"])
            )
            usable = (
                check_survey(answer, habitat, lat, lon, facts, animals) is None
                and answer["name_en"]
                and answer["name_fi"]
            )
        except (KeyError, TypeError, AttributeError):
            usable = False
        if usable:
            return {**answer, "date": cached.get("date", "unknown")}
    if source.client is None:
        raise ValueError(
            f"no usable cached species for {tile} and MODEL_API_KEY is not set"
        )
    first = ask(source.client, lat, lon, animals, plants, trees, facts)
    refused = check_survey(first, habitat, lat, lon, facts, animals)
    if refused is None:
        date = datetime.now(UTC).date().isoformat()
        write_cache(
            source.cache_dir, tile, first, fingerprint, plants_print, facts_print, date
        )
        return {**first, "date": date}
    second = ask(source.client, lat, lon, animals, plants, trees, facts, note=refused)
    problem = check_survey(second, habitat, lat, lon, facts, animals)
    if problem is None:
        date = datetime.now(UTC).date().isoformat()
        write_cache(
            source.cache_dir, tile, second, fingerprint, plants_print, facts_print, date
        )
        return {**second, "date": date}
    raise SpeciesPlaceError(tile, second["place"], problem)


def rank_weights(present: list[str]) -> dict[str, float]:
    """Model order as weights: first listed counts most, then by id.

    The picker ranks by count, so weights keep the model's order
    with the picker untouched.
    """
    total = len(present)
    return {species: float(total - rank) for rank, species in enumerate(present)}
