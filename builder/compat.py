"""Offline compatibility of one world checkout with this builder (#160.1).

`builder check --world` runs these probes: the required tools are
present, the required data parses, the import contracts the builder
relies on hold, and the world's own offline self-tests pass. A
missing or misbehaving required probe fails the command; pieces the
builder only uses opportunistically skip with a reason. Self-tests
that read built models skip, visibly, when the checkout's models
were never built: the .glb files are git-ignored build output, so
unbuilt models are unknown, never failures (#173.3). No model,
no network, no deploy, no publish.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED_TOOLS = (
    "tile_region.py",
    "check_animals.py",
    "check_tile.py",
    "check_draft.py",
    "validate_region.py",
    "adapt_home.py",
    "adapt_model.py",
)
OPTIONAL_TOOLS = (
    # taxa.py missing: taxon lookups fall back to English names.
    ("taxon.py", "taxa fall back to English names"),
)
SELFTESTS = (
    "test_tile_region.py",
    "test_check_tile.py",
    "test_food.py",
    "test_broken.py",
    "test_check_draft.py",
    "test_adapt_home.py",
    "test_adapt_model.py",
)
SELFTEST_TIMEOUT = 300
PROBE_TIMEOUT = 120

#: Self-tests that read built models (.glb). On a fresh clone they
#: fail with "build it first" (test_check_tile, test_adapt_home);
#: on a partial build test_broken fails with missing-.glb errors.
#: A failure elsewhere, or without the missing-model signature,
#: still fails, so a real regression never hides behind this list.
MODEL_SELFTESTS = ("test_check_tile.py", "test_broken.py", "test_adapt_home.py")

#: The world's own missing-model signatures in self-test output.
UNBUILT_MARKERS = (
    "build it first with tools/build_model.sh",
    ".glb is missing. Run tools/build_model.sh",
    "model file not found: res://assets/",
)

_CONTRACTS = {
    "contract tile_region.place": (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "import tile_region;"
        "assert callable(tile_region.place), 'place is not callable';"
        "print('place callable')"
    ),
    "contract check_animals": (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "import check_animals;"
        "assert hasattr(check_animals, 'Content'), 'no Content';"
        "assert hasattr(check_animals, 'Checker'), 'no Checker';"
        "print('Content and Checker present')"
    ),
}


@dataclass(frozen=True)
class Probe:
    """One compatibility verdict: pass, fail, or skip with a reason."""

    name: str
    status: str  # "pass", "fail", or "skip"
    detail: str


@dataclass
class CompatReport:
    """The whole checkout verdict `builder check --world` prints."""

    world: Path
    revision: str
    probes: list[Probe] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        counts = {"passed": 0, "failed": 0, "skipped": 0}
        for probe in self.probes:
            if probe.status == "pass":
                counts["passed"] += 1
            elif probe.status == "fail":
                counts["failed"] += 1
            else:
                counts["skipped"] += 1
        return counts

    @property
    def ok(self) -> bool:
        return all(probe.status != "fail" for probe in self.probes)


def world_revision(world: Path) -> str:
    """The checkout's short commit, or 'unknown' outside git."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=world,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    rev = proc.stdout.strip()
    return rev if proc.returncode == 0 and rev else "unknown"


def check_world(world: Path) -> CompatReport:
    """Probe one checkout; every probe runs, failures don't stop it."""
    report = CompatReport(world=world, revision="unknown")
    if not world.is_dir():
        report.probes.append(Probe("checkout", "fail", f"not a directory: {world}"))
        return report
    report.revision = world_revision(world)
    report.probes.append(Probe("checkout", "pass", f"directory: {world}"))
    tools = world / "tools"
    for name in REQUIRED_TOOLS:
        if (tools / name).is_file():
            report.probes.append(Probe(f"tool {name}", "pass", "present"))
        else:
            report.probes.append(
                Probe(f"tool {name}", "fail", "missing from this checkout")
            )
    for name, fallback in OPTIONAL_TOOLS:
        if (tools / name).is_file():
            report.probes.append(Probe(f"tool {name}", "pass", "present"))
        else:
            report.probes.append(Probe(f"tool {name}", "skip", f"absent ({fallback})"))
    report.probes.extend(_data_probes(world))
    report.probes.extend(_contract_probes(tools))
    unbuilt = missing_models(world)
    report.probes.append(_models_probe(unbuilt))
    report.probes.extend(_selftest_probes(world, tools, unbuilt))
    return report


def missing_models(world: Path) -> list[str]:
    """Library model specs with no built .glb, sorted (#160.4).

    Mirrors the world's model checker: every `models/*.json`
    animal needs `assets/animals/<id>.glb`, every
    `models/plants/*.json` needs `assets/plants/<id>.glb`.
    """
    missing: list[str] = []
    pairs = (
        (world / "models", world / "assets" / "animals"),
        (world / "models" / "plants", world / "assets" / "plants"),
    )
    for specs, built in pairs:
        if not specs.is_dir():
            continue
        for path in sorted(specs.glob("*.json")):
            if not (built / f"{path.stem}.glb").is_file():
                missing.append(f"{built.name}/{path.stem}")
    return missing


