"""The web app's static contract: valid JSON, English only,
and every referenced string present."""

from __future__ import annotations

import json
import math
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


def test_no_map_code_ships() -> None:
    app = (WEB / "app.js").read_text(encoding="utf-8")
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for banned in (
        "leaflet",
        "L.map",
        "tileLayer",
        "geoJSON",
        "createPane",
        "fitBounds",
        "snapCell",
        "placeAt",
        "recordsNear",
        "onMapTap",
        "initMap",
        "armRange",
        "urbanAt",
        "countryAt",
        "ne_110m",
        "ne_50m_land",
        "ne_50m_coastline",
        "ne_10m",
        "v2/map/occurrence/density",
        "cell-card",
        "map-pane",
        "selectedCell",
    ):
        assert banned not in app, banned
        assert banned not in html, banned
    assert not (WEB / "vendor" / "leaflet").exists()
    urban = _json(WEB / "vendor" / "natural-earth" / "ne_50m_urban_areas.geojson")
    assert urban["type"] == "FeatureCollection"
    assert len(urban["features"]) > 1000
    assert "api.gbif.org/v1/occurrence/search" in app


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


def test_picker_waits_for_the_pick() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'id="place-picker" hidden' in html
    assert 'id="picked-card" hidden' in html
    assert 'id="animal-picker"' in html
    assert "openPicker()" in app and "ensurePickerData" in app
    # The big files load once, on the picker step, never at boot.
    assert app.count('fetch("places-of-interest.json")') == 1
    assert app.count('fetch("animal-places.json")') == 1
    assert "geoDistance" in app and "liveCount" in app


def test_your_animals_is_the_start_page() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    en = _json(WEB / "i18n" / "en.json")
    assert 'id="animals-pane"' in html
    assert 'id="new-pane" hidden' in html
    assert 'id="animal-list"' in html
    assert en["animals.title"] == "Your animals"
    assert en["animals.new"] == "New animal"
    assert "renderAnimals()" in app
    assert "data-new" in app
    # With no drafts yet, straight to New animal (#92.1).
    assert 'showView("new")' in app
    assert "drafts.lede" not in en


def test_only_sighted_places_build() -> None:
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "MIN_SIGHTINGS = 5" in app
    assert "seen >= MIN_SIGHTINGS" in app
    assert "place.none_seen" in app


def test_country_never_asks_location() -> None:
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "geolocation" not in app
    assert "resolvedOptions().timeZone" in app
    assert "tz-country.json" in app
    assert 'collection("grownups")' in app


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
    # the safe lower-case place id lives only in the pack (#93) and
    # in the published card's place address (#92.3).
    assert "?draft=${encodeURIComponent(draft.id)}" in app
    assert app.count("draft_place_id") == 2


def test_published_drafts_play_as_places() -> None:
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "playUrl(draft)" in app
    assert "?place=${encodeURIComponent" in app
    assert "&animal=${encodeURIComponent(animal.id)}" in app
    en = _json(WEB / "i18n" / "en.json")
    assert en["draft.status.public"] == "In the game for everyone"


def test_emulator_play_stays_local() -> None:
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "gameBase()" in app and "cloudSuffix()" in app
    assert "location.origin" in app and "&cloud=emulator" in app
    assert "GAME_URL}?place" not in app and "GAME_URL}?draft" not in app


def test_app_fits_the_rules_bounds() -> None:
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "place.n.slice(0, 80)" in app
    assert 'name="species" maxlength="64"' in app


def test_places_of_interest_shape() -> None:
    data = _json(WEB / "places-of-interest.json")
    assert set(data) == {"about", "places"}
    places = data["places"]
    assert len(places) >= 10000
    kinds = {
        "park",
        "marine_area",
        "marine_park",
        "protected_area",
        "island",
        "lake",
        "mountain",
        "bay",
        "cape",
        "reef",
        "desert",
        "forest",
        "wetland",
        "glacier",
        "valley",
        "waterfall",
        "volcano",
        "peninsula",
        "delta",
    }
    by_id = {}
    for place in places:
        assert set(place) == {"i", "n", "f", "k", "c", "la", "lo", "a", "l"}, place
        assert re.fullmatch(r"Q\d+", place["i"]), place["i"]
        assert place["i"] not in by_id, place["i"]
        by_id[place["i"]] = place
        assert place["n"] and place["k"] in kinds
        assert -90 <= place["la"] <= 90 and -180 <= place["lo"] <= 180
        assert place["l"] >= 1
    assert by_id["Q1815268"]["n"] == "Nuuksio National Park"
    assert by_id["Q192770"]["n"] == "Saimaa"
    assert by_id["Q41858"]["n"] == "Banff National Park"
    assert by_id["Q41858"]["c"] == "CA"
    assert by_id["Q547012"]["c"] is None  # Cape Crozier, Antarctica


def test_animal_places_shape() -> None:
    animals = _json(WEB / "animal-list.json")["animals"]
    interest = {
        place["i"]: place for place in _json(WEB / "places-of-interest.json")["places"]
    }
    data = _json(WEB / "animal-places.json")
    keyed = [animal for animal in animals if isinstance(animal.get("gbif_key"), int)]
    assert len(keyed) >= 200
    for animal in keyed:
        assert animal["id"] in data, animal["id"]
    for animal_id, rows in data.items():
        last = math.inf
        for qid, seen in rows:
            assert qid in interest, (animal_id, qid)
            assert seen >= 5, (animal_id, qid, seen)
            rank = math.log(seen + 1) * interest[qid]["l"]
            assert rank <= last, (animal_id, qid)
            last = rank
    assert "Q192770" in [qid for qid, _ in data["moose"]]
    assert "Q41858" in [qid for qid, _ in data["moose"]]


def test_timezone_country_and_alias_tables() -> None:
    zones = _json(WEB / "tz-country.json")["zones"]
    assert len(zones) > 200
    assert zones["Europe/Helsinki"] == "FI"
    assert zones["America/Toronto"] == "CA"
    names = _json(WEB / "country-names.json")["names"]
    assert len(names) > 100
    assert names["FI"] == "Finland"
    assert names["CA"] == "Canada"
    aliases = _json(WEB / "place-aliases.json")["aliases"]
    assert aliases == {"Q192770": "fi_saimaa"}


def test_improve_other_sends_species_when_named() -> None:
    app = (WEB / "app.js").read_text(encoding="utf-8")
    # "Something else" routes by the animal too, when one is named;
    # only "looks wrong" requires it (#82.6).
    assert 'if (box.value === "animal_looks_wrong" || box.value === "other") {' in app
    assert 'if (box.value === "animal_looks_wrong" && !species) {' in app
