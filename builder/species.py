"""Tile species from Muse Spark (D18): one call per tile, cached.

The tile maker asks Muse Spark which of the animal list's animals
live wild in the tile's cell, instead of counting GBIF records.
The answer is checked against the habitat grid (sea or land, and
the continent) and cached per tile; a re-run calls the model only
when the animal list changes. Every call goes through MetaClient
with the Meta caps. GBIF stays for the T40 eval and the planner's
improve list, never for a tile's species.
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
    "You are a field biologist who knows where wild animals live today. "
    "For one 25 km square of land or sea and a list of animals, name every "
    "animal on the list with a wild population living in that square today: "
    "resident, breeding, or a regular seasonal visitor. Leave out zoo, farm "
    "and pet animals, rare strays, and animals whose range stops short of "
    "the square. The list names exact species (see the Latin names), not "
    "their relatives. Say what the square is in a few words. Give the "
    "square a short child-friendly name in English and in Finnish. If none "
    "live there, name none."
)


class SpeciesPlaceError(ValueError):
    """The species answer named somewhere else, twice. The tile blocks."""

    def __init__(self, tile: str, place: str, reason: str) -> None:
        super().__init__(f"{tile} answered as {place!r}: {reason}")
        self.tile = tile
        self.place = place
        self.reason = reason


def species_schema(ids: list[str]) -> dict[str, Any]:
    """The strict answer shape: a place, present ids, both names (#135.5)."""
    return {
        "type": "object",
        "properties": {
            "place": {
                "type": "string",
                "description": "What this square is, in a few words.",
            },
            "present": {"type": "array", "items": {"type": "string", "enum": ids}},
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
        "required": ["place", "present", "name_en", "name_fi"],
        "additionalProperties": False,
    }


def user_message(lat: float, lon: float, animals: list[dict[str, Any]]) -> str:
    """The square's centre both ways, and the animals as `id: name (Latin)`.

    Decimals alone once read 120 W as 120 E, so the hemisphere
    letters ride along.
    """
    ns, ew = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
    listing = "\n".join(f"- {a['id']}: {a['name']} ({a['latin']})" for a in animals)
    return (
        f"The square is centred at {abs(lat):.2f}° {ns}, {abs(lon):.2f}° {ew} "
        f"(latitude {lat}, longitude {lon} in decimals; about 25 km across). "
        f"Which of these animals live wild there today?\n\nAnimals:\n{listing}"
    )


def decode_answer(content: str, ids: list[str]) -> dict[str, Any]:
    """The answer off the wire, refused unless the schema takes it.

    The API enforces the id enum; this refuses it again, so a
    recording with an unknown id fails here, never in a tile. The
    model's order stands: ranking follows it.
    """
    try:
        data = json.loads(content)
    except ValueError as error:
        raise ValueError(f"the species answer is not JSON: {error}") from error
    return check_answer(data, ids)


def check_answer(data: Any, ids: list[str]) -> dict[str, Any]:
    """A decoded answer, refused unless the schema takes it.

    The id enum refuses anything off the list, live or recorded.
    The model's order stands: ranking follows it.
    """
    try:
        jsonschema.validate(data, species_schema(ids))
    except jsonschema.ValidationError as error:
        raise ValueError(f"the species answer is refused: {error.message}") from error
    return {
        "place": data["place"],
        "present": list(dict.fromkeys(data["present"])),
        "name_en": data["name_en"],
        "name_fi": data["name_fi"],
    }


def list_animals(world: Path) -> list[dict[str, Any]]:
    """Every animal on the list, by id: the model goes by id, not GBIF key."""
    from .animal_list import find_list, load_list

    return [e for e in load_list(find_list(world))["animals"] if e.get("id")]


def ask(
    client: Any, lat: float, lon: float, animals: list[dict[str, Any]]
) -> dict[str, Any]:
    """One species call through MetaClient, with its caps and logging."""
    ids = [a["id"] for a in animals]
    response = client.chat(
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user_message(lat, lon, animals)},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "species",
                "schema": species_schema(ids),
                "strict": True,
            },
        },
        max_tokens=MAX_TOKENS,
        reasoning_effort=EFFORT,
    )
    return decode_answer(response.choices[0].message.content, ids)


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
    """One cached answer, or None when there is none worth reading."""
    try:
        data = json.loads(cache_path(cache_dir, tile).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("place"), str) or not isinstance(
        data.get("present"), list
    ):
        return None
    return data


def write_cache(
    cache_dir: Path,
    tile: str,
    answer: dict[str, Any],
    fingerprint: str,
    date: str,
) -> None:
    """Keep one checked answer: only checked answers are kept."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path(cache_dir, tile).write_text(
        json.dumps(
            {
                "tile": tile,
                "place": answer["place"],
                "present": answer["present"],
                "name_en": answer.get("name_en"),
                "name_fi": answer.get("name_fi"),
                "model": MODEL,
                "date": date,
                "list": fingerprint,
            },
            indent=1,
        ),
        encoding="utf-8",
    )


def fetch_species(
    tile: str,
    animals: list[dict[str, Any]],
    habitat: str,
    lat: float,
    lon: float,
    source: Source,
    fingerprint: str,
) -> dict[str, Any]:
    """One tile's species answer: the cache, else one call, else a retry.

    A cached answer is checked again (cheap, no network) and used
    only when the list is unchanged. On a `place` mismatch the call
    goes out once more; a second mismatch blocks the tile.
    """
    cached = read_cache(source.cache_dir, tile)
    if cached is not None and cached.get("list") == fingerprint:
        answer = {
            "place": cached["place"],
            "present": list(cached["present"]),
            "name_en": cached.get("name_en"),
            "name_fi": cached.get("name_fi"),
        }
        if check_place(answer["place"], habitat, lat, lon) is None:
            return {**answer, "date": cached.get("date", "unknown")}
    if source.client is None:
        raise ValueError(
            f"no usable cached species for {tile} and MODEL_API_KEY is not set"
        )
    first = ask(source.client, lat, lon, animals)
    if check_place(first["place"], habitat, lat, lon) is None:
        date = datetime.now(UTC).date().isoformat()
        write_cache(source.cache_dir, tile, first, fingerprint, date)
        return {**first, "date": date}
    second = ask(source.client, lat, lon, animals)
    problem = check_place(second["place"], habitat, lat, lon)
    if problem is None:
        date = datetime.now(UTC).date().isoformat()
        write_cache(source.cache_dir, tile, second, fingerprint, date)
        return {**second, "date": date}
    raise SpeciesPlaceError(tile, second["place"], problem)


def rank_weights(present: list[str]) -> dict[str, float]:
    """Model order as weights: first listed counts most, then by id.

    The picker ranks by count, so weights keep the model's order
    with the picker untouched.
    """
    total = len(present)
    return {species: float(total - rank) for rank, species in enumerate(present)}
