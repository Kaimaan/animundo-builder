"""Stage 1, intake: no agent, just checks.

A park resolves its area from the index and writes `area.json`;
an animal home resolves its animal from the static list, checks
the cell still holds it, and writes `home.json`. Excluded ground
is refused either way.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..animal_list import find_entry, find_list, home_for, load_list
from ..schemas import ROOT, validate
from .species_plan import RecordedFn, recorded_near

TEMP_AREAS = Path(__file__).resolve().parent.parent / "data" / "areas_temp.json"
CELL_PATTERN = re.compile(r"^-?\d+\.\d_-?\d+\.\d$")


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
        cell: str | None = None,
        place_name: str | None = None,
        country_name: str | None = None,
        animal_list_path: str | Path | None = None,
        recorded: RecordedFn = None,
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
        self.cell = cell
        self.place_name = place_name
        self.country_name = country_name
        self.animal_list_path = (
            Path(animal_list_path) if animal_list_path is not None else None
        )
        self._recorded = recorded or recorded_near

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
        """Check the animal and cell; write `home.json` for later stages."""
        if ctx.world_path is None:
            raise ValueError("intake needs --world")
        world = Path(ctx.world_path)
        if self.cell is None or not CELL_PATTERN.match(self.cell):
            raise ValueError(f"bad cell, want like 61.2_26.9: {self.cell}")
        if self.place_name is None or not self.place_name.strip():
            raise ValueError("intake needs the card's place name")
        if self.country_name is None or not self.country_name.strip():
            raise ValueError("intake needs the card's country name")
        data = load_list(self.animal_list_path or find_list(world))
        entry = find_entry(data["animals"], self.species_key)
        if not entry.get("can_make", False):
            raise ValueError(f"{entry.get('latin')} cannot be made yet")
        home = home_for(entry, data["homes"])
        if self.country:
            self._check_excluded_country(self.country)
        south, west = (float(part) for part in self.cell.split("_"))
        center = {"lat": round(south + 0.05, 3), "lon": round(west + 0.05, 3)}
        if not self._recorded(center["lat"], center["lon"], self.species_key):
            raise ValueError(
                f"no {entry.get('latin')} recorded near {self.cell} anymore"
            )
        brief = {
            "species_key": self.species_key,
            "latin": entry.get("latin", ""),
            "name": entry.get("name", ""),
            "in_library": entry.get("in_library"),
            "start_from": entry.get("start_from", ""),
            "home": home,
            "cell": self.cell,
            "center": center,
            "country": self.country or "",
            "country_name": self.country_name,
            "place_name": self.place_name,
        }
        out = ctx.job_dir / "home.json"
        out.write_text(json.dumps(brief, indent=2), encoding="utf-8")
        return {"home_file": "home.json", "cell": self.cell, "home": home}

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
