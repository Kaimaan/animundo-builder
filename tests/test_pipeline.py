"""A fake three-stage pipeline: crash in the middle, resume, budgets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from builder.ledger import CapExceeded
from builder.meta import MetaError, Usage
from builder.pipeline import (
    BudgetExceeded,
    BudgetTracker,
    JobContext,
    Pipeline,
    StageFailedError,
    stages_for_kind,
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


class NamedStage:
    def __init__(self, name: str) -> None:
        self.name = name

    def run(self, ctx):  # type: ignore[no-untyped-def]
        return {}


def test_new_place_runs_everything() -> None:
    stages = [NamedStage("intake"), NamedStage("survey")]
    run, missing = stages_for_kind(stages, "new_place")
    assert [s.name for s in run] == ["intake", "survey"]
    assert missing == []


def test_new_place_skips_home_only() -> None:
    stages = [
        NamedStage(n)
        for n in ("intake", "research", "minimum", "home", "survey", "pack")
    ]
    run, missing = stages_for_kind(stages, "new_place")
    assert [s.name for s in run] == ["intake", "survey", "pack"]
    assert missing == []


def test_animal_home_runs_home_subset() -> None:
    stages = [NamedStage(n) for n in ("intake", "home", "survey", "words")]
    run, missing = stages_for_kind(stages, "animal_home")
    assert [s.name for s in run] == ["intake", "home", "words"]
    assert missing == ["research", "minimum", "pack"]


def test_animal_home_safety_flag_restores_review() -> None:
    stages = [NamedStage(n) for n in ("intake", "home", "words", "safety", "pack")]
    run, _ = stages_for_kind(stages, "animal_home", safety=True)
    assert [s.name for s in run] == ["intake", "home", "words", "safety", "pack"]


def test_submit_needs_pull_request() -> None:
    run, missing = stages_for_kind([NamedStage("intake")], "submit")
    assert run == []
    assert missing == ["pull_request"]


def test_improve_runs_only_touched_stages() -> None:
    stages = [NamedStage(n) for n in ("region", "species_plan", "animals")]
    run, missing = stages_for_kind(stages, "improve", ["colours_off"])
    assert [s.name for s in run] == ["region"]
    assert missing == ["models", "safety", "pack"]
    run, missing = stages_for_kind(stages, "improve", ["too_few_animals"])
    assert [s.name for s in run] == ["species_plan", "animals"]
    assert missing == ["models", "words", "safety", "pack"]


def test_improve_unknown_choice_fails() -> None:
    with pytest.raises(ValueError, match="unknown improve choice"):
        stages_for_kind([NamedStage("region")], "improve", ["add_dragon"])
    with pytest.raises(ValueError, match="unknown job kind"):
        stages_for_kind([NamedStage("region")], "add_animal")
