"""The game-side addon keeps its contract with the game (T25, spec 06)."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "godot" / "addons" / "animundo_builder"

# The game's copy script (tools/sync_builder.sh there) takes only these.
SYNC_SUFFIXES = {
    ".gd",
    ".tscn",
    ".tres",
    ".json",
    ".png",
    ".svg",
    ".md",
    ".uid",
    ".cfg",
}

# Game classes the addon must never name directly: only BuilderHooks and the
# generic Cloud calls, reached through load() and injection (a bare Cloud
# would also match BuilderCloud, so the dot is part of the pattern).
FORBIDDEN_REFS = [
    "BuilderHooks",
    "Region",
    "Species",
    "Saves",
    "I18n",
    "AnimalModel",
    "Cloud",
]

PLUGIN_CALLS = [
    "add_content_folder",
    "register_model",
    "set_place_look",
    "add_grownup_button",
    "add_places_button",
    "firestore_query",
    "firestore_get",
    "firestore_patch",
    "storage_get",
]


def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_addon_files_exist():
    for rel in [
        "builder_mode.gd",
        "builder_cloud.gd",
        "pack_rules.gd",
        "explore_states.gd",
        "builder_strings.gd",
        "plugin.cfg",
        "areas.json",
        "locale/en.json",
        "locale/fi.json",
        "tests/check_explore_states.gd",
        "tests/check_content_packs.gd",
        "tests/check_home_pack.gd",
        "tests/fixture/fox-pack/manifest.json",
        "tests/fixture/fox-pack/regions/draft_review_fox.json",
        "tests/stubs/stub_cloud.gd",
        "tests/stubs/stub_hooks.gd",
    ]:
        assert (ADDON / rel).is_file(), f"missing {rel}"


def test_addon_files_pass_the_game_sync_filter():
    bad = [
        p.relative_to(ADDON).as_posix()
        for p in sorted(ADDON.rglob("*"))
        if p.is_file() and p.suffix not in SYNC_SUFFIXES
    ]
    assert bad == [], f"files the game doesn't take: {bad}"


def _code_lines(path):
    for line in path.read_text(encoding="utf-8").splitlines():
        yield line.split("#", 1)[0]


def test_addon_names_no_game_class_directly():
    offenders = []
    for path in sorted(ADDON.rglob("*.gd")):
        for lineno, line in enumerate(_code_lines(path), 1):
            if 'load("res://scripts/' in line:
                continue
            for name in FORBIDDEN_REFS:
                if re.search(rf"(?<![\w]){name}\.", line):
                    offenders.append(f"{path.relative_to(ADDON)}:{lineno}: {name}")
    assert offenders == [], f"direct game references: {offenders}"


def test_addon_areas_match_the_web_app():
    addon = [a["area_id"] for a in _load_json(ADDON / "areas.json")]
    web = [a["area_id"] for a in _load_json(ROOT / "web" / "build" / "areas.json")]
    assert addon == web


def test_addon_states_cover_every_draft_status():
    schema = _load_json(ROOT / "contracts" / "draft.schema.json")
    statuses = schema["properties"]["status"]["enum"]
    for lang in ("en", "fi"):
        words = _load_json(ADDON / "locale" / f"{lang}.json")
        for status in statuses:
            key = f"explore.state.{status}"
            assert words.get(key), f"{lang} has no {key}"


def test_addon_locales_match():
    en = _load_json(ADDON / "locale" / "en.json")
    fi = _load_json(ADDON / "locale" / "fi.json")
    assert set(en) == set(fi)


def test_addon_reasons_match_the_web_app_word_for_word():
    # The web app is English only during the event; the game keeps both.
    addon = _load_json(ADDON / "locale" / "en.json")
    web = _load_json(ROOT / "web" / "build" / "i18n" / "en.json")
    web_reasons = {k[7:]: v for k, v in web.items() if k.startswith("reason.")}
    addon_reasons = {
        k[15:]: v for k, v in addon.items() if k.startswith("explore.reason.")
    }
    assert addon_reasons
    assert addon_reasons == web_reasons


def test_game_interface_documents_every_plugin_call():
    doc = (ROOT / "docs" / "game-interface.md").read_text(encoding="utf-8")
    for call in PLUGIN_CALLS:
        assert call in doc, f"{call} isn't in docs/game-interface.md"


def test_checks_report_and_quit():
    for name in (
        "check_explore_states.gd",
        "check_content_packs.gd",
        "check_home_pack.gd",
    ):
        src = (ADDON / "tests" / name).read_text(encoding="utf-8")
        assert "errors" in src and "quit(" in src, f"{name} never reports"


def test_plugin_manifest_points_at_the_addon():
    cfg = (ADDON / "plugin.cfg").read_text(encoding="utf-8")
    assert 'name="Animundo Builder"' in cfg
    assert 'script="builder_mode.gd"' in cfg
