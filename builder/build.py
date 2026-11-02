"""Item builds, tile publishing, previews and take (T76, T84, #101).

The worker orchestrates; this module does the honest work. A build
item runs the kept stages in its own job folder
(`jobs/<job>/<item>/`): research, minimum, looks, newhome,
oneshot. A new home first, then food, then the animal. Food items
are code-only. Publishing writes one group file and the tile index,
both to Storage; previews are merged manifests only their judge
opens; take lays a job's checked items out like animundo-world.
"""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .grid import NAME, group, group_path
from .pipeline import Pipeline, StageFailedError
from .report import build_report
from .schemas import validate
from .stages.looks import LooksStage
from .stages.maker import AnimalMakerStage, HomeMakerStage
from .stages.minimum import MinimumStage
from .stages.plants import PlantsStage
from .stages.research import ResearchStage

#: The kept steps for one border item, in D2/D10's order. Each
#: maker skips when the minset holds nothing for it, so home,
#: animal and plant items share this list.
ITEM_STAGES = ("research", "minimum", "looks", "newhome", "oneshot", "plants")

#: What take downloads and previews carry, laid out like the world.
#: Border jobs never write `regions/`, so it travels only for places.
TAKE_DIRS = ("homes", "animals", "models", "plants", "locale", "regions")

#: The published tile index's blob.
INDEX_BLOB = "tiles/v2/index.json"

#: Publish attempts per file before a conflict fails loud (#216.1).
PUBLISH_ATTEMPTS = 5


class PublishConflictError(ValueError):
    """A publish lost its race too often: loud, with the path (#216.1)."""

    def __init__(self, path: str, attempts: int) -> None:
        super().__init__(
            f"{path}: still conflicting after {attempts} writes; a person looks"
        )
        self.path = path


def _group_upload_bytes(data: dict[str, Any]) -> bytes:
    """A group file's Storage bytes: slim, compact, gzipped (#216.2, #216.3).

    Players never read a tile's `sources` or `made`, so published
    tiles leave them out (choice (a): `wanted` stays, the server
    reads it). Provenance stays in tile history, review bundles
    and build_jobs. Every reader unzips by content encoding.
    """
    slim = copy.deepcopy(data)
    for tile in (slim.get("tiles") or {}).values():
        if isinstance(tile, dict):
            tile.pop("sources", None)
            tile.pop("made", None)
    return gzip.compress(json.dumps(slim, separators=(",", ":")).encode("utf-8"))


def _index_upload_bytes(index: dict[str, Any]) -> bytes:
    """The tile index's Storage bytes: compact, gzipped (#216.2)."""
    return gzip.compress(json.dumps(index, separators=(",", ":")).encode("utf-8"))


def _empty_group(gcol: int, grow: int) -> dict[str, Any]:
    """A group file's skeleton before its first tile lands."""
    return {
        "format": 1,
        "grid": NAME,
        "group": [gcol, grow],
        "version": 0,
        "tiles": {},
        "blocked": {},
    }


def _empty_index() -> dict[str, Any]:
    """The tile index's skeleton before its first group lands."""
    return {
        "format": 1,
        "made_at": "",
        "world": "",
        "groups": {},
        "counts": {"ready": 0, "blocked": {}},
    }


def item_pipeline(
    chat: Any = None,
    build: Any = None,
    adapt: Any = None,
    state_path: Path | None = None,
    guard: Any = None,
    plant_cache: Path | None = None,
    extra: list[Any] | None = None,
) -> Pipeline:
    """The item pipeline, with the makers' seams open for tests.

    `extra` stages run last; the worker ships enrich items with one.
    """
    return Pipeline(
        [
            ResearchStage(),
            MinimumStage(),
            LooksStage(),
            HomeMakerStage(chat=chat, adapt=adapt, plant_cache=plant_cache),
            AnimalMakerStage(chat=chat, build=build, adapt=adapt),
            PlantsStage(chat=chat, build=build),
            *(extra or []),
        ],
        state_path=state_path or Path("state.json"),
        guard=guard,
    )


