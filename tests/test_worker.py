"""The worker's flows, against an in-memory store. No Firebase."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from builder import worker as worker_module
from builder.config import Config
from builder.ledger import CapExceeded, Caps, FileLedger
from builder.pipeline import StageFailedError
from builder.stages.capability import NotPossibleError
from builder.stages.pack import draft_place_id
from builder.worker import DELETE, RoundFailed, Worker
from builder.worker_store import STORE_METHODS, FirebaseStore

AREA = {
    "area_id": "fi_repovesi",
    "name": "Repovesi",
    "country": "FI",
    "centroid": {"lat": 61.18, "lon": 26.88},
    "biome": "boreal forest",
    "buildable": True,
}


class FakeStore:
    """An in-memory Firestore + Storage."""

    def __init__(self) -> None:
        self.drafts: dict[str, dict[str, Any]] = {}
        self.feed: dict[str, list[dict[str, Any]]] = {}
        self.inbox: list[dict[str, Any]] = []
        self.jobs: dict[str, dict[str, Any]] = {}
        self.public: dict[str, dict[str, Any]] = {}
        self.watchers: list[tuple[str, Any]] = []
        self.actions: dict[str, dict[str, Any]] = {}
        self.uploads: list[tuple[str, ...]] = []
        self.deleted: list[str] = []
        self._lock = threading.Lock()

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        return self.drafts.get(draft_id)

    def claim_draft(self, draft_id: str, worker_id: str) -> bool:
        with self._lock:
            doc = self.drafts.get(draft_id)
            if doc is None or doc.get("status") != "requested":
                return False
            doc["status"] = "queued"
            doc["worker_id"] = worker_id
            return True

    def update_draft(self, draft_id: str, fields: dict[str, Any]) -> None:
        doc = self.drafts[draft_id]
        for key, value in fields.items():
            if value is DELETE:
                doc.pop(key, None)
            else:
                doc[key] = value

    def drafts_by_owner(self, owner_uid: str) -> list[tuple[str, dict[str, Any]]]:
        return [
            (doc_id, doc)
            for doc_id, doc in self.drafts.items()
            if doc.get("owner_uid") == owner_uid
        ]

    def requested_drafts(self) -> list[tuple[str, dict[str, Any]]]:
        return [
            (doc_id, doc)
            for doc_id, doc in self.drafts.items()
            if doc.get("status") == "requested"
        ]

    def drafts_in_statuses(
        self, statuses: list[str]
    ) -> list[tuple[str, dict[str, Any]]]:
        return [
            (doc_id, doc)
            for doc_id, doc in self.drafts.items()
            if doc.get("status") in statuses
        ]

    def add_feed(self, draft_id: str, entry: dict[str, Any]) -> None:
        self.feed.setdefault(draft_id, []).append(entry)

    def feed_count(self, draft_id: str) -> int:
        return len(self.feed.get(draft_id, []))

    def add_inbox(self, item: dict[str, Any]) -> str:
        self.inbox.append(item)
        return f"inbox-{len(self.inbox)}"

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self.jobs.get(job_id)

    def set_job(self, job_id: str, doc: dict[str, Any]) -> None:
        self.jobs[job_id] = doc

    def update_job(self, job_id: str, fields: dict[str, Any]) -> None:
        self.jobs[job_id].update(fields)

    def set_public(self, area_id: str, doc: dict[str, Any]) -> None:
        self.public[area_id] = doc

    def get_action(self, action_id: str) -> dict[str, Any] | None:
        return self.actions.get(action_id)

    def update_action(self, action_id: str, fields: dict[str, Any]) -> None:
        self.actions[action_id].update(fields)

    def pending_actions(self) -> list[tuple[str, dict[str, Any]]]:
        return [
            (action_id, doc)
            for action_id, doc in self.actions.items()
            if not doc.get("done_at")
        ]

    def upload_pack(self, draft_id: str, version: int, pack_dir: Path) -> None:
        self.uploads.append(("pack", draft_id, str(version), str(pack_dir)))

    def upload_report(self, draft_id: str, round_no: int, report: Path) -> None:
        self.uploads.append(("report", draft_id, str(round_no), str(report)))

    def delete_pack(self, draft_id: str) -> None:
        self.deleted.append(draft_id)

    def watch_drafts(self, callback: Any) -> Any:
        self.watchers.append(("drafts", callback))
        return lambda: None

    def watch_actions(self, callback: Any) -> Any:
        self.watchers.append(("actions", callback))
        return lambda: None


def _areas(tmp_path: Path, area: dict[str, Any] | None = AREA) -> Path:
    path = tmp_path / "areas.json"
    path.write_text(json.dumps([area] if area else []))
    return path


def _worker(
    tmp_path: Path, store: FakeStore, area: dict[str, Any] | None = AREA
) -> Worker:
    return Worker(
        store=store,
        jobs_dir=tmp_path / "jobs",
        world=tmp_path / "world",
        areas_path=_areas(tmp_path, area),
        config=Config(ledger_path=str(tmp_path / "ledger.json")),
    )


def _seed_pack(job_dir: Path, version: int = 1) -> None:
    pack = job_dir / "pack"
    (pack / "regions").mkdir(parents=True)
    (pack / "manifest.json").write_text(
        json.dumps(
            {
                "draft_id": job_dir.name,
                "draft_place_id": f"draft_{job_dir.name}",
                "place_id": "fi_repovesi",
                "version": version,
                "format": 1,
                "files": [],
                "new_species": ["red_deer"],
                "reused_species": [],
            }
        )
    )
    regions = job_dir / "regions"
    regions.mkdir(exist_ok=True)
    (regions / "fi_repovesi.json").write_text(
        json.dumps(
            {
                "id": "fi_repovesi",
                "name": "Repovesi",
                "country": "FI",
                "center": {"lat": 61.184, "lon": 26.881},
            }
        )
    )


class ScriptRunner:
    """Plays back stage results, recording the stages it ran."""

    def __init__(self, script: dict[str, Any] | None = None) -> None:
        self.script = script or {}
        self.ran: list[str] = []
        self.versions: list[int] = []

    def __call__(self, stage: Any, ctx: Any, state_file: Path) -> dict[str, Any]:
        self.ran.append(stage.name)
        if stage.name == "pack":
            self.versions.append(stage.version)
        action = self.script.get(stage.name, {})
        if isinstance(action, list):
            action = action.pop(0)
        if isinstance(action, Exception):
            raise action
        return dict(action)


def _draft(owner: str = "grown", **over: Any) -> dict[str, Any]:
    doc = {
        "kind": "new_place",
        "area_id": "fi_repovesi",
        "owner_uid": owner,
        "family_uids": [owner],
        "status": "requested",
    }
    doc.update(over)
    return doc


def test_stores_expose_every_worker_method() -> None:
    """Inbox 2026-10-28 #94.2: #81 nested half the real class inside a
    function and no test noticed. The fake and the real store must both
    expose every method the worker calls."""
    assert len(STORE_METHODS) == len(set(STORE_METHODS))
    for name in STORE_METHODS:
        assert callable(getattr(FakeStore, name, None)), f"fake lacks {name}"
        assert callable(getattr(FirebaseStore, name, None)), f"real lacks {name}"


def test_note_draft_finds_creates(tmp_path: Path) -> None:
    worker = _worker(tmp_path, FakeStore())
    assert worker.note_draft("d1", None, _draft()) == [("create", "d1")]
    assert worker.note_draft("d2", None, _draft(status="ready")) == []


def test_note_draft_retries_a_failed_draft(tmp_path: Path) -> None:
    worker = _worker(tmp_path, FakeStore())
    failed = _draft(status="failed", error="build_failed")
    assert worker.note_draft("d1", failed, _draft()) == [("create", "d1")]
    # Still requested is still running: no second event.
    assert worker.note_draft("d1", _draft(), _draft()) == []


def test_note_draft_finds_updates_and_restarts(tmp_path: Path) -> None:
    worker = _worker(tmp_path, FakeStore())
    ready = _draft(status="ready")
    assert worker.note_draft("d1", None, ready) == []
    improved = dict(ready, improve={"choices": [{"choice": "other"}]})
    assert worker.note_draft("d1", ready, improved) == [("improve", "d1")]
    # A restart sees the improve still waiting.
    fresh = _worker(tmp_path, FakeStore())
    assert fresh.note_draft("d1", None, improved) == [("improve", "d1")]
    submitted = dict(ready, submit={"at": "now"})
    assert worker.note_draft("d2", ready, submitted) == [("submit", "d2")]
    reported = dict(ready, feedback={"kind": "problem", "reason": "other"})
    assert worker.note_draft("d3", ready, reported) == [("feedback", "d3")]
    # A handled problem stays quiet.
    hidden = dict(reported, status="hidden")
    assert fresh.note_draft("d3", None, hidden) == []


def test_note_draft_skips_in_flight(tmp_path: Path) -> None:
    worker = _worker(tmp_path, FakeStore())
    worker.in_flight.add(("create", "d1"))
    assert worker.note_draft("d1", None, _draft()) == []


def test_create_builds_ready_with_feed_and_cost(tmp_path: Path) -> None:
    store = FakeStore()
    store.drafts["d1"] = _draft()
    worker = _worker(tmp_path, store)
    runner = ScriptRunner(
        {
            "survey": {"sources": 4, "species": 9},
            "models": {"model_files": ["red_deer.json"]},
        }
    )
    worker.runner = runner
    job_dir = tmp_path / "jobs" / "d1"
    _seed_pack(job_dir)
    (job_dir / "log.jsonl").write_text(
        '{"model": "m", "input_tokens": 10, "output_tokens": 5, '
        '"searches": 2, "images": 1, "generated": 1, "cost_usd": 0.5}\n'
        "not json\n"
    )
    ledger = FileLedger(tmp_path / "ledger.json")
    ledger.settle(ledger.reserve("d1-r1", 0.75, Caps(8.0, 20.0, 120.0)), 0.75)
    worker.handle_create("d1")
    draft = store.drafts["d1"]
    assert draft["status"] == "ready"
    assert draft["round"] == 1
    assert draft["pack_version"] == 1
    assert draft["place_name"] == "Repovesi"
    assert draft["country"] == "FI"
    assert draft["center"] == {"lat": 61.18, "lon": 26.88}
    assert draft["draft_place_id"] == draft_place_id("d1")
    assert draft["place_id"] == "fi_repovesi"
    assert runner.ran[0] == "intake" and runner.ran[-1] == "pack"
    assert runner.versions == [1]
    keys = [entry["text_key"] for entry in store.feed["d1"]]
    assert keys[0] == "explore.feed.round_started"
    assert "explore.feed.sources_found" in keys
    assert "explore.feed.models_built" in keys
    assert "explore.feed.preview" in keys
    assert keys[-1] == "explore.feed.round_done"
    assert all(
        entry["text_key"].startswith("explore.feed.") for entry in store.feed["d1"]
    )
    assert [entry["n"] for entry in store.feed["d1"]] == list(
        range(len(store.feed["d1"]))
    )
    preview = next(entry for entry in store.feed["d1"] if entry["kind"] == "preview")
    assert preview["image"] == "packs/d1/1/previews/red_deer.png"
    job = store.jobs["d1-r1"]
    assert job["kind"] == "new_place" and job["round"] == 1
    assert job["stages"]["pack"]["result"] == "ok"
    assert job["cost"]["m"] == {
        "input_tokens": 10,
        "output_tokens": 5,
        "searches": 2,
        "images": 2,
        "cost_usd": 0.5,
    }
    assert draft["cost_usd"] == 0.75
    assert ("pack", "d1", "1", str(job_dir / "pack")) in store.uploads
    assert ("report", "d1", "1", str(job_dir / "report.html")) in store.uploads
    assert store.inbox == []


def test_claim_draft_leaves_one_winner() -> None:
    store = FakeStore()
    store.drafts["d1"] = _draft()
    assert store.claim_draft("d1", "mac:111") is True
    assert store.claim_draft("d1", "mac:222") is False
    assert store.claim_draft("missing", "mac:222") is False
    assert store.drafts["d1"]["status"] == "queued"
    assert store.drafts["d1"]["worker_id"] == "mac:111"


def test_two_workers_on_one_draft_only_one_builds(tmp_path: Path) -> None:
    store = FakeStore()
    store.drafts["d1"] = _draft()
    runners = []
    workers = []
    for name in ("w1", "w2"):
        base = tmp_path / name
        base.mkdir()
        worker = _worker(base, store)
        worker.worker_id = name
        worker.runner = ScriptRunner(
            {
                "survey": {"sources": 4, "species": 9},
                "models": {"model_files": ["red_deer.json"]},
            }
        )
        runners.append(worker.runner)
        workers.append(worker)
        _seed_pack(base / "jobs" / "d1")
    errors: list[str] = []

    def build(worker: Worker) -> None:
        try:
            worker.handle_create("d1")
        except Exception as error:  # noqa: BLE001 - threads must report
            errors.append(f"{worker.worker_id}: {error!r}")

    threads = [threading.Thread(target=build, args=(w,)) for w in workers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    built = [runner for runner in runners if runner.ran]
    assert len(built) == 1
    assert store.drafts["d1"]["status"] == "ready"
    winner = workers[runners.index(built[0])]
    assert store.drafts["d1"]["worker_id"] == winner.worker_id


def _seed_home(job_dir: Path) -> None:
    pack = job_dir / "pack"
    (pack / "regions").mkdir(parents=True)
    (pack / "manifest.json").write_text(
        json.dumps(
            {
                "draft_id": job_dir.name,
                "draft_place_id": f"draft_{job_dir.name}",
                "place_id": "fi_home_601_249_fox",
                "version": 1,
                "format": 1,
                "files": [],
                "new_species": [],
                "reused_species": ["fox", "hare", "lynx"],
            }
        )
    )
    regions = job_dir / "regions"
    regions.mkdir(exist_ok=True)
    (regions / "fi_home_601_249_fox.json").write_text(
        json.dumps(
            {
                "id": "fi_home_601_249_fox",
                "name": "Fox copse",
                "country": "Finland",
                "center": {"lat": 60.151, "lon": 24.951},
            }
        )
    )


def _home_draft(owner: str = "grown", **over: Any) -> dict[str, Any]:
    doc = {
        "kind": "animal_home",
        "species_key": 5219243,
        "cell": "60.1_24.9",
        "owner_uid": owner,
        "family_uids": [owner],
        "status": "requested",
    }
    doc.update(over)
    return doc


def test_create_home_runs_home_subset(tmp_path: Path) -> None:
    store = FakeStore()
    store.drafts["d1"] = _home_draft()
    worker = _worker(tmp_path, store)
    runner = ScriptRunner()
    worker.runner = runner
    job_dir = tmp_path / "jobs" / "d1"
    _seed_home(job_dir)
    worker.handle_create("d1")
    draft = store.drafts["d1"]
    assert draft["status"] == "ready"
    assert draft["place_name"] == "Fox copse"
    assert draft["place_id"] == "fi_home_601_249_fox"
    assert runner.ran == [
        "intake",
        "research",
        "minimum",
        "looks",
        "newhome",
        "home",
        "oneshot",
        "words",
        "pack",
    ]
    assert store.jobs["d1-r1"]["kind"] == "animal_home"
    assert ("pack", "d1", "1", str(job_dir / "pack")) in store.uploads
    assert ("report", "d1", "1", str(job_dir / "report.html")) in store.uploads
    assert store.inbox == []


def test_create_home_in_place_runs_reuse_subset(tmp_path: Path) -> None:
    store = FakeStore()
    store.drafts["d1"] = _home_draft(place_id="tt_park")
    worker = _worker(tmp_path, store)
    runner = ScriptRunner()
    worker.runner = runner
    job_dir = tmp_path / "jobs" / "d1"
    _seed_home(job_dir)
    manifest_path = job_dir / "pack" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["draft_place_id"] = "tt_park"
    manifest["place_id"] = "tt_park"
    manifest_path.write_text(json.dumps(manifest))
    worker.handle_create("d1")
    assert store.drafts["d1"]["status"] == "ready"
    assert store.drafts["d1"]["draft_place_id"] == "tt_park"
    assert runner.ran == [
        "intake",
        "research",
        "minimum",
        "looks",
        "reuse",
        "oneshot",
        "words",
        "pack",
    ]
    params = json.loads((job_dir / "state.json").read_text())["params"]
    assert params["place_id"] == "tt_park"


def test_create_home_missing_fields_fails(tmp_path: Path) -> None:
    store = FakeStore()
    store.drafts["d1"] = _home_draft(cell="")
    worker = _worker(tmp_path, store)
    worker.runner = ScriptRunner()
    worker.handle_create("d1")
    assert store.drafts["d1"]["status"] == "failed"
    assert store.drafts["d1"]["error"] == "bad_draft"


def test_create_refuses_unknown_areas(tmp_path: Path) -> None:
    store = FakeStore()
    store.drafts["d1"] = _draft(area_id="xx_nowhere")
    worker = _worker(tmp_path, store)
    worker.handle_create("d1")
    assert store.drafts["d1"]["status"] == "failed"
    assert store.drafts["d1"]["error"] == "unknown_area"
    assert store.inbox[0]["kind"] == "failed"


def test_create_leaves_unbuildable_areas_out(tmp_path: Path) -> None:
    store = FakeStore()
    store.drafts["d1"] = _draft()
    worker = _worker(tmp_path, store, area={**AREA, "buildable": False})
    worker.handle_create("d1")
    assert store.drafts["d1"]["status"] == "not_possible_yet"
    assert store.drafts["d1"]["error"] == "not_buildable"
    assert store.inbox == []


def test_create_enforces_limits(tmp_path: Path) -> None:
    store = FakeStore()
    store.drafts["d1"] = _draft()
    store.drafts["d2"] = _draft(status="ready")
    store.drafts["d3"] = _draft(status="building")
    worker = _worker(tmp_path, store)
    worker.handle_create("d1")
    assert store.drafts["d1"]["error"] == "limit"

    store2 = FakeStore()
    store2.drafts["d1"] = _draft()
    for i in range(3):
        store2.drafts[f"old-{i}"] = _draft(status="ready")
    worker2 = _worker(tmp_path, store2)
    worker2.handle_create("d1")
    assert store2.drafts["d1"]["error"] == "limit"


def test_create_refuses_when_the_day_is_spent(tmp_path: Path) -> None:
    from builder.ledger import today_iso

    store = FakeStore()
    store.drafts["d1"] = _draft()
    worker = _worker(tmp_path, store)
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        json.dumps({"jobs": {}, "days": {today_iso(): 20.0}, "total": 20.0})
    )
    worker.handle_create("d1")
    assert store.drafts["d1"]["error"] == "limit"

    store2 = FakeStore()
    store2.drafts["d1"] = _draft()
    worker2 = _worker(tmp_path, store2)
    ledger.write_text("{corrupt")
    worker2.handle_create("d1")
    assert store2.drafts["d1"]["error"] == "limit"


def test_create_fails_cleanly_on_stage_errors(tmp_path: Path) -> None:
    store = FakeStore()
    store.drafts["d1"] = _draft()
    worker = _worker(tmp_path, store)
    worker.runner = ScriptRunner({"region": StageFailedError("stage region failed")})
    worker.handle_create("d1")
    assert store.drafts["d1"]["status"] == "failed"
    assert store.drafts["d1"]["error"] == "build_failed"
    assert store.inbox[0] == {
        "kind": "failed",
        "draft_id": "d1",
        "reason": "build_failed",
        "urgent": False,
        "handled": False,
        "at": store.inbox[0]["at"],
        "job_id": "d1-r1",
    }
    kinds = [entry["kind"] for entry in store.feed["d1"]]
    assert "check_failed" in kinds


def test_create_retry_clears_the_error_and_resumes(tmp_path: Path) -> None:
    store = FakeStore()
    store.drafts["d1"] = _draft()
    worker = _worker(tmp_path, store)
    worker.runner = ScriptRunner({"region": StageFailedError("stage region failed")})
    worker.handle_create("d1")
    assert store.drafts["d1"]["status"] == "failed"
    store.drafts["d1"]["status"] = "requested"
    worker.runner = ScriptRunner({})
    job_dir = tmp_path / "jobs" / "d1"
    _seed_pack(job_dir)
    worker.handle_create("d1")
    assert store.drafts["d1"]["status"] == "ready"
    assert "error" not in store.drafts["d1"]


def test_create_maps_not_possible_and_budget(tmp_path: Path) -> None:
    store = FakeStore()
    store.drafts["d1"] = _draft()
    worker = _worker(tmp_path, store)
    worker.runner = ScriptRunner(
        {"capability": StageFailedError("x").with_traceback(None)}
    )
    worker.runner.script["capability"].__cause__ = NotPossibleError("tall trees")
    worker.handle_create("d1")
    assert store.drafts["d1"]["status"] == "not_possible_yet"
    assert store.drafts["d1"]["error"] == "needs_code"

    store2 = FakeStore()
    store2.drafts["d1"] = _draft()
    worker2 = _worker(tmp_path, store2)
    worker2.runner = ScriptRunner({"survey": CapExceeded("day cap $20 refuses")})
    worker2.handle_create("d1")
    assert store2.drafts["d1"]["error"] == "budget"


def test_create_retries_transient_errors_twice(tmp_path: Path) -> None:
    store = FakeStore()
    store.drafts["d1"] = _draft()
    worker = _worker(tmp_path, store)
    _seed_pack(tmp_path / "jobs" / "d1")
    worker.runner = ScriptRunner(
        {"survey": [ConnectionError("down"), {"sources": 1, "species": 2}]}
    )
    worker.handle_create("d1")
    assert store.drafts["d1"]["status"] == "ready"
    assert "explore.feed.retry" in [entry["text_key"] for entry in store.feed["d1"]]

    store2 = FakeStore()
    store2.drafts["d1"] = _draft()
    worker2 = _worker(tmp_path, store2)
    worker2.runner = ScriptRunner({"survey": TimeoutError("slow")})
    worker2.handle_create("d1")
    assert store2.drafts["d1"]["error"] == "build_failed"


def test_improve_runs_the_touched_stages(tmp_path: Path) -> None:
    store = FakeStore()
    store.drafts["d1"] = _draft(
        status="ready",
        round=1,
        improve={"choices": [{"choice": "colours_off"}]},
    )
    worker = _worker(tmp_path, store)
    job_dir = tmp_path / "jobs" / "d1"
    job_dir.mkdir(parents=True)
    (job_dir / "state.json").write_text(
        json.dumps(
            {
                "params": {"area": "fi_repovesi"},
                "completed": [],
                "results": {},
                "failed": None,
            }
        )
    )
    _seed_pack(job_dir, version=2)
    runner = ScriptRunner()
    worker.runner = runner
    worker.handle_improve("d1")
    assert runner.ran == ["region", "models", "safety", "pack"]
    assert runner.versions == [2]
    draft = store.drafts["d1"]
    assert draft["status"] == "ready"
    assert draft["round"] == 2
    assert draft["pack_version"] == 2
    assert "improve" not in draft


def test_improve_home_reruns_remapped_stages_with_the_note(
    tmp_path: Path,
) -> None:
    store = FakeStore()
    store.drafts["d1"] = _home_draft(
        status="ready",
        round=1,
        improve={
            "choices": [{"choice": "animal_looks_wrong", "species": "lynx"}],
            "note": "rounder ears",
        },
    )
    worker = _worker(tmp_path, store)
    job_dir = tmp_path / "jobs" / "d1"
    _seed_home(job_dir)
    manifest_path = job_dir / "pack" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["version"] = 2
    manifest_path.write_text(json.dumps(manifest))
    done = [
        "intake",
        "research",
        "minimum",
        "looks",
        "newhome",
        "home",
        "oneshot",
        "words",
        "pack",
    ]
    (job_dir / "state.json").write_text(
        json.dumps(
            {
                "params": {
                    "kind": "animal_home",
                    "species_key": 5219243,
                    "cell": "60.1_24.9",
                    "world": str(tmp_path / "world"),
                },
                "completed": done,
                "results": {},
                "failed": None,
            }
        )
    )
    runner = ScriptRunner()
    worker.runner = runner
    worker.handle_improve("d1")
    assert runner.ran == ["oneshot", "pack"]
    improve = json.loads((job_dir / "improve.json").read_text())
    assert improve == {
        "round": 2,
        "choices": [{"choice": "animal_looks_wrong", "species": "lynx"}],
        "note": "rounder ears",
    }
    draft = store.drafts["d1"]
    assert draft["status"] == "ready"
    assert draft["round"] == 2
    assert draft["pack_version"] == 2
    assert "improve" not in draft
    completed = json.loads((job_dir / "state.json").read_text())["completed"]
    assert completed == [
        "intake",
        "research",
        "minimum",
        "looks",
        "newhome",
        "home",
        "words",
    ]


def test_improve_home_other_runs_home_then_oneshot(tmp_path: Path) -> None:
    store = FakeStore()
    store.drafts["d1"] = _home_draft(
        status="ready",
        round=1,
        improve={"choices": [{"choice": "other", "species": "lynx"}]},
    )
    worker = _worker(tmp_path, store)
    job_dir = tmp_path / "jobs" / "d1"
    _seed_home(job_dir)
    (job_dir / "state.json").write_text(
        json.dumps(
            {
                "params": {
                    "kind": "animal_home",
                    "species_key": 5219243,
                    "cell": "60.1_24.9",
                    "world": str(tmp_path / "world"),
                },
                "completed": [],
                "results": {},
                "failed": None,
            }
        )
    )
    runner = ScriptRunner()
    worker.runner = runner
    worker.handle_improve("d1")
    assert runner.ran == ["home", "oneshot", "pack"]


def test_improve_stops_after_three_rounds(tmp_path: Path) -> None:
    store = FakeStore()
    store.drafts["d1"] = _draft(
        status="ready", round=4, improve={"choices": [{"choice": "other"}]}
    )
    worker = _worker(tmp_path, store)
    runner = ScriptRunner()
    worker.runner = runner
    worker.handle_improve("d1")
    assert runner.ran == []
    assert store.drafts["d1"]["status"] == "ready"


def test_improve_keeps_the_last_good_pack_on_failure(tmp_path: Path) -> None:
    store = FakeStore()
    store.drafts["d1"] = _draft(
        status="ready",
        round=1,
        pack_version=1,
        improve={"choices": [{"choice": "colours_off"}]},
    )
    worker = _worker(tmp_path, store)
    job_dir = tmp_path / "jobs" / "d1"
    job_dir.mkdir(parents=True)
    (job_dir / "state.json").write_text(json.dumps({"params": {"area": "fi_repovesi"}}))
    worker.runner = ScriptRunner({"models": StageFailedError("x")})
    worker.handle_improve("d1")
    draft = store.drafts["d1"]
    assert draft["status"] == "ready"
    assert draft["error"] == "build_failed"
    assert draft["pack_version"] == 1
    assert store.deleted == []
    assert store.inbox[0]["reason"] == "build_failed"


def test_submit_moves_to_review(tmp_path: Path) -> None:
    store = FakeStore()
    store.drafts["d1"] = _draft(status="ready", submit={"at": "now"})
    worker = _worker(tmp_path, store)
    worker.handle_submit("d1")
    assert store.drafts["d1"]["status"] == "in_review"
    assert store.inbox[0]["kind"] == "submitted"

    worker.handle_submit("d1")
    assert len(store.inbox) == 1


def test_feedback_hides_removes_or_asks(tmp_path: Path) -> None:
    store = FakeStore()
    store.drafts["d1"] = _draft(
        status="ready",
        feedback={"kind": "problem", "reason": "other", "note": "scary bit"},
    )
    store.drafts["d2"] = _draft(
        status="ready", feedback={"kind": "not_right", "reason": "looks_wrong"}
    )
    store.drafts["d3"] = _draft(
        status="ready", feedback={"kind": "ask_person", "reason": "still_wrong"}
    )
    worker = _worker(tmp_path, store)
    worker.handle_feedback("d1")
    worker.handle_feedback("d2")
    worker.handle_feedback("d3")
    assert store.drafts["d1"]["status"] == "hidden"
    assert store.inbox[0]["urgent"] is True
    assert store.inbox[0]["note"] == "scary bit"
    assert store.drafts["d2"]["status"] == "removed"
    assert store.deleted == ["d2"]
    assert store.drafts["d3"]["status"] == "ready"
    assert "feedback" not in store.drafts["d3"]
    assert store.inbox[2]["kind"] == "ask_person"


def test_send_on_runs_submit_and_links_back(tmp_path: Path, monkeypatch: Any) -> None:
    from builder.submit import SubmitFailed

    store = FakeStore()
    store.drafts["d1"] = _draft(status="in_review", place_id="fi_repovesi")
    store.actions["a1"] = {"draft_id": "d1", "action": "send_on"}
    worker = _worker(tmp_path, store)

    def fake_submit(draft_id: str, jobs_dir: Path, *args: Any, **kwargs: Any) -> None:
        job = jobs_dir / draft_id
        job.mkdir(parents=True, exist_ok=True)
        (job / "submit.json").write_text(
            json.dumps({"pr_url": "https://example.org/pr/7"})
        )
        return None

    monkeypatch.setattr(worker_module, "run_submit", fake_submit)
    worker.handle_action("a1")
    assert store.drafts["d1"]["pr_url"] == "https://example.org/pr/7"
    assert store.actions["a1"]["done_at"]
    assert "error" not in store.actions["a1"]

    def failing_submit(*args: Any, **kwargs: Any) -> None:
        raise SubmitFailed("gh refused")

    store.actions["a2"] = {"draft_id": "d1", "action": "send_on"}
    monkeypatch.setattr(worker_module, "run_submit", failing_submit)
    worker.handle_action("a2")
    assert store.actions["a2"]["error"] == "submit_failed"
    assert store.inbox[0]["reason"] == "submit_failed"

    store.drafts["d1"]["status"] = "ready"
    store.actions["a3"] = {"draft_id": "d1", "action": "send_on"}
    worker.handle_action("a3")
    assert store.actions["a3"]["error"] == "not_submitted"


def test_admin_actions_move_statuses(tmp_path: Path) -> None:
    store = FakeStore()
    store.drafts["d1"] = _draft(
        status="in_review",
        place_id="fi_repovesi",
        first_explorer_species="red_deer",
    )
    store.drafts["d2"] = _draft(status="in_review")
    store.drafts["d3"] = _draft(status="hidden")
    store.actions["a1"] = {"draft_id": "d1", "action": "published"}
    store.actions["a2"] = {
        "draft_id": "d2",
        "action": "not_accepted",
        "reason": "Too close.",
    }
    store.actions["a3"] = {
        "draft_id": "d3",
        "action": "remove",
        "answer": "Thank you, it is gone.",
    }
    store.actions["a4"] = {"draft_id": "d3", "action": "show_again"}
    store.actions["a5"] = {"draft_id": "d9", "action": "remove"}
    store.actions["a6"] = {"draft_id": "d1", "action": "launch"}
    store.actions["a7"] = {
        "draft_id": "d1",
        "action": "published",
        "done_at": "done",
    }
    worker = _worker(tmp_path, store)
    worker.handle_action("a1")
    assert store.drafts["d1"]["status"] == "public"
    assert store.drafts["d1"]["review_result"]["result"] == "accepted"
    assert store.public["fi_repovesi"]["first_explorer_species"] == "red_deer"
    worker.handle_action("a2")
    assert store.drafts["d2"]["review_result"]["reason"] == "Too close."
    worker.handle_action("a4")
    assert store.drafts["d3"]["status"] == "ready"
    worker.handle_action("a3")
    assert store.drafts["d3"]["status"] == "removed"
    assert store.drafts["d3"]["admin_answer"] == "Thank you, it is gone."
    assert store.deleted == ["d3"]
    worker.handle_action("a5")
    assert store.actions["a5"]["error"] == "no_draft"
    worker.handle_action("a6")
    assert store.actions["a6"]["error"] == "unknown_action"
    worker.handle_action("a7")
    assert store.actions["a7"] == {
        "draft_id": "d1",
        "action": "published",
        "done_at": "done",
    }


def test_dispatch_runs_and_releases(tmp_path: Path) -> None:
    from builder.worker import Listener

    store = FakeStore()
    store.drafts["d1"] = _draft()
    worker = _worker(tmp_path, store)
    worker.runner = ScriptRunner()
    _seed_pack(tmp_path / "jobs" / "d1")
    Listener(worker=worker, store=store)._dispatch("create", "d1")
    assert store.drafts["d1"]["status"] == "ready"
    assert worker.in_flight == set()


def test_dispatch_survives_unknown_kinds(tmp_path: Path, capsys: Any) -> None:
    from builder.worker import Listener

    store = FakeStore()
    worker = _worker(tmp_path, store)
    Listener(worker=worker, store=store)._dispatch("teleport", "d1")
    assert worker.in_flight == set()


def test_listen_reports_a_dead_backend(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    from builder import worker_store
    from builder.worker import listen

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise worker_store.StoreError("no credentials")

    monkeypatch.setattr(worker_store, "FirebaseStore", boom)
    code = listen(
        Config(ledger_path=str(tmp_path / "ledger.json")),
        tmp_path / "jobs",
        tmp_path / "world",
        tmp_path / "areas.json",
        "demo-x",
    )
    assert code == 1
    assert "no credentials" in capsys.readouterr().err


def test_area_index_accepts_either_shape(tmp_path: Path) -> None:
    store = FakeStore()
    worker = _worker(tmp_path, store)
    assert worker._area("fi_repovesi")["name"] == "Repovesi"
    worker.areas_path.write_text(json.dumps({"areas": [AREA]}))
    assert worker._area("fi_repovesi")["name"] == "Repovesi"
    with pytest.raises(RoundFailed):
        worker._area("xx_nowhere")
