"""Tests for the Meta client, against canned responses. No network, no key."""

from __future__ import annotations

import json
from pathlib import Path

import httpx2
import pytest

from builder.config import BASENTEN_OPEN_MODEL, Config, Prices
from builder.meta import (
    BASE_URL,
    CostLogger,
    MetaAuthError,
    MetaClient,
    MetaError,
    Usage,
)

FAKE_KEY = "sk-test-fake-key-999"
MODEL = "muse-spark-1.3"


def chat_body(**overrides):
    body = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1_700_000_000,
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "twelve"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    body.update(overrides)
    return body


def tool_call_body():
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "multiply", "arguments": '{"a": 6, "b": 2}'},
            }
        ],
    }
    return chat_body(
        choices=[{"index": 0, "message": message, "finish_reason": "tool_calls"}]
    )


def responses_body():
    return {
        "id": "resp-test",
        "object": "response",
        "created_at": 1_700_000_000,
        "status": "completed",
        "model": MODEL,
        "output": [
            {"type": "web_search_call", "id": "ws_1", "status": "completed"},
            {
                "type": "message",
                "id": "m_1",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "The winner.", "annotations": []}
                ],
            },
        ],
        "usage": {"input_tokens": 20, "output_tokens": 8, "total_tokens": 28},
    }


def error_body(message: str):
    return {"error": {"message": message, "type": "test", "code": "test"}}


class Script:
    """A canned sequence of HTTP answers, recording every request."""

    def __init__(self, answers: list[tuple[int, dict]]) -> None:
        self.answers = list(answers)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        status, body = self.answers.pop(0)
        return httpx2.Response(status, json=body)


def make_client(
    script: Script, tmp_path: Path, **kwargs
) -> tuple[MetaClient, CostLogger]:
    transport = httpx2.MockTransport(script)
    logger = CostLogger(tmp_path / "cost.jsonl")
    client = MetaClient(
        api_key=FAKE_KEY,
        model=MODEL,
        logger=logger,
        http_client=httpx2.Client(transport=transport),
        sleeper=lambda seconds: None,
        **kwargs,
    )
    return client, logger


def read_log(logger: CostLogger) -> list[dict]:
    return [
        json.loads(line)
        for line in logger.path.read_text().splitlines()
        if line.strip()
    ]


def test_chat_returns_content(tmp_path: Path) -> None:
    client, _ = make_client(Script([(200, chat_body())]), tmp_path)
    response = client.chat([{"role": "user", "content": "What is 6 * 2?"}])
    assert response.choices[0].message.content == "twelve"


def test_chat_sends_bearer_key_and_model(tmp_path: Path) -> None:
    script = Script([(200, chat_body())])
    client, _ = make_client(script, tmp_path)
    client.chat([{"role": "user", "content": "hi"}])
    request = script.requests[0]
    assert request.url == f"{BASE_URL}/chat/completions"
    assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert json.loads(request.content)["model"] == MODEL


def test_chat_tool_calls_parse(tmp_path: Path) -> None:
    client, _ = make_client(Script([(200, tool_call_body())]), tmp_path)
    response = client.chat(
        [{"role": "user", "content": "multiply"}],
        tools=[{"type": "function", "function": {"name": "multiply"}}],
    )
    call = response.choices[0].message.tool_calls[0]
    assert call.function.name == "multiply"
    assert json.loads(call.function.arguments) == {"a": 6, "b": 2}


def test_chat_passes_format_and_effort(tmp_path: Path) -> None:
    script = Script([(200, chat_body())])
    client, _ = make_client(script, tmp_path)
    schema = {"type": "json_schema", "json_schema": {"name": "N", "schema": {}}}
    client.chat(
        [{"role": "user", "content": "hi"}],
        response_format=schema,
        reasoning_effort="high",
    )
    sent = json.loads(script.requests[0].content)
    assert sent["response_format"] == schema
    assert sent["reasoning_effort"] == "high"


def test_chat_retries_then_succeeds(tmp_path: Path) -> None:
    script = Script(
        [(429, error_body("slow down")), (500, error_body("boom")), (200, chat_body())]
    )
    client, _ = make_client(script, tmp_path)
    response = client.chat([{"role": "user", "content": "hi"}])
    assert response.choices[0].message.content == "twelve"
    assert len(script.requests) == 3