def order_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expand items in build order: a new home first, then food, then animals."""
    rank = {"home": 0, "food": 1, "animal": 2, "plant": 3}
    return sorted(items, key=lambda i: (rank.get(i.get("kind", ""), 9), i["id"]))


def _home_brief(
    item: dict[str, Any], habitat: str, lat: float, lon: float
) -> dict[str, Any]:
    """A home item's brief: a border ecoregion or a place's example.

    A border home's `what` is an ecoregion number; a place
    home's `what` names the example it adapts and carries no
    ecoregion, so the choices keep none either.
    """
    try:
        eco: int | None = int(item["what"])
    except (TypeError, ValueError):
        eco = None
    if eco is None:
        return {
            "home": habitat,
            "home_id": str(item["what"]),
            "ecoregion": None,
            "base_home": item["from"],
            "based_on": item["from"],
            "needs_home": True,
            "center": {"lat": lat, "lon": lon},
        }
    return {
        "home": habitat,
        "home_id": f"{habitat}_{eco}",
        "ecoregion": eco,
        "base_home": item["from"],
        "based_on": item["from"],
        "needs_home": True,
        "center": {"lat": lat, "lon": lon},
    }


def write_brief(
    item_dir: Path,
    item: dict[str, Any],
    habitat: str,
    lat: float,
    lon: float,
    entry: dict[str, Any] | None,
    plant_names: list[str] | None = None,
    enrich_tiles: list[str] | None = None,
    place: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The item's `home.json` brief from its plan item (the intake).

    A home item carries no species: research covers the habitat's
    surroundings and minimum makes only the home. An animal item
    carries its animal-list entry. A plant item carries its names
    and the filed plant it starts from, when the plan names one.
    Ids and keys only, never free text, and never anything about
    the player. An enrich item names its target tiles, whose
    converter places the checks run against (#161.1). A place
    item's home is the place's own home, not the grid cell's
    habitat, and it carries the place's region id (#175.2).
    """
    if item["kind"] == "home":
        brief = _home_brief(item, habitat, lat, lon)
    elif item["kind"] == "plant":
        brief = {
            "plant": item["what"],
            "names": plant_names or [item["what"]],
            "home": habitat,
            "needs_home": False,
            "center": {"lat": lat, "lon": lon},
        }
        if item.get("from"):
            brief["from"] = item["from"]
    else:
        assert entry is not None, f"animal item {item['id']} needs its list entry"
        brief = {
            "species_key": entry["gbif_key"],
            "id": entry["id"],
            "latin": entry.get("latin", ""),
            "name": entry.get("name", ""),
            "home": habitat,
            "needs_home": False,
            "center": {"lat": lat, "lon": lon},
        }
    if enrich_tiles is not None:
        brief["enrich"] = {"tiles": list(enrich_tiles)}
    if place is not None and item["kind"] != "home":
        if place.get("home"):
            brief["home"] = place["home"]
        brief["region_id"] = place["id"]
    item_dir.mkdir(parents=True, exist_ok=True)
    (item_dir / "home.json").write_text(json.dumps(brief, indent=2))
    return brief


def write_scaffold(
    item_dir: Path,
    home: dict[str, Any],
    plants: list[dict[str, Any]] | None = None,
    *,
    lat: float,
    lon: float,
    species: dict[str, Any] | None = None,
) -> Path:
    """A working region for the one-shot maker, from the tile's home.

    Real food and fish (the checks mean something), empty casts
    unless the tile's species are given, no welcome: the region
    is scaffolding, discarded after the item's animals and
    models are made. A plant item names its plant in the ground
    cover for the plants step. It is a valid region file (the
    checks also read the scaffold): `zz` marks the reserved
    country, and the centre is the tile's.
    """
    regions = item_dir / "regions"
    regions.mkdir(parents=True, exist_ok=True)
    casts = species or {}
    region: dict[str, Any] = {
        "id": "zz_work",
        "name": "Work region",
        "country": "Scaffolding",
        "description": (
            "Working region for one item's checks; discarded after the item is made."
        ),
        "author": "Animundo Builder",
        "center": {"lat": lat, "lon": lon},
        "sources": list(home.get("sources") or ["Scaffolding for one item's checks."]),
        "animals": {
            "playable": list(casts.get("playable") or []),
            "prey": list(casts.get("prey") or []),
            "predators": list(casts.get("predators") or []),
        },
        "food": home.get("food", []),
        "fish": home.get("fish", []),
    }
    if plants:
        region["ground_cover"] = {"plants": plants}
    path = regions / "zz_work.json"
    path.write_text(json.dumps(region, indent=2))
    return path


