"""The survey lab: old answers against new surveys, side by side. No network."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

from builder.habitats import HabitatGrid
from builder.survey_lab import TILES, comparison, run_lab, write_lab


def test_tiles_are_the_six_we_have() -> None:
    assert TILES == (
        "n_414_478",
        "n_414_480",
        "n_415_479",
        "n_414_481",
        "n_416_482",
        "n_417_469",
    )


def _old() -> dict[str, dict[str, Any]]:
    return {
        "n_413_478": {
            "answer": {
                "place": "Boreal forest in southern Finland",
                "present": ["fox"],
                "name_en": "Seal Forest",
                "name_fi": "Hyljemetsä",
            }
        }
    }


def _results() -> list[dict[str, Any]]:
    return [
        {
            "tile": "n_413_478",
            "facts": {
                "habitat": "boreal_forest",
                "eco1": 717,
                "land": 1.0,
                "lake": 0.0,
                "sea": 0.0,
                "level": 9,
                "edge_lo": 8,
                "edge_hi": 11,
                "home": "boreal_forest",
                "adapt": False,
            },
            "status": "ready",
            "survey": {
                "place": "Boreal forest in southern Finland",
                "parts": [{"kind": "forest", "species": ["fox"]}],
                "find": "bilberry",
                "name_en": "Nuuksio forest",
                "name_fi": "Nuuksion metsä",
            },
            "check": None,
        }
    ]


def _animals() -> list[dict[str, Any]]:
    return [
        {"id": "fox", "name": "Red fox", "latin": "Vulpes vulpes"},
        {"id": "grey_seal", "name": "Grey seal", "latin": "Halichoerus grypus"},
    ]


def test_comparison_reads_old_against_new() -> None:
    text = comparison(_old(), _results(), _animals())
    assert "## n_413_478" in text
    assert "Old: Boreal forest in southern Finland — Seal Forest" in text
    assert "Old name: names seal, which is not on the tile" in text
    assert "New: Boreal forest in southern Finland — Nuuksio forest" in text
    assert "parts forest (1), find bilberry" in text
    assert "New checks: all pass." in text


def test_write_lab_writes_new_and_comparison(tmp_path: Path) -> None:
    new_path, comparison_path = write_lab(tmp_path, _old(), _results(), _animals())
    data = json.loads(new_path.read_text())
    assert data["tiles"] == _results()
    assert "## n_413_478" in comparison_path.read_text()


def test_run_lab_surveys_each_tile(tmp_path: Path, monkeypatch: Any) -> None:
    """#141.1: the lab surveys with our facts; failures carry reasons."""
    import builder.survey_lab as lab_mod

    world = tmp_path / "world"
    (world / "homes").mkdir(parents=True)
    (world / "homes" / "boreal_forest.json").write_text(
        json.dumps({"habitat": "boreal_forest", "ecoregions": [717]})
    )
    (world / "animal-list.json").write_text(
        json.dumps(
            {
                "homes": ["boreal_forest"],
                "not_yet": {},
                "animals": [
                    {
                        "id": "fox",
                        "name": "Red fox",
                        "latin": "Vulpes vulpes",
                        "gbif_key": 1,
                        "habitats": ["boreal_forest"],
                    }
                ],
            }
        )
    )
    cell = [717, 1.0, None, 0.0, 6, 1.0, 0.0, 80.0]
    cells = {f"{c},{r}": list(cell) for r in (477, 478, 479) for c in (412, 413, 414)}
    path = tmp_path / "ease2_n25.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump({"grid": "ease2_n25", "cells": cells}, handle)
    grids = {"ease2_n25": HabitatGrid.load(path, "ease2_n25")}

    def fake_fetch(
        tile: str,
        animals: list[dict[str, Any]],
        habitat: str,
        lat: float,
        lon: float,
        source: Any,
        fingerprint: str,
        facts: dict[str, Any],
    ) -> dict[str, Any]:
        assert facts["home"] == "boreal_forest"
        assert facts["sea"] == 0.0
        return {
            "place": "Boreal forest in southern Finland",
            "parts": [{"kind": "forest", "species": ["fox"]}],
            "find": None,
            "present": ["fox"],
            "name_en": "Nuuksio forest",
            "name_fi": "Nuuksion metsä",
        }

    monkeypatch.setattr(lab_mod, "fetch_species", fake_fetch)
    results = run_lab(["n_413_478"], grids, world, source=None)  # type: ignore[arg-type]
    assert len(results) == 1
    assert results[0]["status"] == "ready"
    assert results[0]["check"] is None
    assert results[0]["survey"]["present"] == ["fox"]
