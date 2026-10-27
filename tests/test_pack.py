"""Pack stage: gate on safety and the draft checks, then lay out pack/."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from builder.pipeline import BudgetTracker, JobContext
from builder.stages import pack as pack_module
from builder.stages.pack import PackStage

PASS_STUB = 'import json; print(json.dumps({"ok": True, "results": []}))\n'

FAIL_LOCALE_STUB = (
    "import json; print(json.dumps({'ok': False, 'results': ["
    "{'check': 'locale', 'status': 'fail', 'problems': ['bad key']}]}))\n"
)

PREVIEW_ONLY_STUB = (
    "import json; print(json.dumps({'ok': False, 'results': ["
    "{'check': 'models', 'status': 'fail', "
    "'problems': ['wood_sorrel: the preview sheet is missing']}]}))\n"
)


def _world(tmp_path: Path, stub: str = PASS_STUB) -> Path:
    world = tmp_path / "world"
    (world / "tools").mkdir(parents=True)
    (world / "tools" / "check_draft.py").write_text(stub)
    return world


def _job(tmp_path: Path) -> Path:
    job = tmp_path / "job"
    (job / "regions").mkdir(parents=True)
    (job / "regions" / "xx_test.json").write_text(
        json.dumps({"id": "xx_test", "name": "Test Meadow"})
    )
    (job / "species-plan.json").write_text(
        json.dumps(
            {
                "species": [
                    {"species": "red_deer", "decision": "new"},
                    {"species": "red_fox", "decision": "reuse"},
                ]
            }
        )
    )
    (job / "animals").mkdir()
    (job / "animals" / "red_deer.json").write_text(
        json.dumps({"id": "red_deer", "name": "Red deer"})
    )
    (job / "models").mkdir()
    (job / "models" / "red_deer.glb").write_bytes(b"GLB" * 10)
    (job / "models" / "red_deer.png").write_bytes(b"PNG" * 10)
    (job / "models" / "plants").mkdir()
    (job / "models" / "plants" / "wood_sorrel.glb").write_bytes(b"GLB" * 5)
    (job / "models" / "plants" / "wood_sorrel.png").write_bytes(b"PNG" * 5)
    (job / "locale" / "en").mkdir(parents=True)
    (job / "locale" / "en" / "animals.json").write_text(
        json.dumps({"animal.red_deer.title": "Red deer"})
    )
    (job / "locale" / "en" / "regions.json").write_text(
        json.dumps({"region.xx_test.name": "Test Meadow"})
    )
    (job / "safety.json").write_text(
        json.dumps(
            {"items": [{"key": "a", "verdict": "pass", "reasons": [], "rule": ""}]}
        )
    )
    return job


def _ctx(job: Path, world: Path | None) -> JobContext:
    return JobContext(
        job_id="job-1",
        job_dir=job,
        client=None,
        tracker=BudgetTracker(budget_usd=8.0),
        world_path=world,
    )


def test_pack_lays_out_spec_layout(tmp_path: Path) -> None:
    world = _world(tmp_path)
    job = _job(tmp_path)
    result = PackStage().run(_ctx(job, world))
    assert result["version"] == 1
    assert result["manifest"] == "pack/manifest.json"
    pack = job / "pack"
    region = json.loads((pack / "regions" / "draft_job-1.json").read_text())
    assert region["id"] == "draft_job-1"
    assert (pack / "animals" / "red_deer.json").is_file()
    assert (pack / "models" / "red_deer.glb").read_bytes() == b"GLB" * 10
    assert (pack / "previews" / "red_deer.png").is_file()
    assert (pack / "models" / "plants" / "wood_sorrel.glb").is_file()
    assert (pack / "previews" / "wood_sorrel.png").is_file()
    locale = json.loads((pack / "locale" / "en" / "regions.json").read_text())
    assert locale == {"region.draft_job-1.name": "Test Meadow"}
    manifest = json.loads((pack / "manifest.json").read_text())
    assert manifest["draft_id"] == "job-1"
    assert manifest["draft_place_id"] == "draft_job-1"
    assert manifest["place_id"] == "xx_test"
    assert manifest["version"] == 1
    assert manifest["format"] == 1
    assert manifest["new_species"] == ["red_deer"]
    assert manifest["reused_species"] == ["red_fox"]
    assert result["pack_files"] == len(manifest["files"])
    assert "manifest.json" not in [entry["path"] for entry in manifest["files"]]
    entry = next(e for e in manifest["files"] if e["path"] == "models/red_deer.glb")
    assert entry["bytes"] == 30
    assert entry["sha256"] == hashlib.sha256(b"GLB" * 10).hexdigest()
    assert result["pack_bytes"] == sum(e["bytes"] for e in manifest["files"])


def test_pack_gate_stops_without_safety(tmp_path: Path) -> None:
    world = _world(tmp_path)
    job = _job(tmp_path)
    (job / "safety.json").unlink()
    with pytest.raises(Exception, match="safety.*must pass first"):
        PackStage().run(_ctx(job, world))


def test_pack_gate_skips_missing_safety_for_homes(tmp_path: Path) -> None:
    world = _world(tmp_path)
    job = _job(tmp_path)
    (job / "safety.json").unlink()
    (job / "home.json").write_text(json.dumps({"latin": "Vulpes vulpes"}))
    result = PackStage(version=1).run(_ctx(job, world))
    assert result["manifest"] == "pack/manifest.json"


def test_pack_gate_stops_on_failed_verdict(tmp_path: Path) -> None:
    world = _world(tmp_path)
    job = _job(tmp_path)
    record: dict[str, Any] = json.loads((job / "safety.json").read_text())
    record["items"][0]["verdict"] = "fail"
    (job / "safety.json").write_text(json.dumps(record))
    with pytest.raises(Exception, match="safety failed 1 items"):
        PackStage().run(_ctx(job, world))


def test_pack_gate_stops_on_draft_problems(tmp_path: Path) -> None:
    world = _world(tmp_path, FAIL_LOCALE_STUB)
    job = _job(tmp_path)
    with pytest.raises(Exception, match="pack gate failed on locale"):
        PackStage().run(_ctx(job, world))
    assert (job / "checks" / "pack.json").is_file()


def test_pack_gate_ignores_missing_preview(tmp_path: Path) -> None:
    world = _world(tmp_path, PREVIEW_ONLY_STUB)
    job = _job(tmp_path)
    result = PackStage().run(_ctx(job, world))
    assert result["version"] == 1


def test_pack_refuses_a_big_model(tmp_path: Path) -> None:
    world = _world(tmp_path)
    job = _job(tmp_path)
    (job / "models" / "red_deer.glb").write_bytes(b"g" * (2 * 1024 * 1024 + 1))
    with pytest.raises(Exception, match="at most 2 MB a model"):
        PackStage().run(_ctx(job, world))


def test_pack_refuses_a_big_pack(tmp_path: Path, monkeypatch: Any) -> None:
    world = _world(tmp_path)
    job = _job(tmp_path)
    monkeypatch.setattr(pack_module, "MAX_PACK_BYTES", 10)
    with pytest.raises(Exception, match="at most 8 MB a pack"):
        PackStage().run(_ctx(job, world))


def test_pack_needs_world(tmp_path: Path) -> None:
    job = _job(tmp_path)
    with pytest.raises(ValueError, match="needs --world"):
        PackStage().run(_ctx(job, None))
