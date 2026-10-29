"""Record and replay model calls. No network, no key."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from builder.meta import CostLogger
from builder.pipeline import BudgetTracker, JobContext, Pipeline
from builder.records import (
    RecordingClient,
    ReplayClient,
    ReplayError,
    dump_request,
    dump_response,
    load_response,
)


class ScriptClient:
    """A live client stand-in with canned answers."""

    model = "fake-test-model"
    host = "meta"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def chat(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        self.calls.append("chat")
        return _chat('{"verdicts": []}')

    def responses_create(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        self.calls.append("responses_create")
        return _respond('{"foods": {}}')

    def images_generate(self, *args: Any, **kwargs: Any) -> bytes:
        self.calls.append("images_generate")
        return b"fake-png"

    def responses_stream(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append("responses_stream")
        yield SimpleNamespace(type="response.output_text.delta", delta="a")
        yield SimpleNamespace(type="response.output_text.delta", delta="b")

    def for_image(self) -> ScriptClient:
        return self

    def for_sam(self) -> ScriptClient:
        return self


def _usage() -> SimpleNamespace:
    return SimpleNamespace(
        prompt_tokens=10, completion_tokens=5, prompt_tokens_details=None
    )


def _chat(content: str) -> SimpleNamespace:
    message = SimpleNamespace(
        content=content, reasoning_content="private", tool_calls=None
    )
    return SimpleNamespace(
        model="fake-test-model",
        choices=[SimpleNamespace(message=message)],
        usage=_usage(),
    )


def _respond(text: str) -> SimpleNamespace:
    part = SimpleNamespace(type="output_text", text=text, annotations=[])
    return SimpleNamespace(
        model="fake-test-model",
        output=[SimpleNamespace(type="message", content=[part])],
        usage=_usage(),
    )


def _ctx(tmp_path: Path, client: Any) -> JobContext:
    return JobContext(
        job_id="job-1",
        job_dir=tmp_path,
        client=client,
        tracker=BudgetTracker(budget_usd=8.0),
        world_path=tmp_path,
    )


def test_round_trip_keeps_attribute_access() -> None:
    loaded = load_response(dump_response(_respond("hi")))
    assert loaded.output[0].content[0].text == "hi"
    assert loaded.usage.prompt_tokens == 10


def test_private_reasoning_is_scrubbed() -> None:
    dumped = dump_response(_chat("ok"))
    assert "reasoning_content" not in json.dumps(dumped)
    assert dumped["choices"][0]["message"]["content"] == "ok"


def test_bytes_round_trip() -> None:
    assert load_response(dump_response(b"fake-png")) == b"fake-png"


def test_long_requests_are_truncated() -> None:
    dumped = dump_request(("x" * 5000,), {})
    assert dumped["args"][0].endswith("<5000 chars>")


def test_recording_writes_per_stage_files(tmp_path: Path) -> None:
    records = tmp_path / "records"
    client = RecordingClient(ScriptClient(), records, "words")
    client.chat([{"role": "user", "content": "hi"}])
    client.set_stage("home")
    client.responses_create("feed me")
    client.for_image().images_generate("a fox")
    list(client.for_sam().responses_stream("masks"))
    words = records / "words.jsonl"
    home = records / "home.jsonl"
    assert [json.loads(line)["method"] for line in words.read_text().splitlines()] == [
        "chat"
    ]
    assert [json.loads(line)["method"] for line in home.read_text().splitlines()] == [
        "responses_create",
        "images_generate",
        "responses_stream",
    ]
    stream = json.loads(home.read_text().splitlines()[2])
    assert [event["delta"] for event in stream["response"]] == ["a", "b"]


def test_replay_serves_in_order_and_logs_zero(tmp_path: Path) -> None:
    records = tmp_path / "records"
    RecordingClient(ScriptClient(), records, "words").chat(
        [{"role": "user", "content": "hi"}]
    )
    replay = ReplayClient(records, "words", logger=CostLogger(tmp_path / "log.jsonl"))
    ctx = _ctx(tmp_path, replay)
    answer = ctx.chat([{"role": "user", "content": "hi"}])
    assert answer.choices[0].message.content == '{"verdicts": []}'
    assert ctx.tracker.spent_usd == 0
    replay.verify_consumed()
    lines = (tmp_path / "log.jsonl").read_text().splitlines()
    assert len(lines) == 1
    logged = json.loads(lines[0])
    assert logged["cost_usd"] == 0
    assert logged["host"] == "replay"
    assert logged["result"] == "replayed"


def test_replay_wrong_method_raises(tmp_path: Path) -> None:
    records = tmp_path / "records"
    RecordingClient(ScriptClient(), records, "words").chat("hi")
    replay = ReplayClient(records, "words")
    with pytest.raises(ReplayError, match="recorded chat"):
        replay.responses_create("hi")


def test_replay_exhausted_raises(tmp_path: Path) -> None:
    records = tmp_path / "records"
    RecordingClient(ScriptClient(), records, "words").chat("hi")
    replay = ReplayClient(records, "words")
    replay.chat("hi")
    with pytest.raises(ReplayError, match="out of answers"):
        replay.chat("hi")


def test_replay_leftover_raises(tmp_path: Path) -> None:
    records = tmp_path / "records"
    client = RecordingClient(ScriptClient(), records, "words")
    client.chat("one")
    client.chat("two")
    replay = ReplayClient(records, "words")
    replay.chat("one")
    with pytest.raises(ReplayError, match="1 unplayed"):
        replay.verify_consumed()


def test_replay_fix_round_continues_the_stage(tmp_path: Path) -> None:
    """#T39: a re-run stage plays on, it does not start over."""
    records = tmp_path / "records"
    client = RecordingClient(ScriptClient(), records, "words")
    client.chat("one")
    client.set_stage("words")
    client.chat("two")
    replay = ReplayClient(records, "words")
    replay.chat("one")
    replay.set_stage("words")
    replay.chat("two")
    replay.verify_consumed()


def test_replay_fix_round_needing_nothing_stays_clean(
    tmp_path: Path,
) -> None:
    """#T39: a healed re-run with no new calls verifies."""
    records = tmp_path / "records"
    client = RecordingClient(ScriptClient(), records, "words")
    client.chat("one")
    replay = ReplayClient(records, "words")
    replay.chat("one")
    replay.set_stage("words")
    replay.verify_consumed()


def test_replay_changed_request_warns_but_plays(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    records = tmp_path / "records"
    RecordingClient(ScriptClient(), records, "words").chat("summer fox")
    replay = ReplayClient(records, "words")
    answer = replay.chat("winter fox")
    assert answer.choices[0].message.content == '{"verdicts": []}'
    assert "differs from the recorded one" in capsys.readouterr().err


def test_pipeline_tells_the_stage(tmp_path: Path) -> None:
    class Named:
        name = "words"

        def run(self, ctx: Any) -> dict[str, Any]:
            ctx.chat("hi")
            return {}

    records = tmp_path / "records"
    ctx = _ctx(tmp_path, RecordingClient(ScriptClient(), records))
    Pipeline([Named()], tmp_path / "state.json").run(ctx)
    assert (records / "words.jsonl").is_file()
