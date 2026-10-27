"""The web app's static contract: valid JSON, matching languages,
and every referenced string present."""

from __future__ import annotations

import json
import re
from pathlib import Path

from builder.schemas import ROOT

WEB = ROOT / "web" / "build"


def _json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def test_all_web_json_parses() -> None:
    files = sorted(WEB.rglob("*.json"))
    assert files
    for path in files:
        assert _json(path) is not None, path


def test_languages_match() -> None:
    en = _json(WEB / "i18n" / "en.json")
    fi = _json(WEB / "i18n" / "fi.json")
    assert set(en) == set(fi)
    assert all(isinstance(text, str) and text for text in en.values())


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


def test_app_uses_no_tile_service() -> None:
    app = (WEB / "app.js").read_text(encoding="utf-8")
    for banned in ("basemaps", "cartocdn", "tile.openstreetmap", "tileLayer"):
        assert banned not in app, banned
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
