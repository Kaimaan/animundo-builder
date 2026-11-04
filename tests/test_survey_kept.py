"""New tiles keep what their survey found (#278)."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import pytest
from fake_store import FakeStore

from builder.config import Config
from builder.habitats import HabitatGrid
from builder.tiles import make_tile
from builder.worker import Worker


def _world(tmp_path: Path) -> Path:
    world = tmp_path / "world"
    (world / "homes").mkdir(parents=True)
    (world / "animals").mkdir(parents=True)
    (world / "homes" / "boreal_forest.json").write_text(
        json.dumps(
            {
                "habitat": "boreal_forest",
                "ecoregions": [717],
                "food": [{"kind": "grass"}, {"kind": "berries"}],
            }
        )
    )
    (world / "animals" / "fox.json").write_text(
        json.dumps(
            {
                "playable": True,
                "tags": ["predator"],
                "diet": ["berries"],
                "hunts": ["small_mammal"],
            }
        )
    )
    (world / "animals" / "herring_gull.json").write_text(
        json.dumps(
            {
                "playable": True,
                "tags": ["bird"],
                "diet": ["bottom_bugs"],
                "hunts": [],
            }
        )
    )
    (world / "animals" / "perch.json").write_text(
        json.dumps(
            {
                "playable": True,
                "tags": ["fish"],
                "diet": ["bottom_bugs"],
                "hunts": [],
                "archetype": "fish",
                "open_sea": True,
            }
        )
    )
    (world / "animal-list.json").write_text(
        json.dumps(
            {
                "homes": ["boreal_forest"],
                "not_yet": {},
                "animals": [
                    {
                        "id": "fox",
                        "gbif_key": 5219243,
                        "habitats": ["boreal_forest"],
                    },
                    {
                        "id": "herring_gull",
                        "gbif_key": 1,
                        "habitats": ["sea", "boreal_lake"],
                    },
                    {
                        "id": "perch",
                        "gbif_key": 2,
                        "habitats": ["boreal_lake", "river"],
                    },
                ],
            }
        )
    )
    return world


def _habitat_grid(tmp_path: Path, cells: dict[str, list[Any]]) -> HabitatGrid:
    path = tmp_path / "z10.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump({"grid": "z10", "cells": cells}, handle)
    return HabitatGrid.load(path)


def _dry_taiga() -> dict[str, list[Any]]:
    cell = [717, 1.0, None, 0.0, 6, 1.0, 0.0, 80.0]
    return {f"{c},{r}": list(cell) for r in (293, 294, 295) for c in (580, 581, 582)}


def test_wet_cell_takes_its_land_habitat() -> None:
    """#278.1: a lakeland cell also offers its forests' animals."""
    from builder.tiles import _candidate_habitats

    labels = _candidate_habitats("boreal_lake", 717, 6, 1.0, 0.8)
    assert {"boreal_lake", "boreal_forest"} <= labels
    dry = _candidate_habitats("boreal_forest", 717, 6, 1.0, 0.0)
    assert dry == {"boreal_forest"}


def test_lake_and_shore_parts_add_the_wet_habitat() -> None:
    """#278.2: lake and shore parts widen a land cell to its wet side."""
    from builder.tiles import _candidate_habitats

    lake = _candidate_habitats("boreal_forest", 717, 6, 1.0, 0.0, ("forest", "lake"))
    assert "boreal_lake" in lake
    shore = _candidate_habitats("boreal_forest", 717, 6, 1.0, 0.0, ("shore",))
    assert "boreal_lake" in shore
    assert "sea" not in shore


def test_river_part_adds_river() -> None:
    """#278.2: a river part widens a land cell to river animals."""
    from builder.tiles import _candidate_habitats

    labels = _candidate_habitats("boreal_forest", 717, 6, 1.0, 0.0, ("forest", "river"))
    assert "river" in labels
    assert "sea" not in labels


def test_town_with_a_river_part_adds_sea() -> None:
    """#278.2: town gulls come in through a lake, river or sea part."""
    from builder.tiles import _candidate_habitats

    river = _candidate_habitats("boreal_forest", 717, 6, 1.0, 0.0, ("town", "river"))
    assert "sea" in river
    lake = _candidate_habitats("boreal_forest", 717, 6, 1.0, 0.0, ("town", "lake"))
    assert "sea" in lake


def test_town_alone_adds_nothing() -> None:
    """#278.2: no gulls in every town; a dry town part widens nothing."""
    from builder.tiles import _candidate_habitats

    labels = _candidate_habitats("boreal_forest", 717, 6, 1.0, 0.0, ("forest", "town"))
    assert labels == {"boreal_forest"}


def test_shore_on_a_coast_adds_sea() -> None:
    """#278.2: a shore part on a coast widens to sea animals."""
    from builder.tiles import _candidate_habitats

    coast = _candidate_habitats("boreal_forest", 717, 6, 0.9, 0.0, ("shore",))
    assert "sea" in coast


def _live_world_and_grid() -> tuple[Path, Any]:
    from builder.habitats import load_grid
    from builder.schemas import ROOT

    world = ROOT.parent / "animundo-world"
    grid_dir = ROOT / "data" / "habitat-grid"
    if not (world / "animal-list.json").is_file():
        pytest.skip("needs the world checkout next to the builder")
    if not (grid_dir / "z10.json.gz").is_file():
        pytest.skip("needs the baked habitat grid")
    return world, load_grid(grid_dir)


