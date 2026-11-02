"""Offline world-compatibility probes: fixtures only, no checkout. (#160.1)"""

from __future__ import annotations

import json
from pathlib import Path

from builder.compat import REQUIRED_TOOLS, SELFTESTS, check_world, format_report


def make_world(
    path: Path,
    *,
    missing: tuple[str, ...] = (),
    broken_contract: bool = False,
    failing_selftest: str | None = None,
    failing_output: str | None = None,
    unbuilt_model: str | None = None,
    built_model: str | None = None,
    bad_list: bool = False,
    bad_schema: bool = False,
) -> Path:
    """A stub world checkout: tools, data and self-tests that pass."""
    tools = path / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    (tools / "tile_region.py").write_text(
        "def place(tile):\n    return {'id': tile.get('id')}\n"
        if not broken_contract
        else "PLACEHOLDER = True\n"
    )
    (tools / "check_animals.py").write_text("class Content: ...\nclass Checker: ...\n")
    for name in REQUIRED_TOOLS:
        if name in ("tile_region.py", "check_animals.py"):
            continue
        (tools / name).write_text("# stub\n")
    (tools / "taxon.py").write_text("# stub\n")
    for name in SELFTESTS:
        if name == failing_selftest:
            if failing_output is None:
                (tools / name).write_text("raise SystemExit('stub blew up')\n")
            else:
                (tools / name).write_text(
                    f"print({failing_output!r})\nraise SystemExit(1)\n"
                )
        else:
            (tools / name).write_text("print('stub ok')\n")
    if unbuilt_model is not None:
        models = path / "models"
        models.mkdir(exist_ok=True)
        (models / f"{unbuilt_model}.json").write_text(json.dumps({"id": unbuilt_model}))
    if built_model is not None:
        models = path / "models"
        models.mkdir(exist_ok=True)
        (models / f"{built_model}.json").write_text(json.dumps({"id": built_model}))
        built = path / "assets" / "animals"
        built.mkdir(parents=True, exist_ok=True)
        (built / f"{built_model}.glb").write_bytes(b"glb")
    (path / "animal-list.json").write_text(
        json.dumps({} if bad_list else {"homes": [], "not_yet": [], "animals": []})
    )
    schemas = path / "schemas"
    schemas.mkdir(exist_ok=True)
    (schemas / "region.schema.json").write_text("[" if bad_schema else "{}")
    lands = path / "lands"
    lands.mkdir(exist_ok=True)
    (lands / "index.json").write_text(json.dumps({"styles": []}))
    for name in missing:
        for candidate in (
            tools / name,
            path / name,
            path / "schemas" / name,
            path / "lands" / name,
        ):
            if candidate.is_file():
                candidate.unlink()
    return path


def by_name(report):  # type: ignore[no-untyped-def]
    return {probe.name: probe for probe in report.probes}


def test_full_stub_world_passes(tmp_path: Path) -> None:
    report = check_world(make_world(tmp_path / "world"))
    assert report.ok is True
    assert report.counts()["failed"] == 0
    assert report.counts()["skipped"] == 0
    assert report.revision == "unknown"
    text = format_report(report)
    assert "failed 0" in text


def test_missing_converter_fails_tool_and_contract(tmp_path: Path) -> None:
    report = check_world(make_world(tmp_path / "world", missing=("tile_region.py",)))
    assert report.ok is False
    probes = by_name(report)
    assert probes["tool tile_region.py"].status == "fail"
    assert probes["contract tile_region.place"].status == "fail"
    assert probes["contract check_animals"].status == "pass"


def test_broken_contract_fails_with_reason(tmp_path: Path) -> None:
    report = check_world(make_world(tmp_path / "world", broken_contract=True))
    probes = by_name(report)
    assert probes["tool tile_region.py"].status == "pass"
    assert probes["contract tile_region.place"].status == "fail"
    assert "place" in probes["contract tile_region.place"].detail


def test_failing_selftest_fails_with_tail(tmp_path: Path) -> None:
    report = check_world(
        make_world(tmp_path / "world", failing_selftest="test_food.py")
    )
    assert report.ok is False
    probes = by_name(report)
    assert probes["selftest test_food.py"].status == "fail"
    assert "stub blew up" in probes["selftest test_food.py"].detail


def test_missing_selftest_fails(tmp_path: Path) -> None:
    report = check_world(make_world(tmp_path / "world", missing=("test_food.py",)))
    assert report.ok is False
    assert by_name(report)["selftest test_food.py"].status == "fail"


def test_optional_pieces_skip_not_fail(tmp_path: Path) -> None:
    report = check_world(
        make_world(tmp_path / "world", missing=("taxon.py", "index.json"))
    )
    assert report.ok is True
    probes = by_name(report)
    assert probes["tool taxon.py"].status == "skip"
    assert probes["data lands/index.json"].status == "skip"
    assert report.counts()["skipped"] == 2


def test_bad_data_fails(tmp_path: Path) -> None:
    report = check_world(make_world(tmp_path / "world", bad_list=True, bad_schema=True))
    assert report.ok is False
    probes = by_name(report)
    assert probes["data animal-list.json"].status == "fail"
    assert probes["data region.schema.json"].status == "fail"


def test_missing_checkout_fails_alone(tmp_path: Path) -> None:
    report = check_world(tmp_path / "nope")
    assert report.ok is False
    assert [probe.name for probe in report.probes] == ["checkout"]


def test_unbuilt_models_skip_model_selftests(tmp_path: Path) -> None:
    """#160.4: unbuilt .glb is unknown, never a failure (#173.3)."""
    report = check_world(
        make_world(
            tmp_path / "world",
            failing_selftest="test_broken.py",
            failing_output="ERROR: whale: assets/animals/whale.glb is missing. "
            "Run tools/build_model.sh models/whale.json",
            unbuilt_model="whale",
        )
    )
    assert report.ok is True
    probes = by_name(report)
    assert probes["data models built"].status == "skip"
    assert "animals/whale" in probes["data models built"].detail
    skipped = probes["selftest test_broken.py"]
    assert skipped.status == "skip"
    assert "cannot verify" in skipped.detail
    assert report.counts()["failed"] == 0


def test_built_models_keep_marked_failures(tmp_path: Path) -> None:
    """#160.4: with every .glb built, a marked failure still fails."""
    report = check_world(
        make_world(
            tmp_path / "world",
            failing_selftest="test_broken.py",
            failing_output="build it first with tools/build_model.sh x",
            built_model="whale",
        )
    )
    assert report.ok is False
    probes = by_name(report)
    assert probes["data models built"].status == "pass"
    assert probes["selftest test_broken.py"].status == "fail"


def test_unbuilt_models_keep_unmarked_failures(tmp_path: Path) -> None:
    """#160.4: without the missing-model signature, a failure stands."""
    report = check_world(
        make_world(
            tmp_path / "world",
            failing_selftest="test_broken.py",
            unbuilt_model="whale",
        )
    )
    assert report.ok is False
    assert by_name(report)["selftest test_broken.py"].status == "fail"


def test_unbuilt_models_keep_other_selftests(tmp_path: Path) -> None:
    """#160.4: only the self-tests that read models can skip."""
    report = check_world(
        make_world(
            tmp_path / "world",
            failing_selftest="test_food.py",
            failing_output="build it first with tools/build_model.sh x",
            unbuilt_model="whale",
        )
    )
    assert report.ok is False
    assert by_name(report)["selftest test_food.py"].status == "fail"
