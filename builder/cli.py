"""Command line for Animundo Builder.

`build` runs the pipeline as far as implemented stages go; `resume`
carries a job on; `check` runs every checker; `report` writes a round's
`report.html`. Unbuilt commands say so.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from .config import load as load_config
from .ledger import CapExceeded, Caps, FileLedger
from .meta import CostLogger, MetaAuthError, MetaClient
from .pipeline import (
    IMPROVE_CHOICES,
    BudgetTracker,
    JobContext,
    Pipeline,
    StageFailedError,
    stages_for_kind,
)
from .prices import UnknownModelError
from .report import build_report
from .stages import (
    AnimalsStage,
    CapabilityStage,
    IntakeStage,
    ModelsStage,
    PlantsStage,
    ReferencesStage,
    RegionStage,
    SafetyStage,
    SpeciesPlanStage,
    SurveyStage,
    WordsStage,
)

NOT_BUILT_YET = 3
NEXT_STAGE = "pack"


def _pipeline_stages(
    intake: IntakeStage,
    author: str,
    max_new_species: int,
    reference: str,
    max_new_plants: int = 3,
) -> list:
    return [
        intake,
        SurveyStage(),
        CapabilityStage(),
        RegionStage(author=author),
        SpeciesPlanStage(max_new_species=max_new_species),
        AnimalsStage(),
        ReferencesStage(reference=reference),
        ModelsStage(reference=reference, author=author),
        PlantsStage(max_new_plants=max_new_plants, author=author),
        WordsStage(),
        SafetyStage(),
    ]


def _check_choices(choices: list[str]) -> None:
    for choice in choices:
        if choice not in IMPROVE_CHOICES:
            raise ValueError(
                f"unknown improve choice {choice!r} "
                f"(choose from {', '.join(IMPROVE_CHOICES)})"
            )


def _caps(config: Any) -> Caps:
    return Caps(
        job_usd=config.job_budget_usd,
        day_usd=config.daily_budget_usd,
        total_usd=config.total_budget_usd,
    )


def _open_client(config: Any, job_dir: Path, job_id: str) -> MetaClient | None:
    """The open-weights client, or None when reviews stay on Muse Spark."""
    if config.open_model_host == "meta":
        return None
    try:
        return MetaClient.for_open_weights(
            config,
            logger=CostLogger(job_dir / "log.jsonl"),
            ledger=FileLedger(config.baseten_ledger_path()),
            job_id=job_id,
            caps=Caps(
                job_usd=config.job_budget_usd,
                day_usd=config.daily_budget_usd,
                total_usd=config.baseten_total_usd,
            ),
        )
    except (MetaAuthError, ValueError, UnknownModelError) as error:
        print(
            f"open weights off ({config.open_model_host}): {error}; "
            "reviews run on Muse Spark"
        )
        return None


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
    client = None
    if config.model_api_key:
        client = MetaClient(
            api_key=config.model_api_key,
            model=config.model,
            logger=CostLogger(job_dir / "log.jsonl"),
            ledger=FileLedger(config.ledger_path),
            job_id=job_id,
            caps=_caps(config),
        )
    ctx = JobContext(
        job_id=job_id,
        job_dir=job_dir,
        client=client,
        tracker=BudgetTracker(budget_usd=config.job_budget_usd),
        world_path=world,
        open_client=_open_client(config, job_dir, job_id),
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
                    "kind": args.kind,
                    "choices": [],
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
            _pipeline_stages(
                stage,
                args.author,
                config.max_new_species,
                config.reference,
                config.max_new_plants,
            ),
            job_dir / "state.json",
        ).run(ctx)
    except (StageFailedError, CapExceeded, MetaAuthError) as error:
        return _explain_stop(job_id, error)
    print(
        "job "
        + job_id
        + ": intake, survey, capability, region, species plan, animals, "
        + "references, models done"
    )
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
    client = None
    if config.model_api_key:
        client = MetaClient(
            api_key=config.model_api_key,
            model=config.model,
            logger=CostLogger(job_dir / "log.jsonl"),
            ledger=FileLedger(config.ledger_path),
            job_id=args.job_id,
            caps=_caps(config),
        )
    ctx = JobContext(
        job_id=args.job_id,
        job_dir=job_dir,
        client=client,
        tracker=BudgetTracker(budget_usd=config.job_budget_usd),
        world_path=world,
        open_client=_open_client(config, job_dir, args.job_id),
    )
    stage = IntakeStage(
        area_id=params.get("area"),
        area_name=params.get("name"),
        country=params.get("country"),
    )
    stages = _pipeline_stages(
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
    return cmd_not_built_yet(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
