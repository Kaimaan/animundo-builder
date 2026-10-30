"""Tests for the Meta client, against canned responses. No network, no key."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx2
import openai
import pytest

import builder.meta as meta_module
from builder.config import Config
from builder.ledger import (
    CapExceeded,
    Caps,
    CombinedLedger,
    FileLedger,
    LedgerError,
    Receipt,
)
from builder.meta import (
    BASE_URL,
    BASETEN_BASE_URL,
    OPEN_DEFAULT_MAX_TOKENS,
    CostLogger,
    MetaAuthError,
    MetaClient,
    MetaError,
    SpendUncertain,
    Usage,
    _ApiKeyAuth,
    is_open_host_refusal,
)
from builder.prices import UnknownModelError, prices_for

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
        model=kwargs.pop("model", MODEL),
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
    assert usage.cost_usd(prices_for(MODEL)) == pytest.approx(expected)


def test_open_weights_default_is_meta(tmp_path: Path) -> None:
    config = Config(model_api_key=FAKE_KEY)
    client = MetaClient.for_open_weights(config)
    assert client.model == MODEL
    assert str(client._client.base_url).rstrip("/") == BASE_URL


def test_open_weights_baseten_confirmed() -> None:
    config = Config(open_model_host="baseten")
    with pytest.raises(MetaAuthError, match="BASETEN_API_KEY"):
        MetaClient.for_open_weights(config)
    config = Config(open_model_host="baseten", baseten_api_key="b-key")
    client = MetaClient.for_open_weights(config)
    assert client.model == "meta-models/muse-spark-open-1026"
    assert str(client._client.base_url).rstrip("/") == BASETEN_BASE_URL
    assert client.host == "baseten"
    assert client.default_max_tokens == OPEN_DEFAULT_MAX_TOKENS


def test_open_weights_unknown_model_still_refused() -> None:
    config = Config(
        open_model_host="baseten", baseten_api_key="b-key", open_model="nope"
    )
    with pytest.raises(UnknownModelError, match="no price"):
        MetaClient.for_open_weights(config)


def test_open_weights_unknown_host_refused() -> None:
    """Inbox 2026-10-28 #103: no host reaches a non-Meta model."""
    config = Config(open_model_host="fireworks")
    with pytest.raises(ValueError, match="unknown open-weights host"):
        MetaClient.for_open_weights(config)


def test_baseten_auth_sends_api_key_scheme() -> None:
    auth = _ApiKeyAuth("b-key")
    request = httpx2.Request(
        "POST",
        "https://inference.baseten.co/v1/chat/completions",
        headers={"Authorization": "Bearer b-key"},
    )
    next(auth.auth_flow(request))
    assert request.headers["Authorization"] == "Api-Key b-key"


def test_baseten_client_installs_api_key_auth() -> None:
    config = Config(open_model_host="baseten", baseten_api_key="b-key")
    client = MetaClient.for_open_weights(config)
    assert isinstance(client._client._client.auth, _ApiKeyAuth)


def test_chat_logs_which_host_ran_it(tmp_path: Path) -> None:
    script = Script([(200, chat_body())])
    logger = CostLogger(tmp_path / "cost.jsonl")
    config = Config(open_model_host="baseten", baseten_api_key="b-key")
    client = MetaClient.for_open_weights(
        config,
        logger=logger,
        http_client=httpx2.Client(transport=httpx2.MockTransport(script)),
    )
    client.chat([{"role": "user", "content": "hi"}])
    (line,) = read_log(logger)
    assert line["host"] == "baseten"
    meta, meta_logger = make_client(Script([(200, chat_body())]), tmp_path / "meta")
    meta.chat([{"role": "user", "content": "hi"}])
    (meta_line,) = read_log(meta_logger)
    assert meta_line["host"] == "meta"


def test_chat_extra_body_reaches_request(tmp_path: Path) -> None:
    script = Script([(200, chat_body())])
    client, _ = make_client(script, tmp_path)
    client.chat(
        [{"role": "user", "content": "hi"}],
        extra_body={"chat_template_kwargs": {"reasoning_effort": "low"}},
    )
    body = json.loads(script.requests[0].content)
    assert body["chat_template_kwargs"] == {"reasoning_effort": "low"}


