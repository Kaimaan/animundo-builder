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

#: Places-of-interest rows (the committed file's short keys): a park,
#: a country-less cape in Antarctica, a country-less sea, and a lake
#: the aliases call a built place.
TEST_PLACES = [
    {
        "i": "Q1",
        "n": "Test Park",
        "f": None,
        "k": "park",
        "c": "TT",
        "la": 61.5,
        "lo": 26.0,
        "a": 100.0,
        "l": 20,
    },
    {
        "i": "Q2",
        "n": "Cape Crozier",
        "f": None,
        "k": "cape",
        "c": None,
        "la": -77.5,
        "lo": 169.33,
        "a": None,
        "l": 21,
    },
    {
        "i": "Q3",
        "n": "South Atlantic",
        "f": None,
        "k": "marine_area",
        "c": None,
        "la": 0.0,
        "lo": -10.0,
        "a": 500000.0,
        "l": 15,
    },
    {
        "i": "Q9",
        "n": "Test Lake",
        "f": None,
        "k": "lake",
        "c": "TT",
        "la": 61.6,
        "lo": 26.1,
        "a": 50.0,
        "l": 30,
    },
]


def _home_files(tmp_path: Path, entry: dict | None = None) -> dict[str, Path]:
    animals = dict(FOX_ENTRY if entry is None else entry)
    return {
        "animal_list_path": _write(
            tmp_path / "animal-list.json",
            {
                "homes": ["boreal_forest"],
                "not_yet": {},
                "animals": [animals],
            },
        ),
        "places_path": _write(
            tmp_path / "places-of-interest.json", {"places": TEST_PLACES}
        ),
        "aliases_path": _write(
            tmp_path / "place-aliases.json", {"aliases": {"Q9": "tt_park"}}
        ),
        "names_path": _write(
            tmp_path / "country-names.json", {"names": {"TT": "Testland"}}
        ),
    }


def _home_ctx(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(job_dir=tmp_path, world_path=tmp_path)


def _home_stage(
    tmp_path: Path,
    qid: str = "Q1",
    seen: int = 12,
    points: list[tuple[float, float]] | None = None,
    **over: object,
) -> IntakeStage:
    files = _home_files(tmp_path, over.pop("entry", None))  # type: ignore[arg-type]
    return IntakeStage(
        species_key=5219243,
        place={"wikidata_id": qid, "name": "Client lies", "country": "XX"},
        animal_list_path=files["animal_list_path"],
        places_path=files["places_path"],
        aliases_path=files["aliases_path"],
        names_path=files["names_path"],
        sightings=lambda key, lat, lon, radius: seen,
        sample=lambda key, lat, lon, radius: list(points or []),
        **over,  # type: ignore[arg-type]
    )


def _brief(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "home.json").read_text(encoding="utf-8"))


def test_home_writes_brief_with_place_name(tmp_path: Path) -> None:
    result = _home_stage(tmp_path).run(_home_ctx(tmp_path))
    assert result["home"] == "boreal_forest"
    assert result["place"] == "Q1"
    brief = _brief(tmp_path)
    assert brief["place_name"] == "Test Park"
    assert brief["country"] == "tt"
    assert brief["country_name"] == "Testland"
    assert brief["center"] == {"lat": 61.5, "lon": 26.0}
    assert brief["in_library"] == "fox"
    assert brief["new_place_id"] == "tt_test_park"
    assert brief["place"] == {
        "wikidata_id": "Q1",
        "name": "Test Park",
        "fi": None,
        "kind": "park",
        "country": "TT",
        "lat": 61.5,
        "lon": 26.0,
        "area": 100.0,
        "radius_km": 5.64,
    }


def test_home_bad_place_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="bad place"):
        _home_stage(tmp_path, qid="here").run(_home_ctx(tmp_path))


def test_home_missing_place_refused(tmp_path: Path) -> None:
    files = _home_files(tmp_path)
    stage = IntakeStage(
        species_key=5219243,
        animal_list_path=files["animal_list_path"],
        places_path=files["places_path"],
        aliases_path=files["aliases_path"],
        names_path=files["names_path"],
        sightings=lambda key, lat, lon, radius: 12,
        sample=lambda key, lat, lon, radius: [],
    )
    with pytest.raises(ValueError, match="bad place"):
        stage.run(_home_ctx(tmp_path))


def test_home_unlisted_place_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown place: Q999"):
        _home_stage(tmp_path, qid="Q999").run(_home_ctx(tmp_path))


def test_home_unmakable_refused(tmp_path: Path) -> None:
    entry = {**FOX_ENTRY, "can_make": False}
    with pytest.raises(ValueError, match="cannot be made yet"):
        _home_stage(tmp_path, entry=entry).run(_home_ctx(tmp_path))


def test_home_without_home_is_flagged(tmp_path: Path) -> None:
    entry = {**FOX_ENTRY, "habitats": ["grassland"]}
    result = _home_stage(tmp_path, entry=entry).run(_home_ctx(tmp_path))
    assert result["home"] == "grassland"
    brief = _brief(tmp_path)
    assert brief["needs_home"] is True
    assert brief["habitats"] == ["grassland"]
    assert brief["place_name"] == "Test Park"


def test_home_too_few_recorded_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="need 5"):
        _home_stage(tmp_path, seen=3).run(_home_ctx(tmp_path))


def test_home_excluded_country_refused(tmp_path: Path) -> None:
    exclusions = _write(
        tmp_path / "exclusions.json", {"area_ids": [], "country_codes": ["TT"]}
    )
    with pytest.raises(ValueError, match="country is excluded"):
        _home_stage(tmp_path, exclusions_path=exclusions).run(_home_ctx(tmp_path))


