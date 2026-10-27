"""The web app's static contract: valid JSON, English only,
and every referenced string present."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from builder.schemas import ROOT

WEB = ROOT / "web" / "build"


def _json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def test_all_web_json_parses() -> None:
    files = sorted(WEB.rglob("*.json"))
    assert files
    for path in files:
        assert _json(path) is not None, path


def test_english_only() -> None:
    assert [path.name for path in sorted((WEB / "i18n").glob("*.json"))] == ["en.json"]
    en = _json(WEB / "i18n" / "en.json")
    assert all(isinstance(text, str) and text for text in en.values())
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert "lang-fi" not in html and "animundoBuildLang" not in (
        WEB / "app.js"
    ).read_text(encoding="utf-8")


def test_html_strings_exist() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    keys = set(re.findall(r'data-i18n(?:-ph)?="([^"]+)"', html))
    assert keys
    en = _json(WEB / "i18n" / "en.json")
    assert keys <= set(en), keys - set(en)


def test_feed_strings_cover_the_worker() -> None:
    worker = (ROOT / "builder" / "worker.py").read_text(encoding="utf-8")
    needed = set(re.findall(r'"(explore\.feed\.[a-z_]+)"', worker))
    assert needed
    en = _json(WEB / "i18n" / "en.json")
    assert needed <= set(en), needed - set(en)


def test_emulator_ports_match_firebase_json() -> None:
    config = _json(ROOT / "firebase" / "firebase.json")["emulators"]
    app = (WEB / "app.js").read_text(encoding="utf-8")
    for service in ("auth", "firestore", "storage"):
        entry = config[service]
        assert str(entry["port"]) in app, service
        assert entry["host"] in app, service


def test_natural_earth_shapes_are_vendored() -> None:
    for name in ("ne_110m_land.geojson", "ne_110m_coastline.geojson"):
        data = _json(WEB / "vendor" / "natural-earth" / name)
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) > 100
    admin = _json(
        WEB / "vendor" / "natural-earth" / "ne_110m_admin_0_countries.geojson"
    )
    assert admin["type"] == "FeatureCollection"
    assert len(admin["features"]) > 100
    for feature in admin["features"]:
        assert set(feature["properties"]) == {"NAME", "ISO_A2"}, feature
    urban = _json(WEB / "vendor" / "natural-earth" / "ne_50m_urban_areas.geojson")
    assert urban["type"] == "FeatureCollection"
    assert len(urban["features"]) > 1000


def test_app_uses_only_gbif_tiles() -> None:
    app = (WEB / "app.js").read_text(encoding="utf-8")
    for banned in ("basemaps", "cartocdn", "tile.openstreetmap"):
        assert banned not in app, banned
    assert "api.gbif.org/v2/map/occurrence/density" in app
    assert "ne_110m_land.geojson" in app


def test_app_references_only_shipped_files() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    local = set(
        ref
        for ref in re.findall(r'(?:src|href)="([^"]+)"', html)
        if not ref.startswith(("http", "data:", "#", "/"))
    )
    assert local
    for ref in local:
        assert (WEB / ref).is_file(), ref


def test_nothing_shows_until_sign_in() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert re.search(r'<main id="app" hidden>', html)
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert '$("app").hidden = !user' in app


def test_js_strings_exist() -> None:
    app = (WEB / "app.js").read_text(encoding="utf-8")
    keys = set(re.findall(r'\bt\("([a-z0-9_.]+)"', app))
    assert keys
    en = _json(WEB / "i18n" / "en.json")
    assert keys <= set(en), keys - set(en)


def test_fetched_files_are_shipped() -> None:
    app = (WEB / "app.js").read_text(encoding="utf-8")
    refs = set(re.findall(r'fetch\("([^"]+)"\)', app))
    refs = {ref for ref in refs if not ref.startswith("http")}
    assert refs
    for ref in refs:
        assert (WEB / ref).is_file(), ref


def test_animal_list_shape() -> None:
    data = _json(WEB / "animal-list.json")
    assert set(data) == {"about", "habitats", "homes", "not_yet", "animals"}
    assert set(data["homes"]) <= set(data["habitats"])
    animals = data["animals"]
    assert len(animals) >= 200
    for animal in animals:
        assert set(animal) >= {
            "id",
            "name",
            "latin",
            "gbif_key",
            "start_from",
            "habitats",
            "can_make",
        }, animal
        assert isinstance(animal["gbif_key"], int) and animal["gbif_key"] > 0
        assert isinstance(animal["can_make"], bool)
        assert animal["name"] and animal["latin"]
        assert animal["habitats"] and set(animal["habitats"]) <= set(data["habitats"])
        if not animal["can_make"]:
            assert animal["not_yet"] in data["not_yet"], animal["id"]
    keys = [animal["gbif_key"] for animal in animals]
    assert len(set(keys)) == len(keys)
    ids = [animal["id"] for animal in animals]
    assert len(set(ids)) == len(ids)


def test_animal_list_matches_world() -> None:
    world = ROOT.parent / "animundo-world" / "animal-list.json"
    if not world.is_file():
        pytest.skip(f"needs {world}")
    assert _json(WEB / "animal-list.json") == _json(world)
