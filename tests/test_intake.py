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


FOX_ENTRY = {
    "id": "fox",
    "name": "Red fox",
    "latin": "Vulpes vulpes",
    "gbif_key": 5219243,
    "start_from": "fox",
    "habitats": ["boreal_forest"],
    "can_make": True,
    "in_library": "fox",
}

TEST_SQUARE = [
    [20.0, 60.0],
    [30.0, 60.0],
    [30.0, 70.0],
    [20.0, 70.0],
    [20.0, 60.0],
]

FAR_SQUARE = [
    [0.0, 0.0],
    [1.0, 0.0],
    [1.0, 1.0],
    [0.0, 1.0],
    [0.0, 0.0],
]


def _home_files(
    tmp_path: Path, entry: dict | None = None, square: list | None = None
) -> tuple[Path, Path]:
    animals = dict(FOX_ENTRY if entry is None else entry)
    list_path = _write(
        tmp_path / "animal-list.json",
        {
            "homes": ["boreal_forest"],
            "not_yet": {},
            "animals": [animals],
        },
    )
    shapes_path = _write(
        tmp_path / "admin.geojson",
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"NAME": "Testland", "ISO_A2": "TT"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [square or TEST_SQUARE],
                    },
                }
            ],
        },
    )
    return list_path, shapes_path


def _home_ctx(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(job_dir=tmp_path, world_path=tmp_path)


def _home_stage(tmp_path: Path, cell: str = "61.5_26.0", **over: object) -> IntakeStage:
    list_path, shapes_path = _home_files(
        tmp_path,
        over.pop("entry", None),
        over.pop("square", None),
    )
    return IntakeStage(
        species_key=5219243,
        cell=cell,
        animal_list_path=list_path,
        shapes_path=shapes_path,
        recorded=lambda lat, lon, key: True,
        **over,  # type: ignore[arg-type]
    )


def test_home_writes_brief_with_worker_name(tmp_path: Path) -> None:
    result = _home_stage(tmp_path).run(_home_ctx(tmp_path))
    assert result["home"] == "boreal_forest"
    brief = json.loads((tmp_path / "home.json").read_text(encoding="utf-8"))
    assert brief["place_name"] == "Fox forest, Testland"
    assert brief["country"] == "TT"
    assert brief["country_name"] == "Testland"
    assert brief["center"] == {"lat": 61.55, "lon": 26.05}
    assert brief["in_library"] == "fox"


def test_home_bad_cell_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="bad cell"):
        _home_stage(tmp_path, cell="here").run(_home_ctx(tmp_path))


def test_home_outside_country_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside any country"):
        _home_stage(tmp_path, square=FAR_SQUARE).run(_home_ctx(tmp_path))


def test_home_unmakable_refused(tmp_path: Path) -> None:
    entry = {**FOX_ENTRY, "can_make": False}
    with pytest.raises(ValueError, match="cannot be made yet"):
        _home_stage(tmp_path, entry=entry).run(_home_ctx(tmp_path))


def test_home_without_home_refused(tmp_path: Path) -> None:
    entry = {**FOX_ENTRY, "habitats": ["grassland"]}
    with pytest.raises(ValueError, match="no built home"):
        _home_stage(tmp_path, entry=entry).run(_home_ctx(tmp_path))


def test_home_unrecorded_refused(tmp_path: Path) -> None:
    list_path, shapes_path = _home_files(tmp_path)
    stage = IntakeStage(
        species_key=5219243,
        cell="61.5_26.0",
        animal_list_path=list_path,
        shapes_path=shapes_path,
        recorded=lambda lat, lon, key: False,
    )
    with pytest.raises(ValueError, match="recorded near"):
        stage.run(_home_ctx(tmp_path))


def test_home_excluded_country_refused(tmp_path: Path) -> None:
    exclusions = _write(
        tmp_path / "exclusions.json", {"area_ids": [], "country_codes": ["TT"]}
    )
    with pytest.raises(ValueError, match="country is excluded"):
        _home_stage(tmp_path, exclusions_path=exclusions).run(_home_ctx(tmp_path))
