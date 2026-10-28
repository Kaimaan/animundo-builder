"""Stage 1, intake: no agent, just checks.

A park resolves its area from the index and writes `area.json`;
an animal home resolves its animal from the static list, checks
the picked place of interest still holds it, and writes
`home.json`. Excluded ground is refused either way.
"""

from __future__ import annotations

import json
import math
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..animal_list import (
    find_entry,
    find_list,
    home_for,
    load_list,
    needs_home,
)
from ..schemas import ROOT, validate

TEMP_AREAS = Path(__file__).resolve().parent.parent / "data" / "areas_temp.json"
PLACES_OF_INTEREST = ROOT / "web" / "build" / "places-of-interest.json"
PLACE_ALIASES = ROOT / "web" / "build" / "place-aliases.json"
COUNTRY_NAMES = ROOT / "web" / "build" / "country-names.json"
WIKIDATA_PATTERN = re.compile(r"^Q\d+$")

#: At least this many wild sightings, or the place can't be built (#87.3).
MIN_SIGHTINGS = 5

#: One GBIF page of points for the region's center (#87.5).
SAMPLE_LIMIT = 300

GBIF_WILD = {
    "basisOfRecord": ["HUMAN_OBSERVATION", "MACHINE_OBSERVATION"],
    "year": "1996,2026",
    "hasCoordinate": "true",
    "occurrenceStatus": "PRESENT",
}
GBIF_TRIES = 3

#: (species key, lat, lon, radius km) -> wild sightings in the circle.
SightingsFn = Callable[[int, float, float, float], int]

#: (species key, lat, lon, radius km) -> sampled points in the circle.
SampleFn = Callable[[int, float, float, float], list[tuple[float, float]]]


def _gbif(params: dict[str, Any]) -> dict[str, Any]:
    """One GBIF occurrence call, retried; raises when it stays down."""
    query = urllib.parse.urlencode(params, doseq=True)
    last_error = ""
    for attempt in range(1, GBIF_TRIES + 1):
        try:
            url = f"https://api.gbif.org/v1/occurrence/search?{query}"
            with urllib.request.urlopen(url, timeout=20) as response:
                data = json.load(response)
                assert isinstance(data, dict)
                return data
        except (OSError, ValueError, AssertionError) as error:
            last_error = str(error)
            time.sleep(2.0**attempt)
    raise ValueError(f"GBIF did not answer ({last_error})")


def sightings_in(key: int, lat: float, lon: float, radius_km: float) -> int:
    """Wild sightings of the taxon inside the place's circle."""
    payload = _gbif(
        {
            "taxonKey": key,
            "limit": 0,
            **GBIF_WILD,
            "geoDistance": f"{lat},{lon},{radius_km:.0f}km",
        }
    )
    return int(payload.get("count", 0))


def sample_in(
    key: int, lat: float, lon: float, radius_km: float
) -> list[tuple[float, float]]:
    """One page of wild points inside the place's circle, for the center."""
    payload = _gbif(
        {
            "taxonKey": key,
            "limit": SAMPLE_LIMIT,
            **GBIF_WILD,
            "geoDistance": f"{lat},{lon},{radius_km:.0f}km",
        }
    )
    return [
        (float(row["decimalLatitude"]), float(row["decimalLongitude"]))
        for row in payload.get("results", [])
        if row.get("decimalLatitude") is not None
        and row.get("decimalLongitude") is not None
    ]


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle kilometres between two points."""
    p = math.pi / 180
    return 6371 * math.hypot(
        (lon2 - lon1) * p * math.cos((lat1 + lat2) / 2 * p), (lat2 - lat1) * p
    )


def place_radius_km(area: float | None) -> float:
    """The lab's circle: 10 km without an area, clamped to 5–50 km."""
    if not area or area <= 0:
        return 10
    return max(5, min(50, math.sqrt(area / math.pi)))


def slug(name: str) -> str:
    """The English name as an id stem: `Cape Crozier` -> `cape_crozier`."""
    plain = (
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    )
    stem = re.sub(r"[^a-z0-9]+", "_", plain.lower()).strip("_")
    return stem or "place"