def find_plant_names(home: dict[str, Any], what: str) -> list[str]:
    """The home's human names for one findable plant id.

    Food entries name their plants ("Arctic Willow"); the ones
    slugifying to the plan item's id ground the draft. Without a
    match, the id itself, so the draft still has a name.
    """
    from .plan import plant_slug

    names = [
        name
        for entry in home.get("food", []) or []
        for name in entry.get("names", []) or []
        if plant_slug(name) == what
    ]
    return names or [what]


def write_flora_survey(item_dir: Path, names: list[str]) -> Path:
    """`survey.json`'s flora facts from the home's named plants.

    The plants step grounds its draft in these; each name the
    home gives its findables becomes one fact. Code-only.
    """
    path = item_dir / "survey.json"
    path.write_text(
        json.dumps(
            {"facts": [{"topic": "plant", "text": f"{n} grows here"} for n in names]},
            indent=2,
        )
    )
    return path


def prepare_item(
    item_dir: Path,
    item: dict[str, Any],
    habitat: str,
    lat: float,
    lon: float,
    entry: dict[str, Any] | None,
    home: dict[str, Any] | None,
    species: dict[str, Any] | None = None,
    enrich_tiles: list[str] | None = None,
    place: dict[str, Any] | None = None,
) -> None:
    """Brief and scaffold for one item's stages (the intake).

    A plant item carries its names, scaffold and flora survey; an
    animal item its entry and a scaffold from the tile's home; a
    home item only its brief. The worker and the local replay run
    the same intake. Given the tile's species, the scaffold seats
    them, so a hunter's prey check sees the tile's cast (#150.1).
    An enrich item skips the scaffold: the worker lays the target
    tiles' converter places instead (#161.1). A place item's
    brief names the place's own home and region id (#175.2).
    """
    if item["kind"] == "plant":
        assert home is not None
        names = find_plant_names(home, item["what"])
        write_brief(item_dir, item, habitat, lat, lon, None, names, enrich_tiles, place)
        if enrich_tiles is None:
            write_scaffold(
                item_dir,
                home,
                [{"model": item["what"], "names": names}],
                lat=lat,
                lon=lon,
                species=species,
            )
        write_flora_survey(item_dir, names)
    else:
        write_brief(item_dir, item, habitat, lat, lon, entry, None, enrich_tiles, place)
        if item["kind"] == "animal" and enrich_tiles is None:
            assert home is not None
            write_scaffold(item_dir, home, lat=lat, lon=lon, species=species)


def write_state_params(item_dir: Path, params: dict[str, Any]) -> None:
    """Merge labels into the item state for its report (#150.2).

    Retry-safe: the pipeline's completed stages and results stay
    untouched; the labels land under `params` again as they were.
    """
    path = item_dir / "state.json"
    state: dict[str, Any] = {}
    if path.is_file():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            state = {}
    if not isinstance(state, dict):
        state = {}
    state["params"] = {**state.get("params", {}), **params}
    item_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def run_item_stages(ctx: Any, item_dir: Path, pipeline: Pipeline) -> dict[str, Any]:
    """The item's stages, one try and at most one fix round.

    The pipeline resumes after completed stages, so the second run
    retries only what failed, with the checks' errors on disk. An
    item that fails twice is failed.
    """
    try:
        return pipeline.run(ctx)
    except StageFailedError:
        return pipeline.run(ctx)


def build_food_item(item_dir: Path, item: dict[str, Any], home: dict[str, Any]) -> Path:
    """Add one food kind to a copy of the home, by code, for $0.

    The review commits the patched file; the worker never touches
    the world's checkout.
    """
    homes = item_dir / "homes"
    homes.mkdir(parents=True, exist_ok=True)
    patched = json.loads(json.dumps(home))
    kinds = {f.get("kind") for f in patched.get("food", []) if isinstance(f, dict)}
    if item["what"] not in kinds:
        patched.setdefault("food", []).append({"kind": item["what"], "names": []})
    out = homes / f"{item['from']}.json"
    out.write_text(json.dumps(patched, indent=2))
    return out


def upload_bundle(
    store: Any, prefix: str, item_dir: Path, report: str = "report.html"
) -> str:
    """An item's deliverables to Storage, world-shaped, report included.

    Only homes, animals, models, plants, locale and the report
    travel: tries, checks and logs stay in the job folder. Returns
    the report's Storage path for the job's progress.
    """
    report_path = ""
    wanted = [item_dir / name for name in TAKE_DIRS] + [item_dir / report]
    for root in wanted:
        if root.is_file():
            todo = [root]
        elif root.is_dir():
            todo = sorted(p for p in root.rglob("*") if p.is_file())
        else:
            continue
        for path in todo:
            rel = path.relative_to(item_dir).as_posix()
            if path.suffix == ".html":
                content_type = "text/html"
            elif path.suffix == ".json":
                content_type = "application/json"
            else:
                content_type = "application/octet-stream"
            store.write_blob(f"{prefix}/{rel}", path.read_bytes(), content_type)
            if rel == report:
                report_path = f"{prefix}/{rel}"
    return report_path


