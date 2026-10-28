"""D11 place list and suggestions, run by hand (inbox #87).

    python3 scripts/places_of_interest.py fetch    # Wikidata -> places-of-interest.json
    python3 scripts/places_of_interest.py suggest  # GBIF -> animal-places.json
    python3 scripts/places_of_interest.py tables   # tz + country names

Matches the reviewer's lab step for step (the SPARQL, the WILD
filter, MIN_SIGHTINGS, the sampling, the ranking); the lab is the
spec's read-only sketch, this is the builder's runnable copy.
Standard library only, no key. `fetch` takes minutes, `suggest`
takes hours (one page per top-10 country plus up to 40
confirmations per animal, 3 GBIF calls at a time); it writes
after every animal, so stopping and rerunning resumes.
"""

from __future__ import annotations

import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web" / "build"
PLACES = WEB / "places-of-interest.json"
ANIMAL_PLACES = WEB / "animal-places.json"
ANIMAL_LIST = WEB / "animal-list.json"
URBAN = WEB / "vendor" / "natural-earth" / "ne_50m_urban_areas.geojson"
COUNTRIES = WEB / "vendor" / "natural-earth" / "ne_110m_admin_0_countries.geojson"

UA = {"User-Agent": "animundo-builder-places/0.1 (https://animundo.app)"}
WILD = {
    "basisOfRecord": ["HUMAN_OBSERVATION", "MACHINE_OBSERVATION"],
    "year": "1996,2026",
    "hasCoordinate": "true",
    "occurrenceStatus": "PRESENT",
}
MIN_LINKS = 12  # well-known; parks and marine areas are kept whatever their count
MIN_SIGHTINGS = 5
# kind: (Wikidata class, also its subclasses?). Parks need subclasses
# ("national park of Finland").
KINDS = {
    "park": ("Q46169", True),
    "marine_area": ("Q1367500", True),
    "marine_park": ("Q1185274", True),
    "protected_area": ("Q473972", False),
    "island": ("Q23442", False),
    "lake": ("Q23397", False),
    "mountain": ("Q8502", False),
    "bay": ("Q39594", False),
    "cape": ("Q185113", False),
    "reef": ("Q184358", False),
    "desert": ("Q8514", False),
    "forest": ("Q4421", False),
    "wetland": ("Q170321", False),
    "glacier": ("Q35666", False),
    "valley": ("Q39816", False),
    "waterfall": ("Q34038", False),
    "volcano": ("Q8072", False),
    "peninsula": ("Q34763", False),
    "delta": ("Q43197", False),
}
ARCHAEOLOGICAL_SITE = "Q839954"
ZONE1970_URL = "https://raw.githubusercontent.com/eggert/tz/main/zone1970.tab"


