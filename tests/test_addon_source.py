"""The game addon's sources never gate on a res:// script file (#68).

An exported Godot 4 game compiles scripts: the .pck holds .gdc, never
.gd, so FileAccess.file_exists on a .gd path is false there and Builder
mode would never boot. ResourceLoader.exists follows the remap.
Godot can't export in CI cheaply, so a source rule stands in.
"""

from __future__ import annotations

import re

from builder.schemas import ROOT

ADDON = ROOT / "godot" / "addons" / "animundo_builder"
_SCRIPT_GATE = re.compile(r'FileAccess\.file_exists\(\s*"[^"]*\.gd')


def test_no_file_exists_on_script_paths() -> None:
    bad = []
    for path in sorted(ADDON.rglob("*.gd")):
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), 1):
            if _SCRIPT_GATE.search(line):
                bad.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert bad == []
