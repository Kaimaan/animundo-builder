"""Command line for Animundo Builder.

`build` runs the pipeline as far as implemented stages go; `resume`
carries a job on; `check` runs every checker; `report` writes a round's
`report.html`; `submit` sends a finished draft on as a pull request.
Unbuilt commands say so.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from .config import load as load_config
from .jobs import fresh_state, make_context, pipeline_stages
from .ledger import CapExceeded
from .meta import MetaAuthError
from .pipeline import (
    IMPROVE_CHOICES,
    Pipeline,
    StageFailedError,
    stages_for_kind,
)
from .report import build_report
from .stages import IntakeStage
from .submit import SubmitFailed, run_submit

NOT_BUILT_YET = 3


def _check_choices(choices: list[str]) -> None:
    for choice in choices:
        if choice not in IMPROVE_CHOICES:
            raise ValueError(
                f"unknown improve choice {choice!r} "
                f"(choose from {', '.join(IMPROVE_CHOICES)})"
            )


def _explain_stop(job_id: str, error: Exception) -> int:
    if isinstance(error, CapExceeded):
        print(f"job {job_id}: stopping, {error}", file=sys.stderr)
        return 1
    if isinstance(error, MetaAuthError):
        print(f"job {job_id}: stopping, MODEL_API_KEY is not set", file=sys.stderr)
        print("resume with a key to carry on from the last finished stage")
        return NOT_BUILT_YET
    print(f"error: job {job_id}: {error.__cause__}", file=sys.stderr)
    return 1


def _finish(job_id: str, run: list[Any], state_file: Path) -> int:
    """Say what is left after the pipeline ran, or that the job is done."""
    try:
        completed = set(
            json.loads(state_file.read_text(encoding="utf-8")).get("completed", [])
        )
    except (OSError, ValueError):
        completed = set()
    rest = [stage.name for stage in run if stage.name not in completed]
    if rest:
        print(f"job {job_id}: carried on")
        print(f"next stage: {rest[0]}")
        return NOT_BUILT_YET
    print(f"job {job_id}: done")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="builder", description="Build real places into Animundo."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Build a place from a natural area.")
    build.add_argument("--area", help="Area id from the area index.")
    build.add_argument("--name", help="Area name (looked up in the index).")
    build.add_argument("--country", help="ISO 3166-1 alpha-2, with --name.")
    build.add_argument(
        "--world", required=True, help="Path to an animundo-world checkout."
    )
    build.add_argument(
        "--dry-run", action="store_true", help="Stop before pushing or uploading."
    )
    build.add_argument("--replay", metavar="RECORDING", help="Run on a recording.")
    build.add_argument(
        "--jobs-dir", default="jobs", help="Where job folders live (default: jobs)."
    )
    build.add_argument(
        "--author",
        default="Animundo Builder",
        help="Name signed on the region file (default: Animundo Builder).",
    )
    build.add_argument(
        "--kind",
        default="new_place",
        choices=("new_place", "submit", "improve"),
        help="The job kind (default: new_place).",
    )

    resume = sub.add_parser("resume", help="Carry on an interrupted job.")
    resume.add_argument("job_id")
    resume.add_argument(
        "--jobs-dir", default="jobs", help="Where job folders live (default: jobs)."
    )
    resume.add_argument(
        "--kind",
        default=None,
        choices=("new_place", "submit", "improve"),
        help="Run a different kind on this job (default: the job's kind).",
    )
    resume.add_argument(
        "--choices",
        default=None,
        help="Comma-separated improve choices (with --kind improve).",
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

    submit = sub.add_parser(
        "submit", help="Send a finished draft on as a pull request."
    )
    submit.add_argument("draft_id")
    submit.add_argument(
        "--world", required=True, help="Path to an animundo-world checkout."
    )
    submit.add_argument(
        "--jobs-dir", default="jobs", help="Where job folders live (default: jobs)."
    )
    submit.add_argument(
        "--place",
        default=None,
        help="The real place id (default: the pack's place id).",
    )
    submit.add_argument(
        "--base", default="main", help="The branch to open against (default: main)."
    )

    worker = sub.add_parser("worker", help="Run the hosted worker.")
    worker.add_argument(
        "--listen",
        action="store_true",
        help="Listen on Firestore (the Mac mode for milestone 1).",
    )
    worker.add_argument(
        "--world", required=True, help="Path to an animundo-world checkout."
    )
    worker.add_argument(
        "--jobs-dir", default="jobs", help="Where job folders live (default: jobs)."
    )
    worker.add_argument(
        "--areas",
        default="web/build/areas.json",
        help="The area index (default: web/build/areas.json).",
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


def _intake_from(args: argparse.Namespace) -> IntakeStage:
    return IntakeStage(area_id=args.area, area_name=args.name, country=args.country)


def cmd_build(args: argparse.Namespace) -> int:
    if args.replay is not None:
        return cmd_not_built_yet("build --replay")
    if args.kind == "submit":
        return cmd_not_built_yet("build --kind submit")
    if args.kind == "improve":
        print(
            "error: improve runs on an existing job: "
            "`builder resume <job_id> --kind improve --choices ...`",
            file=sys.stderr,
        )
        return 1
    world = _world_or_error(args.world)
    if world is None:
        return 1
    try:
        stage = _intake_from(args)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if args.dry_run:
        try:
            area = stage.resolve()
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        print(f"would build {area['area_id']} ({area['name']}); wrote nothing")
        return 0
    config = load_config()
    job_id = uuid.uuid4().hex[:12]
    job_dir = Path(args.jobs_dir) / job_id
    job_dir.mkdir(parents=True)
    ctx = make_context(config, job_id, job_dir, world)
    (job_dir / "state.json").write_text(
        json.dumps(
            fresh_state(
                {
                    "area": args.area,
                    "name": args.name,
                    "country": args.country,
                    "world": str(world),
                    "author": args.author,
                    "kind": args.kind,
                    "choices": [],
                }
            )
        ),
        encoding="utf-8",
    )
    stages = pipeline_stages(
        stage,
        args.author,
        config.max_new_species,
        config.reference,
        config.max_new_plants,
    )
    try:
        Pipeline(stages, job_dir / "state.json").run(ctx)
    except (StageFailedError, CapExceeded, MetaAuthError) as error:
        return _explain_stop(job_id, error)
    return _finish(job_id, stages, job_dir / "state.json")


def cmd_resume(args: argparse.Namespace) -> int:
    job_dir = Path(args.jobs_dir) / args.job_id
    state_file = job_dir / "state.json"
    if not state_file.is_file():
        print(f"error: no such job: {args.job_id}", file=sys.stderr)
        return 1
    params = json.loads(state_file.read_text(encoding="utf-8")).get("params", {})
    world = _world_or_error(params.get("world", ""))
    if world is None:
        return 1
    kind = args.kind or params.get("kind", "new_place")
    if kind == "submit":
        return cmd_not_built_yet("resume --kind submit")
    choices: list[str] = []
    if kind == "improve":
        if not args.choices:
            print("error: --kind improve needs --choices a,b,...", file=sys.stderr)
            return 1
        choices = [c.strip() for c in args.choices.split(",") if c.strip()]
        try:
            _check_choices(choices)
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
    config = load_config()
    ctx = make_context(config, args.job_id, job_dir, world)
    stage = IntakeStage(
        area_id=params.get("area"),
        area_name=params.get("name"),
        country=params.get("country"),
    )
    stages = pipeline_stages(
        stage,
        params.get("author", "Animundo Builder"),
        config.max_new_species,
        config.reference,
        config.max_new_plants,
    )
    run, missing = stages_for_kind(stages, kind, choices)
    if missing:
        print(
            f"job {args.job_id}: needs {', '.join(missing)}, not built yet",
            file=sys.stderr,
        )
        return NOT_BUILT_YET
    try:
        Pipeline(run, state_file).run(ctx)
    except (StageFailedError, CapExceeded, MetaAuthError) as error:
        return _explain_stop(args.job_id, error)
    return _finish(args.job_id, run, state_file)


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
        Path(args.areas),
        args.project,
        args.storage_bucket,
    )


def cmd_submit(args: argparse.Namespace) -> int:
    world = _world_or_error(args.world)
    if world is None:
        return 1
    try:
        record = run_submit(
            args.draft_id,
            Path(args.jobs_dir),
            world,
            place_id=args.place,
            base=args.base,
        )
    except SubmitFailed as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"draft {args.draft_id}: {record['pr_url']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "check":
        return cmd_check(args.world)
    if args.command == "build":
        return cmd_build(args)
    if args.command == "resume":
        return cmd_resume(args)
    if args.command == "report":
        return cmd_report(args.job_id, args.jobs_dir)
    if args.command == "submit":
        return cmd_submit(args)
    if args.command == "worker":
        return cmd_worker(args)
    return cmd_not_built_yet(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