def get(url: str, params: dict | None = None) -> dict:
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={**UA, "Accept": "application/json"})
    for wait in (2, 5, 10, 20, 40, 80):
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code != 429:
                raise
            time.sleep(wait)
        except (urllib.error.URLError, OSError) as error:
            # A dropped connection on an hours-long run: wait it out.
            print(f"retrying after {error} ({url[:80]})", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError("rate limited")


def fetch() -> None:
    """Wikidata's well-known natural places, minus the ones in cities."""
    places: dict[str, dict] = {}
    for kind, (cls, sub) in KINDS.items():
        path = "wdt:P31/wdt:P279*" if sub else "wdt:P31"
        min_links = 1 if sub else MIN_LINKS
        query = f"""SELECT ?p ?pLabel ?fiLabel ?iso ?c ?area ?links WHERE {{
          ?p {path} wd:{cls}; wdt:P625 ?c; wikibase:sitelinks ?links.
          FILTER(?links >= {min_links})
          FILTER NOT EXISTS {{ ?p wdt:P31 wd:{ARCHAEOLOGICAL_SITE} }}
          OPTIONAL {{ ?p wdt:P17 ?country. ?country wdt:P297 ?iso. }}
          OPTIONAL {{ ?p wdt:P2046 ?area. }}
          OPTIONAL {{ ?p rdfs:label ?fiLabel FILTER(lang(?fiLabel) = "fi") }}
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
        }}"""
        rows = get(
            "https://query.wikidata.org/sparql", {"query": query, "format": "json"}
        )["results"]["bindings"]
        for row in rows:
            coords = row["c"]["value"]
            if not coords.startswith("Point("):
                continue  # coordinates on another planet
            qid = row["p"]["value"].rsplit("/", 1)[1]
            if qid in places or row["pLabel"]["value"] == qid:
                continue  # first kind wins; skip places with no English name
            lon, lat = coords[6:-1].split()
            area = row.get("area", {}).get("value")
            places[qid] = {
                "id": qid,
                "name": row["pLabel"]["value"],
                "fi": row.get("fiLabel", {}).get("value"),
                "kind": kind,
                "country": row.get("iso", {}).get("value"),
                "lat": float(lat),
                "lon": float(lon),
                "links": int(row["links"]["value"]),
                "area": float(area) if area is not None else None,
            }
        print(f"{kind}: {len(rows)}", file=sys.stderr)
        time.sleep(4)
    kept = _drop_cities(list(places.values()))
    small = [
        {
            "i": p["id"],
            "n": p["name"],
            "f": p["fi"],
            "k": p["kind"],
            "c": p["country"],
            "la": round(p["lat"], 4),
            "lo": round(p["lon"], 4),
            "a": round(p["area"], 1) if p["area"] is not None else None,
            "l": p["links"],
        }
        for p in kept
    ]
    PLACES.write_text(
        json.dumps(
            {
                "about": (
                    "Well-known natural places from Wikidata (CC0), for"
                    " the D11 picker. Written by"
                    " scripts/places_of_interest.py fetch; edit nothing"
                    " by hand except place-aliases.json next door."
                ),
                "places": small,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"{len(small)} places -> {PLACES}", file=sys.stderr)


def _drop_cities(places: list[dict]) -> list[dict]:
    """Places whose center falls inside a city never reach the picker."""
    boxes: list[tuple[float, float, float, float, list]] = []
    for feature in json.loads(URBAN.read_text(encoding="utf-8"))["features"]:
        geometry = feature.get("geometry") or {}
        polys = []
        if geometry.get("type") == "Polygon":
            polys = [geometry["coordinates"][0]]
        elif geometry.get("type") == "MultiPolygon":
            polys = [poly[0] for poly in geometry["coordinates"]]
        for ring in polys:
            lons = [pt[0] for pt in ring]
            lats = [pt[1] for pt in ring]
            boxes.append((min(lons), min(lats), max(lons), max(lats), ring))
    kept = []
    for place in places:
        if not any(
            west <= place["lon"] <= east
            and south <= place["lat"] <= north
            and _ring_has(ring, place["lon"], place["lat"])
            for west, south, east, north, ring in boxes
        ):
            kept.append(place)
    print(f"dropped {len(places) - len(kept)} places inside cities", file=sys.stderr)
    return kept


def _ring_has(ring: list[list[float]], lon: float, lat: float) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def radius_km(area: float | None) -> float:
    return 10 if not area or area <= 0 else max(5, min(50, math.sqrt(area / math.pi)))


def km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p = math.pi / 180
    return 6371 * math.hypot(
        (lon2 - lon1) * p * math.cos((lat1 + lat2) / 2 * p), (lat2 - lat1) * p
    )


def sample(
    key: int, country: str | None = None, pages: int = 4
) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    extra = {"country": country} if country else {}
    for i in range(pages):
        response = get(
            "https://api.gbif.org/v1/occurrence/search",
            {"taxonKey": key, "limit": 300, "offset": i * 300, **WILD, **extra},
        )
        pts += [
            (o["decimalLatitude"], o["decimalLongitude"]) for o in response["results"]
        ]
        if response["endOfRecords"]:
            break
    return pts


def sightings(key: int, lat: float, lon: float, area: float | None) -> int:
    return get(
        "https://api.gbif.org/v1/occurrence/search",
        {
            "taxonKey": key,
            "limit": 0,
            **WILD,
            "geoDistance": f"{lat},{lon},{radius_km(area):.0f}km",
        },
    )["count"]


def top_countries(key: int, limit: int = 10) -> list[str]:
    """The animal's top countries by wild sightings (GBIF facet)."""
    payload = get(
        "https://api.gbif.org/v1/occurrence/search",
        {"taxonKey": key, "limit": 0, "facet": "country", **WILD},
    )
    for facet in payload.get("facets", []):
        if facet.get("field") == "COUNTRY":
            return [bucket["name"] for bucket in facet.get("counts", [])[:limit]]
    return []


def rank(seen: int, links: int) -> float:
    return math.log(seen + 1) * links


def suggest(only: list[str] | None = None) -> None:
    """Confirmed places for every animal, by GBIF key (resumable)."""
    animals = json.loads(ANIMAL_LIST.read_text(encoding="utf-8"))["animals"]
    if only:
        animals = [a for a in animals if a.get("id") in only]
    places = [
        {
            "id": p["i"],
            "lat": p["la"],
            "lon": p["lo"],
            "area": p["a"],
            "links": p["l"],
            "country": p["c"],
        }
        for p in json.loads(PLACES.read_text(encoding="utf-8"))["places"]
    ]
    done: dict[str, list] = {}
    if ANIMAL_PLACES.is_file():
        done = json.loads(ANIMAL_PLACES.read_text(encoding="utf-8"))
    missing = [a for a in animals if a.get("id") not in done]
    print(
        f"{len(places)} places, {len(animals)} animals, {len(missing)} to do",
        file=sys.stderr,
    )
    for pos, animal in enumerate(missing):
        key = animal.get("gbif_key")
        if not isinstance(key, int):
            print(
                f"[{pos + 1}/{len(missing)}] {animal.get('id')}: no GBIF key, skipped",
                file=sys.stderr,
            )
            continue
        found = _suggest_one(key, places)
        done[animal["id"]] = [[p["id"], p["seen"]] for p in found]
        tmp = ANIMAL_PLACES.with_suffix(".tmp")
        tmp.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
        tmp.replace(ANIMAL_PLACES)
        print(
            f"[{pos + 1}/{len(missing)}] {animal['id']}: {len(found)} confirmed",
            file=sys.stderr,
        )
        time.sleep(5)  # stay under GBIF's rate limit across animals


def _suggest_one(key: int, places: list[dict]) -> list[dict]:
    """Confirmed places for one taxon; waits out rate limits."""
    while True:
        try:
            return _suggest_attempt(key, places)
        except RuntimeError as error:
            print(f"rate limited, waiting 2 minutes ({error})", file=sys.stderr)
            time.sleep(120)


def _suggest_attempt(key: int, places: list[dict]) -> list[dict]:
    pts = sample(key)
    for country in top_countries(key):
        pts += sample(key, country, pages=1)
    near = [
        p
        for p in places
        if any(
            abs(la - p["lat"]) < 1
            and km(p["lat"], p["lon"], la, lo) < radius_km(p["area"])
            for la, lo in pts
        )
    ]
    near = sorted(near, key=lambda p: -p["links"])[:40]
    with ThreadPoolExecutor(3) as pool:  # GBIF answers 429 above about 3 at a time
        counts = pool.map(lambda p: sightings(key, p["lat"], p["lon"], p["area"]), near)
        for place, n in zip(near, counts):
            place["seen"] = n
    return sorted(
        (p for p in near if p["seen"] >= MIN_SIGHTINGS),
        key=lambda p: -rank(p["seen"], p["links"]),
    )


def tables() -> None:
    """The time-zone table (IANA zone1970.tab) and country names."""
    req = urllib.request.Request(ZONE1970_URL, headers=UA)
    zones: dict[str, str] = {}
    with urllib.request.urlopen(req, timeout=120) as response:
        for line in response.read().decode("utf-8").splitlines():
            if not line or line.startswith("#"):
                continue
            cols = line.split()
            if len(cols) >= 3:
                zones[cols[2]] = cols[0].split(",")[0]
    (WEB / "tz-country.json").write_text(
        json.dumps(
            {
                "about": (
                    "Time zone to country, from the IANA zone1970.tab"
                    " (public domain). Written by"
                    " scripts/places_of_interest.py tables."
                ),
                "zones": zones,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    names: dict[str, str] = {}
    for feature in json.loads(COUNTRIES.read_text(encoding="utf-8"))["features"]:
        props = feature.get("properties", {})
        iso = props.get("ISO_A2", "")
        if iso and iso != "-99":
            names.setdefault(iso, props.get("NAME", ""))
    (WEB / "country-names.json").write_text(
        json.dumps(
            {
                "about": (
                    "ISO code to English country name, from the"
                    " vendored Natural Earth admin shapes. Written by"
                    " scripts/places_of_interest.py tables."
                ),
                "names": names,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"{len(zones)} zones, {len(names)} countries", file=sys.stderr)


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:]
    if cmd == "suggest":
        suggest(rest or None)
    else:
        {"fetch": fetch, "tables": tables}[cmd]()