def test_chat_gives_up_after_five_tries(tmp_path: Path) -> None:
    script = Script([(500, error_body("boom"))] * 5)
    client, _ = make_client(script, tmp_path)
    with pytest.raises(MetaError, match="failed after 5 tries"):
        client.chat([{"role": "user", "content": "hi"}])
    assert len(script.requests) == 5


def test_chat_does_not_retry_auth(tmp_path: Path) -> None:
    script = Script([(401, error_body("bad key"))])
    client, _ = make_client(script, tmp_path)
    with pytest.raises(MetaAuthError):
        client.chat([{"role": "user", "content": "hi"}])
    assert len(script.requests) == 1


def test_missing_key_refused() -> None:
    with pytest.raises(MetaAuthError, match="MODEL_API_KEY"):
        MetaClient(api_key="", model=MODEL)


def test_ensure_model(tmp_path: Path) -> None:
    listing = {
        "object": "list",
        "data": [{"id": MODEL, "object": "model", "created": 1, "owned_by": "meta"}],
    }
    client, _ = make_client(Script([(200, listing)]), tmp_path)
    client.ensure_model()  # must not raise
    client2, _ = make_client(Script([(200, listing)]), tmp_path)
    client2.model = "no-such-model"
    with pytest.raises(MetaError, match="not on the Meta Model API"):
        client2.ensure_model()


def test_cost_log_math_and_hygiene(tmp_path: Path) -> None:
    script = Script([(200, chat_body())])
    client, logger = make_client(script, tmp_path)
    client.chat([{"role": "user", "content": "hi"}])
    (line,) = read_log(logger)
    assert line["call"] == "chat"
    assert line["input_tokens"] == 10
    assert line["output_tokens"] == 5
    expected = 10 * 1.25 / 1e6 + 5 * 4.25 / 1e6
    assert line["cost_usd"] == pytest.approx(expected, abs=1e-6)
    assert line["result"] == "ok"
    assert FAKE_KEY not in logger.path.read_text()


def test_usage_cost_with_search_and_cache() -> None:
    usage = Usage(
        model=MODEL, input_tokens=1000, cached_input_tokens=100, searches=4, images=2
    )
    expected = 1000 * 1.25 / 1e6 + 100 * 0.15 / 1e6 + 4 * 2.50 / 1000 + 2 * 2.50 / 1000
    assert usage.cost_usd(Prices()) == pytest.approx(expected)


def test_open_weights_default_is_meta(tmp_path: Path) -> None:
    config = Config(model_api_key=FAKE_KEY)
    client = MetaClient.for_open_weights(config)
    assert client.model == MODEL
    assert str(client._client.base_url).rstrip("/") == BASE_URL


def test_open_weights_baseten_needs_key_and_url() -> None:
    config = Config(open_model_host="baseten")
    with pytest.raises(MetaAuthError, match="BASETEN_API_KEY"):
        MetaClient.for_open_weights(config)
    config = Config(open_model_host="baseten", baseten_api_key="b-key")
    with pytest.raises(ValueError, match="BUILDER_OPEN_BASE_URL"):
        MetaClient.for_open_weights(config)
    config = Config(
        open_model_host="baseten",
        baseten_api_key="b-key",
        open_base_url="https://example.org/v1",
    )
    client = MetaClient.for_open_weights(config)
    assert client.model == BASENTEN_OPEN_MODEL
    assert str(client._client.base_url).rstrip("/") == "https://example.org/v1"


def test_open_weights_unconfirmed_host_refused() -> None:
    config = Config(open_model_host="modal-glimmer")
    with pytest.raises(ValueError, match="not confirmed yet"):
        MetaClient.for_open_weights(config)


def test_responses_counts_searches(tmp_path: Path) -> None:
    script = Script([(200, responses_body())])
    client, logger = make_client(script, tmp_path)
    response = client.responses_create("Who won?", tools=[{"type": "web_search"}])
    assert response.status == "completed"
    (line,) = read_log(logger)
    assert line["call"] == "responses"
    assert line["searches"] == 1
    assert line["input_tokens"] == 20
    assert line["output_tokens"] == 8
    request = script.requests[0]
    assert request.url == f"{BASE_URL}/responses"
    assert json.loads(request.content)["tools"] == [{"type": "web_search"}]
