"""A fake three-stage pipeline: crash in the middle, resume, budgets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from builder.ledger import CapExceeded
from builder.meta import MetaError, SpendUncertain, Usage
from builder.pipeline import (
    BudgetExceeded,
    BudgetTracker,
    JobContext,
    Pipeline,
    StageFailedError,
    reset_stages,
)
from builder.prices import Prices


class FakeClient:
    """A model client double that records calls and spends fake tokens."""

    model = "fake-test-model"

    def __init__(self, input_tokens: int = 1000, output_tokens: int = 500) -> None:
        self.calls = 0
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return _FakeResponse(self.input_tokens, self.output_tokens)

    def responses_create(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return _FakeResponse(self.input_tokens, self.output_tokens)


class _FakeResponse:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.model = "fake-test-model"
        self.usage = _FakeUsage(prompt_tokens, completion_tokens)
        self.output: list = []


class _FakeUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.prompt_tokens_details = None


class FakeStage:
    """Writes a marker, optionally crashing the first time."""

    def __init__(self, name: str, job_dir: Path, crash_once: bool = False) -> None:
        self.name = name
        self.job_dir = job_dir
        self.crash_once = crash_once
        self.runs = 0

    def run(self, ctx: JobContext) -> dict[str, Any]:
        self.runs += 1
        if self.crash_once:
            self.crash_once = False
            raise RuntimeError(f"{self.name} blew up")
        (self.job_dir / f"{self.name}.done").write_text(f"run {self.runs}")
        return {"runs": self.runs}


def make_ctx(tmp_path: Path, budget: float = 8.0) -> tuple[JobContext, FakeClient]:
    client = FakeClient()
    ctx = JobContext(
        job_id="job-1",
        job_dir=tmp_path,
        client=client,  # type: ignore[arg-type]
        tracker=BudgetTracker(budget_usd=budget),
    )
    return ctx, client


class RefusingClient(FakeClient):
    """An open host whose budget is spent."""

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        raise CapExceeded("baseten total spent")


class BrokenClient(FakeClient):
    """An open host failing for no good reason."""

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        raise MetaError("baseten: failed after 1 tries")


def test_chat_prefers_the_open_host(tmp_path: Path) -> None:
    ctx, meta = make_ctx(tmp_path)
    open_client = FakeClient()
    ctx.open_client = open_client  # type: ignore[assignment]
    ctx.chat([{"role": "user", "content": "hi"}])
    assert open_client.calls == 1
    assert meta.calls == 0


def test_chat_falls_back_when_the_open_host_refuses(tmp_path: Path) -> None:
    ctx, meta = make_ctx(tmp_path)
    ctx.open_client = RefusingClient()  # type: ignore[assignment]
    ctx.chat([{"role": "user", "content": "hi"}])
    assert meta.calls == 1


def test_chat_reraises_unrelated_open_errors(tmp_path: Path) -> None:
    ctx, meta = make_ctx(tmp_path)
    ctx.open_client = BrokenClient()  # type: ignore[assignment]
    with pytest.raises(MetaError, match="failed after 1 tries"):
        ctx.chat([{"role": "user", "content": "hi"}])
    assert meta.calls == 0


class UncertainClient(FakeClient):
    """An open host that timed out: its spend is unknown (#157)."""

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        raise SpendUncertain("chat: sent, spend unknown")


def test_chat_does_not_mask_open_host_uncertainty(tmp_path: Path) -> None:
    ctx, meta = make_ctx(tmp_path)
    ctx.open_client = UncertainClient()  # type: ignore[assignment]
    with pytest.raises(SpendUncertain):
        ctx.chat([{"role": "user", "content": "hi"}])
    assert meta.calls == 0, "no fallback may hide the open host's unknown spend"


def test_spend_uncertain_passes_through_unwrapped(tmp_path: Path) -> None:
    class PausingStage(FakeStage):
        def run(self, ctx: JobContext) -> dict[str, Any]:
            raise SpendUncertain("chat: sent, spend unknown")

    pipeline = Pipeline([PausingStage("one", tmp_path)], tmp_path / "state.json")
    ctx, _ = make_ctx(tmp_path)
    with pytest.raises(SpendUncertain):
        pipeline.run(ctx)


def test_ledger_error_passes_through_unwrapped(tmp_path: Path) -> None:
    from builder.ledger import LedgerError

    class BrokeStage(FakeStage):
        def run(self, ctx: JobContext) -> dict[str, Any]:
            raise LedgerError("cannot settle spend: disk full")

    pipeline = Pipeline([BrokeStage("one", tmp_path)], tmp_path / "state.json")
    ctx, _ = make_ctx(tmp_path)
    with pytest.raises(LedgerError, match="cannot settle spend"):
        pipeline.run(ctx)


def test_runs_stages_in_order_and_saves_state(tmp_path: Path) -> None:
    stages = [FakeStage(name, tmp_path) for name in ("one", "two", "three")]
    pipeline = Pipeline(stages, tmp_path / "state.json")
    ctx, _ = make_ctx(tmp_path)
    results = pipeline.run(ctx)
    assert list(results) == ["one", "two", "three"]
    assert (tmp_path / "state.json").is_file()


def test_crash_then_resume_skips_finished_stages(tmp_path: Path) -> None:
    one = FakeStage("one", tmp_path)
    two = FakeStage("two", tmp_path, crash_once=True)
    three = FakeStage("three", tmp_path)
    pipeline = Pipeline([one, two, three], tmp_path / "state.json")
    ctx, _ = make_ctx(tmp_path)
    with pytest.raises(StageFailedError, match="two"):
        pipeline.run(ctx)
    assert one.runs == 1
    results = pipeline.run(ctx)
    assert list(results) == ["one", "two", "three"]
    assert one.runs == 1, "finished stages must not run again"
    assert two.runs == 2
    assert (tmp_path / "one.done").read_text() == "run 1"


def test_budget_checked_before_every_call(tmp_path: Path) -> None:
    ctx, client = make_ctx(tmp_path, budget=0.0)
    with pytest.raises(BudgetExceeded):
        ctx.chat([{"role": "user", "content": "hi"}])
    assert client.calls == 0, "no call may go out over budget"


def test_spend_accumulates_and_stops_the_job(tmp_path: Path) -> None:
    prices = Prices()
    one_call = Usage(model="m", input_tokens=1000, output_tokens=500).cost_usd(prices)
    ctx, client = make_ctx(tmp_path, budget=one_call * 1.5)
    ctx.chat([{"role": "user", "content": "hi"}])
    ctx.chat([{"role": "user", "content": "hi"}])
    assert client.calls == 2
    with pytest.raises(BudgetExceeded):
        ctx.chat([{"role": "user", "content": "hi"}])
    assert client.calls == 2


def test_tracker_math() -> None:
    tracker = BudgetTracker(budget_usd=8.0)
    tracker.add(Usage(model="m", input_tokens=1000))
    assert tracker.spent_usd == pytest.approx(1000 * 1.25 / 1e6)


def test_reset_stages_forgets_only_named(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"completed": ["home", "oneshot", "pack"]}))
    reset_stages(state_file, ["home", "pack"])
    assert json.loads(state_file.read_text())["completed"] == ["oneshot"]
    reset_stages(tmp_path / "missing.json", ["home"])