def test_chat_nests_effort_on_baseten(tmp_path: Path) -> None:
    script = Script([(200, chat_body())])
    client, _ = make_client(script, tmp_path, host="baseten")
    client.chat([{"role": "user", "content": "hi"}], reasoning_effort="low")
    body = json.loads(script.requests[0].content)
    assert body["chat_template_kwargs"] == {"reasoning_effort": "low"}
    assert body.get("reasoning_effort") is None


def test_chat_explicit_body_wins_on_baseten(tmp_path: Path) -> None:
    script = Script([(200, chat_body())])
    client, _ = make_client(script, tmp_path, host="baseten")
    client.chat(
        [{"role": "user", "content": "hi"}],
        reasoning_effort="low",
        extra_body={"chat_template_kwargs": {"reasoning_effort": "minimal"}},
    )
    body = json.loads(script.requests[0].content)
    assert body["chat_template_kwargs"] == {"reasoning_effort": "minimal"}


def test_open_model_price_matches_baseten_page() -> None:
    prices = prices_for("meta-models/muse-spark-open-1026")
    assert (
        prices.input_per_million,
        prices.output_per_million,
        prices.cached_input_per_million,
    ) == (1.25, 4.25, 0.15)


def _chained(status: int | None) -> MetaError:
    error = MetaError("chat: failed")
    if status is not None:
        request = httpx2.Request("POST", "https://inference.baseten.co/v1/x")
        error.__cause__ = openai.APIStatusError(
            "refused", response=httpx2.Response(status, request=request), body={}
        )
    return error


def test_open_host_refusal_sorting() -> None:
    assert is_open_host_refusal(MetaAuthError("not authorised"))
    assert is_open_host_refusal(_chained(402))
    assert is_open_host_refusal(_chained(429))
    assert not is_open_host_refusal(_chained(400))
    assert not is_open_host_refusal(_chained(500))
    assert not is_open_host_refusal(_chained(None))
    assert not is_open_host_refusal(ValueError("boom"))


