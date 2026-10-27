"""`builder worker --listen`: build drafts from Firestore on this machine.

For milestone 1 the worker runs on Kai's Mac against the real
Firestore and Storage (Application Default Credentials, no key
file). It listens on `drafts` (created + updated) and
`admin_actions` (created): new drafts are limits-checked and
built round by round with a live feed, improves run the touched
stages again, submits move to `in_review`, feedback hides or
removes, and admin actions send on, publish, turn down, show
again or remove. The Eventarc HTTP handler for Cloud Run lands
later; these flows are shared.

Every status on a draft is the guard: a crash or restart only
replays events whose guard still holds, and processed improves
and ask-person notes are cleared so they never fire twice.
"""

from __future__ import annotations

import json
import queue
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .config import Config
from .jobs import fresh_state, make_context, pipeline_stages
from .ledger import CapExceeded, FileLedger, LedgerError
from .meta import MetaAuthError
from .pipeline import BudgetExceeded, Pipeline, StageFailedError, stages_for_kind
from .report import build_report
from .schemas import validate
from .stages import IntakeStage
from .stages.capability import NotPossibleError
from .submit import SubmitFailed, run_submit

MAX_ATTEMPTS = 3
MAX_ROUND = 4
ACTIVE_STATUSES = ("building", "improving", "ready")
WEEK_DAYS = 7

#: A draft update value that deletes the field.
DELETE = object()

#: Pipeline stage -> the feed's five building phases.
PHASES = {
    "intake": "land",
    "survey": "land",
    "capability": "land",
    "region": "land",
    "species_plan": "animals",
    "animals": "animals",
    "references": "animals",
    "models": "models",
    "plants": "models",
    "words": "checking",
    "safety": "checking",
    "pack": "packing",
}

#: Errors worth one more attempt (two retries, then a person looks).
TRANSIENT = (ConnectionError, TimeoutError)