def publish_group(
    tile: str,
    made_tile: dict[str, Any],
    store: Any,
    world_commit: str,
    history: dict[str, Any] | None = None,
) -> str:
    """One made tile to Storage: its group file and the tile index.

    The group merges over a freshly re-read copy and writes with
    its generation, retrying races instead of losing the other
    writer's tiles (#216.1); the index counts move by the honest
    delta (this tile newly ready, or no longer blocked for its
    old reason). Returns the group Storage path. With `history`
    (job, kind, cost_usd), a made/remade row lands in the tile
    history (#165), and the tile's latest row at
    `tile_history/<tile>` (#195.2).
    """
    from . import history as history_mod
    from .tiles import write_group
    from .worker_store import GenerationMismatch

    gcol, grow = group(tile)
    blob_path = group_path(tile)
    last: GenerationMismatch | None = None
    data: dict[str, Any] = {}
    old: dict[str, Any] = {"tiles": {}, "blocked": {}}
    for _ in range(PUBLISH_ATTEMPTS):
        old_raw, gen = store.read_blob(blob_path)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            if old_raw is not None:
                old = json.loads(old_raw.decode("utf-8"))
                (tmp_dir / NAME).mkdir(parents=True, exist_ok=True)
                (tmp_dir / NAME / f"{gcol:03d}_{grow:03d}.json").write_bytes(old_raw)
            else:
                old = {"tiles": {}, "blocked": {}}
            path = write_group(tmp_dir, tile, made_tile, None)
            data = json.loads(path.read_bytes())
        try:
            store.write_blob(
                blob_path,
                _group_upload_bytes(data),
                "application/json",
                if_generation_match=gen or 0,
                content_encoding="gzip",
            )
        except GenerationMismatch as error:
            last = error
            continue
        break
    else:
        raise PublishConflictError(blob_path, PUBLISH_ATTEMPTS) from last
    _update_index(
        store, gcol, grow, data["version"], old, [(tile, True, None)], world_commit
    )
    if history is not None:
        event = (
            "remade"
            if tile in (old.get("tiles") or {}) or tile in (old.get("blocked") or {})
            else "made"
        )
        entry = history_mod.row(
            tile,
            event,
            job=history.get("job"),
            kind=history["kind"],
            world=world_commit,
            version=data["version"],
            cost_usd=history.get("cost_usd"),
        )
        history_mod.record(store, entry)
        history_mod.record_tile(store, entry)
    return blob_path


def _apply_index_delta(
    index: dict[str, Any],
    old_group: dict[str, Any],
    tile: str,
    made: bool,
    blocked_reason: str | None,
) -> None:
    """One tile's honest delta on the index counts."""
    was_ready = tile in (old_group.get("tiles") or {})
    was_blocked = (old_group.get("blocked") or {}).get(tile, {}).get("reason")
    counts = index["counts"]
    if made and not was_ready:
        counts["ready"] += 1
    if not made and was_ready:
        counts["ready"] = max(0, counts["ready"] - 1)
    if was_blocked and was_blocked != blocked_reason:
        blocked = counts["blocked"]
        blocked[was_blocked] = max(0, blocked.get(was_blocked, 0) - 1)
        if blocked[was_blocked] == 0:
            del blocked[was_blocked]
    if blocked_reason and blocked_reason != was_blocked:
        blocked = counts["blocked"]
        blocked[blocked_reason] = blocked.get(blocked_reason, 0) + 1