def _recorded_survey(tile: str) -> dict[str, Any]:
    """The tile's survey, recorded from the Nov 1 cached answer."""
    from builder.schemas import ROOT

    path = ROOT / "tests" / "fixtures" / "species" / f"{tile}.json"
    return json.loads(path.read_text())


def _kept(made: dict[str, Any]) -> set[str]:
    species = made["species"]
    return set(species["playable"]) | set(species["prey"]) | set(species["predators"])


def test_taiga_replay_keeps_gull_perch_pike() -> None:
    """#278.4: z10_0583_0295 keeps its surveyed water animals."""
    from builder.schemas import ROOT

    world, habitat_grid = _live_world_and_grid()
    made, blocked = make_tile(
        "z10_0583_0295",
        habitat_grid,
        world,
        builder_root=ROOT,
        species=_recorded_survey("z10_0583_0295"),
    )
    assert blocked is None, blocked
    assert made is not None
    kept = _kept(made)
    for name in ("herring_gull", "perch", "pike"):
        assert name in kept


def test_saimaa_replay_keeps_forest_animals() -> None:
    """#278.4: z10_0592_0284 keeps its surveyed forest animals."""
    from builder.schemas import ROOT

    world, habitat_grid = _live_world_and_grid()
    made, blocked = make_tile(
        "z10_0592_0284",
        habitat_grid,
        world,
        builder_root=ROOT,
        species=_recorded_survey("z10_0592_0284"),
    )
    assert blocked is None, blocked
    assert made is not None
    kept = _kept(made)
    for name in ("fox", "mountain_hare", "squirrel", "great_tit"):
        assert name in kept


def test_dry_town_keeps_no_water_animals(tmp_path: Path) -> None:
    """#278.4: forest + town parts on a dry cell keep the gull out."""
    world = _world(tmp_path)
    made, blocked = make_tile(
        "z10_0581_0294",
        _habitat_grid(tmp_path, _dry_taiga()),
        world,
        builder_root=tmp_path,
        species={
            "place": "Dry taiga with a town",
            "parts": [
                {"kind": "forest", "species": ["fox"]},
                {"kind": "town", "species": ["fox", "herring_gull"]},
            ],
            "plants": [],
            "trees": [],
            "outside_animals": [],
            "outside_plants": [],
            "name_en": "Dry town",
            "name_fi": "Kuiva kaupunki",
        },
    )
    assert blocked is None, blocked
    assert made is not None
    assert "herring_gull" not in _kept(made)
    assert {
        "what": "herring_gull",
        "part": "town",
        "why": "habitat",
    } in made["survey_left_out"]


def test_land_cell_ignores_family_presence(tmp_path: Path, monkeypatch: Any) -> None:
    """#301.2: a land cell never seats a fish by family records."""
    import builder.presence as presence_mod

    calls: list[int] = []

    def fake_has_records(key: int, tile: str, span: int = 2) -> bool:
        calls.append(key)
        return True

    monkeypatch.setattr(presence_mod, "has_records_near", fake_has_records)
    world = _world(tmp_path)
    made, blocked = make_tile(
        "z10_0581_0294",
        _habitat_grid(tmp_path, _dry_taiga()),
        world,
        builder_root=tmp_path,
        species={
            "place": "Dry taiga with a town",
            "parts": [
                {"kind": "forest", "species": ["fox"]},
                {"kind": "town", "species": ["fox", "perch"]},
            ],
            "plants": [],
            "trees": [],
            "outside_animals": [],
            "outside_plants": [],
            "name_en": "Dry town",
            "name_fi": "Kuiva kaupunki",
        },
    )
    assert blocked is None, blocked
    assert made is not None
    assert "perch" not in _kept(made)
    assert {
        "what": "perch",
        "part": "town",
        "why": "habitat",
    } in made["survey_left_out"]
    assert calls == []


def test_merge_survey_left_out_dedupes() -> None:
    """#278.3: the job's list merges its tiles' lists, each once."""
    from builder.tiles import merge_survey_left_out

    entry = {"what": "a", "part": "town", "why": "habitat"}
    merged = merge_survey_left_out(
        {
            "t1": {"survey_left_out": [dict(entry)]},
            "t2": {
                "survey_left_out": [
                    dict(entry),
                    {"what": "b", "part": "lake", "why": "not_candidate"},
                ]
            },
            "t3": {},
        }
    )
    assert merged == [
        {"what": "a", "part": "town", "why": "habitat"},
        {"what": "b", "part": "lake", "why": "not_candidate"},
    ]


def test_job_doc_carries_survey_left_out(tmp_path: Path) -> None:
    """#278.3: a published tile's left-out list lands on the job doc."""
    store = FakeStore()
    worker = Worker(
        store=store,
        jobs_dir=tmp_path / "jobs",
        world=None,
        config=Config(ledger_path=str(tmp_path / "ledger.json")),
    )
    left_out = [{"what": "herring_gull", "part": "town", "why": "habitat"}]
    store.build_jobs["job-1"] = {
        "kind": "build",
        "status": "building",
        "worker_id": worker.worker_id,
        "tile": "z10_0581_0294",
        "tiles": ["z10_0581_0294"],
        "items": [],
    }
    worker._set_published(
        "job-1",
        store.build_jobs["job-1"],
        "z10_0581_0294",
        {"version": "v3", "survey_left_out": left_out},
    )
    assert store.build_jobs["job-1"]["survey_left_out"] == left_out