class RoundFailed(Exception):
    """One round failed with a short error code for the draft."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


class Store(Protocol):
    """The Firestore + Storage surface the worker needs."""

    def get_draft(self, draft_id: str) -> dict[str, Any] | None: ...
    def update_draft(self, draft_id: str, fields: dict[str, Any]) -> None: ...
    def drafts_by_owner(self, owner_uid: str) -> list[tuple[str, dict[str, Any]]]: ...
    def requested_drafts(self) -> list[tuple[str, dict[str, Any]]]: ...
    def drafts_in_statuses(
        self, statuses: list[str]
    ) -> list[tuple[str, dict[str, Any]]]: ...
    def add_feed(self, draft_id: str, entry: dict[str, Any]) -> None: ...
    def feed_count(self, draft_id: str) -> int: ...
    def add_inbox(self, item: dict[str, Any]) -> str: ...
    def get_job(self, job_id: str) -> dict[str, Any] | None: ...
    def set_job(self, job_id: str, doc: dict[str, Any]) -> None: ...
    def update_job(self, job_id: str, fields: dict[str, Any]) -> None: ...
    def set_public(self, area_id: str, doc: dict[str, Any]) -> None: ...
    def get_action(self, action_id: str) -> dict[str, Any] | None: ...
    def update_action(self, action_id: str, fields: dict[str, Any]) -> None: ...
    def pending_actions(self) -> list[tuple[str, dict[str, Any]]]: ...
    def upload_pack(self, draft_id: str, version: int, pack_dir: Path) -> None: ...
    def upload_report(self, draft_id: str, round_no: int, report: Path) -> None: ...
    def delete_pack(self, draft_id: str) -> None: ...


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Worker:
    """The milestone-1 funnel: drafts in, packs and inbox items out."""

    store: Store
    jobs_dir: Path
    world: Path | None
    areas_path: Path
    config: Config
    author: str = "Animundo Builder"
    seen: dict[str, dict[str, Any]] = field(default_factory=dict)
    in_flight: set[tuple[str, str]] = field(default_factory=set)
    runner: Callable[..., dict[str, Any]] | None = None

    # -- events ----------------------------------------------------

    def note_draft(
        self,
        draft_id: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> list[tuple[str, str]]:
        """Turn a draft snapshot into queued events. Pure dispatch."""
        events: list[tuple[str, str]] = []
        if after is None:
            self.seen.pop(draft_id, None)
            return events
        if before is None and draft_id in self.seen:
            before = self.seen[draft_id]
        # A new request, or Try again on a failed draft: any arrival
        # at requested needs work (the worker itself only ever leaves
        # it, for queued).
        if (
            after.get("status") == "requested"
            and (before or {}).get("status") != "requested"
        ):
            events.append(("create", draft_id))
        # Processed improves are cleared, so a present improve on a
        # ready draft always needs work, even after a restart.
        if after.get("status") == "ready" and after.get("improve"):
            if after.get("improve") != (before or {}).get("improve"):
                events.append(("improve", draft_id))
        if after.get("submit") != (before or {}).get("submit") and after.get("submit"):
            if after.get("status") in ("ready", "not_accepted"):
                events.append(("submit", draft_id))
        if "feedback" in after and "feedback" not in (before or {}):
            if after.get("status") in ("ready", "in_review", "not_accepted"):
                events.append(("feedback", draft_id))
        self.seen[draft_id] = after
        return [event for event in events if event not in self.in_flight]

    def note_action(self, action_id: str, data: dict[str, Any]) -> bool:
        """Queue an admin action unless it is done or already queued."""
        if data.get("done_at") or ("action", action_id) in self.in_flight:
            return False
        return True

    # -- top-level flows --------------------------------------------

    def handle_create(self, draft_id: str) -> None:
        doc = self.store.get_draft(draft_id)
        if doc is None or doc.get("status") != "requested":
            return
        if "error" in doc:
            self._update(draft_id, {"error": DELETE})
        try:
            area = self._area(doc.get("area_id", ""))
        except RoundFailed as failed:
            self._fail(draft_id, failed.code, None)
            return
        if area.get("buildable") is False:
            self._fail_status(draft_id, "not_possible_yet", "not_buildable")
            return
        limited = self._limited(doc, draft_id)
        if limited is not None:
            self._fail(draft_id, limited, None)
            return
        self._update(draft_id, {"status": "queued"})
        try:
            manifest = self.run_round(draft_id, "new_place", 1, [])
        except NotPossibleError as error:
            self._fail_status(draft_id, "not_possible_yet", "needs_code")
            self.feed(
                draft_id,
                1,
                "land",
                "review_note",
                "explore.feed.not_possible",
                {"reason": str(error)[:200]},
            )
            return
        except RoundFailed as failed:
            self._fail(draft_id, failed.code, f"{draft_id}-r1")
            return
        region = self._region(draft_id)
        self._update(
            draft_id,
            {
                "status": "ready",
                "round": 1,
                "pack_version": manifest["version"],
                "place_name": region.get("name", ""),
                "country": region.get("country", ""),
                "center": _rounded(region.get("center")),
                "draft_place_id": f"draft_{draft_id}",
                "place_id": doc["area_id"],
            },
        )
        self.feed(
            draft_id,
            1,
            "packing",
            "done",
            "explore.feed.round_done",
            {"version": manifest["version"]},
        )

    def handle_improve(self, draft_id: str) -> None:
        doc = self.store.get_draft(draft_id)
        if doc is None or doc.get("status") != "ready" or not doc.get("improve"):
            return
        if self._limited(doc, draft_id) is not None:
            self._fail_improve(draft_id, "limit")
            return
        round_no = int(doc.get("round", 1)) + 1
        if round_no > MAX_ROUND:
            print(f"draft {draft_id}: revisions used up, improve dropped")
            return
        choices = [
            entry.get("choice", "") for entry in doc["improve"].get("choices", [])
        ]
        self._update(draft_id, {"status": "improving"})
        try:
            manifest = self.run_round(draft_id, "improve", round_no, choices)
        except NotPossibleError as error:
            self._fail_status(draft_id, "not_possible_yet", "needs_code")
            self.feed(
                draft_id,
                round_no,
                "land",
                "review_note",
                "explore.feed.not_possible",
                {"reason": str(error)[:200]},
            )
            return
        except RoundFailed as failed:
            self._fail_improve(draft_id, failed.code)
            return
        self._update(
            draft_id,
            {
                "status": "ready",
                "round": round_no,
                "pack_version": manifest["version"],
                "improve": DELETE,
            },
        )
        self.feed(
            draft_id,
            round_no,
            "packing",
            "done",
            "explore.feed.round_done",
            {"version": manifest["version"]},
        )

    def handle_submit(self, draft_id: str) -> None:
        doc = self.store.get_draft(draft_id)
        if (
            doc is None
            or not doc.get("submit")
            or doc.get("status") not in ("ready", "not_accepted")
        ):
            return
        self._update(draft_id, {"status": "in_review"})
        self.inbox({"kind": "submitted", "draft_id": draft_id, "urgent": False})

    def handle_feedback(self, draft_id: str) -> None:
        doc = self.store.get_draft(draft_id)
        if doc is None or not doc.get("feedback"):
            return
        if doc.get("status") not in ("ready", "in_review", "not_accepted"):
            return
        item = doc["feedback"]
        kind = item.get("kind", "")
        if kind == "problem":
            self._update(draft_id, {"status": "hidden"})
            self.inbox(
                {
                    "kind": "problem",
                    "draft_id": draft_id,
                    "reason": item.get("reason", ""),
                    "note": item.get("note", ""),
                    "urgent": True,
                }
            )
        elif kind == "not_right":
            self._update(draft_id, {"status": "removed"})
            self.store.delete_pack(draft_id)
            self.inbox(
                {
                    "kind": "not_right",
                    "draft_id": draft_id,
                    "reason": item.get("reason", ""),
                    "urgent": False,
                }
            )
        elif kind == "ask_person":
            self._update(draft_id, {"feedback": DELETE})
            self.inbox(
                {
                    "kind": "ask_person",
                    "draft_id": draft_id,
                    "reason": item.get("reason", ""),
                    "urgent": False,
                }
            )

    def handle_action(self, action_id: str) -> None:
        doc = self.store.get_action(action_id)
        if doc is None or doc.get("done_at"):
            return
        draft_id = doc.get("draft_id", "")
        draft = self.store.get_draft(draft_id)
        if draft is None:
            return self._finish_action(action_id, "no_draft")
        action = doc.get("action", "")
        if action == "send_on":
            return self._send_on(action_id, draft_id, draft)
        if action == "published":
            if draft.get("status") == "public":
                return self._finish_action(action_id, None)
            if draft.get("status") != "in_review":
                return self._finish_action(action_id, "not_in_review")
            self._update(
                draft_id,
                {
                    "status": "public",
                    "review_result": {"result": "accepted", "reason": ""},
                },
            )
            if draft.get("area_id"):
                self.store.set_public(
                    draft["area_id"],
                    {
                        "status": "public",
                        "place_id": draft.get("place_id", ""),
                        "first_explorer_species": draft.get(
                            "first_explorer_species", ""
                        ),
                    },
                )
            return self._finish_action(action_id, None)
        if action == "not_accepted":
            if draft.get("status") != "in_review":
                return self._finish_action(action_id, "not_in_review")
            self._update(
                draft_id,
                {
                    "status": "not_accepted",
                    "review_result": {
                        "result": "not_accepted",
                        "reason": doc.get("reason", ""),
                    },
                },
            )
            return self._finish_action(action_id, None)
        if action == "show_again":
            if draft.get("status") != "hidden":
                return self._finish_action(action_id, "not_hidden")
            fields: dict[str, Any] = {"status": "ready"}
            if doc.get("answer"):
                fields["admin_answer"] = doc["answer"]
            self._update(draft_id, fields)
            return self._finish_action(action_id, None)
        if action == "remove":
            fields = {"status": "removed"}
            if doc.get("answer"):
                fields["admin_answer"] = doc["answer"]
            self._update(draft_id, fields)
            self.store.delete_pack(draft_id)
            return self._finish_action(action_id, None)
        return self._finish_action(action_id, "unknown_action")

    # -- one round ---------------------------------------------------

    def run_round(
        self, draft_id: str, kind: str, round_no: int, choices: list[str]
    ) -> dict[str, Any]:
        """Build round `round_no` into `jobs/<draft_id>`. Returns the manifest."""
        job_dir = self.jobs_dir / draft_id
        state_file = job_dir / "state.json"
        if kind == "new_place" and not state_file.is_file():
            job_dir.mkdir(parents=True, exist_ok=True)
            draft = self.store.get_draft(draft_id)
            if draft is None:
                raise RoundFailed("no_draft", "the draft is gone")
            area = self._area(draft.get("area_id", ""))
            state_file.write_text(
                json.dumps(
                    fresh_state(
                        {
                            "area": area["area_id"],
                            "name": area.get("name"),
                            "country": area.get("country"),
                            "world": str(self.world),
                            "author": self.author,
                            "kind": kind,
                            "choices": [],
                        }
                    )
                ),
                encoding="utf-8",
            )
        if not state_file.is_file():
            raise RoundFailed("no_job", "no local job for this draft")
        params = json.loads(state_file.read_text(encoding="utf-8")).get("params", {})
        intake = IntakeStage(
            area_id=params.get("area"),
            area_name=params.get("name"),
            country=params.get("country"),
            areas_path=self.areas_path,
        )
        stages = pipeline_stages(
            intake,
            self.author,
            self.config.max_new_species,
            self.config.reference,
            self.config.max_new_plants,
            pack_version=round_no,
        )
        run, missing = stages_for_kind(stages, kind, choices)
        if missing:
            raise RoundFailed(
                "choice_unbuilt", f"needs {', '.join(missing)}, not built yet"
            )
        ctx = make_context(self.config, draft_id, job_dir, self.world)
        job_id = f"{draft_id}-r{round_no}"
        if self.store.get_job(job_id) is None:
            self.store.set_job(
                job_id,
                {
                    "draft_id": draft_id,
                    "kind": kind,
                    "round": round_no,
                    "attempt": 0,
                    "stages": {},
                    "cost": {},
                    "budget": {
                        "job_budget_usd": self.config.job_budget_usd,
                        "daily_budget_usd": self.config.daily_budget_usd,
                        "max_new_species": self.config.max_new_species,
                    },
                },
            )
        phase_status = "building" if kind == "new_place" else "improving"
        self._update(draft_id, {"status": phase_status, "stage": "land"})
        self.feed(
            draft_id,
            round_no,
            "land",
            "round",
            "explore.feed.round_started",
            {"round": round_no},
        )
        for attempt in range(MAX_ATTEMPTS):
            try:
                return self._run_stages(
                    draft_id, job_id, job_dir, state_file, ctx, run, round_no
                )
            except RoundFailed:
                raise
            except NotPossibleError:
                raise
            except TRANSIENT as error:
                print(f"job {job_id}: transient error, retrying: {error}")
                self.feed(
                    draft_id,
                    round_no,
                    "land",
                    "review_note",
                    "explore.feed.retry",
                    {"attempt": attempt + 2},
                )
        raise RoundFailed("build_failed", "three attempts failed")

    def _run_stages(
        self,
        draft_id: str,
        job_id: str,
        job_dir: Path,
        state_file: Path,
        ctx: Any,
        run: list,
        round_no: int,
    ) -> dict[str, Any]:
        runner = self.runner or _pipeline_runner
        done = _completed(state_file)
        for stage in run:
            if stage.name in done:
                continue
            phase = PHASES.get(stage.name, "land")
            self._update(
                draft_id,
                {"stage": phase, "stage_detail": f"{phase}: {stage.name}"},
            )
            self._job_stage(job_id, stage.name, None)
            try:
                result = runner(stage, ctx, state_file)
            except StageFailedError as error:
                self._job_stage(job_id, stage.name, f"failed: {error.__cause__}")
                self.feed(
                    draft_id,
                    round_no,
                    phase,
                    "check_failed",
                    "explore.feed.stage_failed",
                    {"stage": stage.name},
                )
                cause = error.__cause__
                if isinstance(cause, NotPossibleError):
                    raise cause
                raise RoundFailed("build_failed", str(cause or error))
            except CapExceeded as error:
                self._job_stage(job_id, stage.name, f"failed: {error}")
                raise RoundFailed("budget", str(error))
            except BudgetExceeded as error:
                self._job_stage(job_id, stage.name, f"failed: {error}")
                raise RoundFailed("budget", str(error))
            except MetaAuthError as error:
                self._job_stage(job_id, stage.name, f"failed: {error}")
                raise RoundFailed("config", str(error))
            self._job_stage(job_id, stage.name, "ok")
            self._stage_feed(draft_id, round_no, stage.name, result)
        manifest = self._finish_round(draft_id, job_id, job_dir, round_no)
        self._summarize_cost(job_id, job_dir)
        return manifest

    def _finish_round(
        self, draft_id: str, job_id: str, job_dir: Path, round_no: int
    ) -> dict[str, Any]:
        try:
            manifest = json.loads(
                (job_dir / "pack" / "manifest.json").read_text(encoding="utf-8")
            )
            self.store.upload_pack(draft_id, manifest["version"], job_dir / "pack")
        except TRANSIENT:
            raise
        except Exception as error:
            raise RoundFailed("build_failed", f"no shippable pack: {error}")
        try:
            report = build_report(job_dir)
            self.store.upload_report(draft_id, round_no, report)
        except TRANSIENT:
            raise
        except Exception as error:
            raise RoundFailed("build_failed", f"no report: {error}")
        for species in manifest.get("new_species", []):
            self.feed(
                draft_id,
                round_no,
                "models",
                "preview",
                "explore.feed.preview",
                {"species": species},
                image=(
                    f"packs/{draft_id}/{manifest['version']}/previews/{species}.png"
                ),
            )
        return manifest

    # -- helpers ------------------------------------------------------

    def feed(
        self,
        draft_id: str,
        round_no: int,
        phase: str,
        kind: str,
        key: str,
        args: dict[str, Any],
        image: str | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "n": self.store.feed_count(draft_id),
            "at": now_iso(),
            "round": round_no,
            "stage": phase,
            "kind": kind,
            "text_key": key,
            "args": args,
        }
        if image is not None:
            entry["image"] = image
        validate("feed-entry", entry)
        self.store.add_feed(draft_id, entry)

    def inbox(self, item: dict[str, Any]) -> str:
        full = {"handled": False, "at": now_iso(), **item}
        validate("admin-inbox-item", full)
        return self.store.add_inbox(full)

    def _update(self, draft_id: str, fields: dict[str, Any]) -> None:
        self.store.update_draft(draft_id, {"updated_at": now_iso(), **fields})

    def _fail(self, draft_id: str, code: str, job_id: str | None) -> None:
        self._update(draft_id, {"status": "failed", "error": code})
        item: dict[str, Any] = {
            "kind": "failed",
            "draft_id": draft_id,
            "reason": code,
            "urgent": False,
        }
        if job_id is not None:
            item["job_id"] = job_id
        self.inbox(item)

    def _fail_status(self, draft_id: str, status: str, code: str) -> None:
        """A normal dead end: the drafts list shows the reason, no inbox."""
        self._update(draft_id, {"status": status, "error": code})

    def _fail_improve(self, draft_id: str, code: str) -> None:
        """A failed improvement keeps the last good pack playable."""
        self._update(draft_id, {"status": "ready", "error": code})
        self.inbox(
            {
                "kind": "failed",
                "draft_id": draft_id,
                "reason": code,
                "urgent": False,
            }
        )

    def _finish_action(self, action_id: str, error: str | None) -> None:
        fields: dict[str, Any] = {"done_at": now_iso()}
        if error is not None:
            fields["error"] = error
        self.store.update_action(action_id, fields)

    def _send_on(self, action_id: str, draft_id: str, draft: dict[str, Any]) -> None:
        if draft.get("status") != "in_review":
            return self._finish_action(action_id, "not_submitted")
        if self.world is None:
            return self._finish_action(action_id, "no_world")
        try:
            run_submit(
                draft_id,
                self.jobs_dir,
                self.world,
                place_id=draft.get("place_id"),
            )
        except SubmitFailed:
            self.inbox(
                {
                    "kind": "failed",
                    "draft_id": draft_id,
                    "reason": "submit_failed",
                    "urgent": False,
                }
            )
            return self._finish_action(action_id, "submit_failed")
        submit_record = json.loads(
            (self.jobs_dir / draft_id / "submit.json").read_text(encoding="utf-8")
        )
        self._update(draft_id, {"pr_url": submit_record["pr_url"]})
        return self._finish_action(action_id, None)

    def _area(self, area_id: str) -> dict[str, Any]:
        try:
            raw = json.loads(self.areas_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise RoundFailed("unknown_area", f"no area index: {error}")
        areas = raw.get("areas", raw) if isinstance(raw, dict) else raw
        for area in areas:
            if area.get("area_id") == area_id:
                try:
                    validate("area", area)
                except Exception as error:
                    raise RoundFailed("unknown_area", f"bad area: {error}")
                return area
        raise RoundFailed("unknown_area", f"unknown area: {area_id}")

    def _limited(self, doc: dict[str, Any], draft_id: str) -> str | None:
        """The limit this draft breaks, or None when it may build."""
        owner = doc.get("owner_uid", "")
        others = [
            data
            for other_id, data in self.store.drafts_by_owner(owner)
            if other_id != draft_id and data.get("status") in ACTIVE_STATUSES
        ]
        if len(others) >= 2:
            return "limit"
        week_ago = datetime.now(UTC).timestamp() - WEEK_DAYS * 86400
        recent = sum(
            1
            for _, data in self.store.drafts_by_owner(owner)
            if _as_timestamp(data.get("created_at")) is None
            or _as_timestamp(data.get("created_at")) >= week_ago
        )
        if recent >= 3:
            return "limit"
        try:
            spent = FileLedger(self.config.ledger_path).day_spent()
        except (LedgerError, OSError, ValueError):
            return "limit"
        if spent >= self.config.daily_budget_usd:
            return "limit"
        return None

    def _region(self, draft_id: str) -> dict[str, Any]:
        regions = sorted((self.jobs_dir / draft_id / "regions").glob("*.json"))
        return json.loads(regions[0].read_text(encoding="utf-8"))

    def _job_stage(self, job_id: str, stage: str, result: str | None) -> None:
        job = self.store.get_job(job_id) or {}
        stages = dict(job.get("stages", {}))
        if result is None:
            stages[stage] = {"started_at": now_iso(), "result": "running"}
        else:
            started = stages.get(stage, {}).get("started_at", now_iso())
            stages[stage] = {
                "started_at": started,
                "finished_at": now_iso(),
                "result": result,
            }
        self.store.update_job(job_id, {"stages": stages})

    def _stage_feed(
        self, draft_id: str, round_no: int, name: str, result: dict[str, Any]
    ) -> None:
        phase = PHASES.get(name, "land")
        if name == "survey":
            self.feed(
                draft_id,
                round_no,
                phase,
                "source_found",
                "explore.feed.sources_found",
                {
                    "sources": int(result.get("sources", 0)),
                    "species": int(result.get("species", 0)),
                },
            )
        elif name == "models":
            files = result.get("model_files", [])
            self.feed(
                draft_id,
                round_no,
                phase,
                "review_note",
                "explore.feed.models_built",
                {"models": len(files)},
            )
        else:
            self.feed(
                draft_id,
                round_no,
                phase,
                "check_passed",
                "explore.feed.stage_done",
                {"stage": name},
            )

    def _summarize_cost(self, job_id: str, job_dir: Path) -> None:
        totals: dict[str, dict[str, Any]] = {}
        log = job_dir / "log.jsonl"
        if log.is_file():
            for line in log.read_text(encoding="utf-8").splitlines():
                try:
                    call = json.loads(line)
                except ValueError:
                    continue
                model = totals.setdefault(
                    str(call.get("model", "unknown")),
                    {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "searches": 0,
                        "images": 0,
                        "cost_usd": 0.0,
                    },
                )
                model["input_tokens"] += int(call.get("input_tokens", 0))
                model["output_tokens"] += int(call.get("output_tokens", 0))
                model["searches"] += int(call.get("searches", 0))
                model["images"] += int(call.get("images", 0)) + int(
                    call.get("generated", 0)
                )
                model["cost_usd"] = round(
                    model["cost_usd"] + float(call.get("cost_usd", 0.0)), 6
                )
        self.store.update_job(job_id, {"cost": totals})


def _pipeline_runner(stage: Any, ctx: Any, state_file: Path) -> dict[str, Any]:
    """Run one stage through the real pipeline, resumable on retry."""
    return Pipeline([stage], state_file).run(ctx)[stage.name]


def _completed(state_file: Path) -> set[str]:
    """Stages the state file already holds, so retries stay quiet."""
    if not state_file.is_file():
        return set()
    try:
        return set(
            json.loads(state_file.read_text(encoding="utf-8")).get("completed", [])
        )
    except ValueError:
        return set()


def _rounded(center: Any) -> Any:
    if not isinstance(center, dict):
        return center
    return {key: round(center[key], 2) for key in ("lat", "lon") if key in center}


def _as_timestamp(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.timestamp()
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return None


@dataclass
class Listener:
    """The `--listen` loop: Firestore snapshots in, one queue, FIFO out."""

    worker: Worker
    store: Any  # FirebaseStore: watch_drafts/watch_actions live there
    events: queue.Queue = field(default_factory=queue.Queue)
    stop: threading.Event = field(default_factory=threading.Event)

    def start(self) -> None:
        for draft_id, doc in self.store.requested_drafts():
            self.worker.seen[draft_id] = doc
            self.events.put(("create", draft_id))
        for action_id, doc in self.store.pending_actions():
            self.events.put(("action", action_id))
        for draft_id, doc in self.store.drafts_in_statuses(
            ["queued", "building", "improving"]
        ):
            print(f"draft {draft_id}: {doc.get('status')}, left alone")
        unsub_drafts = self.store.watch_drafts(self._on_drafts)
        unsub_actions = self.store.watch_actions(self._on_actions)
        print("worker listening (Ctrl-C stops it)")
        try:
            while not self.stop.is_set():
                try:
                    kind, doc_id = self.events.get(timeout=1.0)
                except queue.Empty:
                    continue
                self._dispatch(kind, doc_id)
                self.events.task_done()
        except KeyboardInterrupt:
            pass
        finally:
            unsub_drafts()
            unsub_actions()

    def _on_drafts(self, snapshots: Any, changes: Any, _read_time: Any) -> None:
        for change in changes:
            doc_id = change.document.id
            after = change.document.to_dict()
            if change.type.name == "REMOVED":
                self.worker.note_draft(doc_id, None, None)
                continue
            before = self.worker.seen.get(doc_id)
            for event in self.worker.note_draft(doc_id, before, after):
                self.events.put(event)

    def _on_actions(self, snapshots: Any, changes: Any, _read_time: Any) -> None:
        for change in changes:
            if change.type.name == "REMOVED":
                continue
            data = change.document.to_dict() or {}
            if self.worker.note_action(change.document.id, data):
                self.events.put(("action", change.document.id))

    def _dispatch(self, kind: str, doc_id: str) -> None:
        key = (kind, doc_id)
        if key in self.worker.in_flight:
            return
        self.worker.in_flight.add(key)
        try:
            print(f"worker: {kind} {doc_id}")
            if kind == "create":
                self.worker.handle_create(doc_id)
            elif kind == "improve":
                self.worker.handle_improve(doc_id)
            elif kind == "submit":
                self.worker.handle_submit(doc_id)
            elif kind == "feedback":
                self.worker.handle_feedback(doc_id)
            elif kind == "action":
                self.worker.handle_action(doc_id)
        except (RoundFailed, NotPossibleError) as error:
            print(f"worker: {kind} {doc_id} failed: {error}", file=sys.stderr)
        except Exception as error:  # noqa: BLE001 - the loop must live
            print(f"worker: {kind} {doc_id} crashed: {error!r}", file=sys.stderr)
        finally:
            self.worker.in_flight.discard(key)


def listen(
    config: Config,
    jobs_dir: Path,
    world: Path | None,
    areas_path: Path,
    project: str | None,
    bucket: str | None = None,
) -> int:
    """Connect with Application Default Credentials and listen forever."""
    from .worker_store import FirebaseStore, StoreError

    try:
        store = FirebaseStore(project, bucket)
    except StoreError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    worker = Worker(
        store=store,
        jobs_dir=jobs_dir,
        world=world,
        areas_path=areas_path,
        config=config,
    )
    Listener(worker=worker, store=store).start()
    return 0