def test_baseten_low_credit_warns_once(
    tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(meta_module, "_baseten_low_warned", False)
    script = Script([(200, chat_body()), (200, chat_body())])
    logger = CostLogger(tmp_path / "cost.jsonl")
    ledger = FakeLedger(Caps(8.0, 20.0, 100.0))
    ledger.spent = 85.0
    config = Config(open_model_host="baseten", baseten_api_key="b-key")
    client = MetaClient.for_open_weights(
        config,
        logger=logger,
        http_client=httpx2.Client(transport=httpx2.MockTransport(script)),
        ledger=ledger,
        job_id="job-1",
        caps=ledger.caps,
    )
    client.chat([{"role": "user", "content": "hi"}])
    client.chat([{"role": "user", "content": "hi"}])
    err = capsys.readouterr().err
    assert err.count("of Baseten credit left") == 1


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


class FakeLedger:
    """A ledger that records reserve and settle calls."""

    def __init__(self, caps: Caps) -> None:
        self.caps = caps
        self.reserved: list[float] = []
        self.settled: list[float] = []
        self.spent = 0.0
        self.job_id: str | None = None

    def reserve(self, job_id: str, amount_usd: float, caps: Caps) -> Receipt:
        self.job_id = job_id
        self.reserved.append(amount_usd)
        return Receipt(job_id=job_id, day="2026-10-26", reserved_usd=amount_usd)

    def settle(self, receipt: Receipt, actual_usd: float) -> None:
        self.settled.append(actual_usd)
        self.spent += actual_usd

    def total_spent(self) -> float:
        return self.spent

    def job_spent(self, job_id: str) -> float:
        return self.spent if job_id == self.job_id else 0.0


def test_unknown_model_has_no_price() -> None:
    with pytest.raises(UnknownModelError, match="no price"):
        prices_for("muse-spark-9.9")
    assert prices_for(MODEL).input_per_million == 1.25


def test_unpriced_model_cannot_be_called() -> None:
    with pytest.raises(UnknownModelError, match="no price"):
        MetaClient(api_key=FAKE_KEY, model="muse-spark-9.9")


def test_ledger_needs_caps() -> None:
    with pytest.raises(ValueError, match="needs caps"):
        MetaClient(api_key=FAKE_KEY, model=MODEL, ledger=FakeLedger(Caps(1, 1, 1)))


def test_chat_sends_max_tokens(tmp_path: Path) -> None:
    script = Script([(200, chat_body())])
    client, _ = make_client(script, tmp_path)
    client.chat([{"role": "user", "content": "hi"}])
    assert json.loads(script.requests[0].content)["max_tokens"] == 16384
    script2 = Script([(200, chat_body())])
    client2, _ = make_client(script2, tmp_path)
    client2.chat([{"role": "user", "content": "hi"}], max_tokens=100)
    assert json.loads(script2.requests[0].content)["max_tokens"] == 100


def test_too_many_images_refused_before_send(tmp_path: Path) -> None:
    script = Script([(200, responses_body())])
    client, _ = make_client(script, tmp_path)
    with pytest.raises(CapExceeded, match="at most 4 images"):
        client.responses_create("see", images=5)
    assert script.requests == []


def test_reserve_then_settle(tmp_path: Path) -> None:
    script = Script([(200, chat_body())])
    ledger = FakeLedger(Caps(8.0, 20.0, 120.0))
    client, logger = make_client(
        script, tmp_path, ledger=ledger, job_id="job-1", caps=ledger.caps
    )
    client.chat([{"role": "user", "content": "What is 6 * 2?"}])
    (reserved,) = ledger.reserved
    (actual,) = ledger.settled
    assert ledger.job_id == "job-1"
    assert reserved > actual > 0
    (line,) = read_log(logger)
    assert line["cost_usd"] == pytest.approx(actual, abs=1e-6)


def test_cap_refusal_sends_nothing(tmp_path: Path) -> None:
    script = Script([(200, chat_body())])

    class Broke(FakeLedger):
        def reserve(self, job_id: str, amount_usd: float, caps: Caps) -> Receipt:
            raise CapExceeded("day cap $0.00 refuses everything")

    client, _ = make_client(
        script, tmp_path, ledger=Broke(Caps(8.0, 0.0, 120.0)), caps=Caps(8, 0, 120)
    )
    with pytest.raises(CapExceeded, match="day cap"):
        client.chat([{"role": "user", "content": "hi"}])
    assert script.requests == []


def test_unreadable_ledger_sends_nothing(tmp_path: Path) -> None:
    script = Script([(200, chat_body())])

    class Down(FakeLedger):
        def reserve(self, job_id: str, amount_usd: float, caps: Caps) -> Receipt:
            raise LedgerError("cannot read the ledger")

    client, _ = make_client(
        script, tmp_path, ledger=Down(Caps(8.0, 20.0, 120.0)), caps=Caps(8, 20, 120)
    )
    with pytest.raises(LedgerError, match="cannot read"):
        client.chat([{"role": "user", "content": "hi"}])
    assert script.requests == []


def test_failed_call_releases_reserve(tmp_path: Path) -> None:
    script = Script([(500, error_body("boom"))] * 5)
    ledger = FakeLedger(Caps(8.0, 20.0, 120.0))
    client, _ = make_client(
        script, tmp_path, ledger=ledger, job_id="job-1", caps=ledger.caps
    )
    with pytest.raises(MetaError, match="failed after 5 tries"):
        client.chat([{"role": "user", "content": "hi"}])
    assert len(ledger.reserved) == 1
    assert ledger.settled == [0.0]


class FakeStream:
    """A stand-in for the SDK's streaming context manager."""

    def __init__(self, events: list, fail: bool = False) -> None:
        self.events = events
        self.fail = fail
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        if self.fail:
            raise MetaError("stream broke")
        return iter(self.events)


def test_stream_settles_image_price(tmp_path: Path) -> None:
    script = Script([])
    ledger = FakeLedger(Caps(8.0, 20.0, 120.0))
    client, logger = make_client(
        script, tmp_path, model="sam-3.1", ledger=ledger, caps=ledger.caps
    )
    events = [SimpleNamespace(type="mask", id=1), SimpleNamespace(type="done")]
    client._client.responses.stream = FakeStream(events)
    assert list(client.responses_stream("see", images=1)) == events
    (reserved,) = ledger.reserved
    (actual,) = ledger.settled
    assert reserved == pytest.approx(0.0025)
    assert actual == pytest.approx(0.0025)
    (line,) = read_log(logger)
    assert line["call"] == "responses"
    assert line["images"] == 1


def test_stream_failure_releases_reserve(tmp_path: Path) -> None:
    script = Script([])
    ledger = FakeLedger(Caps(8.0, 20.0, 120.0))
    client, _ = make_client(
        script, tmp_path, model="sam-3.1", ledger=ledger, caps=ledger.caps
    )
    client._client.responses.stream = FakeStream([], fail=True)
    with pytest.raises(MetaError, match="stream broke"):
        list(client.responses_stream("see", images=1))
    assert len(ledger.reserved) == 1
    assert ledger.settled == [0.0]


def test_stream_images_refused_before_send(tmp_path: Path) -> None:
    script = Script([])
    client, _ = make_client(script, tmp_path, model="sam-3.1")
    stream = FakeStream([])
    client._client.responses.stream = stream
    with pytest.raises(CapExceeded, match="at most 4 images"):
        list(client.responses_stream("see", images=5))
    assert stream.calls == 0


def test_for_sam_shares_key_ledger_and_log(tmp_path: Path) -> None:
    script = Script([])
    ledger = FakeLedger(Caps(8.0, 20.0, 120.0))
    client, logger = make_client(script, tmp_path, ledger=ledger, caps=ledger.caps)
    sam = client.for_sam()
    assert sam.model == "sam-3.1"
    assert sam._client is client._client
    assert sam.logger is logger
    assert sam.ledger is ledger


def _images_body(data: str) -> dict:
    return {"created": 1, "data": [{"b64_json": data}], "output_format": "png"}


def test_images_generate_round_trip(tmp_path: Path) -> None:
    import base64 as b64lib

    raw = b64lib.b64encode(b"fake-png-bytes").decode()
    script = Script([(200, _images_body(raw))])
    ledger = FakeLedger(Caps(8.0, 20.0, 120.0))
    client, logger = make_client(
        script, tmp_path, model="muse-image-1.0", ledger=ledger, caps=ledger.caps
    )
    assert client.images_generate("a red deer") == b"fake-png-bytes"
    (reserved,) = ledger.reserved
    (actual,) = ledger.settled
    assert reserved == pytest.approx(0.01)
    assert actual == pytest.approx(0.01)
    (line,) = read_log(logger)
    assert line["call"] == "images"
    assert line["generated"] == 1
    assert line["cost_usd"] == pytest.approx(0.01)
    body = json.loads(script.requests[0].content)
    assert body["model"] == "muse-image-1.0"
    assert body["response_format"] == "b64_json"


def test_images_failure_releases_reserve(tmp_path: Path) -> None:
    script = Script([(500, error_body("boom"))] * 5)
    ledger = FakeLedger(Caps(8.0, 20.0, 120.0))
    client, _ = make_client(
        script, tmp_path, model="muse-image-1.0", ledger=ledger, caps=ledger.caps
    )
    with pytest.raises(MetaError, match="failed after 5 tries"):
        client.images_generate("a red deer")
    assert ledger.settled == [0.0]


def test_for_image_shares_key_ledger_and_log(tmp_path: Path) -> None:
    script = Script([])
    ledger = FakeLedger(Caps(8.0, 20.0, 120.0))
    client, logger = make_client(script, tmp_path, ledger=ledger, caps=ledger.caps)
    image = client.for_image()
    assert image.model == "muse-image-1.0"
    assert image._client is client._client
    assert image.logger is logger
    assert image.ledger is ledger


def test_is_cut_off() -> None:
    from builder.meta import is_cut_off

    def resp(**kwargs):  # type: ignore[no-untyped-def]
        return SimpleNamespace(**kwargs)

    assert is_cut_off(resp(status="incomplete"), 100) is True
    assert is_cut_off(resp(status="completed"), 100) is False
    assert (
        is_cut_off(resp(choices=[SimpleNamespace(finish_reason="length")]), 100) is True
    )
    assert (
        is_cut_off(resp(choices=[SimpleNamespace(finish_reason="stop")]), 100) is False
    )
    usage = SimpleNamespace(output_tokens=100, completion_tokens=0)
    assert is_cut_off(resp(usage=usage), 100) is True
    usage = SimpleNamespace(output_tokens=0, completion_tokens=99)
    assert is_cut_off(resp(usage=usage), 100) is False
    assert is_cut_off(resp(), 100) is False
    assert is_cut_off(resp(), 0) is False


class FlakyScript:
    """A canned sequence that may raise instead of answering (#157)."""

    def __init__(self, answers: list) -> None:
        self.answers = list(answers)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        status, body = answer
        return httpx2.Response(status, json=body)


def test_timeout_holds_reservation_and_marks_uncertain(tmp_path: Path) -> None:
    request = httpx2.Request("POST", "https://meta.test/v1/chat/completions")
    script = FlakyScript([httpx2.ReadTimeout("read timed out", request=request)])
    provider = FileLedger(tmp_path / "meta-pool.json")
    allowance = FileLedger(tmp_path / "allowance.json")
    ledger = CombinedLedger(provider, allowance, Caps(8.0, 20.0, 120.0))
    client, logger = make_client(
        script, tmp_path, ledger=ledger, caps=Caps(8.0, 20.0, 120.0)
    )
    with pytest.raises(SpendUncertain) as caught:
        client.chat([{"role": "user", "content": "hi"}], max_tokens=5)
    assert isinstance(caught.value, TimeoutError)
    assert len(script.requests) == 1  # never retry after the request went out
    receipt = caught.value.receipt
    assert receipt is not None
    # Both sides still count the estimate: the unknown is not zero.
    assert provider.job_spent("local") == pytest.approx(receipt.reserved_usd)
    assert allowance.job_spent("local") == pytest.approx(receipt.reserved_usd)
    lines = read_log(logger)
    assert lines[-1]["result"] == "timeout"
    assert lines[-1]["spend_uncertain"] is True
    assert lines[-1]["receipts"]["provider"]["id"] == receipt.provider.id
    assert lines[-1]["receipts"]["allowance"]["id"] == receipt.allowance.id


def test_connect_error_still_retries_and_settles_zero(tmp_path: Path) -> None:
    script = FlakyScript([httpx2.ConnectError("no route to host"), (200, chat_body())])
    ledger = FakeLedger(Caps(8.0, 20.0, 120.0))
    client, logger = make_client(script, tmp_path, ledger=ledger, caps=ledger.caps)
    response = client.chat([{"role": "user", "content": "hi"}], max_tokens=5)
    assert response.choices[0].message.content == "twelve"
    assert len(script.requests) == 2  # never sent: known-zero, safe to retry
    assert len(ledger.settled) == 1
    assert all(line["result"] == "ok" for line in read_log(logger))


def test_lost_connection_pauses_without_retry(tmp_path: Path) -> None:
    request = httpx2.Request("POST", "https://meta.test/v1/chat/completions")
    script = FlakyScript([httpx2.ReadError("connection reset", request=request)])
    ledger = FakeLedger(Caps(8.0, 20.0, 120.0))
    client, _ = make_client(script, tmp_path, ledger=ledger, caps=ledger.caps)
    with pytest.raises(SpendUncertain):
        client.chat([{"role": "user", "content": "hi"}], max_tokens=5)
    assert len(script.requests) == 1
    assert ledger.settled == []  # held, not released


def test_billed_failure_settles_reported_usage(tmp_path: Path) -> None:
    body = {
        "error": {"message": "boom", "type": "server_error"},
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }
    script = Script([(500, body)])
    ledger = FakeLedger(Caps(8.0, 20.0, 120.0))
    client, _ = make_client(script, tmp_path, ledger=ledger, caps=ledger.caps)
    with pytest.raises(MetaError, match="billed usage"):
        client.chat([{"role": "user", "content": "hi"}], max_tokens=5)
    assert len(script.requests) == 1  # spend occurred: no retry
    expected = Usage(model=MODEL, input_tokens=100, output_tokens=50).cost_usd(
        prices_for(MODEL)
    )
    assert ledger.settled == [pytest.approx(expected)]


def test_open_host_uncertainty_is_not_a_refusal() -> None:
    assert is_open_host_refusal(SpendUncertain("sent, spend unknown")) is False