def _update_index(
    store: Any,
    gcol: int,
    grow: int,
    version: int,
    old_group: dict[str, Any],
    deltas: list[tuple[str, bool, str | None]],
    world_commit: str,
) -> None:
    """The tile index: this group's version, counts by honest delta.

    `deltas` is (tile, made, blocked reason) per tile this
    publish changed, applied against the fresh group copy; empty
    for a places-only filing, which bumps the version with no
    count delta (#170.2). The group's version never goes down,
    and the write carries the read generation, retried on races
    (#216.1).
    """
    from .worker_store import GenerationMismatch

    key = f"{NAME}/{gcol:03d}_{grow:03d}"
    last: GenerationMismatch | None = None
    for _ in range(PUBLISH_ATTEMPTS):
        raw, gen = store.read_blob(INDEX_BLOB)
        index = json.loads(raw.decode("utf-8")) if raw is not None else _empty_index()
        index["groups"][key] = max(int(index["groups"].get(key, 0)), version)
        for tile, made, reason in deltas:
            _apply_index_delta(index, old_group, tile, made, reason)
        index["made_at"] = datetime.now(UTC).isoformat()
        index["world"] = world_commit
        validate("tile-index", index)
        try:
            store.write_blob(
                INDEX_BLOB,
                _index_upload_bytes(index),
                "application/json",
                if_generation_match=gen or 0,
                content_encoding="gzip",
            )
        except GenerationMismatch as error:
            last = error
            continue
        return
    raise PublishConflictError(INDEX_BLOB, PUBLISH_ATTEMPTS) from last


def publish_places(
    tile: str,
    entry: dict[str, Any],
    store: Any,
    world_commit: str,
) -> str:
    """One place's wanted list into its group file, under `places`.

    The tile's own entry is untouched: the tile stays the
    hand-made region (#170.2). Merges over a freshly re-read
    copy and writes with its generation, retried on races
    (#216.1). Returns the group Storage path.
    """
    from .worker_store import GenerationMismatch

    gcol, grow = group(tile)
    blob_path = group_path(tile)
    last: GenerationMismatch | None = None
    data: dict[str, Any] = {}
    old: dict[str, Any] = {"tiles": {}, "blocked": {}}
    for _ in range(PUBLISH_ATTEMPTS):
        old_raw, gen = store.read_blob(blob_path)
        if old_raw is not None:
            old = json.loads(old_raw.decode("utf-8"))
        else:
            old = {"tiles": {}, "blocked": {}}
        data = {
            "format": 1,
            "grid": NAME,
            "group": [gcol, grow],
            "version": int(old.get("version", 0)) + 1,
            "tiles": old.get("tiles") or {},
            "blocked": old.get("blocked") or {},
        }
        places = dict(old.get("places") or {})
        places[tile] = entry
        data["places"] = places
        validate("tile-group", data)
        try:
            store.write_blob(
                blob_path,
                _group_upload_bytes(data),
                "application/json",
                if_generation_match=gen or 0,
                content_encoding="gzip",
            )
        except GenerationMismatch as error:
            last = error
            continue
        break
    else:
        raise PublishConflictError(blob_path, PUBLISH_ATTEMPTS) from last
    _update_index(store, gcol, grow, data["version"], old, [], world_commit)
    return blob_path


class EnrichRemakeError(ValueError):
    """A remade tile broke enrichment's promise: loud, with the tile."""

    def __init__(self, tile: str, reason: str) -> None:
        super().__init__(f"{tile}: {reason}")
        self.tile = tile
        self.reason = reason


#: A remake's verdict per tile: the new entry, or nothing changed.
Remake = tuple[str, dict[str, Any] | None]


def _content(tile: dict[str, Any]) -> dict[str, Any]:
    """The tile without provenance: what a version bump means.

    Published tiles leave `sources` and `made` out (#216.3),
    so neither counts, or no remake would ever come back skip.
    """
    return {k: v for k, v in tile.items() if k not in ("version", "made", "sources")}


def _filed_now(world: Path, kind: str, what: str) -> bool:
    if kind == "animal":
        return (world / "animals" / f"{what}.json").is_file()
    return (world / "models" / "plants" / f"{what}.json").is_file()


