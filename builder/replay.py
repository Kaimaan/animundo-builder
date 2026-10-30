"""Run one border request locally, recorded or replayed (T39).

`builder request build <tile> --record <dir>` plans live and
builds with a recording client; `--replay <dir>` rebuilds from
the recording with no key and no model cost. No Firestore either
way: the job lives under `--out`, laid out like a worker's, with
a report per item. A replay always runs clean (its own state
files go first) and fails loudly when the recording no longer
matches the code.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import build as build_mod
from .config import Config
from .grid import center, parse_tile
from .meta import CostLogger
from .pipeline import BudgetTracker, JobContext, StageFailedError
from .records import ReplayError

#: The local job id. One replay per `--out`: a second run starts
#: from its own clean state, never from the last run's.
JOB_ID = "replay"


def _worker(out_dir: Path, world: Path, grid_dir: Path | None) -> Any:
    """A worker with no store: tile homes and grids only, never Firebase."""
    from .worker import Worker

    return Worker(
        store=None,
        jobs_dir=out_dir,
        world=world,
        config=Config(),
        grid_dir=grid_dir,
    )


def _entry_for(world: Path, item: dict[str, Any]) -> dict[str, Any] | None:
    """The animal-list entry an animal item names, or nothing else needs."""
    if item["kind"] != "animal":
        return None
    from .animal_list import find_list, load_list

    data = load_list(find_list(world))
    by_id = {e["id"]: e for e in data["animals"]}
    entry = by_id.get(item["what"])
    if entry is None:
        raise ValueError(f"item {item['id']} names {item['what']}, off the list")
    return entry


def run_request(
    tile: str,
    plan: dict[str, Any],
    out_dir: Path,
    world: Path,
    grid_dir: Path | None,
    make_ctx: Callable[[dict[str, Any], Path], Any],
) -> dict[str, Path]:
    """Every expand item through its stages. Reports by item id.

    `make_ctx(item, item_dir)` returns the item's context; food
    builds by code, like live. A client with `verify_consumed`
    must have played every answer it holds. Like a border job
    (#135.3), one failed item never stops the rest (#146.1):
    every item that doesn't need it still runs, the failed one is
    reported, and only a run where nothing built raises.
    """
    from .ledger import CapExceeded, LedgerError
    from .meta import MetaAuthError
    from .pipeline import BudgetExceeded, OffSwitch
    from .report import build_report
    from .worker import _NoHome

    worker = _worker(out_dir, world, grid_dir)
    job_dir = out_dir / JOB_ID
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "plan.json").write_text(json.dumps(plan, indent=1), encoding="utf-8")
    grid, col, row = parse_tile(tile)
    habitat = worker._grids()[grid].habitat(col, row)
    lat, lon = center(tile)
    reports: dict[str, Path] = {}
    built: dict[str, Path] = {}
    failures: dict[str, Exception] = {}
    skipped: dict[str, Exception] = {}
    for item in build_mod.order_items(plan.get("items", [])):
        item_dir = job_dir / item["id"]
        item_dir.mkdir(parents=True, exist_ok=True)
        state = item_dir / "state.json"
        if state.is_file():
            state.unlink()
        try:
            if item["kind"] == "food":
                home = worker._tile_home(tile, plan, JOB_ID)
                build_mod.build_food_item(item_dir, item, home)
            else:
                entry = _entry_for(world, item)
                home = (
                    worker._tile_home(tile, plan, JOB_ID)
                    if item["kind"] in ("plant", "animal")
                    else None
                )
                build_mod.prepare_item(item_dir, item, habitat, lat, lon, entry, home)
                ctx = make_ctx(item, item_dir)
                pipeline = build_mod.item_pipeline(state_path=item_dir / "state.json")
                build_mod.run_item_stages(ctx, item_dir, pipeline)
                verify = getattr(ctx.client, "verify_consumed", None)
                if verify is not None:
                    verify()
        except _NoHome as error:
            need = next(
                (i["id"] for i in plan.get("items", []) if i.get("kind") == "home"),
                "a home",
            )
            skipped[item["id"]] = error
            print(f"{item['id']} skipped: needs {need}", file=sys.stderr)
            continue
        except ReplayError:
            raise
        except StageFailedError as error:
            if isinstance(error.__cause__, ReplayError):
                raise error.__cause__ from error
            failures[item["id"]] = error
            print(f"{item['id']} failed: {error}", file=sys.stderr)
            try:
                reports[item["id"]] = build_report(item_dir)
            except Exception:  # noqa: BLE001 - the failure itself is the report
                pass
            continue
        except (CapExceeded, LedgerError, MetaAuthError, BudgetExceeded, OffSwitch):
            raise
        except Exception as error:  # noqa: BLE001 - one item never stops the rest
            failures[item["id"]] = error
            print(f"{item['id']} failed: {error}", file=sys.stderr)
            try:
                reports[item["id"]] = build_report(item_dir)
            except Exception:  # noqa: BLE001 - the failure itself is the report
                pass
            continue
        try:
            built[item["id"]] = reports[item["id"]] = build_report(item_dir)
        except Exception as error:  # noqa: BLE001 - one item never stops the rest
            failures[item["id"]] = error
            print(f"{item['id']} failed: {error}", file=sys.stderr)
    if not built:
        if failures:
            raise next(iter(failures.values()))
        if skipped:
            raise next(iter(skipped.values()))
    if failures:
        total = len(failures) + len(skipped) + len(built)
        print(f"{len(failures)} of {total} items failed", file=sys.stderr)
    return reports


def replay_request(
    tile: str,
    recording: Path,
    out_dir: Path,
    world: Path,
    grid_dir: Path | None,
) -> dict[str, Path]:
    """Rebuild a recorded request: no key, no model cost."""
    from .records import ReplayClient, ReplayError

    _check_tile(tile)
    plan = json.loads((recording / "plan.json").read_text(encoding="utf-8"))
    if plan.get("tile") != tile:
        raise ValueError(f"the recording plans {plan.get('tile')}, not {tile}")

    def make_ctx(item: dict[str, Any], item_dir: Path) -> JobContext:
        records = recording / "items" / item["id"] / "records"
        if not records.is_dir():
            raise ReplayError(
                f"[{item['id']}] the recording holds no answers for this item"
            )
        return JobContext(
            job_id=f"{JOB_ID}/{item['id']}",
            job_dir=item_dir,
            client=ReplayClient(records, logger=CostLogger(item_dir / "log.jsonl")),
            tracker=BudgetTracker(budget_usd=8.0),
            world_path=world,
        )

    return run_request(tile, plan, out_dir, world, grid_dir, make_ctx)


def record_request(
    tile: str,
    recording: Path,
    out_dir: Path,
    world: Path,
    grid_dir: Path | None,
    config: Config,
) -> dict[str, Path]:
    """Plan live and build with a recording client. Spends real money."""
    from .jobs import make_context
    from .meta import MetaAuthError
    from .plan import plan_tile
    from .records import RecordingClient
    from .species import source_from_config
    from .worker import git_commit

    _check_tile(tile)
    if not config.model_api_key:
        raise MetaAuthError("MODEL_API_KEY is not set")
    worker = _worker(out_dir, world, grid_dir)
    plan = plan_tile(
        tile,
        None,
        worker._grids(),
        world,
        git_commit(world),
        species_source=source_from_config(config, worker.builder_root),
    )
    recording.mkdir(parents=True, exist_ok=True)
    (recording / "plan.json").write_text(json.dumps(plan, indent=1), encoding="utf-8")

    def make_ctx(item: dict[str, Any], item_dir: Path) -> Any:
        records = recording / "items" / item["id"] / "records"
        ctx = make_context(config, f"{JOB_ID}/{item['id']}", item_dir, world)
        ctx.client = RecordingClient(ctx.client, records)
        if ctx.open_client is not None:
            ctx.open_client = RecordingClient(ctx.open_client, records)
        return ctx

    return run_request(tile, plan, out_dir, world, grid_dir, make_ctx)


def _check_tile(tile: str) -> None:
    """A tile id, or a clear error (not a job id)."""
    try:
        parse_tile(tile)
    except ValueError as error:
        raise ValueError(f"{tile!r} is not a tile id: {error}") from error
