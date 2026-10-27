"""Intake finds areas, refuses excluded ones, writes area.json."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from builder.schemas import validate
from builder.stages import IntakeStage
from builder.stages.intake import TEMP_AREAS

TEMP_IDS = ["fi_urho_kekkonen", "gb_cairngorms", "no_hardangervidda"]


def _ctx(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(job_dir=tmp_path)


def _write(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_temp_areas_all_validate() -> None:
    areas = json.loads(TEMP_AREAS.read_text(encoding="utf-8"))
    assert [a["area_id"] for a in areas] == TEMP_IDS
    for area in areas:
        validate("area", area)


@pytest.mark.parametrize("area_id", TEMP_IDS)
def test_intake_writes_area_json(tmp_path: Path, area_id: str) -> None:
    stage = IntakeStage(area_id=area_id)
    result = stage.run(_ctx(tmp_path))
    assert result["area_id"] == area_id
    written = json.loads((tmp_path / "area.json").read_text(encoding="utf-8"))
    assert written["area_id"] == area_id
    validate("area", written)


def test_unknown_area_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown area"):
        IntakeStage(area_id="xx_nowhere").run(_ctx(tmp_path))


def test_excluded_area_refused(tmp_path: Path) -> None:
    exclusions = _write(
        tmp_path / "exclusions.json",
        {"area_ids": ["gb_cairngorms"], "country_codes": []},
    )
    stage = IntakeStage(area_id="gb_cairngorms", exclusions_path=exclusions)
    with pytest.raises(ValueError, match="excluded"):
        stage.run(_ctx(tmp_path))


def test_excluded_country_refused(tmp_path: Path) -> None:
    exclusions = _write(
        tmp_path / "exclusions.json", {"area_ids": [], "country_codes": ["GB"]}
    )
    stage = IntakeStage(area_id="gb_cairngorms", exclusions_path=exclusions)
    with pytest.raises(ValueError, match="excluded"):
        stage.run(_ctx(tmp_path))


def test_find_by_name(tmp_path: Path) -> None:
    stage = IntakeStage(area_name="Cairngorms", country="GB")
    assert stage.run(_ctx(tmp_path))["area_id"] == "gb_cairngorms"


def test_find_by_name_needs_country_when_ambiguous(tmp_path: Path) -> None:
    areas = _write(
        tmp_path / "areas.json",
        [
            {
                "area_id": "aa_green",
                "name": "Green",
                "country": "AA",
                "centroid": {"lat": 1.0, "lon": 1.0},
            },
            {
                "area_id": "bb_green",
                "name": "Green",
                "country": "BB",
                "centroid": {"lat": 2.0, "lon": 2.0},
            },
        ],
    )
    with pytest.raises(ValueError, match="add --country"):
        IntakeStage(area_name="Green", areas_path=areas).run(_ctx(tmp_path))
    stage = IntakeStage(area_name="Green", country="BB", areas_path=areas)
    assert stage.run(_ctx(tmp_path))["area_id"] == "bb_green"


def test_needs_area_or_name() -> None:
    with pytest.raises(ValueError, match="--area, --name or a species key"):
        IntakeStage()


def test_resolve_writes_nothing(tmp_path: Path) -> None:
    area = IntakeStage(area_id="gb_cairngorms").resolve()
    assert area["name"] == "Cairngorms"
    assert list(tmp_path.iterdir()) == []
