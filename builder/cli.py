"""Command line for Animundo Builder.

`stage` runs one stage of a job on its own; `check` runs every
checker; `report` writes a round's `report.html`; `worker`
listens on Firestore. Unbuilt commands say so.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load as load_config
from .jobs import make_context, pipeline_stages
from .ledger import CapExceeded
from .meta import CostLogger, MetaAuthError
from .pipeline import BudgetTracker, JobContext, Pipeline, StageFailedError
from .records import RecordingClient, ReplayClient, ReplayError
from .report import build_report

NOT_BUILT_YET = 3


def _explain_stop(job_id: str, error: Exception) -> int:
    if isinstance(error, CapExceeded):
        print(f"job {job_id}: stopping, {error}", file=sys.stderr)
        return 1
    if isinstance(error, ReplayError):
        print(f"job {job_id}: stopping, {error}", file=sys.stderr)
        return 1
    if isinstance(error, MetaAuthError):
        print(f"job {job_id}: stopping, MODEL_API_KEY is not set", file=sys.stderr)
        print("resume with a key to carry on from the last finished stage")
        return NOT_BUILT_YET
    print(f"error: job {job_id}: {error.__cause__}", file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="builder", description="Grow Animundo's world ahead of its players."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    stage = sub.add_parser("stage", help="Run one stage of a job on its own.")
    stage.add_argument("name", help="The stage to run.")
    stage.add_argument("--job", required=True, help="The job folder to run in.")
    stage.add_argument(
        "--world", default=None, help="Path to an animundo-world checkout."
    )
    stage.add_argument(
        "--record",
        action="store_true",
        help="Save the stage's model answers under the job's records/.",
    )
    stage.add_argument(
        "--replay",
        action="store_true",
        help="Run on the job's records/ instead of the model API.",
    )

    check = sub.add_parser("check", help="Run every checker, nothing else.")
    check.add_argument(
        "--world", required=True, help="Path to an animundo-world checkout."
    )

    report = sub.add_parser("report", help="Write a round's report.html.")
    report.add_argument("job_id")
    report.add_argument(
        "--jobs-dir", default="jobs", help="Where job folders live (default: jobs)."
    )

    worker = sub.add_parser("worker", help="Run the hosted worker.")
    worker.add_argument(
        "--listen",
        action="store_true",
        help="Listen on Firestore.",
    )
    worker.add_argument(
        "--world", required=True, help="Path to an animundo-world checkout."
    )
    worker.add_argument(
        "--jobs-dir", default="jobs", help="Where job folders live (default: jobs)."
    )
    worker.add_argument(
        "--project",
        default=None,
        help="The Firebase project (default: Application Default Credentials).",
    )
    worker.add_argument(
        "--storage-bucket",
        default=None,
        help="The Storage bucket (default: the project's default bucket).",
    )
    worker.add_argument(
        "--habitat-grid",
        default="data/habitat-grid",
        help="Baked habitat grids (default: data/habitat-grid).",
    )
    habitats = sub.add_parser("habitats", help="The world habitat grid.")
    habitats_sub = habitats.add_subparsers(dest="habitats_command", required=True)
    bake = habitats_sub.add_parser("bake", help="Bake the habitat grid files.")
    bake.add_argument("--resolve", required=True, help="RESOLVE shapefile path.")
    bake.add_argument("--land", required=True, help="Natural Earth land path.")
    bake.add_argument("--lakes", required=True, help="Natural Earth lakes path.")
    bake.add_argument(
        "--dem",
        default=None,
        help="Elevation GeoTIFF path (elevation is 0 without it).",
    )
    bake.add_argument(
        "--out", required=True, help="Directory for the baked grid files."
    )
    presence = sub.add_parser("presence", help="Species presence from GBIF.")
    presence_sub = presence.add_subparsers(dest="presence_command", required=True)
    fetch = presence_sub.add_parser("fetch", help="Fetch one cell's counts.")
    fetch.add_argument("--tile", required=True, help="The tile id.")
    fetch.add_argument(
        "--world", required=True, help="Path to an animundo-world checkout."
    )
    fetch.add_argument("--habitat", default=None, help="Only animals that live here.")
    tiles = sub.add_parser("tiles", help="Make tiles from the grid.")
    tiles_sub = tiles.add_subparsers(dest="tiles_command", required=True)
    make = tiles_sub.add_parser("make", help="Make one tile.")
    make.add_argument(
        "--at",
        required=True,
        help="Latitude,longitude (south of the equator: --at=-1.00,-75.50).",
    )
    make.add_argument("--out", required=True, help="Directory for group files.")
    make.add_argument(
        "--world", required=True, help="Path to an animundo-world checkout."
    )
    make.add_argument(
        "--habitat-grid",
        default="data/habitat-grid",
        help="Baked habitat grids (default: data/habitat-grid).",
    )
    lab = tiles_sub.add_parser("lab", help="Run the $0 lab over sample spots.")
    lab.add_argument("--out", required=True, help="Directory for counts and globe.")
    lab.add_argument(
        "--world", required=True, help="Path to an animundo-world checkout."
    )
    lab.add_argument(
        "--habitat-grid",
        default="data/habitat-grid",
        help="Baked habitat grids (default: data/habitat-grid).",
    )
    lab.add_argument(
        "--land",
        default=None,
        help="Natural Earth land shapefile for the globe background.",
    )
    request = sub.add_parser("request", help="Border requests: plan them, build them.")
    request_sub = request.add_subparsers(dest="request_command", required=True)
    plan = request_sub.add_parser("plan", help="Print one tile's border plan.")
    plan.add_argument("tile", help="The border tile id, e.g. n_413_478.")
    plan.add_argument(
        "--world", required=True, help="Path to an animundo-world checkout."
    )
    plan.add_argument(
        "--habitat-grid",
        default="data/habitat-grid",
        help="Baked habitat grids (default: data/habitat-grid).",
    )
    build = request_sub.add_parser("build", help="Run one build round for a job.")
    build.add_argument("job", help="The build job id.")
    build.add_argument(
        "--world", required=True, help="Path to an animundo-world checkout."
    )
    build.add_argument(
        "--habitat-grid",
        default="data/habitat-grid",
        help="Baked habitat grids (default: data/habitat-grid).",
    )
    build.add_argument("--project", default=None, help="The Firebase project.")
    build.add_argument(
        "--storage-bucket",
        default=None,
        help="The Storage bucket (default: the project's default bucket).",
    )
    build.add_argument(
        "--jobs-dir", default="jobs", help="Job folders (default: jobs)."
    )
    take = sub.add_parser("take", help="Take a job's checked items for review.")
    take.add_argument("job", help="The build job id.")
    take.add_argument("--out", required=True, help="Directory for the taken files.")
    take.add_argument(
        "--world", required=True, help="Path to an animundo-world checkout."
    )
    take.add_argument("--project", default=None, help="The Firebase project.")
    take.add_argument(
        "--storage-bucket",
        default=None,
        help="The Storage bucket (default: the project's default bucket).",
    )
    sub.add_parser("eval", help="Run the eval suite over the test places.")
    return parser


def _world_or_error(world: str) -> Path | None:
    path = Path(world)
    if not path.is_dir():
        print(f"error: not a directory: {world}", file=sys.stderr)
        return None
    return path


def _recording(ctx: JobContext, job_dir: Path) -> JobContext:
    """The context with every model answer saved under records/."""
    records = job_dir / "records"
    if ctx.client is not None:
        ctx.client = RecordingClient(ctx.client, records)
    if ctx.open_client is not None:
        ctx.open_client = RecordingClient(ctx.open_client, records)
    return ctx


def _replay_context(
    job_id: str, job_dir: Path, world: Path, records: Path, budget_usd: float
) -> JobContext:
    """A keyless context that plays a recording back at $0."""
    return JobContext(
        job_id=job_id,
        job_dir=job_dir,
        client=ReplayClient(records, logger=CostLogger(job_dir / "log.jsonl")),
        tracker=BudgetTracker(budget_usd=budget_usd),
        world_path=world,
    )


def cmd_stage(args: argparse.Namespace) -> int:
    """Run one stage of a job on its own, live, recorded or replayed."""
    if args.record and args.replay:
        print("error: --record and --replay do not mix", file=sys.stderr)
        return 1
    job_dir = Path(args.job)
    state_file = job_dir / "state.json"
    if not state_file.is_file():
        print(f"error: no such job: {args.job}", file=sys.stderr)
        return 1
    params = json.loads(state_file.read_text(encoding="utf-8")).get("params", {})
    if args.world is not None:
        world = _world_or_error(args.world)
    else:
        world = _world_or_error(params.get("world", ""))
    if world is None:
        return 1
    config = load_config()
    stages = pipeline_stages(
        params.get("author", "Animundo Builder"),
        config.max_new_species,
        config.reference,
        config.max_new_plants,
    )
    wanted = [stage for stage in stages if stage.name == args.name]
    if not wanted:
        names = ", ".join(stage.name for stage in stages)
        print(f"error: no stage {args.name!r} (stages: {names})", file=sys.stderr)
        return 1
    if args.replay:
        records = job_dir / "records"
        if not records.is_dir():
            print(f"error: no records/ in {args.job}", file=sys.stderr)
            return 1
        ctx = _replay_context(
            job_dir.name, job_dir, world, records, config.job_budget_usd
        )
    else:
        ctx = make_context(config, job_dir.name, job_dir, world)
        if args.record:
            ctx = _recording(ctx, job_dir)
    try:
        results = Pipeline(wanted, state_file).run(ctx)
        if args.replay:
            ctx.client.verify_consumed()
    except (StageFailedError, CapExceeded, MetaAuthError, ReplayError) as error:
        return _explain_stop(job_dir.name, error)
    print(json.dumps(results.get(args.name, {}), indent=2))
    return 0


def cmd_check(world: str) -> int:
    """Validate the world checkout path. Real checkers land with later tasks."""
    path = _world_or_error(world)
    if path is None:
        return 1
    print(f"world: {path.resolve()}")
    print("checkers: none yet (skeleton)")
    return 0


def cmd_report(job_id: str, jobs_dir: str) -> int:
    job_dir = Path(jobs_dir) / job_id
    if not (job_dir / "state.json").is_file():
        print(f"error: no such job: {job_id}", file=sys.stderr)
        return 1
    out = build_report(job_dir)
    print(f"job {job_id}: wrote {out}")
    return 0


def cmd_not_built_yet(command: str) -> int:
    print(f"error: `{command}` is not built yet", file=sys.stderr)
    return NOT_BUILT_YET


def cmd_worker(args: argparse.Namespace) -> int:
    if not args.listen:
        return cmd_not_built_yet("worker without --listen")
    from .worker import listen

    world = _world_or_error(args.world)
    if world is None:
        return 1
    return listen(
        load_config(),
        Path(args.jobs_dir),
        world,
        args.project,
        args.storage_bucket,
        Path(args.habitat_grid),
    )


def cmd_habitats_bake(args: argparse.Namespace) -> int:
    from .habitats import bake

    dem = Path(args.dem) if args.dem else None
    counts = bake(
        Path(args.resolve), Path(args.land), Path(args.lakes), dem, Path(args.out)
    )
    for grid, count in sorted(counts.items()):
        print(f"{grid}: {count} cells")
    return 0


def cmd_presence_fetch(args: argparse.Namespace) -> int:
    from .presence import animals_for_habitat, fetch_smoothed, library_animals

    world = _world_or_error(args.world)
    if world is None:
        return 1
    animals = library_animals(world)
    if args.habitat is not None:
        animals = animals_for_habitat(animals, args.habitat)
    try:
        counts = fetch_smoothed(args.tile, animals)
    except ConnectionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    print(json.dumps({species: round(n, 2) for species, n in ranked}, indent=1))
    return 0


def cmd_tiles_make(args: argparse.Namespace) -> int:
    from .grid import GridName
    from .habitats import HabitatGrid
    from .tiles import make_at

    try:
        lat_text, lon_text = args.at.split(",")
        lat, lon = float(lat_text), float(lon_text)
    except ValueError:
        print("error: --at wants latitude,longitude", file=sys.stderr)
        return 1
    world = _world_or_error(args.world)
    if world is None:
        return 1
    grid_dir = Path(args.habitat_grid)
    name: GridName = "ease2_n25" if lat >= 0 else "ease2_s25"
    path = grid_dir / f"{name}.json.gz"
    if not path.is_file():
        print(f"error: no baked grid: {path}", file=sys.stderr)
        return 1
    grids: dict[GridName, HabitatGrid] = {name: HabitatGrid.load(path, name)}
    try:
        tile, path, status = make_at(lat, lon, grids, world, Path(args.out))
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except ConnectionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"{tile}: {status} -> {path}")
    return 0


def cmd_tiles_lab(args: argparse.Namespace) -> int:
    from .grid import GridName
    from .habitats import HabitatGrid
    from .lab import run_lab, write_lab

    world = _world_or_error(args.world)
    if world is None:
        return 1
    grid_dir = Path(args.habitat_grid)
    grids: dict[GridName, HabitatGrid] = {}
    for name in ("ease2_n25", "ease2_s25"):
        path = grid_dir / f"{name}.json.gz"
        if not path.is_file():
            print(f"error: no baked grid: {path}", file=sys.stderr)
            return 1
        grids[name] = HabitatGrid.load(path, name)  # type: ignore[arg-type]
    try:
        results = run_lab(grids, world)
    except ConnectionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    land = Path(args.land) if args.land else None
    counts_path, globe_path = write_lab(Path(args.out), results, land)
    print(f"wrote {counts_path} and {globe_path}")
    for entry in results:
        print(f"  {entry['spot']}: {entry.get('status')} {entry.get('tile', '')}")
    return 0


def cmd_request_plan(args: argparse.Namespace) -> int:
    import json
    import subprocess

    from .grid import GridName, parse_tile
    from .habitats import HabitatGrid
    from .plan import plan_tile

    world = _world_or_error(args.world)
    if world is None:
        return 1
    try:
        grid, _col, _row = parse_tile(args.tile)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    path = Path(args.habitat_grid) / f"{grid}.json.gz"
    if not path.is_file():
        print(f"error: no baked grid: {path}", file=sys.stderr)
        return 1
    grids: dict[GridName, HabitatGrid] = {grid: HabitatGrid.load(path, grid)}
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=world,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    except OSError:
        commit = ""
    try:
        plan = plan_tile(args.tile, None, grids, world, commit or "unknown")
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except ConnectionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(plan, indent=1))
    return 0


def cmd_request_build(args: argparse.Namespace) -> int:
    from .config import load_config
    from .worker import Worker
    from .worker_store import FirebaseStore, StoreError

    world = _world_or_error(args.world)
    if world is None:
        return 1
    try:
        store = FirebaseStore(args.project, args.storage_bucket)
    except StoreError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    worker = Worker(
        store=store,
        jobs_dir=Path(args.jobs_dir),
        world=world,
        config=load_config(),
        grid_dir=Path(args.habitat_grid),
    )
    worker.handle_build_job(args.job)
    return 0


def cmd_take(args: argparse.Namespace) -> int:
    from .build import check_draft, take_bundle, take_report
    from .worker_store import FirebaseStore, StoreError

    world = _world_or_error(args.world)
    if world is None:
        return 1
    try:
        store = FirebaseStore(args.project, args.storage_bucket)
    except StoreError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    try:
        dest = take_bundle(store, args.job, Path(args.out))
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    items = sorted(p for p in dest.iterdir() if p.is_dir())
    for item_dir in items:
        try:
            problems = check_draft(item_dir, world)
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        if problems:
            print(f"error: {item_dir.name} fails the draft check:", file=sys.stderr)
            for problem in problems:
                print(f"  {problem}", file=sys.stderr)
            return 1
    report = take_report(dest)
    print(f"{args.job}: taken to {dest}, report {report.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "check":
        return cmd_check(args.world)
    if args.command == "stage":
        return cmd_stage(args)
    if args.command == "report":
        return cmd_report(args.job_id, args.jobs_dir)
    if args.command == "worker":
        return cmd_worker(args)
    if args.command == "habitats" and args.habitats_command == "bake":
        return cmd_habitats_bake(args)
    if args.command == "presence" and args.presence_command == "fetch":
        return cmd_presence_fetch(args)
    if args.command == "tiles" and args.tiles_command == "make":
        return cmd_tiles_make(args)
    if args.command == "tiles" and args.tiles_command == "lab":
        return cmd_tiles_lab(args)
    if args.command == "request" and args.request_command == "plan":
        return cmd_request_plan(args)
    if args.command == "request" and args.request_command == "build":
        return cmd_request_build(args)
    if args.command == "take":
        return cmd_take(args)
    return cmd_not_built_yet(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
