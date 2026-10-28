"""The web app's static contract: valid JSON, English only,
and every referenced string present."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
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


def _inside(ring: list[list[float]], lon: float, lat: float) -> bool:
    hits = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            hits = not hits
        j = i
    return hits


def _on_land(geo: dict, lon: float, lat: float) -> bool:
    for feature in geo["features"]:
        geom = feature["geometry"]
        coords = geom["coordinates"]
        polys = coords if geom["type"] == "MultiPolygon" else [coords]
        for poly in polys:
            if _inside(poly[0], lon, lat) and not any(
                _inside(hole, lon, lat) for hole in poly[1:]
            ):
                return True
    return False


def test_web_harness_drives_the_app() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    done = subprocess.run(
        [node, str(ROOT / "tests" / "web-harness.cjs")],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "harness: all checks passed" in done.stdout


def test_packed_drafts_play_in_the_game() -> None:
    app = (WEB / "app.js").read_text(encoding="utf-8")
    en = _json(WEB / "i18n" / "en.json")
    assert en["draft.play"] == "Play in the game"
    assert 'data-act="play"' in app
    assert "(draft.pack_version || 0) >= 1" in app
    assert "?draft=${encodeURIComponent(draft.id)}" in app
    assert '"_blank"' in app


def test_coastline_gains_detail_with_zoom() -> None:
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "updateBaseDetail" in app
    assert "zoomend" in app
    assert "zoom >= 6" in app and "ne_10m_land" in app
    assert "zoom >= 4" in app and "ne_50m_land" in app
    assert "ne_50m_coastline" in app
    for name in ("ne_50m_land.geojson", "ne_50m_coastline.geojson"):
        geo = _json(WEB / "vendor" / "natural-earth" / name)
        assert geo["type"] == "FeatureCollection"
        assert len(geo["features"]) > 1000
    land10 = _json(WEB / "vendor" / "natural-earth" / "ne_10m_land.geojson")
    assert land10["type"] == "FeatureCollection"
    assert _on_land(land10, 16.0, 78.2)  # Spitsbergen, Svalbard
    assert _on_land(land10, 24.95, 60.15)  # Helsinki
    assert not _on_land(land10, 10.0, 78.0)  # Fram Strait
    assert not _on_land(land10, 20.0, 59.0)  # Åland Sea


def test_map_waits_for_the_pick() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    en = _json(WEB / "i18n" / "en.json")
    assert 'id="map-pane" hidden' in html
    assert 'id="drafts" hidden' in html
    assert 'id="animal-picker"' in html
    assert "map-pane" in app and "initMap()" in app
    assert "drafts.lede" in en
    assert en["drafts.lede"] == "These are your drafts."
    assert "drafts.none" not in en
    assert "drafts.none" not in app


def test_drafts_show_from_sign_in_not_from_the_pick() -> None:
    app = (WEB / "app.js").read_text(encoding="utf-8")
    # Unhidden in the sign-in handler, before any animal is picked (#90).
    head = app.split("state.picked = animal")[0]
    assert '$("drafts").hidden = !user' in head
    # Every draft renders a card, and a packed one gets Play in the game.
    assert "renderDrafts()" in app
    assert "(draft.pack_version || 0) >= 1" in app
    assert 'data-act="play"' in app
    en = _json(WEB / "i18n" / "en.json")
    assert en["draft.play"] == "Play in the game"


def test_taps_only_pick_recorded_cells() -> None:
    app = (WEB / "app.js").read_text(encoding="utf-8")
    en = _json(WEB / "i18n" / "en.json")
    assert "recordsNear" in app
    assert "pickMark" in app and "L.rectangle" in app
    assert 't("home.outside"' not in app
    assert "home.outside" not in en
    assert "home.no_records" in en
    assert "We don't know where" in en["home.no_records"]


def test_map_snaps_taps_and_fits_the_range() -> None:
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "bin=hex" in app and "hexPerTile" in app
    assert "recordsNear" in app and "ringWithRecords" in app
    assert "fitBounds" in app and "has_coordinate=true" in app


def test_records_draw_above_the_land() -> None:
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'createPane("records")' in app
    assert "zIndex = 450" in app
    assert 'pane: "records"' in app
    assert 'pointerEvents = "none"' in app


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


def test_play_link_uses_the_firestore_draft_id() -> None:
    app = (WEB / "app.js").read_text(encoding="utf-8")
    # The address carries the draft's Firestore id, capitals and all;
    # the safe lower-case place id lives only in the pack (#93).
    assert "?draft=${encodeURIComponent(draft.id)}" in app
    assert "draft_place_id" not in app


def test_improve_other_sends_species_when_named() -> None:
    app = (WEB / "app.js").read_text(encoding="utf-8")
    # "Something else" routes by the animal too, when one is named;
    # only "looks wrong" requires it (#82.6).
    assert 'if (box.value === "animal_looks_wrong" || box.value === "other") {' in app
    assert 'if (box.value === "animal_looks_wrong" && !species) {' in app
