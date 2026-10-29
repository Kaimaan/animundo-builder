"""The survey lab (#141.1): old answers against the new survey, side by side.

`builder survey lab` surveys the six tiles we have with the new
facts-first prompt and writes the answers next to the old ones
(`--old`, committed), with a comparison a person can read. Six
model calls, about $0.10. Open water is surveyed too, for the
comparison only: the planner makes no call there (#141.3).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .grid import GridName, center, parse_tile
from .habitats import HabitatGrid
from .species import (
    SpeciesPlaceError,
    check_name,
    check_place,
    check_survey,
    fetch_species,
    list_animals,
    list_fingerprint,
)
from .tiles import available_homes, home_for_cell, tile_facts

#: The six tiles we have: five border tiles and a Saimaa-side one.
TILES = (
    "n_414_478",
    "n_414_480",
    "n_415_479",
    "n_414_481",
    "n_416_482",
    "n_417_469",
)


def run_lab(
    tiles: list[str],
    grids: dict[GridName, HabitatGrid],
    world: Path,
    source: Any,
) -> list[dict[str, Any]]:
    """Survey each tile with the new prompt; failures carry their reason."""
    from .plan import nearest_home

    animals = list_animals(world)
    fingerprint = list_fingerprint(animals)
    homes = available_homes(world)
    results: list[dict[str, Any]] = []
    for tile in tiles:
        grid, col, row = parse_tile(tile)
        habitat = grids[grid].habitat(col, row)
        eco1 = grids[grid].cell_data(col, row)[0]
        home = home_for_cell(eco1, habitat, homes)
        if home is None:
            home = nearest_home(tile, grids, homes, habitat)
            adapt = True
        else:
            adapt = False
        facts = tile_facts(tile, grids, world, home, adapt)
        lat, lon = center(tile)
        try:
            survey = fetch_species(
                tile, animals, habitat, lat, lon, source, fingerprint, facts
            )
        except SpeciesPlaceError as error:
            results.append(
                {
                    "tile": tile,
                    "facts": facts,
                    "status": "blocked",
                    "detail": str(error),
                }
            )
            continue
        except ValueError as error:
            results.append(
                {"tile": tile, "facts": facts, "status": "error", "detail": str(error)}
            )
            continue
        problem = check_survey(survey, habitat, lat, lon, facts, animals)
        results.append(
            {
                "tile": tile,
                "facts": facts,
                "status": "ready",
                "survey": survey,
                "check": problem,
            }
        )
    return results


def _old_name_problem(old: dict[str, Any], animals: list[dict[str, Any]]) -> str | None:
    answer = old.get("answer", old)
    return check_name(answer["name_en"], answer["present"], animals)


def comparison(
    old: dict[str, dict[str, Any]],
    results: list[dict[str, Any]],
    animals: list[dict[str, Any]],
) -> str:
    """Old answers against new surveys, one section per tile."""
    lines = [
        "# The survey lab: old answers against the new survey",
        "",
        f"Run {datetime.now(UTC).date().isoformat()}: the old species call "
        "(lat/lon and the list) against the survey (our facts first), on "
        "the six tiles we have.",
        "",
    ]
    for entry in results:
        tile = entry["tile"]
        facts = entry["facts"]
        lines.append(f"## {tile}")
        lines.append("")
        lines.append(
            f"Facts: habitat {facts['habitat']}, ecoregion {facts['eco1']}, "
            f"land {round(facts['land'] * 25)} of 25 points, lake "
            f"{round(facts['lake'] * 25)}, sea {round(facts['sea'] * 25)}, "
            f"level {facts['level']}, edges {facts['edge_lo']} to "
            f"{facts['edge_hi']}, home {facts['home']}"
            f" ({'adapt from' if facts['adapt'] else 'build from'})."
        )
        lines.append("")
        before = old.get(tile, {}).get("answer", old.get(tile, {}))
        if before:
            lines.append(
                f"Old: {before.get('place')} — {before.get('name_en')} / "
                f"{before.get('name_fi')}, {len(before.get('present', []))} "
                "animals."
            )
            problem = _old_name_problem(old[tile], animals)
            lines.append(
                "Old name: "
                + (problem if problem is not None else "names nothing missing.")
            )
        else:
            lines.append("Old: no answer recorded.")
        lines.append("")
        if entry["status"] != "ready":
            lines.append(f"New: {entry['status']}: {entry.get('detail')}.")
            lines.append("")
            continue
        survey = entry["survey"]
        parts = ", ".join(f"{p['kind']} ({len(p['species'])})" for p in survey["parts"])
        lines.append(
            f"New: {survey['place']} — {survey['name_en']} / "
            f"{survey['name_fi']}, parts {parts}, find "
            f"{survey['find'] or 'none'}."
        )
        check = entry.get("check")
        lines.append("New checks: " + (check if check is not None else "all pass."))
        lines.append("")
    return "\n".join(lines)


def write_lab(
    out_dir: Path,
    old: dict[str, dict[str, Any]],
    results: list[dict[str, Any]],
    animals: list[dict[str, Any]],
) -> tuple[Path, Path]:
    """Write new.json and comparison.md for one survey lab run."""
    out_dir.mkdir(parents=True, exist_ok=True)
    new_path = out_dir / "new.json"
    new_path.write_text(
        json.dumps(
            {
                "at": datetime.now(UTC).isoformat(),
                "tiles": results,
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    comparison_path = out_dir / "comparison.md"
    comparison_path.write_text(comparison(old, results, animals), encoding="utf-8")
    return new_path, comparison_path


def old_name_problems(
    old: dict[str, dict[str, Any]], animals: list[dict[str, Any]]
) -> dict[str, str | None]:
    """Each old answer's name against its own animals, for the verdict."""
    return {tile: _old_name_problem(entry, animals) for tile, entry in old.items()}


def old_place_problems(
    old: dict[str, dict[str, Any]],
    grids: dict[GridName, HabitatGrid],
) -> dict[str, str | None]:
    """Each old answer's place against its cell, for the verdict."""
    problems: dict[str, str | None] = {}
    for tile, entry in old.items():
        answer = entry.get("answer", entry)
        grid, col, row = parse_tile(tile)
        habitat = grids[grid].habitat(col, row)
        lat, lon = center(tile)
        problems[tile] = check_place(answer["place"], habitat, lat, lon)
    return problems
