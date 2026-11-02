"""Planned resurvey rounds: ranking, delta, merge, dry run. No network."""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fake_store import FakeStore
from fake_world import boreal_3x3, make_world, write_grids

from builder.habitats import load_grid
from builder.resurvey import (
    first_seen,
    merge_parts,
    plan_round,
    play_by_tile,
    round_delta,
    run_round,
)


def _cached(
    tile: str = "z10_0581_0294",
    species: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    cached: dict[str, Any] = {
        "tile": tile,
        "place": "Boreal forest in southern Finland",
        "parts": [{"kind": "forest", "species": species or ["fox", "mountain_hare"]}],
        "plants": [],
        "trees": [],
        "outside_animals": [],
        "outside_plants": [],
        "name_en": "Fox woods",
        "name_fi": "Kettujen metsä",
        "model": "muse-spark-1.3",
        "date": "2026-10-20",
        "survey_round": 0,
        "plants_list": "pp",
        "facts": "ff",
    }
    return {**cached, **extra}


def test_play_counts_notes_on_tile_and_neighbours() -> None:
    """#196.5: last-30-days notes, priority fallback, +1 when ready."""
    today = date(2026, 11, 1)
    requests = [
        (
            "z10_0581_0294",
            {
                "tile": "z10_0581_0294",
                "notes_by_day": {"2026-10-30": 3, "2026-09-01": 9, "junk": 5},
            },
        ),
        ("z10_0582_0294", {"tile": "z10_0582_0294", "priority": 2}),
        ("n_414_478", {"tile": "n_414_478", "priority": 50}),
    ]
    areas = [("z10_0581_0294", {"status": "ready"}), ("z10_0580_0294", {})]
    play = play_by_tile(requests, areas, today)
    assert play["z10_0581_0294"] == 3 + 2 + 1
    assert play["z10_0582_0294"] == 3 + 2
    assert play["z10_0580_0294"] == 3
    assert "n_414_478" not in play


def test_first_seen_dates_ids_from_list_history(tmp_path: Path) -> None:
    """#196.5: one git log dates every id's arrival."""
    world = tmp_path / "world"
    world.mkdir()

    def commit(day: str, ids: list[str]) -> None:
        (world / "animal-list.json").write_text(
            json.dumps(
                {
                    "homes": [],
                    "not_yet": {},
                    "animals": [{"id": i} for i in ids],
                },
                indent=1,
            )
        )
        subprocess.run(
            ["git", "add", "animal-list.json"],
            cwd=world,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=t@t",
                "-c",
                "user.name=t",
                "commit",
                f"--date={day}T12:00:00",
                "-m",
                day,
            ],
            cwd=world,
            check=True,
            capture_output=True,
        )

    subprocess.run(["git", "init"], cwd=world, check=True, capture_output=True)
    commit("2026-10-01", ["fox", "vole"])
    commit("2026-10-15", ["fox", "vole", "lynx"])
    assert first_seen(world) == {
        "fox": "2026-10-01",
        "vole": "2026-10-01",
        "lynx": "2026-10-15",
    }


def test_round_delta_prefers_surveyed_ids() -> None:
    """#196.5: new caches diff exactly; merged ids stay out."""
    cached = _cached(surveyed_ids=["fox", "vole"], resurveyed=["lynx"])
    assert round_delta(cached, ["fox", "vole", "lynx", "wolverine"], {}) == [
        "wolverine"
    ]


def test_round_delta_falls_back_to_git_dates() -> None:
    """#196.5: old caches compare first-seen dates; unknowns count new."""
    cached = _cached()
    assert "surveyed_ids" not in cached
    seen = {"fox": "2026-10-01", "lynx": "2026-10-25"}
    assert round_delta(cached, ["fox", "lynx", "mystery"], seen) == ["lynx", "mystery"]


def test_merge_parts_appends_in_resurvey_order() -> None:
    """#196.5: new ids land after cached ones, new kinds as new parts."""
    merged, new = merge_parts(
        [{"kind": "forest", "species": ["fox"]}],
        [
            {"kind": "forest", "species": ["fox", "lynx"]},
            {"kind": "lake", "species": ["vole"]},
        ],
    )
    assert merged == [
        {"kind": "forest", "species": ["fox", "lynx"]},
        {"kind": "lake", "species": ["vole"]},
    ]
    assert new == ["lynx", "vole"]


