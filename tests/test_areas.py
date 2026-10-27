"""The short T20 area list: spec/09's milestone-1 places, in order."""

from __future__ import annotations

import json

from builder.schemas import ROOT, validate

AREAS = ROOT / "web" / "build" / "areas.json"

ORDER = [
    "fi_repovesi",
    "fi_urho_kekkonen",
    "gb_cairngorms",
    "no_hardangervidda",
    "pl_bialowieza",
    "ch_swiss_np",
    "no_naeroyfjord",
    "es_donana",
    "tz_serengeti",
    "no_svalbard",
    "au_ningaloo",
    "bd_sundarbans",
]


def _load() -> list:
    return json.loads(AREAS.read_text(encoding="utf-8"))


def test_areas_match_spec_order() -> None:
    assert [area["area_id"] for area in _load()] == ORDER


def test_every_area_validates_and_is_buildable() -> None:
    areas = _load()
    assert len({area["area_id"] for area in areas}) == len(areas)
    for area in areas:
        validate("area", area)
        assert area["buildable"] is True
        assert area["biome"]
        outline = area["outline"]
        assert len(outline) >= 4
        for lon, lat in outline:
            assert -180 <= lon <= 180
            assert -90 <= lat <= 90
        lons = [point[0] for point in outline]
        lats = [point[1] for point in outline]
        assert min(lons) <= area["centroid"]["lon"] <= max(lons)
        assert min(lats) <= area["centroid"]["lat"] <= max(lats)
