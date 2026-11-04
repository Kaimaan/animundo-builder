"""Planned resurvey rounds: new list animals join old tiles (#196.5).

A round picks the most-played cached tiles, asks the survey
again about library animals added since each tile's survey,
and merges the found ids into the cached answer in the
re-survey's order, so the next remake wants them. Ranking
mirrors the game's enrich ranking until T71 counts player-days.
"""

from __future__ import annotations

import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

from .grid import ID_PATTERN, center, neighbours, parse_tile
from .prices import SURVEY_ESTIMATE_USD

#: Border notes count for this many days back, like the game.
DAYS_KEPT = 30


def play_by_tile(
    requests: list[tuple[str, dict[str, Any]]],
    areas: list[tuple[str, dict[str, Any]]],
    today: date,
) -> dict[str, float]:
    """Each tile's play, mirroring the enrich ranking minus T71.

    Border notes on the tile and its neighbours in the last 30
    days (or the request's priority when it keeps no days),
    plus 1 for a ready area. Current-grid tiles only.
    """
    play: dict[str, float] = {}

    def add(tile: str, n: float) -> None:
        if n > 0:
            play[tile] = play.get(tile, 0.0) + n

    oldest = today.toordinal() - (DAYS_KEPT - 1)
    for doc_id, doc in requests:
        tile = doc.get("tile") or doc_id
        if not ID_PATTERN.match(tile):
            continue
        by_day = doc.get("notes_by_day") or {}
        recent = [n for day, n in by_day.items() if _day_ordinal(day) >= oldest]
        if recent:
            n = sum(float(x or 0) for x in recent)
        else:
            n = float(doc.get("priority") or 0)
        add(tile, n)
        for near in neighbours(tile).values():
            if near is not None:
                add(near, n)
    for doc_id, doc in areas:
        tile = doc.get("tile") or doc_id
        if ID_PATTERN.match(tile) and doc.get("status") == "ready":
            add(tile, 1)
    return play


def _day_ordinal(day: str) -> int:
    """A `YYYY-MM-DD` day as an ordinal; garbage reads as ancient."""
    try:
        year, month, day_no = (int(part) for part in str(day).split("-"))
        return date(year, month, day_no).toordinal()
    except (ValueError, TypeError):
        return 0


_ID_LINE = re.compile(r'^\+[^+].*"id"\s*:\s*"([^"]+)"')


def first_seen(world: Path) -> dict[str, str]:
    """Each list id's first date in the world's history (`YYYY-MM-DD`).

    One `git log` over `animal-list.json`, oldest first; the
    first commit adding an id's line dates it. Loud when git
    fails: a wrong map would resurvey the wrong animals.
    """
    try:
        out = subprocess.run(
            [
                "git",
                "-C",
                str(world),
                "log",
                "--reverse",
                "--format=COMMIT:%ad",
                "--date=short",
                "--follow",
                "-p",
                "--",
                "animal-list.json",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=300,
        )
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
    ) as error:
        raise RuntimeError(f"cannot read the list's history: {error}") from error
    seen: dict[str, str] = {}
    day = ""
    for line in out.stdout.splitlines():
        if line.startswith("COMMIT:"):
            day = line[len("COMMIT:") :]
        elif day:
            match = _ID_LINE.match(line)
            if match is not None:
                seen.setdefault(match.group(1), day)
    return seen


def round_delta(
    cached: dict[str, Any], current_ids: list[str], seen: dict[str, str]
) -> list[str]:
    """List ids the tile's survey never saw, in list order (#196.5).

    New caches record the surveyed ids, so the delta is exact.
    Old caches fall back to git dates against the survey date;
    an id git never saw counts as new (the call costs the same
    per tile either way). Already-merged ids stay out.
    """
    resurveyed = set(cached.get("resurveyed") or [])
    if "surveyed_ids" in cached:
        known = set(cached.get("surveyed_ids") or [])
        return [i for i in current_ids if i not in known and i not in resurveyed]
    since = cached.get("date") or "unknown"
    return [
        i for i in current_ids if i not in resurveyed and seen.get(i, "9999") > since
    ]