def _models_probe(unbuilt: list[str]) -> Probe:
    """Whether the checkout's library models are built (#160.4)."""
    if not unbuilt:
        return Probe("data models built", "pass", "every library model built")
    shown = ", ".join(unbuilt[:5])
    if len(unbuilt) > 5:
        shown += ", ..."
    return Probe(
        "data models built",
        "skip",
        f"{len(unbuilt)} without .glb ({shown}): git-ignored build "
        "output; run tools/build_model.sh in the world to verify",
    )


def _data_probes(world: Path) -> list[Probe]:
    from .animal_list import load_list

    probes: list[Probe] = []
    animal_list = world / "animal-list.json"
    if not animal_list.is_file():
        probes.append(Probe("data animal-list.json", "fail", "missing"))
    else:
        try:
            load_list(animal_list)
        except (OSError, ValueError) as error:
            probes.append(Probe("data animal-list.json", "fail", str(error)[:160]))
        else:
            probes.append(Probe("data animal-list.json", "pass", "homes/animals found"))
    schema_path = world / "schemas" / "region.schema.json"
    if not schema_path.is_file():
        probes.append(Probe("data region.schema.json", "fail", "missing"))
    else:
        try:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            probes.append(
                Probe("data region.schema.json", "fail", f"not JSON: {error}"[:160])
            )
        else:
            probes.append(
                Probe(
                    "data region.schema.json",
                    "pass" if isinstance(schema, dict) else "fail",
                    "schema parses" if isinstance(schema, dict) else "not an object",
                )
            )
    index_path = world / "lands" / "index.json"
    if not index_path.is_file():
        probes.append(
            Probe(
                "data lands/index.json",
                "skip",
                "absent (land styles degrade to None)",
            )
        )
    else:
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            probes.append(
                Probe(
                    "data lands/index.json",
                    "skip",
                    "unreadable (land styles degrade to None)",
                )
            )
        else:
            probes.append(
                Probe(
                    "data lands/index.json",
                    "pass" if isinstance(index, dict) else "skip",
                    "styles indexed"
                    if isinstance(index, dict)
                    else "not an object (land styles degrade)",
                )
            )
    return probes


def _contract_probes(tools: Path) -> list[Probe]:
    probes: list[Probe] = []
    for name, script in _CONTRACTS.items():
        module = "tile_region.py" if "tile_region" in name else "check_animals.py"
        if not (tools / module).is_file():
            probes.append(Probe(name, "fail", f"{module} is missing"))
            continue
        try:
            proc = subprocess.run(
                [sys.executable, "-c", script, str(tools)],
                capture_output=True,
                text=True,
                check=False,
                timeout=PROBE_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            probes.append(Probe(name, "fail", f"did not run: {error}"[:160]))
            continue
        if proc.returncode == 0:
            probes.append(Probe(name, "pass", _last_line(proc.stdout)))
        else:
            probes.append(Probe(name, "fail", _last_line(proc.stdout + proc.stderr)))
    return probes


def _selftest_probes(world: Path, tools: Path, unbuilt: list[str]) -> list[Probe]:
    probes: list[Probe] = []
    for name in SELFTESTS:
        script = tools / name
        if not script.is_file():
            probes.append(
                Probe(f"selftest {name}", "fail", "missing from this checkout")
            )
            continue
        try:
            proc = subprocess.run(
                [sys.executable, str(script)],
                cwd=world,
                capture_output=True,
                text=True,
                check=False,
                timeout=SELFTEST_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            probes.append(
                Probe(f"selftest {name}", "fail", f"did not run: {error}"[:160])
            )
            continue
        if proc.returncode == 0:
            probes.append(
                Probe(
                    f"selftest {name}",
                    "pass",
                    _last_line(proc.stdout + proc.stderr),
                )
            )
        elif _unbuilt_failure(name, proc.stdout + proc.stderr, unbuilt):
            probes.append(
                Probe(
                    f"selftest {name}",
                    "skip",
                    f"{_last_line(proc.stdout + proc.stderr)}; "
                    f"{len(unbuilt)} models unbuilt here, cannot verify",
                )
            )
        else:
            probes.append(
                Probe(f"selftest {name}", "fail", _last_line(proc.stdout + proc.stderr))
            )
    return probes


def _unbuilt_failure(name: str, output: str, unbuilt: list[str]) -> bool:
    """A model self-test failure explained by unbuilt models (#160.4).

    All three hold, or the failure stands: the checkout really
    lacks built models, the failing test is one that reads
    them, and its output carries the world's missing-model
    signature. Anything else is a real regression, never a skip.
    """
    return (
        bool(unbuilt)
        and name in MODEL_SELFTESTS
        and any(mark in output for mark in UNBUILT_MARKERS)
    )


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:160] if lines else "(no output)"


def format_report(report: CompatReport) -> str:
    """The `check --world` output: what passed, failed, and skipped."""
    lines = [f"world: {report.world}", f"revision: {report.revision}"]
    for probe in report.probes:
        lines.append(f"{probe.status} {probe.name}: {probe.detail}")
    counts = report.counts()
    lines.append(
        f"passed {counts['passed']}, "
        f"failed {counts['failed']}, "
        f"skipped {counts['skipped']}"
    )
    return "\n".join(lines) + "\n"