def test_plan_picks_top_play_within_budget(tmp_path: Path) -> None:
    """#196.5: most-played first; the top and budget trim the rest."""
    world = make_world(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    for tile in ("z10_0581_0294", "z10_0582_0294"):
        (cache / f"{tile}.json").write_text(
            json.dumps(_cached(tile, surveyed_ids=["fox", "mountain_hare", "vole"]))
        )
    grid = load_grid(write_grids(tmp_path / "grid", boreal_3x3()))
    store = FakeStore()
    store.requests["z10_0581_0294"] = {
        "tile": "z10_0581_0294",
        "notes_by_day": {"2026-10-30": 4},
    }
    store.areas["z10_0581_0294"] = {"status": "ready"}
    plan = plan_round(
        cache_dir=cache,
        world=world,
        habitat_grid=grid,
        store=store,
        top=1,
        budget_usd=10.0,
        today=date(2026, 11, 1),
    )
    assert [t["tile"] for t in plan["tiles"]] == ["z10_0581_0294"]
    assert plan["tiles"][0]["new_ids"] == ["lion"]
    assert plan["calls"] == 1
    assert plan["price_usd"] == 0.05
    poor = plan_round(
        cache_dir=cache,
        world=world,
        habitat_grid=grid,
        store=store,
        top=2,
        budget_usd=0.04,
        today=date(2026, 11, 1),
    )
    assert poor["tiles"] == []
    assert poor["calls"] == 0
    assert {s["tile"] for s in poor["skipped"]} == {
        "z10_0581_0294",
        "z10_0582_0294",
    }


def test_run_merges_the_delta_and_keeps_the_date(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#196.5: the re-ask covers added ids; the cache gains them."""
    import builder.species as species_mod

    world = make_world(tmp_path)
    listed = json.loads((world / "animal-list.json").read_text())
    for entry in listed["animals"]:
        entry["name"] = entry["id"].replace("_", " ").title()
        entry["latin"] = f"Latinus {entry['id']}"
    (world / "animal-list.json").write_text(json.dumps(listed))
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "z10_0581_0294.json").write_text(
        json.dumps(_cached(surveyed_ids=["fox", "mountain_hare", "vole"]))
    )
    grid = load_grid(write_grids(tmp_path / "grid", boreal_3x3()))
    answer = {
        "place": "Boreal forest in southern Finland",
        "parts": [{"kind": "forest", "species": ["lion"]}],
        "plants": [],
        "trees": [],
        "outside_animals": [],
        "outside_plants": [],
        "name_en": "Lion woods",
        "name_fi": "Leijonien metsä",
    }

    class _Stub:
        calls = 0

        def chat(self, **kwargs: Any) -> Any:
            _Stub.calls += 1
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content=json.dumps(answer)))
                ]
            )

    monkeypatch.setattr(species_mod, "match_key", lambda name: None)
    results = run_round(
        planned=[{"tile": "z10_0581_0294", "new_ids": ["lion"]}],
        cache_dir=cache,
        world=world,
        habitat_grid=grid,
        client_for=lambda tile: _Stub(),
    )
    assert results == [{"tile": "z10_0581_0294", "merged": ["lion"]}]
    assert _Stub.calls == 1
    written = json.loads((cache / "z10_0581_0294.json").read_text())
    assert written["parts"] == [
        {"kind": "forest", "species": ["fox", "mountain_hare", "lion"]}
    ]
    assert written["date"] == "2026-10-20"
    assert written["resurveyed"] == ["lion"]
    assert written["surveyed_ids"] == ["fox", "mountain_hare", "vole", "lion"]


def test_resurvey_command_dry_runs_without_spending(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    """#196.5: the command reports tiles, calls and price, spending $0."""
    from builder.cli import main

    world = make_world(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "z10_0581_0294.json").write_text(
        json.dumps(_cached(surveyed_ids=["fox", "mountain_hare", "vole"]))
    )
    grid_dir = write_grids(tmp_path / "grid", boreal_3x3())
    store = FakeStore()
    store.requests["z10_0581_0294"] = {
        "tile": "z10_0581_0294",
        "notes_by_day": {"2026-10-30": 4},
    }
    monkeypatch.setattr("builder.worker_store.FirebaseStore", lambda *a, **k: store)
    code = main(
        [
            "tiles",
            "resurvey",
            "--world",
            str(world),
            "--top",
            "5",
            "--budget",
            "1.00",
            "--cache-dir",
            str(cache),
            "--habitat-grid",
            str(grid_dir),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "z10_0581_0294" in out
    assert "1 calls" in out
    assert "about $0.05" in out
    assert "dry run: spends nothing" in out
