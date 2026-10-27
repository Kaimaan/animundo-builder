"""The admin panel's static contract: shipped files, matching ids,
the same Firebase project and emulator ports as the web app."""

from __future__ import annotations

import json
import re
from pathlib import Path

from builder.schemas import ROOT

ADMIN = ROOT / "web" / "admin"
BUILD = ROOT / "web" / "build"


def _json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def test_admin_files_are_shipped() -> None:
    for name in ("index.html", "app.js", "styles.css"):
        assert (ADMIN / name).is_file(), name


def test_app_references_only_shipped_files() -> None:
    html = (ADMIN / "index.html").read_text(encoding="utf-8")
    local = {
        ref
        for ref in re.findall(r'(?:src|href)="([^"]+)"', html)
        if not ref.startswith(("http", "data:", "#", "/"))
    }
    assert local
    for ref in local:
        assert (ADMIN / ref).is_file(), ref


def test_element_ids_exist() -> None:
    html = (ADMIN / "index.html").read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([^"]+)"', html))
    app = (ADMIN / "app.js").read_text(encoding="utf-8")
    needed = set(re.findall(r'\$\("([^"]+)"\)', app))
    assert needed
    assert needed <= ids, needed - ids


def test_nothing_shows_until_admin() -> None:
    html = (ADMIN / "index.html").read_text(encoding="utf-8")
    assert re.search(r'<main id="app" hidden>', html)
    app = (ADMIN / "app.js").read_text(encoding="utf-8")
    assert 'collection("admins").doc(user.uid).get()' in app


def test_same_project_as_web_app() -> None:
    admin = (ADMIN / "app.js").read_text(encoding="utf-8")
    build = (BUILD / "app.js").read_text(encoding="utf-8")
    for key in ("apiKey", "projectId", "storageBucket"):
        found = re.search(rf'{key}: "([^"]+)"', admin)
        assert found, key
        assert f'{key}: "{found.group(1)}"' in build, key


def test_emulator_ports_match_firebase_json() -> None:
    config = _json(ROOT / "firebase" / "firebase.json")["emulators"]
    app = (ADMIN / "app.js").read_text(encoding="utf-8")
    for service in ("auth", "firestore", "storage"):
        entry = config[service]
        assert str(entry["port"]) in app, service
        assert entry["host"] in app, service


def test_test_admin_matches_seed() -> None:
    app = (ADMIN / "app.js").read_text(encoding="utf-8")
    seed = (ROOT / "firebase" / "seed-emulator.mjs").read_text(encoding="utf-8")
    email = re.search(r'TEST_ADMIN = \{ email: "([^"]+)"', app)
    assert email
    assert email.group(1) in seed


def test_actions_match_rules() -> None:
    app = (ADMIN / "app.js").read_text(encoding="utf-8")
    asked = set(re.findall(r'sendAction\(draft\.id, "([a-z_]+)"', app))
    assert asked
    rules = (ROOT / "firebase" / "firestore.rules").read_text(encoding="utf-8")
    allowed = set(re.findall(r"'([a-z_]+)'", rules.split("admin_actions")[1]))
    assert asked <= allowed, asked - allowed


def test_firebase_key_is_allowlisted() -> None:
    allow = (ROOT / ".gitleaks.toml").read_text(encoding="utf-8")
    assert "web/admin/app" in allow
