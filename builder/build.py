"""Item builds, tile publishing and take (T76, #101).

The worker orchestrates; this module does the honest work. A build
item runs the kept stages in its own job folder
(`jobs/<job>/<item>/`): research, minimum, looks, newhome,
oneshot. A new home first, then food, then the animal. Food items
are code-only. Publishing writes one group file and the tile index,
both to Storage; take lays a job's checked items out like
animundo-world. Previews (T84) land next.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .grid import GridName, group, group_path
from .pipeline import Pipeline, StageFailedError
from .report import build_report
from .schemas import validate
from .stages.looks import LooksStage
from .stages.maker import AnimalMakerStage, HomeMakerStage
from .stages.minimum import MinimumStage
from .stages.research import ResearchStage

#: The kept steps for one border item, in D2/D10's order. Each
#: maker skips when the minset holds nothing for it, so home and
#: animal items share this list.
ITEM_STAGES = ("research", "minimum", "looks", "newhome", "oneshot")

#: What take downloads, laid out like the world.
TAKE_DIRS = ("homes", "animals", "models", "plants", "locale")


def item_pipeline(
    chat: Any = None,
    build: Any = None,
    adapt: Any = None,
    state_path: Path | None = None,
    guard: Any = None,
) -> Pipeline:
    """The item pipeline, with the makers' seams open for tests."""
    return Pipeline(
        [
            ResearchStage(),
            MinimumStage(),
            LooksStage(),
            HomeMakerStage(chat=chat, adapt=adapt),
            AnimalMakerStage(chat=chat, build=build, adapt=adapt),
        ],
        state_path=state_path or Path("state.json"),
        guard=guard,
    )


def order_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expand items in build order: a new home first, then food, then animals."""
    rank = {"home": 0, "food": 1, "animal": 2, "plant": 3}
    return sorted(items, key=lambda i: (rank.get(i.get("kind", ""), 9), i["id"]))


def write_brief(
    item_dir: Path,
    item: dict[str, Any],
    habitat: str,
    lat: float,
    lon: float,
    entry: dict[str, Any] | None,
) -> dict[str, Any]:
    """The item's `home.json` brief from its plan item (the intake).

    A home item carries no species: research covers the habitat's
    surroundings and minimum makes only the home. An animal item
    carries its animal-list entry. Ids and keys only, never free
    text, and never anything about the player.
    """
    if item["kind"] == "home":
        eco = int(item["what"])
        brief = {
            "home": habitat,
            "home_id": f"{habitat}_{eco}",
            "ecoregion": eco,
            "base_home": item["from"],
            "based_on": item["from"],
            "needs_home": True,
            "center": {"lat": lat, "lon": lon},
        }
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
    item_dir.mkdir(parents=True, exist_ok=True)
    (item_dir / "home.json").write_text(json.dumps(brief, indent=2))
    return brief


def write_scaffold(item_dir: Path, home: dict[str, Any]) -> Path:
    """A working region for the one-shot maker, from the tile's home.

    Real food and fish (the checks mean something), empty casts,
    no welcome: the region is scaffolding, discarded after the
    item's animals and models are made.
    """
    regions = item_dir / "regions"
    regions.mkdir(parents=True, exist_ok=True)
    path = regions / "work.json"
    path.write_text(
        json.dumps(
            {
                "id": "work",
                "animals": {"playable": [], "prey": [], "predators": []},
                "food": home.get("food", []),
                "fish": home.get("fish", []),
            },
            indent=2,
        )
    )
    return path


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
) -> str:
    """One made tile to Storage: its group file and the tile index.

    The group merges over the published one; the index counts move
    by the honest delta (this tile newly ready, or no longer
    blocked for its old reason). Returns the group Storage path.
    """
    from .tiles import write_group

    grid, gcol, grow = group(tile)
    blob_path = group_path(tile)
    old_raw = store.read_blob(blob_path)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        if old_raw is not None:
            old = json.loads(old_raw.decode("utf-8"))
            (tmp_dir / grid).mkdir(parents=True, exist_ok=True)
            (tmp_dir / grid / f"{gcol:02d}_{grow:02d}.json").write_bytes(old_raw)
        else:
            old = {"tiles": {}, "blocked": {}}
        path = write_group(tmp_dir, tile, made_tile, None)
        data = json.loads(path.read_bytes())
        store.write_blob(blob_path, path.read_bytes())
    _update_index(store, grid, gcol, grow, data["version"], old, tile, world_commit)
    return blob_path


def _update_index(
    store: Any,
    grid: GridName,
    gcol: int,
    grow: int,
    version: int,
    old_group: dict[str, Any],
    tile: str,
    world_commit: str,
) -> None:
    """The tile index: this group's version, counts by honest delta."""
    raw = store.read_blob("tiles/v1/index.json")
    if raw is not None:
        index = json.loads(raw.decode("utf-8"))
    else:
        index = {
            "format": 1,
            "made_at": "",
            "world": "",
            "groups": {},
            "counts": {"ready": 0, "blocked": {}},
        }
    key = f"{grid}/{gcol:02d}_{grow:02d}"
    index["groups"][key] = version
    was_ready = tile in (old_group.get("tiles") or {})
    was_blocked = (old_group.get("blocked") or {}).get(tile, {}).get("reason")
    counts = index["counts"]
    if not was_ready:
        counts["ready"] += 1
    if was_blocked:
        blocked = counts["blocked"]
        blocked[was_blocked] = max(0, blocked.get(was_blocked, 0) - 1)
        if blocked[was_blocked] == 0:
            del blocked[was_blocked]
    index["made_at"] = datetime.now(UTC).isoformat()
    index["world"] = world_commit
    validate("tile-index", index)
    store.write_blob("tiles/v1/index.json", json.dumps(index).encode("utf-8"))


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
        data = store.read_blob(name)
        assert data is not None
        path.write_bytes(data)
    return dest


def check_draft(bundle_dir: Path, world: Path) -> list[str]:
    """The world's draft check over a taken item; problems, [] when clean."""
    from .world import DRAFT_TIMEOUT

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
