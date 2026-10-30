"""Run pipeline stages in a fixed order, with resume and hard budgets.

The model never decides what runs next: `Pipeline` walks its stage list,
saves state after every stage, and skips finished stages when a job is
resumed. Every model call goes through `JobContext`, which checks the job
budget first and records the spend after.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .ledger import CapExceeded, LedgerError
from .meta import (
    MetaAuthError,
    MetaClient,
    SpendUncertain,
    Usage,
    is_open_host_refusal,
    usage_from_response,
)
from .prices import Prices


class BudgetExceeded(Exception):
    """The job spent its budget. It stops, never "just this once"."""


class StageFailedError(Exception):
    """One stage failed. The state on disk says which."""


def reset_stages(state_file: Path, names: Collection[str]) -> None:
    """Forget these stages' completion, so they run again."""
    if not state_file.is_file():
        return
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["completed"] = [
        name for name in state.get("completed", []) if name not in set(names)
    ]
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")


class Stage(Protocol):
    """One step of the pipeline. Real stages live in `builder/stages/`."""

    name: str

    def run(self, ctx: JobContext) -> dict[str, Any]: ...


@dataclass
class BudgetTracker:
    """Spend against one job's dollar budget."""

    budget_usd: float
    prices: Prices = field(default_factory=Prices)
    spent_usd: float = 0.0

    def check(self) -> None:
        if self.spent_usd >= self.budget_usd:
            raise BudgetExceeded(
                f"spent ${self.spent_usd:.2f} of a ${self.budget_usd:.2f} budget"
            )

    def add(self, usage: Usage) -> None:
        self.spent_usd += usage.cost_usd(self.prices)


@dataclass
class JobContext:
    """What a stage may use: the job folder, the model client, the budget."""

    job_id: str
    job_dir: Path
    client: MetaClient | None
    tracker: BudgetTracker
    world_path: Path | None = None
    open_client: MetaClient | None = None

    def check_budget(self) -> None:
        self.tracker.check()

    def _client(self) -> MetaClient:
        if self.client is None:
            raise MetaAuthError("MODEL_API_KEY is not set")
        return self.client

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        """One chat call: the open host when set, else Muse Spark.

        Planning, file writing, reviews, safety and translations all
        prefer the open host; only search grounding, image drawing and
        SAM stay on the Meta Model API (they use other methods). When
        the open host refuses, the call falls back to Meta inside the
        Meta caps; the log's host field shows which ran it.
        """
        self.tracker.check()
        if self.open_client is not None:
            try:
                return self._chat_via(self.open_client, *args, **kwargs)
            except (CapExceeded, LedgerError):
                pass  # spent or unreadable: fall through to Meta
            except Exception as error:  # noqa: BLE001 - sorted by refusal
                if not is_open_host_refusal(error):
                    raise
        return self._chat_via(self._client(), *args, **kwargs)

    def _chat_via(self, client: MetaClient, *args: Any, **kwargs: Any) -> Any:
        response = client.chat(*args, **kwargs)
        self.tracker.add(_usage(client, response))
        return response

    def respond(self, *args: Any, images: int = 0, **kwargs: Any) -> Any:
        self.tracker.check()
        client = self._client()
        response = client.responses_create(*args, images=images, **kwargs)
        self.tracker.add(_usage(client, response, images))
        return response

    def draw(self, prompt: str) -> bytes:
        self.tracker.check()
        client = self._client().for_image()
        data = client.images_generate(prompt)
        if getattr(client, "replays", False):
            self.tracker.add(Usage(model=client.model))
        else:
            self.tracker.add(Usage(model=client.model, generated=1))
        return data


def _set_stage(ctx: JobContext, name: str) -> None:
    """Tell recording and replay clients which stage is running."""
    for client in (ctx.client, ctx.open_client):
        setter = getattr(client, "set_stage", None)
        if setter is not None:
            setter(name)


def _usage(client: Any, response: Any, images: int = 0) -> Usage:
    """What the call spent: nothing when it came from a recording."""
    if getattr(client, "replays", False):
        return Usage(model=getattr(client, "model", "replay"))
    return usage_from_response(response, client.model, images)


class OffSwitch(Exception):
    """The off switch went off: stop at the next stage, resume later."""


class StaleOwner(Exception):
    """Another worker owns this job now: stop at once, write nothing (#155)."""


@dataclass
class Pipeline:
    """A fixed list of stages with crash-safe state."""

    stages: list[Stage]
    state_path: Path
    guard: Callable[[str], None] | None = None

    def run(self, ctx: JobContext) -> dict[str, dict[str, Any]]:
        """Run every unfinished stage. Safe to call again after a crash."""
        state = self._load()
        for stage in self.stages:
            if stage.name in state["completed"]:
                continue
            if self.guard is not None:
                self.guard(stage.name)
            ctx.check_budget()
            _set_stage(ctx, stage.name)
            try:
                result = stage.run(ctx)
            except (
                BudgetExceeded,
                CapExceeded,
                LedgerError,
                MetaAuthError,
                OffSwitch,
                SpendUncertain,
                StaleOwner,
            ):
                raise
            except Exception as error:  # noqa: BLE001 - recorded, then raised
                state["failed"] = {"stage": stage.name, "error": str(error)}
                self._save(state)
                raise StageFailedError(f"stage {stage.name} failed") from error
            state["completed"].append(stage.name)
            state["results"][stage.name] = result
            state["failed"] = None
            self._save(state)
        return state["results"]

    def _load(self) -> dict[str, Any]:
        if self.state_path.is_file():
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
            loaded.setdefault("completed", [])
            loaded.setdefault("results", {})
            return loaded
        return {"completed": [], "results": {}, "failed": None}

    def _save(self, state: dict[str, Any]) -> None:
        state["saved_at"] = datetime.now(UTC).isoformat()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
