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
    )


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
    return cmd_not_built_yet(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