def remake_decision(
    tile: str,
    old_tile: dict[str, Any] | None,
    old_blocked: dict[str, Any] | None,
    made: dict[str, Any] | None,
    blocked: dict[str, Any] | None,
    world: Path,
    what: str,
    spread: str | None = None,
) -> tuple[Remake, bool]:
    """One remade tile's verdict: publish, refresh, or skip (#143.4).

    Enrichment only adds: seed, home, edges and coast (and
    everything but the tile's own lists) stay byte-identical, the
    species lists only grow, and `wanted` drops only what is filed
    now. Anything else fails the job loudly instead of publishing
    a rebuilt tile. Returns the verdict and whether the shipped
    thing missed its food here (`no_food`): wanted but still out
    of the species.
    """
    wanted_before = [e.get("what") for e in (old_tile or {}).get("wanted", [])]
    wanted_before += [e.get("what") for e in (old_blocked or {}).get("wanted", [])]
    if made is None:
        if old_tile is not None:
            raise EnrichRemakeError(tile, "a ready tile remade blocked; a person looks")
        if blocked is None:
            raise EnrichRemakeError(tile, "the remake came back empty")
        return ("blocked", blocked), what in wanted_before
    species = made.get("species", {})
    no_food = what in wanted_before and all(
        what not in species.get(name, []) for name in ("playable", "prey", "predators")
    )
    if old_tile is None:
        made["version"] = 1
        return ("tile", made), no_food
    for key, old_value in old_tile.items():
        if key in ("species", "wanted", "requires", "version", "made"):
            continue
        if made.get(key) != old_value:
            raise EnrichRemakeError(tile, f"enrichment moved {key}; a person looks")
    for name in ("playable", "prey", "predators"):
        if not set(old_tile.get("species", {}).get(name, [])) <= set(
            species.get(name, [])
        ):
            raise EnrichRemakeError(tile, f"enrichment dropped {name}; a person looks")
    if not set(old_tile.get("requires", {}).get("animals", [])) <= set(
        made.get("requires", {}).get("animals", [])
    ):
        raise EnrichRemakeError(tile, "enrichment dropped requires; a person looks")
    dropped = {e.get("what") for e in old_tile.get("wanted", [])} - {
        e.get("what") for e in made.get("wanted", [])
    }
    for_entry = {e.get("what"): e.get("kind") for e in old_tile.get("wanted", [])}
    for missing in sorted(dropped, key=str):
        kind = for_entry.get(missing)
        if spread is not None and missing != spread:
            raise EnrichRemakeError(
                tile, f"spread lost {missing}, not the spread species; a person looks"
            )
        if kind not in ("animal", "plant") or not _filed_now(world, kind, str(missing)):
            raise EnrichRemakeError(
                tile, f"wanted lost {missing}, unfiled; a person looks"
            )
    if _content(made) == _content(old_tile):
        return ("skip", None), no_food
    made["version"] = int(old_tile.get("version", 0)) + 1
    return ("tile", made), no_food


def overlay_decision(
    tile: str,
    old_tile: dict[str, Any] | None,
    old_blocked: dict[str, Any] | None,
    made: dict[str, Any] | None,
    blocked: dict[str, Any] | None,
    kind: str,
    what: str,
) -> tuple[str, str | None]:
    """One overlay tile's verdict from its remake alone (#227.1, #241.2).

    The species decides: `what` in the remake's species, fish or
    (for a plant) plants joins the overlay; anything else skips
    with the reason. The remake keeps every surveyed animal
    since 0061, so this is the survey-and-habitat verdict; food
    never leaves a tile out. Seed, home, edges, coast, plants
    and `wanted` are never compared with the published tile: an
    overlay publishes no land. Returns the verdict ("overlay" or
    "skipped") and the skip reason.
    """
    if made is None:
        reason = (blocked or {}).get("reason") or "blocked"
        return "skipped", f"remade {reason}"
    if kind == "plant":
        plants = made.get("plants") or {}
        grown = [
            entry.get("id")
            for side in ("cover", "water")
            for entry in plants.get(side, []) or []
            if isinstance(entry, dict)
        ]
        if what in grown:
            return "overlay", None
    else:
        species = made.get("species", {})
        if any(
            what in (species.get(name) or [])
            for name in ("playable", "prey", "predators", "fish")
        ):
            return "overlay", None
    return "skipped", f"{what} is not in the remake"


