"""A shipped plant joins its biome, seating it under its ground's budget (#214).

The biome stays the one source of a tile's plants: filing writes
the plant into `biomes/<biome>.json`, scaling the plants already
on the same ground down in proportion so the ground's total stays
at most 1.0, then flattens. A separate step remakes the biome's
published tiles by code ($0) so they grow the newcomer. Packs
keep carrying only the model.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Budgeted grounds, first match wins; anything else is open ground.
GROUNDS = ("reef", "bog")

#: Full ground, in thousandths: integer math keeps the total exact.
FULL = 1000


def plant_ground(grows: dict[str, Any]) -> str:
    """The budget ground a plant grows on: reef, bog, or open ground."""
    on = grows.get("on") or []
    for ground in GROUNDS:
        if ground in on:
            return ground
    return "open"


def plant_side(grows: dict[str, Any]) -> str:
    """The biome list a plant joins: reef and seabed grow in water (#184.1)."""
    on = grows.get("on") or []
    return "water" if "reef" in on or "seabed" in on else "cover"


def _spec(world: Path, plant_id: str) -> dict[str, Any]:
    path = world / "models" / "plants" / f"{plant_id}.json"
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError(f"{plant_id}: no plant spec at {path}") from None
    grows = spec.get("grows") if isinstance(spec, dict) else None
    if not isinstance(grows, dict):
        raise ValueError(f"{plant_id}: its spec names no grows")
    return grows


def _thousandths(density: Any) -> int:
    if not isinstance(density, (int, float)) or density <= 0:
        raise ValueError(f"density {density!r} is not above 0")
    return int(round(density * FULL))


def join_plant(
    world: Path, biome_id: str, plant_id: str, run_flatten: bool = True
) -> dict[str, Any]:
    """Seat a shipped plant in its biome, scaling its ground to fit (#214.1).

    The plant joins the biome's grown side at about the share of
    an average plant already on its ground (0.2 on empty reef
    rock, 0.1 elsewhere, as #184.3 seated them); when the
    ground would pass 1.0, the plants already there scale down
    in proportion. Bog and open ground follow the same rule.
    Then the world's flattener carries the biome into homes/.
    Returns the seating report. A ground already over budget,
    or the plant twice, stops loudly instead of guessing.
    """
    from .enrich import run_flatten as _flatten

    path = world / "biomes" / f"{biome_id}.json"
    try:
        biome = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError(f"{biome_id}: no biome at {path}") from None
    plants = biome.get("plants") or {}
    grows = _spec(world, plant_id)
    ground = plant_ground(grows)
    side = plant_side(grows)
    grown = plants.get(side)
    if not isinstance(grown, list):
        raise ValueError(f"{biome_id}: its plants have no {side} list")
    if any(e.get("id") == plant_id for e in grown if isinstance(e, dict)):
        raise ValueError(f"{plant_id} is already seated in {biome_id}")
    same = [
        e
        for e in grown
        if isinstance(e, dict)
        and e.get("id")
        and plant_ground(_spec(world, str(e["id"]))) == ground
    ]
    total = sum(_thousandths(e.get("density")) for e in same)
    if total > FULL:
        raise ValueError(
            f"{biome_id}: its {ground} is already over budget; a person looks"
        )
    if same:
        new_share = int(round(total / len(same)))
    else:
        new_share = 200 if ground == "reef" else 100
    scaled: dict[str, float] = {}
    if total + new_share > FULL:
        room = FULL - new_share
        marks = [
            int(round(_thousandths(e.get("density")) * room / total)) for e in same
        ]
        over = sum(marks) - room
        if over > 0:
            marks[marks.index(max(marks))] -= over
        for entry, mark in zip(same, marks):
            entry["density"] = mark / FULL
            scaled[str(entry["id"])] = mark / FULL
        total = sum(marks)
    grown.append({"id": plant_id, "density": new_share / FULL})
    path.write_text(json.dumps(biome, indent=2) + "\n", encoding="utf-8")
    flatten_out = _flatten(world) if run_flatten else ""
    return {
        "biome": biome_id,
        "plant": plant_id,
        "ground": ground,
        "side": side,
        "share": new_share / FULL,
        "scaled": scaled,
        "total": (total + new_share) / FULL,
        "flatten": flatten_out,
    }


def remake_biome_tiles(
    store: Any,
    world: Path,
    biome_id: str,
    plant_id: str,
    world_commit: str,
) -> dict[str, Any]:
    """The biome's published tiles grow the newcomer, by code ($0, #214.2).

    Every published tile on the biome takes the biome's own
    seated numbers for the plants it grows, plus the newcomer
    at its biome density (#221.4); the tile and the biome
    agree instead of the tile thinning its old densities.
    Validated and world-checked like any tile; tiles that
    fail for food stay published as they are and are reported
    held, never forced. Publishes as new versions with the
    pass's remake record. Returns versions and held tiles.
    """
    import copy

    from . import build as build_mod
    from .schemas import validate
    from .tiles import _budget_failure, _thin_densities, check_with_world

    path = world / "biomes" / f"{biome_id}.json"
    biome = json.loads(path.read_text(encoding="utf-8"))
    grows = _spec(world, plant_id)
    side = plant_side(grows)
    seated = _seated(biome, side, plant_id)
    numbers = {
        entry["id"]: entry["density"]
        for entry in (biome.get("plants") or {}).get(side, []) or []
        if isinstance(entry, dict) and entry.get("id")
    }
    raw, _ = store.read_blob("tiles/v2/index.json")
    if raw is None:
        raise ValueError("no published tile index to remake")
    index = json.loads(raw.decode("utf-8"))
    decisions: dict[str, build_mod.Remake] = {}
    held: list[str] = []
    for key in sorted(index["groups"]):
        blob = f"tiles/v2/{key}.json"
        data = json.loads(store.read_blob(blob)[0].decode("utf-8"))
        for tile in sorted((data.get("tiles") or {}).keys()):
            old = data["tiles"][tile]
            if old.get("home") != biome_id:
                continue
            grown = ((old.get("plants") or {}).get(side)) or []
            if any(p.get("id") == plant_id for p in grown if isinstance(p, dict)):
                continue
            made = copy.deepcopy(old)
            if any(not isinstance(p, dict) for p in grown):
                raise ValueError(f"{tile}: its {side} plants are no entries")
            seating = [
                {"id": pid, "density": numbers[pid]}
                for pid in (p.get("id") for p in grown)
                if pid in numbers
            ]
            seating += [dict(p) for p in grown if p.get("id") not in numbers]
            seating.append({"id": plant_id, "density": seated})
            made.setdefault("plants", {})[side] = seating
            needs = list((made.get("requires") or {}).get("plants", []))
            if plant_id not in needs:
                made.setdefault("requires", {}).setdefault("plants", []).append(
                    plant_id
                )
            validate("tile", made)
            failures = check_with_world(made, world)
            for _ in range(3):
                if not failures:
                    break
                if all(check == "food" for check, _ in failures):
                    failures = [("held", [])]
                    break
                if not _budget_failure(failures):
                    failures = [("held", [])]
                    break
                _thin_densities(made)
                failures = check_with_world(made, world)
            if failures:
                held.append(tile)
                continue
            made["version"] = int(old.get("version", 0)) + 1
            decisions[tile] = ("tile", made)
    versions = (
        build_mod.publish_remakes(store, decisions, world_commit) if decisions else {}
    )
    return {"versions": versions, "held": held}


def _seated(biome: dict[str, Any], side: str, plant_id: str) -> float:
    for entry in (biome.get("plants") or {}).get(side, []) or []:
        if isinstance(entry, dict) and entry.get("id") == plant_id:
            density = entry.get("density")
            if isinstance(density, (int, float)) and density > 0:
                return float(density)
    raise ValueError(f"{plant_id} is not seated in the biome; join it first")
