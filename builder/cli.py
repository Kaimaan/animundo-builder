"""Command line for Animundo Builder.

Skeleton: only `check` does anything yet. The other commands exist so scripts
and CI can rely on the interface while the pipeline is built task by task.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

NOT_BUILT_YET = 3


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

    resume = sub.add_parser("resume", help="Carry on an interrupted job.")
    resume.add_argument("job_id")

    check = sub.add_parser("check", help="Run every checker, nothing else.")
    check.add_argument(
        "--world", required=True, help="Path to an animundo-world checkout."
    )

    sub.add_parser("worker", help="Run the hosted worker.")
    sub.add_parser("eval", help="Run the eval suite over the test places.")
    return parser


def cmd_check(world: str) -> int:
    """Validate the world checkout path. Real checkers land with later tasks."""
    path = Path(world)
    if not path.is_dir():
        print(f"error: not a directory: {world}", file=sys.stderr)
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
    return cmd_not_built_yet(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