def publish_remakes(
    store: Any,
    decisions: dict[str, Remake],
    world_commit: str,
    history: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Changed remake tiles to Storage: one write per group, index per group.

    Only groups with a changed tile are written, each merged over
    a freshly re-read copy and written with its generation, so a
    concurrent writer's tiles survive (#216.1). Tile versions
    count from the fresh copy, and the index takes the higher
    version with honest deltas. Returns `versions` for the tiles
    whose content changed. With `history` (job, kind, cost_usd),
    every written tile lands a made/remade/blocked row in the
    tile history (#165), and its latest row at `tile_history/<tile>`
    (#195.2). Without a job of their own, the pass writes its own
    history rows plus one `build_jobs` remake record, so areas
    hear of it (#209.1); a conflict that fails loud halfway still
    records what landed.
    """
    from . import history as history_mod
    from .grid import group_path

    remake_id: str | None = None
    if history is None or not history.get("job"):
        remake_id = _remake_job_id(world_commit)
        history = {"job": remake_id, "kind": "remake", "cost_usd": 0}

    by_blob: dict[str, list[str]] = {}
    for tile, (kind, entry) in decisions.items():
        if kind == "skip" or entry is None:
            continue
        by_blob.setdefault(group_path(tile), []).append(tile)
    versions: dict[str, int] = {}
    try:
        for blob_path, tiles in by_blob.items():
            data, old = _write_remake_group(store, blob_path, tiles, decisions)
            for tile in tiles:
                seated = (data.get("tiles") or {}).get(tile)
                if seated is not None:
                    versions[tile] = int(seated["version"])
            gcol, grow = group(tiles[0])
            deltas = []
            for tile in tiles:
                kind, entry = decisions[tile]
                assert entry is not None
                reason = entry.get("reason") if kind == "blocked" else None
                deltas.append((tile, kind == "tile", reason))
            _update_index(store, gcol, grow, data["version"], old, deltas, world_commit)
            for tile in tiles:
                kind, entry = decisions[tile]
                assert entry is not None
                reason = entry.get("reason") if kind == "blocked" else None
                if kind == "blocked":
                    event: str = "blocked"
                elif tile in (old.get("tiles") or {}) or tile in (
                    old.get("blocked") or {}
                ):
                    event = "remade"
                else:
                    event = "made"
                entry = history_mod.row(
                    tile,
                    event,
                    job=history.get("job"),
                    kind=history["kind"],
                    world=world_commit,
                    version=data["version"],
                    reason=reason,
                    cost_usd=history.get("cost_usd"),
                )
                history_mod.record(store, entry)
                history_mod.record_tile(store, entry)
    except PublishConflictError:
        if remake_id is not None and versions and hasattr(store, "set_build_job"):
            _record_remake(store, remake_id, versions, world_commit)
        raise
    if remake_id is not None and versions and hasattr(store, "set_build_job"):
        _record_remake(store, remake_id, versions, world_commit)
    return versions


def _write_remake_group(
    store: Any,
    blob_path: str,
    tiles: list[str],
    decisions: dict[str, Remake],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """One remake group over a fresh copy, matched and retried (#216.1).

    Merges only this job's changed tiles; each tile's version
    counts from the fresh copy's tile, and the group's from the
    fresh group. Returns the written group and the fresh copy it
    merged over, for honest index deltas and history events.
    """
    from .worker_store import GenerationMismatch

    gcol, grow = group(tiles[0])
    last: GenerationMismatch | None = None
    for _ in range(PUBLISH_ATTEMPTS):
        raw, gen = store.read_blob(blob_path)
        old = (
            json.loads(raw.decode("utf-8"))
            if raw is not None
            else _empty_group(gcol, grow)
        )
        data = copy.deepcopy(old)
        data.setdefault("tiles", {})
        data.setdefault("blocked", {})
        for tile in tiles:
            kind, entry = decisions[tile]
            assert entry is not None
            if kind == "tile":
                prior = data["tiles"].get(tile)
                seated = dict(entry)
                seated["version"] = (
                    int(prior.get("version", 0)) + 1 if isinstance(prior, dict) else 1
                )
                data["tiles"][tile] = seated
                data["blocked"].pop(tile, None)
            else:
                data["blocked"][tile] = entry
                data["tiles"].pop(tile, None)
        data["version"] = int(data.get("version", 0)) + 1
        validate("tile-group", data)
        try:
            store.write_blob(
                blob_path,
                _group_upload_bytes(data),
                "application/json",
                if_generation_match=gen or 0,
                content_encoding="gzip",
            )
        except GenerationMismatch as error:
            last = error
            continue
        return data, old
    raise PublishConflictError(blob_path, PUBLISH_ATTEMPTS) from last


def _remake_job_id(world_commit: str) -> str:
    """One id per pass: the world, the day, reruns overwrite (#209.1)."""
    slug = "".join(c for c in world_commit if c.isalnum())[:12] or "0"
    return f"remake-{slug}-{datetime.now(UTC).date().isoformat()}"


def _record_remake(
    store: Any, job_id: str, versions: dict[str, int], world_commit: str
) -> None:
    """The pass as one published remake job: its tiles, versions, world."""
    now = datetime.now(UTC).isoformat()
    tiles = sorted(versions)
    doc = {
        "format": 1,
        "kind": "remake",
        "items": [],
        "tiles": tiles,
        "versions": {tile: versions[tile] for tile in tiles},
        "world": world_commit,
        "budget_usd": 0,
        "visibility": "public",
        "created_by": "builder",
        "status": "published",
        "cost_usd": 0,
        "created_at": now,
        "updated_at": now,
    }
    validate("build-job", doc)
    store.set_build_job(job_id, doc)


def assemble_preview(
    job_id: str,
    tile: str,
    version: int,
    bundles: dict[str, dict[str, bytes]],
) -> dict[str, bytes]:
    """A judge's preview pack: every built item's files plus its manifest.

    Only world-shaped content files travel, never job working
    files: homes, animals, models, plants, locale.
    """
    files: dict[str, bytes] = {}
    for _item_id, bundle in sorted(bundles.items()):
        for rel, data in bundle.items():
            first = rel.split("/", 1)[0]
            if first in TAKE_DIRS:
                files[rel] = data
    if not files:
        raise ValueError("a preview needs at least one file")
    manifest = {
        "draft_id": job_id,
        "draft_place_id": f"draft_{job_id}",
        "place_id": tile,
        "version": version,
        "format": 1,
        "files": [
            {
                "path": rel,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
            for rel, data in sorted(files.items())
        ],
        "new_species": sorted(
            {
                rel.split("/")[1].removesuffix(".json")
                for rel in files
                if rel.startswith("animals/") and rel.endswith(".json")
            }
        ),
        "reused_species": [],
    }
    validate("pack-manifest", manifest)
    return {**files, "manifest.json": json.dumps(manifest, indent=2).encode("utf-8")}


def next_preview_version(store: Any, job_id: str) -> int:
    """One past the newest preview version, or 1 when there is none."""
    versions = []
    for name in store.list_blobs(f"previews/{job_id}/"):
        rest = name.removeprefix(f"previews/{job_id}/")
        head = rest.split("/", 1)[0]
        if head.isdigit():
            versions.append(int(head))
    return max(versions, default=0) + 1


def take_bundle(store: Any, job_id: str, out_dir: Path) -> Path:
    """A job's review bundles to disk, laid out like animundo-world."""
    blobs = store.list_blobs(f"review/{job_id}/")
    if not blobs:
        raise ValueError(f"job {job_id} has nothing under review/")
    dest = out_dir / job_id
    for name in blobs:
        rel = name.removeprefix(f"review/{job_id}/")
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        data, _ = store.read_blob(name)
        assert data is not None
        path.write_bytes(data)
    return dest


def _lay_built_models(bundle_dir: Path) -> None:
    """The bundle's built models where the draft check looks (#140.2).

    The job collects each built .glb under models/; the world's
    check wants it at assets/animals/. Laid from the bundle the
    take downloaded, never required from the world path (the world
    never commits .glb). Existing files stay put.
    """
    dest = bundle_dir / "assets" / "animals"
    for built in sorted((bundle_dir / "models").glob("*.glb")):
        target = dest / built.name
        if target.is_file():
            continue
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(built, target)


def check_draft(bundle_dir: Path, world: Path) -> list[str]:
    """The world's draft check over a taken item; problems, [] when clean."""
    from .world import DRAFT_TIMEOUT

    _lay_built_models(bundle_dir)
    tool = world / "tools" / "check_draft.py"
    if not tool.is_file():
        raise ValueError(f"no draft check in {world}")
    try:
        proc = subprocess.run(
            [sys.executable, str(tool), str(bundle_dir), "--world", str(world)],
            capture_output=True,
            text=True,
            check=False,
            timeout=DRAFT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"the draft check did not run: {error}") from error
    if proc.returncode == 2:
        raise ValueError(f"the draft can't be read: {proc.stderr.strip()}")
    if proc.returncode == 0:
        return []
    try:
        report = json.loads(proc.stdout)
    except ValueError:
        return [proc.stderr.strip() or "the draft check failed"]
    problems = []
    for entry in report.get("results", []):
        if entry.get("status") == "fail":
            problems.extend(entry.get("problems", [entry.get("check", "failed")]))
    return problems or ["the draft check failed"]


def take_report(bundle_dir: Path) -> Path:
    """The build report next to the taken files."""
    return build_report(bundle_dir)
