"""Run pipeline stages in a fixed order, with resume and hard budgets.

The model never decides what runs next: `Pipeline` walks its stage list,
saves state after every stage, and skips finished stages when a job is
resumed. Every model call goes through `JobContext`, which checks the job
budget first and records the spend after.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .config import Prices
from .meta import MetaClient, Usage, usage_from_response


class BudgetExceeded(Exception):
    """The job spent its budget. It stops, never "just this once"."""


class StageFailedError(Exception):
    """One stage failed. The state on disk says which, for `resume`."""


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
    client: MetaClient
    tracker: BudgetTracker

    def check_budget(self) -> None:
        self.tracker.check()

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        self.tracker.check()
        response = self.client.chat(*args, **kwargs)
        self.tracker.add(usage_from_response(response, self.client.model))
        return response

    def respond(self, *args: Any, images: int = 0, **kwargs: Any) -> Any:
        self.tracker.check()
        response = self.client.responses_create(*args, images=images, **kwargs)
        self.tracker.add(usage_from_response(response, self.client.model, images))
        return response


@dataclass
class Pipeline:
    """A fixed list of stages with crash-safe state."""

    stages: list[Stage]
    state_path: Path

    def run(self, ctx: JobContext) -> dict[str, dict[str, Any]]:
        """Run every unfinished stage. Safe to call again after a crash."""
        state = self._load()
        for stage in self.stages:
            if stage.name in state["completed"]:
                continue
            ctx.check_budget()
            try:
                result = stage.run(ctx)
            except BudgetExceeded:
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
