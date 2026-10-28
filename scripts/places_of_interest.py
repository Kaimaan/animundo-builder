"""Well-known natural places from Wikidata, run by hand.

    python3 scripts/places_of_interest.py fetch  # -> data/places-of-interest.json

Kept as data for phase 4 landmarks (T65). The grown-up picker's
suggest and tables commands were removed with the grown-up web app.
Standard library only, no key. `fetch` takes minutes.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PLACES = DATA / "places-of-interest.json"
URBAN = DATA / "vendor" / "natural-earth" / "ne_50m_urban_areas.geojson"

UA = {"User-Agent": "animundo-builder-places/0.1 (https://animundo.app)"}
MIN_LINKS = 12  # well-known; parks and marine areas are kept whatever their count
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
                    " phase 4 landmarks. Written by"
                    " scripts/places_of_interest.py fetch; edit nothing"
                    " by hand except data/place-aliases.json."
                ),
                "places": small,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"{len(small)} places -> {PLACES}", file=sys.stderr)


def _drop_cities(places: list[dict]) -> list[dict]:
    """Places whose center falls inside a city are dropped."""
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


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:]
    if cmd != "fetch" or rest:
        print("usage: python3 scripts/places_of_interest.py fetch", file=sys.stderr)
        raise SystemExit(2)
    fetch()
