"""The lab run: counts and the globe picture. No network."""

from __future__ import annotations

import json
from pathlib import Path

from builder.lab import SPOTS, counts, draw_globe, write_lab


def test_spots_cover_the_five_plus_continents() -> None:
    names = [name for name, _lat, _lon in SPOTS]
    for spot in ("nuuksio", "lapland", "amazon", "savanna", "coast"):
        assert spot in names
    assert len(names) >= 15
    assert len(set(names)) == len(names)


def test_counts_groups_by_reason() -> None:
    results = [
        {"spot": "a", "status": "ready"},
        {"spot": "b", "status": "blocked", "reason": "no_home"},
        {"spot": "c", "status": "blocked", "reason": "no_home"},
        {"spot": "d", "status": "blocked", "reason": "no_animal"},
    ]
    assert counts(results) == {"ready": 1, "blocked": {"no_home": 2, "no_animal": 1}}


def test_draw_globe_marks_cells() -> None:
    results = [
        {"spot": "nuuksio", "tile": "n_413_478", "status": "ready"},
        {
            "spot": "amazon",
            "tile": "s_014_270",
            "status": "blocked",
            "reason": "no_animal",
        },
    ]
    img = draw_globe(results)
    assert img.size == (1440, 720)
    colors = img.getcolors(maxcolors=1 << 20)
    assert colors is not None
    assert len(colors) > 5


def test_write_lab_writes_counts_and_globe(tmp_path: Path) -> None:
    results = [{"spot": "nuuksio", "tile": "n_413_478", "status": "ready"}]
    counts_path, globe_path = write_lab(tmp_path, results)
    data = json.loads(counts_path.read_text())
    assert data["counts"] == {"ready": 1, "blocked": {}}
    assert data["spots"] == results
    assert globe_path.is_file()
    assert globe_path.stat().st_size > 1000