def test_home_center_is_sample_median(tmp_path: Path) -> None:
    points = [(61.52, 26.0), (61.54, 26.0), (70.0, 26.0)]
    _home_stage(tmp_path, points=points).run(_home_ctx(tmp_path))
    assert _brief(tmp_path)["center"] == {"lat": 61.53, "lon": 26.0}


def _place(tmp_path: Path, region: dict, name: str = "tt_park") -> None:
    regions = tmp_path / "regions"
    regions.mkdir(exist_ok=True)
    (regions / f"{name}.json").write_text(json.dumps(region), encoding="utf-8")


def test_home_in_place_writes_place_id(tmp_path: Path) -> None:
    _place(
        tmp_path,
        {"wikidata": "Q1", "animals": {"playable": ["hare"]}},
    )
    result = _home_stage(tmp_path, place_id="tt_park").run(_home_ctx(tmp_path))
    assert result["place"] == "Q1"
    assert _brief(tmp_path)["reuse_place_id"] == "tt_park"


def test_home_without_place_writes_no_place_id(tmp_path: Path) -> None:
    _home_stage(tmp_path).run(_home_ctx(tmp_path))
    assert _brief(tmp_path)["reuse_place_id"] is None


def test_home_unknown_place_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown place"):
        _home_stage(tmp_path, place_id="xx_nowhere").run(_home_ctx(tmp_path))


def test_home_wikidata_mismatch_refused(tmp_path: Path) -> None:
    _place(tmp_path, {"wikidata": "Q2", "animals": {"playable": []}})
    with pytest.raises(ValueError, match="is not Q1 anymore"):
        _home_stage(tmp_path, place_id="tt_park").run(_home_ctx(tmp_path))


def test_home_alias_matches_built_place(tmp_path: Path) -> None:
    _place(tmp_path, {"wikidata": "Q1", "animals": {"playable": []}})
    _home_stage(tmp_path, qid="Q9", place_id="tt_park").run(_home_ctx(tmp_path))
    assert _brief(tmp_path)["reuse_place_id"] == "tt_park"


def test_home_alias_wrong_place_refused(tmp_path: Path) -> None:
    _place(tmp_path, {"wikidata": "Q1", "animals": {"playable": []}}, name="xx_other")
    with pytest.raises(ValueError, match="is the built place tt_park"):
        _home_stage(tmp_path, qid="Q9", place_id="xx_other").run(_home_ctx(tmp_path))


def test_home_already_playable_refused(tmp_path: Path) -> None:
    _place(tmp_path, {"wikidata": "Q1", "animals": {"playable": ["fox"]}})
    with pytest.raises(ValueError, match="already playable"):
        _home_stage(tmp_path, place_id="tt_park").run(_home_ctx(tmp_path))


def test_home_antarctic_place_makes_aq_id(tmp_path: Path) -> None:
    _home_stage(tmp_path, qid="Q2").run(_home_ctx(tmp_path))
    brief = _brief(tmp_path)
    assert brief["new_place_id"] == "aq_cape_crozier"
    assert brief["country"] == "aq"
    assert brief["country_name"] == "Antarctica"


def test_home_ocean_place_makes_int_id(tmp_path: Path) -> None:
    _home_stage(tmp_path, qid="Q3").run(_home_ctx(tmp_path))
    brief = _brief(tmp_path)
    assert brief["new_place_id"] == "int_south_atlantic"
    assert brief["country"] == "int"
    assert brief["country_name"] == ""


def test_home_real_saimaa_makes_fi_saimaa(tmp_path: Path) -> None:
    """Inbox 2026-10-28 #87.9: the intake for a picked place makes fi_saimaa."""
    files = _home_files(tmp_path)
    root = Path(__file__).resolve().parent.parent
    stage = IntakeStage(
        species_key=5219243,
        place={"wikidata_id": "Q192770"},
        animal_list_path=files["animal_list_path"],
        places_path=root / "web" / "build" / "places-of-interest.json",
        aliases_path=root / "web" / "build" / "place-aliases.json",
        names_path=root / "web" / "build" / "country-names.json",
        sightings=lambda key, lat, lon, radius: 12,
        sample=lambda key, lat, lon, radius: [],
    )
    stage.run(_home_ctx(tmp_path))
    brief = _brief(tmp_path)
    assert brief["new_place_id"] == "fi_saimaa"
    assert brief["country"] == "fi"
    assert brief["country_name"] == "Finland"
    assert brief["place_name"] == "Saimaa"


def test_home_real_nuuksio_reuse(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    web = root / "web" / "build"
    world = root.parent / "animundo-world"
    region_path = world / "regions" / "fi_nuuksio.json"
    list_path = world / "animal-list.json"
    if not region_path.is_file() or not list_path.is_file():
        pytest.skip(f"needs {world}")
    stage = IntakeStage(
        species_key=2435240,
        place={"wikidata_id": "Q1815268"},
        place_id="fi_nuuksio",
        animal_list_path=list_path,
        places_path=web / "places-of-interest.json",
        aliases_path=web / "place-aliases.json",
        names_path=web / "country-names.json",
        sightings=lambda key, lat, lon, radius: 12,
        sample=lambda key, lat, lon, radius: [],
    )
    job = tmp_path / "job"
    job.mkdir()
    ctx = SimpleNamespace(job_dir=job, world_path=world)
    result = stage.run(ctx)
    assert result["place"] == "Q1815268"
    brief = json.loads((job / "home.json").read_text(encoding="utf-8"))
    assert brief["id"] == "lynx"
    assert brief["reuse_place_id"] == "fi_nuuksio"
