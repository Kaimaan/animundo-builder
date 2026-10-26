"""Command line for Animundo Builder.

`build` runs the pipeline as far as implemented stages go; `resume`
carries a job on; `check` runs every checker. Unbuilt commands say so.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from .config import load as load_config
from .meta import CostLogger, MetaAuthError, MetaClient
from .pipeline import BudgetTracker, JobContext, Pipeline, StageFailedError
from .stages import (
    CapabilityStage,
    IntakeStage,
    RegionStage,
    SpeciesPlanStage,
    SurveyStage,
)

NOT_BUILT_YET = 3
NEXT_STAGE = "animals"


def _pipeline_stages(intake: IntakeStage, author: str, max_new_species: int) -> list:
    return [
        intake,
        SurveyStage(),
        CapabilityStage(),
        RegionStage(author=author),
        SpeciesPlanStage(max_new_species=max_new_species),
    ]


def _explain_stop(job_id: str, error: Exception) -> int:
    if isinstance(error, MetaAuthError):
        print(f"job {job_id}: stopping, MODEL_API_KEY is not set", file=sys.stderr)
        print("resume with a key to carry on from the last finished stage")
        return NOT_BUILT_YET
    print(f"error: job {job_id}: {error.__cause__}", file=sys.stderr)
    return 1


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

    resume = sub.add_parser("resume", help="Carry on an interrupted job.")
    resume.add_argument("job_id")
    resume.add_argument(
        "--jobs-dir", default="jobs", help="Where job folders live (default: jobs)."
    )

    check = sub.add_parser("check", help="Run every checker, nothing else.")
    check.add_argument(
        "--world", required=True, help="Path to an animundo-world checkout."
    )

    sub.add_parser("worker", help="Run the hosted worker.")
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
    client = None
    if config.model_api_key:
        client = MetaClient(
            api_key=config.model_api_key,
            model=config.model,
            logger=CostLogger(job_dir / "log.jsonl"),
        )
    ctx = JobContext(
        job_id=job_id,
        job_dir=job_dir,
        client=client,
        tracker=BudgetTracker(budget_usd=config.job_budget_usd),
        world_path=world,
    )
    (job_dir / "state.json").write_text(
        json.dumps(
            {
                "params": {
                    "area": args.area,
                    "name": args.name,
                    "country": args.country,
                    "world": str(world),
                    "author": args.author,
                },
                "completed": [],
                "results": {},
                "failed": None,
            }
        ),
        encoding="utf-8",
    )
    try:
        Pipeline(
            _pipeline_stages(stage, args.author, config.max_new_species),
            job_dir / "state.json",
        ).run(ctx)
    except (StageFailedError, MetaAuthError) as error:
        return _explain_stop(job_id, error)
    print(f"job {job_id}: intake, survey, capability, region and species plan done")
    print(f"next stage: {NEXT_STAGE} (not built yet)")
    return NOT_BUILT_YET


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
    config = load_config()
    client = None
    if config.model_api_key:
        client = MetaClient(
            api_key=config.model_api_key,
            model=config.model,
            logger=CostLogger(job_dir / "log.jsonl"),
        )
    ctx = JobContext(
        job_id=args.job_id,
        job_dir=job_dir,
        client=client,
        tracker=BudgetTracker(budget_usd=config.job_budget_usd),
        world_path=world,
    )
    stage = IntakeStage(
        area_id=params.get("area"),
        area_name=params.get("name"),
        country=params.get("country"),
    )
    try:
        Pipeline(
            _pipeline_stages(
                stage,
                params.get("author", "Animundo Builder"),
                config.max_new_species,
            ),
            state_file,
        ).run(ctx)
    except (StageFailedError, MetaAuthError) as error:
        return _explain_stop(args.job_id, error)
    print(f"job {args.job_id}: carried on")
    print(f"next stage: {NEXT_STAGE} (not built yet)")
    return NOT_BUILT_YET


def cmd_check(world: str) -> int:
    """Validate the world checkout path. Real checkers land with later tasks."""
    path = _world_or_error(world)
    if path is None:
        return 1
    print(f"world: {path.resolve()}")
    print("checkers: none yet (skeleton)")
    return 0


def cmd_not_built_yet(command: str) -> int:
    print(f"error: `{command}` is not built yet", file=sys.stderr)
    return NOT_BUILT_YET


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "check":
        return cmd_check(args.world)
    if args.command == "build":
        return cmd_build(args)
    if args.command == "resume":
        return cmd_resume(args)
    return cmd_not_built_yet(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