def merge_parts(
    cached_parts: list[dict[str, Any]], re_parts: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """The re-survey's species into the cached parts, in its order.

    Returns the merged parts with the new ids in re-survey
    order; already-listed ids merge to nothing.
    """
    merged = [
        {"kind": part["kind"], "species": list(part.get("species", []))}
        for part in cached_parts
    ]
    have = {s for part in merged for s in part["species"]}
    new: list[str] = []
    for part in re_parts:
        for species_id in part.get("species", []):
            if species_id in have:
                continue
            have.add(species_id)
            new.append(species_id)
            for target in merged:
                if target["kind"] == part["kind"]:
                    target["species"].append(species_id)
                    break
            else:
                merged.append({"kind": part["kind"], "species": [species_id]})
    return merged, new


def plan_round(
    *,
    cache_dir: Path,
    world: Path,
    habitat_grid: Any,
    store: Any,
    top: int,
    budget_usd: float | None,
    today: date | None = None,
    estimate_usd: float = SURVEY_ESTIMATE_USD,
) -> dict[str, Any]:
    """The round's tiles by play, trimmed to the top and budget.

    Candidates are cached current-grid surveys with list
    animals added since (open sea has no survey to grow).
    One call per tile at the survey estimate; over budget,
    the least-played tiles drop first.
    """
    from .species import list_animals, read_cache

    current = list_animals(world)
    current_ids = [a["id"] for a in current]
    play = play_by_tile(
        store.list_requests(), store.list_areas(), today or date.today()
    )
    seen: dict[str, str] | None = None
    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for path in sorted(cache_dir.glob("z10_*.json")):
        tile = path.stem
        cached = read_cache(cache_dir, tile)
        if cached is None:
            skipped.append({"tile": tile, "reason": "unreadable cache"})
            continue
        col, row = parse_tile(tile)
        if habitat_grid.habitat(col, row) == "open_ocean":
            skipped.append({"tile": tile, "reason": "open sea has no survey"})
            continue
        if "surveyed_ids" not in cached and seen is None:
            seen = first_seen(world)
        delta = round_delta(cached, current_ids, seen or {})
        if not delta:
            continue
        candidates.append(
            {"tile": tile, "play": play.get(tile, 0.0), "new_ids": delta, "calls": 1}
        )
    candidates.sort(key=lambda c: (-c["play"], c["tile"]))
    picked = candidates[: max(top, 0)]
    while (
        budget_usd is not None
        and sum(c["calls"] for c in picked) * estimate_usd > budget_usd
        and picked
    ):
        dropped = picked.pop()
        skipped.append({"tile": dropped["tile"], "reason": "over budget"})
    calls = sum(c["calls"] for c in picked)
    return {
        "tiles": picked,
        "calls": calls,
        "price_usd": round(calls * estimate_usd, 2),
        "estimate_usd": estimate_usd,
        "skipped": skipped,
    }


def run_round(
    *,
    planned: list[dict[str, Any]],
    cache_dir: Path,
    world: Path,
    habitat_grid: Any,
    client_for: Any,
) -> list[dict[str, Any]]:
    """Ask each planned tile about its delta and merge it (spends).

    One re-survey per tile over the added ids only; the found
    ids merge into the cached parts in re-survey order with
    the survey date kept, so the next remake wants them. A
    twice-misread tile is skipped with its reason.
    `client_for` builds the tile's model client, so spend
    attributes per tile (`survey:<tile>`).
    """
    from .species import (
        ask,
        check_survey,
        list_animals,
        read_cache,
        resolve_outside,
        write_cache,
    )
    from .tiles import available_biomes, resolve_home, tile_facts

    animals = list_animals(world)
    by_id = {a["id"]: a for a in animals}
    biomes = available_biomes(world)
    results: list[dict[str, Any]] = []
    for entry in planned:
        tile = entry["tile"]
        cached = read_cache(cache_dir, tile)
        if cached is None:
            results.append({"tile": tile, "merged": [], "reason": "unreadable cache"})
            continue
        delta = [by_id[i] for i in entry["new_ids"] if i in by_id]
        col, row = parse_tile(tile)
        cell = habitat_grid.cell_data(col, row)
        home, habitat, _ = resolve_home(
            tile,
            habitat_grid,
            world,
            biomes,
            habitat_grid.habitat(col, row),
            cell[0],
            cell[4],
            cell[5],
        )
        if home is None:
            results.append({"tile": tile, "merged": [], "reason": "no home"})
            continue
        facts = tile_facts(tile, habitat_grid, world, home, False)
        lat, lon = center(tile)
        client = client_for(tile)
        try:
            reasked = ask(client, lat, lon, delta, [], [], facts)
        except ValueError as error:
            results.append({"tile": tile, "merged": [], "reason": str(error)})
            continue

        def merged_answer(parts: list[dict[str, Any]]) -> dict[str, Any]:
            return {
                "place": cached["place"],
                "parts": parts,
                "plants": cached.get("plants", []),
                "trees": cached.get("trees", []),
                "name_en": cached.get("name_en"),
                "name_fi": cached.get("name_fi"),
                "outside_animals": cached.get("outside_animals") or [],
                "outside_plants": cached.get("outside_plants") or [],
                "present": list(dict.fromkeys(s for p in parts for s in p["species"])),
            }

        merged, new = merge_parts(cached["parts"], reasked["parts"])
        problem = check_survey(merged_answer(merged), habitat, lat, lon, facts, animals)
        if problem is not None:
            try:
                reasked = ask(client, lat, lon, delta, [], [], facts, note=problem)
            except ValueError as error:
                results.append({"tile": tile, "merged": [], "reason": str(error)})
                continue
            merged, new = merge_parts(cached["parts"], reasked["parts"])
            problem = check_survey(
                merged_answer(merged), habitat, lat, lon, facts, animals
            )
            if problem is not None:
                results.append({"tile": tile, "merged": [], "reason": problem})
                continue
        keys = resolve_outside(
            tile,
            reasked.get("outside_animals", []),
            reasked.get("outside_plants", []),
            cached.get("outside_keys", {}),
        )
        write_cache(
            cache_dir,
            tile,
            {
                **cached,
                "parts": merged,
                "outside_animals": list(
                    dict.fromkeys(
                        [
                            *(cached.get("outside_animals") or []),
                            *(reasked.get("outside_animals") or []),
                        ]
                    )
                ),
                "outside_plants": list(
                    dict.fromkeys(
                        [
                            *(cached.get("outside_plants") or []),
                            *(reasked.get("outside_plants") or []),
                        ]
                    )
                ),
                "outside_keys": keys,
                "surveyed_ids": [a["id"] for a in animals],
                "resurveyed": [
                    *(cached.get("resurveyed") or []),
                    *[i for i in new if i not in (cached.get("resurveyed") or [])],
                ],
            },
            cached.get("survey_round", 0),
            cached.get("plants_list", ""),
            cached.get("facts", ""),
            cached.get("date", "unknown"),
        )
        results.append({"tile": tile, "merged": new})
    return results
