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
    survey = sub.add_parser("survey", help="The tile survey: old and new.")
    survey_sub = survey.add_subparsers(dest="survey_command", required=True)
    survey_lab = survey_sub.add_parser(
        "lab", help="Survey tiles with the new prompt and compare with old."
    )
    survey_lab.add_argument(
        "--tiles",
        default=None,
        help="Comma-separated tile ids (default: the six we have).",
    )
    survey_lab.add_argument(
        "--old", required=True, help="JSON file with the old answers by tile."
    )
    survey_lab.add_argument(
        "--out", required=True, help="Directory for new.json and comparison.md."
    )
    survey_lab.add_argument(
        "--world", required=True, help="Path to an animundo-world checkout."
    )
    survey_lab.add_argument(
        "--habitat-grid",
        default="data/habitat-grid",
        help="Baked habitat grids (default: data/habitat-grid).",
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
    build.add_argument(
        "job",
        nargs="?",
        default=None,
        help="The build job id, or a tile id with --replay/--record.",
    )
    build.add_argument(
        "--local",
        default=None,
        metavar="DIR",
        help="A place folder (survey.json, plan.json): build it locally.",
    )
    build.add_argument(
        "--replay",
        default=None,
        metavar="DIR",
        help="Rebuild a recorded request locally: no key, no model cost.",
    )
    build.add_argument(
        "--record",
        default=None,
        metavar="DIR",
        help="Plan live and record the build into DIR. Spends real money.",
    )
    build.add_argument(
        "--out",
        default=None,
        help="Directory for the local job (default: replay-<tile>).",
    )
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
    take.add_argument("job", nargs="?", default=None, help="The build job id.")
    take.add_argument(
        "--local",
        default=None,
        metavar="DIR",
        help="A local job folder: take it instead of a Storage job.",
    )
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
    place = sub.add_parser("place", help="Place requests as files (D20).")
    place_sub = place.add_subparsers(dest="place_command", required=True)
    survey = place_sub.add_parser("survey", help="Survey one place, write survey.json.")
    survey.add_argument("place", help="The place to survey, in words.")
    survey.add_argument(
        "--want",
        default="",
        help="Comma-separated animal ids, kept first.",
    )
    survey.add_argument(
        "--improve",
        default=None,
        help="Survey an existing place: a region id or a tile id.",
    )
    survey.add_argument(
        "--budget", type=float, required=True, help="The request's budget in USD."
    )
    survey.add_argument("--out", required=True, help="The request folder to write.")
    survey.add_argument(
        "--world", required=True, help="Path to an animundo-world checkout."
    )
    survey.add_argument(
        "--habitat-grid",
        default="data/habitat-grid",
        help="Baked habitat grids (default: data/habitat-grid).",
    )
    survey.add_argument(
        "--record",
        action="store_true",
        help="Save the survey answer under the request's records/.",
    )
    survey.add_argument(
        "--replay",
        action="store_true",
        help="Run on the request's records/ instead of the model API.",
    )
    plan = place_sub.add_parser("plan", help="Plan a surveyed place, write plan.json.")
    plan.add_argument("dir", help="The request folder with survey.json.")
    plan.add_argument(
        "--world", required=True, help="Path to an animundo-world checkout."
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


def cmd_place_survey(args: argparse.Namespace) -> int:
    from .animal_list import find_list, load_list
    from .grid import GridName
    from .habitats import HabitatGrid
    from .jobs import make_context
    from .place import (
        SurveyError,
        ask_survey,
        check_survey,
        improve_tile,
        list_homes,
        write_survey,
    )

    if args.record and args.replay:
        print("error: --record and --replay do not mix", file=sys.stderr)
        return 1
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
    out = Path(args.out)
    want = [w for w in args.want.split(",") if w] if args.want else []
    try:
        improve = improve_tile(args.improve, world) if args.improve else None
    except SurveyError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    config = load_config()
    if args.replay:
        records = out / "records"
        if not records.is_dir():
            print(f"error: no records/ in {args.out}", file=sys.stderr)
            return 1
        ctx = _replay_context(out.name, out, world, records, args.budget)
    else:
        ctx = make_context(config, out.name, out, world)
        if ctx.client is None:
            print("error: MODEL_API_KEY is not set", file=sys.stderr)
            return 1
        if args.record:
            ctx = _recording(ctx, out)
    ctx.client.set_stage("survey")
    animals = load_list(find_list(world))["animals"]
    try:
        data, searches = ask_survey(
            ctx, args.place, animals, list_homes(world), want, improve
        )
    except SurveyError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    try:
        survey = check_survey(
            data, args.place, grids, world, animals, want, args.budget, improve
        )
    except SurveyError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    path = write_survey(out, survey)
    if args.replay:
        try:
            ctx.client.verify_consumed()
        except ReplayError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
    print(f"wrote {path} ({searches} searches, {len(survey['animals'])} animals)")
    return 0


def cmd_place_plan(args: argparse.Namespace) -> int:
    from .place import plan_place, write_plan
    from .worker import git_commit

    world = _world_or_error(args.world)
    if world is None:
        return 1
    survey_path = Path(args.dir) / "survey.json"
    if not survey_path.is_file():
        print(f"error: no survey.json in {args.dir}", file=sys.stderr)
        return 1
    try:
        survey = json.loads(survey_path.read_text(encoding="utf-8"))
    except ValueError as error:
        print(f"error: survey.json is not JSON: {error}", file=sys.stderr)
        return 1
    plan = plan_place(survey, world, git_commit(world))
    path = write_plan(Path(args.dir), plan)
    print(f"wrote {path} ({len(plan['items'])} items, ${plan['estimate_usd']:.2f})")
    return 0


def cmd_check(world: str) -> int:
    """Probe one world checkout for compatibility with this builder (#160.1)."""
    from .compat import check_world, format_report

    path = _world_or_error(world)
    if path is None:
        return 1
    report = check_world(path.resolve())
    print(format_report(report), end="")
    return 0 if report.ok else 1


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
    from .species import source_from_config
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
        tile, path, status = make_at(
            lat,
            lon,
            grids,
            world,
            Path(args.out),
            species_source=source_from_config(load_config()),
        )
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
    from .species import source_from_config

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
        results = run_lab(
            grids, world, species_source=source_from_config(load_config())
        )
    except ConnectionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    land = Path(args.land) if args.land else None
    counts_path, globe_path = write_lab(Path(args.out), results, land)
    print(f"wrote {counts_path} and {globe_path}")
    for entry in results:
        print(f"  {entry['spot']}: {entry.get('status')} {entry.get('tile', '')}")
    return 0


def cmd_survey_lab(args: argparse.Namespace) -> int:
    from .grid import GridName
    from .habitats import HabitatGrid
    from .species import list_animals, source_from_config
    from .survey_lab import TILES, run_lab, write_lab

    world = _world_or_error(args.world)
    if world is None:
        return 1
    try:
        old = json.loads(Path(args.old).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"error: cannot read old answers: {error}", file=sys.stderr)
        return 1
    grid_dir = Path(args.habitat_grid)
    grids: dict[GridName, HabitatGrid] = {}
    for name in ("ease2_n25", "ease2_s25"):
        path = grid_dir / f"{name}.json.gz"
        if not path.is_file():
            print(f"error: no baked grid: {path}", file=sys.stderr)
            return 1
        grids[name] = HabitatGrid.load(path, name)  # type: ignore[arg-type]
    tiles = args.tiles.split(",") if args.tiles else list(TILES)
    try:
        results = run_lab(tiles, grids, world, source_from_config(load_config()))
    except ConnectionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    animals = list_animals(world)
    new_path, comparison_path = write_lab(Path(args.out), old, results, animals)
    print(f"wrote {new_path} and {comparison_path}")
    for entry in results:
        print(f"  {entry['tile']}: {entry.get('status')}")
    return 0


def cmd_request_plan(args: argparse.Namespace) -> int:
    import json
    import subprocess

    from .grid import GridName, parse_tile
    from .habitats import HabitatGrid
    from .plan import plan_tile
    from .species import source_from_config

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
        plan = plan_tile(
            args.tile,
            None,
            grids,
            world,
            commit or "unknown",
            species_source=source_from_config(load_config()),
        )
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except ConnectionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(plan, indent=1))
    return 0


def cmd_request_build(args: argparse.Namespace) -> int:
    from .worker import Worker
    from .worker_store import FirebaseStore, StoreError

    world = _world_or_error(args.world)
    if world is None:
        return 1
    if args.replay is not None or args.record is not None or args.local is not None:
        return cmd_request_build_local(args, world)
    if args.out is not None:
        print("error: --out needs --replay, --record or --local", file=sys.stderr)
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


def cmd_request_build_local(args: argparse.Namespace, world: Path) -> int:
    """One border request locally: record it live, or replay it keyless."""
    from .ledger import CapExceeded
    from .meta import MetaAuthError
    from .pipeline import StageFailedError
    from .records import ReplayError
    from .replay import record_request, replay_request

    if args.replay is not None and args.record is not None:
        print("error: --replay and --record do not mix", file=sys.stderr)
        return 1
    if args.local is not None:
        return cmd_place_build(args, world)
    if args.job is None:
        print("error: a job id or --local DIR", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out is not None else Path(f"replay-{args.job}")
    try:
        if args.replay is not None:
            reports = replay_request(
                args.job,
                Path(args.replay),
                out,
                world,
                Path(args.habitat_grid),
            )
        else:
            reports = record_request(
                args.job,
                Path(args.record),
                out,
                world,
                Path(args.habitat_grid),
                load_config(),
            )
    except (StageFailedError, CapExceeded, MetaAuthError, ReplayError) as error:
        return _explain_stop(f"replay-{args.job}", error)
    except (ValueError, FileNotFoundError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for item_id, report in sorted(reports.items()):
        print(f"{item_id}: report {report}")
    return 0


def cmd_place_build(args: argparse.Namespace, world: Path) -> int:
    """One place job locally: replay its own answers, or record them live."""
    from .ledger import CapExceeded
    from .meta import MetaAuthError
    from .pipeline import StageFailedError
    from .place import record_place, replay_place
    from .records import ReplayError

    place_dir = Path(args.local)
    for flag in (args.replay, args.record):
        if flag is not None and Path(flag) != place_dir:
            print(
                "error: with --local the answers live under DIR itself",
                file=sys.stderr,
            )
            return 1
    out = Path(args.out) if args.out is not None else Path(f"replay-{place_dir.name}")
    try:
        if args.record is not None:
            reports, region = record_place(
                place_dir, out, world, Path(args.habitat_grid), load_config()
            )
        else:
            reports, region = replay_place(
                place_dir, out, world, Path(args.habitat_grid)
            )
    except (StageFailedError, CapExceeded, MetaAuthError, ReplayError) as error:
        return _explain_stop(f"replay-{place_dir.name}", error)
    except (ValueError, FileNotFoundError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for item_id, report in sorted(reports.items()):
        print(f"{item_id}: report {report}")
    print(f"region: {region}")
    return 0


def cmd_take(args: argparse.Namespace) -> int:
    from .build import check_draft, take_bundle, take_report
    from .worker_store import FirebaseStore, StoreError

    world = _world_or_error(args.world)
    if world is None:
        return 1
    if args.local is not None:
        from .place import take_place_job

        try:
            dest = take_place_job(Path(args.local), Path(args.out), world)
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        print(f"{Path(args.local).name}: taken to {dest}")
        return 0
    if args.job is None:
        print("error: a job id or --local DIR", file=sys.stderr)
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
    if args.command == "survey" and args.survey_command == "lab":
        return cmd_survey_lab(args)
    if args.command == "place" and args.place_command == "survey":
        return cmd_place_survey(args)
    if args.command == "place" and args.place_command == "plan":
        return cmd_place_plan(args)
    if args.command == "request" and args.request_command == "plan":
        return cmd_request_plan(args)
    if args.command == "request" and args.request_command == "build":
        return cmd_request_build(args)
    if args.command == "take":
        return cmd_take(args)
    return cmd_not_built_yet(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