class IntakeStage:
    """Resolve one area or one animal home into the job folder."""

    name = "intake"

    def __init__(
        self,
        area_id: str | None = None,
        area_name: str | None = None,
        country: str | None = None,
        areas_path: str | Path = TEMP_AREAS,
        exclusions_path: str | Path | None = None,
        species_key: int | None = None,
        place: dict[str, Any] | None = None,
        place_id: str | None = None,
        animal_list_path: str | Path | None = None,
        places_path: str | Path | None = None,
        aliases_path: str | Path | None = None,
        names_path: str | Path | None = None,
        sightings: SightingsFn | None = None,
        sample: SampleFn | None = None,
    ) -> None:
        if species_key is None and area_id is None and area_name is None:
            raise ValueError("intake needs --area, --name or a species key")
        self.area_id = area_id
        self.area_name = area_name
        self.country = country
        self.areas_path = Path(areas_path)
        if exclusions_path is not None:
            self.exclusions_path = Path(exclusions_path)
        else:
            self.exclusions_path = ROOT / "exclusions.json"
        self.species_key = species_key
        self.place = place
        self.place_id = place_id
        self.animal_list_path = (
            Path(animal_list_path) if animal_list_path is not None else None
        )
        self.places_path = Path(places_path or PLACES_OF_INTEREST)
        self.aliases_path = Path(aliases_path or PLACE_ALIASES)
        self.names_path = Path(names_path or COUNTRY_NAMES)
        self._sightings = sightings or sightings_in
        self._sample = sample or sample_in

    def resolve(self) -> dict[str, Any]:
        """Find and check the area without writing anything (`--dry-run`)."""
        areas = json.loads(self.areas_path.read_text(encoding="utf-8"))
        area = self._find(areas)
        self._check_excluded(area)
        validate("area", area)
        return area

    def run(self, ctx: Any) -> dict[str, Any]:
        if self.species_key is not None:
            return self._run_home(ctx)
        area = self.resolve()
        out = ctx.job_dir / "area.json"
        out.write_text(json.dumps(area, indent=2), encoding="utf-8")
        return {"area_id": area["area_id"], "area_file": "area.json"}

    def _run_home(self, ctx: Any) -> dict[str, Any]:
        """Check the animal and the picked place; write `home.json`.

        Everything about the place comes from the committed list,
        never from the client (#41): the client only names its
        Wikidata id.
        """
        if ctx.world_path is None:
            raise ValueError("intake needs --world")
        world = Path(ctx.world_path)
        wanted = (self.place or {}).get("wikidata_id")
        if not wanted or not WIKIDATA_PATTERN.match(wanted):
            raise ValueError(f"bad place, want a Wikidata id like Q1815268: {wanted}")
        data = load_list(self.animal_list_path or find_list(world))
        entry = find_entry(data["animals"], self.species_key)
        if not entry.get("can_make", False):
            raise ValueError(f"{entry.get('latin')} cannot be made yet")
        home = home_for(entry, data["homes"])
        listed = self._find_place(wanted)
        lat, lon = listed["la"], listed["lo"]
        radius = place_radius_km(listed["a"])
        seen = self._sightings(self.species_key, lat, lon, radius)
        if seen < MIN_SIGHTINGS:
            raise ValueError(
                f"only {seen} {entry.get('latin')} recorded in"
                f" {listed['n']} anymore; need {MIN_SIGHTINGS}"
            )
        country = (listed["c"] or ("aq" if lat < -60 else "int")).lower()
        self._check_excluded_country(country)
        center = self._center(self.species_key, lat, lon, radius)
        country_name = self._country_name(country)
        place_id = self._check_place(world, wanted, entry)
        brief = {
            "species_key": self.species_key,
            "id": entry.get("id", ""),
            "latin": entry.get("latin", ""),
            "name": entry.get("name", ""),
            "in_library": entry.get("in_library"),
            "start_from": entry.get("start_from", ""),
            "habitats": entry.get("habitats", []),
            "home": home,
            "needs_home": needs_home(entry, data["homes"]),
            "place": {
                "wikidata_id": wanted,
                "name": listed["n"],
                "fi": listed["f"],
                "kind": listed["k"],
                "country": listed["c"],
                "lat": lat,
                "lon": lon,
                "area": listed["a"],
                "radius_km": round(radius, 2),
            },
            "center": center,
            "country": country,
            "country_name": country_name,
            # The built place to reuse, if any; `home` later writes the
            # made place's id to `place_id`, so this keeps its own key.
            "reuse_place_id": place_id,
            # The id the made region gets: <country>_<slug> (#87.5).
            "new_place_id": f"{country}_{slug(listed['n'])}",
            # Just the place: the game adds the country itself
            # ("{place}, {country}"), so a country here doubles it (#64.4).
            "place_name": listed["n"],
        }
        out = ctx.job_dir / "home.json"
        out.write_text(json.dumps(brief, indent=2), encoding="utf-8")
        return {"home_file": "home.json", "place": wanted, "home": home}

    def _find_place(self, wanted: str) -> dict[str, Any]:
        """The list row for the picked Wikidata id, or a refusal."""
        rows = json.loads(self.places_path.read_text(encoding="utf-8"))["places"]
        for row in rows:
            if row["i"] == wanted:
                return row
        raise ValueError(f"unknown place: {wanted}")

    def _center(
        self, species_key: int, lat: float, lon: float, radius: float
    ) -> dict[str, float]:
        """Where the animal is most: the median of one sampled page.

        None are saved with the suggestions, so one GBIF page is read
        here; with no points in the circle, the place's own center.
        """
        points = [
            (pla, plo)
            for pla, plo in self._sample(species_key, lat, lon, radius)
            if abs(pla - lat) < 1 and _km(lat, lon, pla, plo) < radius
        ]
        if not points:
            return {"lat": round(lat, 3), "lon": round(lon, 3)}
        lats = sorted(pla for pla, _ in points)
        lons = sorted(plo for _, plo in points)
        mid = len(points) // 2
        if len(points) % 2:
            med_lat, med_lon = lats[mid], lons[mid]
        else:
            med_lat = (lats[mid - 1] + lats[mid]) / 2
            med_lon = (lons[mid - 1] + lons[mid]) / 2
        return {"lat": round(med_lat, 3), "lon": round(med_lon, 3)}

    def _country_name(self, country: str) -> str:
        """The English country name, or Antarctica, or nothing (#89.2)."""
        if country == "aq":
            return "Antarctica"
        if country == "int":
            return ""
        names = json.loads(self.names_path.read_text(encoding="utf-8"))["names"]
        return str(names.get(country.upper(), ""))

    def _check_place(
        self, world: Path, wanted: str, entry: dict[str, Any]
    ) -> str | None:
        """The built place the pick reuses, or None for a new one (#82.3).

        D11 keeps the list and drops the 25 km rule: a picked place
        is a built one when the Wikidata ids match (#87.6), or when
        the hand-kept aliases say so (#89.1). Intake rechecks from
        the world's regions, never from the client. A place that
        already has the animal playable is both-exist (#82.2) and
        must not arrive as a draft.
        """
        if not self.place_id:
            return None
        region_path = world / "regions" / f"{self.place_id}.json"
        if not region_path.is_file():
            raise ValueError(f"unknown place: {self.place_id}")
        region = json.loads(region_path.read_text(encoding="utf-8"))
        aliases = json.loads(self.aliases_path.read_text(encoding="utf-8"))["aliases"]
        if wanted in aliases:
            if aliases[wanted] != self.place_id:
                raise ValueError(
                    f"{wanted} is the built place {aliases[wanted]},"
                    f" not {self.place_id}"
                )
        elif region.get("wikidata") != wanted:
            raise ValueError(f"{self.place_id} is not {wanted} anymore")
        stem = entry.get("in_library") or entry.get("id", "")
        if stem and stem in (region.get("animals", {}).get("playable", []) or []):
            raise ValueError(
                f"{entry.get('latin')} is already playable in {self.place_id}"
            )
        return self.place_id

    def _find(self, areas: list[dict[str, Any]]) -> dict[str, Any]:
        if self.area_id is not None:
            for area in areas:
                if area["area_id"] == self.area_id:
                    return area
            raise ValueError(f"unknown area: {self.area_id}")
        assert self.area_name is not None
        wanted = self.area_name.strip().lower()
        matches = [a for a in areas if a["name"].lower() == wanted]
        if self.country is not None:
            matches = [a for a in matches if a["country"] == self.country.upper()]
        if not matches:
            raise ValueError(f"unknown area: {self.area_name}")
        if len(matches) > 1:
            raise ValueError(f"{self.area_name!r} matches several areas; add --country")
        return matches[0]

    def _check_excluded(self, area: dict[str, Any]) -> None:
        if not self.exclusions_path.is_file():
            return
        excluded = json.loads(self.exclusions_path.read_text(encoding="utf-8"))
        if area["area_id"] in excluded.get("area_ids", []):
            raise ValueError(f"excluded area, never built: {area['area_id']}")
        if area["country"] in excluded.get("country_codes", []):
            raise ValueError(f"country is excluded: {area['country']}")

    def _check_excluded_country(self, country: str) -> None:
        if not self.exclusions_path.is_file():
            return
        excluded = json.loads(self.exclusions_path.read_text(encoding="utf-8"))
        if country.upper() in excluded.get("country_codes", []):
            raise ValueError(f"country is excluded: {country}")
