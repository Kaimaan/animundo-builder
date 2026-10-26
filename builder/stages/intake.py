"""Stage 1, intake: no agent, just checks.

Finds the requested area in the index, refuses excluded areas, and writes
`area.json` for the survey stage. Until the real area index lands (T20),
the areas come from a hand-written temporary list.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..schemas import ROOT, validate

TEMP_AREAS = Path(__file__).resolve().parent.parent / "data" / "areas_temp.json"


class IntakeStage:
    """Resolve one area and write `area.json` into the job folder."""

    name = "intake"

    def __init__(
        self,
        area_id: str | None = None,
        area_name: str | None = None,
        country: str | None = None,
        areas_path: str | Path = TEMP_AREAS,
        exclusions_path: str | Path | None = None,
    ) -> None:
        if area_id is None and area_name is None:
            raise ValueError("intake needs --area or --name")
        self.area_id = area_id
        self.area_name = area_name
        self.country = country
        self.areas_path = Path(areas_path)
        if exclusions_path is not None:
            self.exclusions_path = Path(exclusions_path)
        else:
            self.exclusions_path = ROOT / "exclusions.json"

    def resolve(self) -> dict[str, Any]:
        """Find and check the area without writing anything (`--dry-run`)."""
        areas = json.loads(self.areas_path.read_text(encoding="utf-8"))
        area = self._find(areas)
        self._check_excluded(area)
        validate("area", area)
        return area

    def run(self, ctx: Any) -> dict[str, Any]:
        area = self.resolve()
        out = ctx.job_dir / "area.json"
        out.write_text(json.dumps(area, indent=2), encoding="utf-8")
        return {"area_id": area["area_id"], "area_file": "area.json"}

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
