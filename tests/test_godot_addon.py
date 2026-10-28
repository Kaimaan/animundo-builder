"""The game-side addon keeps its contract with the game."""

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
    "remove_content_folder",
    "register_model",
    "unregister_model",
    "set_draft_opener",
    "firestore_get",
    "firestore_patch",
    "storage_get",
]


def test_addon_files_exist():
    for rel in [
        "builder_mode.gd",
        "builder_cloud.gd",
        "pack_rules.gd",
        "plugin.cfg",
        "tests/check_draft_opener.gd",
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


def test_addon_has_no_place_list():
    # No areas.json travels with the addon.
    assert not (ADDON / "areas.json").exists()


def test_addon_adds_no_buttons():
    # Phase 0 (#116): the grown-up buttons are gone with the grown-up
    # builder. The addon registers the draft opener and nothing else.
    mode = (ADDON / "builder_mode.gd").read_text(encoding="utf-8")
    for gone in (
        "add_grownup_button",
        "add_places_button",
        "open_web_app",
        "WEB_BUILD",
        "set_explorer",
        "STRINGS_FOLDER",
    ):
        assert gone not in mode, f"still there: {gone}"
    assert not (ADDON / "locale").exists()


def test_addon_opens_previews_without_a_panel():
    # No Builder screen, no draft looks, no buttons: the draft opener
    # installs one pack for the session.
    mode = (ADDON / "builder_mode.gd").read_text(encoding="utf-8")
    cloud = (ADDON / "builder_cloud.gd").read_text(encoding="utf-8")
    for present in ("set_draft_opener", "open_draft", "pack_folder", "record_played"):
        assert present in mode or present in cloud, f"missing {present}"
    for gone in (
        "refresh_globe",
        "set_place_look",
        "open_builder",
        "family_drafts",
        "send_feedback",
    ):
        assert gone not in mode and gone not in cloud, f"still there: {gone}"
    assert not (ADDON / "explore_states.gd").exists()
    assert not (ADDON / "builder_strings.gd").exists()


def test_game_interface_documents_every_plugin_call():
    doc = (ROOT / "docs" / "game-interface.md").read_text(encoding="utf-8")
    for call in PLUGIN_CALLS:
        assert call in doc, f"{call} isn't in docs/game-interface.md"


def test_checks_report_and_quit():
    for name in (
        "check_draft_opener.gd",
        "check_content_packs.gd",
        "check_home_pack.gd",
    ):
        src = (ADDON / "tests" / name).read_text(encoding="utf-8")
        assert "errors" in src and "quit(" in src, f"{name} never reports"


def test_plugin_manifest_points_at_the_addon():
    cfg = (ADDON / "plugin.cfg").read_text(encoding="utf-8")
    assert 'name="Animundo Builder"' in cfg
    assert 'script="builder_mode.gd"' in cfg
