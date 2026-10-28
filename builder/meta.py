"""The only code that calls Meta models.

Everything the builder asks a model goes through `MetaClient`: agent chat
with tools and structured output on Chat Completions, search grounding and
SAM 3.1 on the Responses API, and partner-hosted open weights (Baseten)
for preview reviews and translations. Retries, timeouts, usage and cost
logging all live here, so every stage gets them the same way.

So do the hard spending caps. Every call carries `max_tokens`, reserves
its worst-case cost against the job, day, and total caps before it is
sent, and settles the real cost after. A refused reservation or an
unreadable ledger means no call is made.

No key, no secret and no personal data is ever written to a log.
"""

from __future__ import annotations

import base64
import json
import random
import sys
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import openai

from .ledger import CapExceeded, Caps, Ledger, LedgerError, Receipt
from .prices import Prices, prices_for

BASE_URL = "https://api.meta.ai/v1"
BASETEN_BASE_URL = "https://inference.baseten.co/v1"
BASETEN_LOW_USD = 20.0
MAX_TRIES = 5
TIMEOUT_TEXT = 60
TIMEOUT_LONG = 180
DEFAULT_MAX_TOKENS = 16384
# Muse Spark Open reasons first and the reasoning counts against the
# cap: a two-image review burns past 2000 tokens before answering.
OPEN_DEFAULT_MAX_TOKENS = 8000
MAX_IMAGES_PER_CALL = 4
MAX_SEARCHES_PER_CALL = 5


class MetaError(Exception):
    """Anything the builder cannot fix by retrying."""


class MetaAuthError(MetaError):
    """The key is missing, wrong or has no credit left."""


@dataclass(frozen=True)
class Usage:
    """What one model call consumed, with its price."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    searches: int = 0
    images: int = 0
    generated: int = 0

    def cost_usd(self, prices: Prices) -> float:
        return (
            self.input_tokens * prices.input_per_million / 1_000_000
            + self.output_tokens * prices.output_per_million / 1_000_000
            + self.cached_input_tokens * prices.cached_input_per_million / 1_000_000
            + self.searches * prices.search_per_thousand_queries / 1_000
            + self.images * prices.sam_image_per_thousand / 1_000
            + self.generated * prices.image_per_thousand / 1_000
        )


class CostLogger:
    """Appends one JSON line per model call. Never holds a key."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def log(
        self,
        call: str,
        usage: Usage,
        prices: Prices,
        duration_s: float,
        result: str,
        host: str = "meta",
    ) -> None:
        line = {
            "at": datetime.now(UTC).isoformat(),
            "call": call,
            "model": usage.model,
            "host": host,
            **{k: v for k, v in asdict(usage).items() if k != "model"},
            "cost_usd": round(usage.cost_usd(prices), 6),
            "duration_s": round(duration_s, 3),
            "result": result,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")


def _backoff_seconds(tries_so_far: int) -> float:
    return min(2.0**tries_so_far, 30.0) + random.uniform(0, 1)


def _is_retriable(error: Exception) -> bool:
    if isinstance(error, openai.RateLimitError):
        return True
    if isinstance(error, openai.APIConnectionError):
        return True
    return (
        isinstance(error, openai.APIStatusError)
        and error.status_code is not None
        and error.status_code >= 500
    )


class MetaClient:
    """One client for Chat Completions and Responses on the Meta Model API."""

    def __init__(
        self,
        api_key: str,
        model: str,
        prices: Prices | None = None,
        logger: CostLogger | None = None,
        http_client: httpx2.Client | None = None,
        sleeper: Any = time.sleep,
        base_url: str = BASE_URL,
        ledger: Ledger | None = None,
        job_id: str = "local",
        caps: Caps | None = None,
        default_max_tokens: int = DEFAULT_MAX_TOKENS,
        host: str = "meta",
    ) -> None:
        if not api_key:
            raise MetaAuthError("MODEL_API_KEY is not set")
        if ledger is not None and caps is None:
            raise ValueError("a ledger needs caps")
        self.host = host
        self.model = model
        self.prices = prices or prices_for(model)
        self.logger = logger
        self.ledger = ledger
        self.job_id = job_id
        self.caps = caps
        self.default_max_tokens = default_max_tokens
        self._sleeper = sleeper
        self._client = openai.OpenAI(
            base_url=base_url,
            api_key=api_key,
            max_retries=0,  # retries are explicit below, and logged
            http_client=http_client,
        )

    @classmethod
    def for_open_weights(
        cls,
        config: Any,
        logger: CostLogger | None = None,
        http_client: httpx2.Client | None = None,
        ledger: Ledger | None = None,
        job_id: str = "local",
        caps: Caps | None = None,
    ) -> MetaClient:
        """Build the client open-weights jobs (reviews, translations) use.

        Default host `meta` runs them on Muse Spark. Baseten is
        confirmed (T33): its URL is built in, its key travels as
        `Api-Key`, and the token default leaves room for the model's
        reasoning, which counts against `max_tokens`. No host takes
        a configured URL, so every call is Meta-only (#103).
        Anything missing fails clearly here, never mid-job.
        """
        host = config.open_model_host
        if host == "meta":
            if not config.model_api_key:
                raise MetaAuthError("MODEL_API_KEY is not set")
            return cls(
                api_key=config.model_api_key,
                model=config.model,
                logger=logger,
                ledger=ledger,
                job_id=job_id,
                caps=caps,
            )
        if host == "modal-glimmer":
            raise ValueError("the modal-glimmer host is not confirmed yet (see T33)")
        if host != "baseten":
            raise ValueError(f"unknown open-weights host {host!r}")
        key = config.open_api_key()
        if not key:
            raise MetaAuthError("BASETEN_API_KEY is not set")
        if http_client is None:
            http_client = httpx2.Client(auth=_ApiKeyAuth(key))
        return cls(
            api_key=key,
            model=config.open_model_id(),
            logger=logger,
            http_client=http_client,
            base_url=BASETEN_BASE_URL,
            ledger=ledger,
            job_id=job_id,
            caps=caps,
            default_max_tokens=OPEN_DEFAULT_MAX_TOKENS,
            host="baseten",
        )

    def ensure_model(self) -> None:
        """Fail clearly if the configured model is not on the API."""
        known = [item.id for item in self._client.models.list()]
        if self.model not in known:
            raise MetaError(f"model {self.model!r} is not on the Meta Model API")

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        timeout: float = TIMEOUT_TEXT,
        max_tokens: int | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> Any:
        """One Chat Completions call, with caps, retries and cost logging."""
        limit = max_tokens or self.default_max_tokens
        body = dict(extra_body or {})
        if (
            reasoning_effort
            and self.host == "baseten"
            and "chat_template_kwargs" not in body
        ):
            # Baseten's front end speaks vLLM: the effort nests in the
            # chat template kwargs, and the top-level field 400s.
            body["chat_template_kwargs"] = {"reasoning_effort": reasoning_effort}
            reasoning_effort = None

        def call() -> Any:
            return self._client.chat.completions.create(
                model=self.model,
                messages=messages,  # type: ignore[arg-type]
                tools=tools,  # type: ignore[arg-type]
                response_format=response_format,  # type: ignore[arg-type]
                reasoning_effort=reasoning_effort,  # type: ignore[arg-type]
                timeout=timeout,
                max_tokens=limit,
                extra_body=body or None,
            )

        receipt = self._reserve(messages, limit, searches=0, images=0)
        start = time.monotonic()
        try:
            response = self._with_retries("chat", call)
        except Exception:
            self._settle(receipt, 0.0)
            raise
        usage = usage_from_response(response, self.model)
        self._settle(receipt, usage.cost_usd(self.prices))
        self._log("chat", usage, time.monotonic() - start)
        return response

    def responses_create(
        self,
        model_input: Any,
        tools: list[dict[str, Any]] | None = None,
        include: list[str] | None = None,
        images: int = 0,
        timeout: float = TIMEOUT_LONG,
        max_tokens: int | None = None,
        max_tool_calls: int | None = None,
        text_format: dict[str, Any] | None = None,
    ) -> Any:
        """One Responses API call (search grounding, SAM), with caps.

        `text_format` is this endpoint's structured output (#58):
        `{"type": "json_schema", "name", "schema", "strict": True}`.
        """
        if images > MAX_IMAGES_PER_CALL:
            raise CapExceeded(
                f"at most {MAX_IMAGES_PER_CALL} images per call, got {images}"
            )
        limit = max_tokens or self.default_max_tokens
        searches = MAX_SEARCHES_PER_CALL if _is_grounded(tools) else 0

        def call() -> Any:
            return self._client.responses.create(
                model=self.model,
                input=model_input,
                tools=tools,  # type: ignore[arg-type]
                include=include,  # type: ignore[arg-type]
                text={"format": text_format}  # type: ignore[arg-type]
                if text_format
                else openai.NOT_GIVEN,
                timeout=timeout,
                max_output_tokens=limit,
                max_tool_calls=max_tool_calls,
            )

        receipt = self._reserve(model_input, limit, searches=searches, images=images)
        start = time.monotonic()
        try:
            response = self._with_retries("responses", call)
        except Exception:
            self._settle(receipt, 0.0)
            raise
        usage = usage_from_response(response, self.model, images)
        self._settle(receipt, usage.cost_usd(self.prices))
        self._log("responses", usage, time.monotonic() - start)
        return response

    def for_sam(self) -> MetaClient:
        """A sibling client for sam-3.1, sharing key, ledger, and log."""
        clone = MetaClient.__new__(MetaClient)
        clone.host = self.host
        clone.model = "sam-3.1"
        clone.prices = prices_for("sam-3.1")
        clone.logger = self.logger
        clone.ledger = self.ledger
        clone.job_id = self.job_id
        clone.caps = self.caps
        clone.default_max_tokens = self.default_max_tokens
        clone._sleeper = self._sleeper
        clone._client = self._client
        return clone

    def for_image(self) -> MetaClient:
        """A sibling client for muse-image-1.0, sharing key, ledger, log."""
        clone = MetaClient.__new__(MetaClient)
        clone.host = self.host
        clone.model = "muse-image-1.0"
        clone.prices = prices_for("muse-image-1.0")
        clone.logger = self.logger
        clone.ledger = self.ledger
        clone.job_id = self.job_id
        clone.caps = self.caps
        clone.default_max_tokens = self.default_max_tokens
        clone._sleeper = self._sleeper
        clone._client = self._client
        return clone

    def images_generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        output_format: str = "png",
        timeout: float = TIMEOUT_LONG,
    ) -> bytes:
        """Draw one image: a flat $0.01, reserved and settled like any call."""
        usage = Usage(model=self.model, generated=1)
        receipt = None
        if self.ledger is not None and self.caps is not None:
            receipt = self.ledger.reserve(
                self.job_id, usage.cost_usd(self.prices), self.caps
            )

        def call() -> Any:
            return self._client.images.generate(
                model=self.model,
                prompt=prompt,
                size=size,  # type: ignore[arg-type]
                response_format="b64_json",
                output_format=output_format,  # type: ignore[arg-type]
                timeout=timeout,
            )

        start = time.monotonic()
        try:
            response = self._with_retries("images", call)
        except Exception:
            self._settle(receipt, 0.0)
            raise
        self._settle(receipt, usage.cost_usd(self.prices))
        self._log("images", usage, time.monotonic() - start)
        return base64.b64decode(response.data[0].b64_json)

    def responses_stream(
        self,
        model_input: Any,
        tools: list[dict[str, Any]] | None = None,
        include: list[str] | None = None,
        images: int = 0,
        timeout: float = TIMEOUT_LONG,
        metadata: dict[str, str] | None = None,
    ) -> Iterator[Any]:
        """Yield a Responses stream's events (SAM 3.1), billed per image.

        SAM has no token cap, so no output limit is sent; the images
        bound the cost instead. A finished stream settles the image
        price; a failed one releases its reservation.
        """
        if images > MAX_IMAGES_PER_CALL:
            raise CapExceeded(
                f"at most {MAX_IMAGES_PER_CALL} images per call, got {images}"
            )
        receipt = self._reserve(model_input, 0, searches=0, images=images)
        price = Usage(model=self.model, images=images).cost_usd(self.prices)
        completed = False
        failed = False
        start = time.monotonic()
        try:
            with self._client.responses.stream(
                model=self.model,
                input=model_input,
                tools=tools or [],
                include=include or [],
                timeout=timeout,
                metadata=metadata,
            ) as stream:
                yield from stream
            completed = True
        except Exception:
            failed = True
            raise
        finally:
            if failed:
                self._settle(receipt, 0.0)
            else:
                # Finished, or abandoned mid-stream: the call went out.
                self._settle(receipt, price)
                if completed:
                    self._log(
                        "responses",
                        Usage(model=self.model, images=images),
                        time.monotonic() - start,
                    )

    def _reserve(
        self, payload: Any, max_tokens: int, searches: int, images: int
    ) -> Receipt | None:
        if self.ledger is None or self.caps is None:
            return None
        worst = Usage(
            model=self.model,
            input_tokens=_estimate_input_tokens(payload),
            output_tokens=max_tokens,
            searches=searches,
            images=images,
        )
        return self.ledger.reserve(self.job_id, worst.cost_usd(self.prices), self.caps)

    def _settle(self, receipt: Receipt | None, actual_usd: float) -> None:
        if receipt is not None and self.ledger is not None:
            self.ledger.settle(receipt, actual_usd)
            self._warn_when_baseten_runs_low()

    def _warn_when_baseten_runs_low(self) -> None:
        """Warn once per process when Baseten credit runs low.

        The admin panel item this deserves lands with T24; until then
        the warning goes to stderr, where the worker's logs keep it. A
        failed warning read never breaks the call it follows.
        """
        global _baseten_low_warned
        if (
            _baseten_low_warned
            or self.host != "baseten"
            or self.ledger is None
            or self.caps is None
        ):
            return
        try:
            left = self.caps.total_usd - self.ledger.total_spent()
        except LedgerError:
            return
        if left < BASETEN_LOW_USD:
            _baseten_low_warned = True
            print(
                f"warning: only ${left:.2f} of Baseten credit left",
                file=sys.stderr,
            )

    def _with_retries(self, call: str, call_fn: Any) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, MAX_TRIES + 1):
            try:
                return call_fn()
            except openai.AuthenticationError as error:
                raise MetaAuthError(f"{call}: not authorised: {error}") from error
            except Exception as error:  # noqa: BLE001 - sorted below
                if not _is_retriable(error) or attempt == MAX_TRIES:
                    raise MetaError(
                        f"{call}: failed after {attempt} tries ({error})"
                    ) from error
                last_error = error
                self._sleeper(_backoff_seconds(attempt))
        raise MetaError(f"{call}: failed") from last_error

    def _log(self, call: str, usage: Usage, duration_s: float) -> None:
        if self.logger is None:
            return
        self.logger.log(call, usage, self.prices, duration_s, "ok", self.host)


def _estimate_input_tokens(payload: Any) -> int:
    """Rough input size for the reservation: four characters a token."""
    return max(1, len(json.dumps(payload, default=str)) // 4)


def _is_grounded(tools: list[dict[str, Any]] | None) -> bool:
    return any(tool.get("type") == "web_search" for tool in tools or [])


_baseten_low_warned = False


def is_open_host_refusal(error: Exception) -> bool:
    """Baseten refusals worth a Meta fallback: auth, spent credit, throttle.

    Anything else (a bad request, a partner outage) stays loud: falling
    back would only spend Meta money on the same failure.
    """
    if isinstance(error, MetaAuthError):
        return True
    if isinstance(error, MetaError):
        return getattr(error.__cause__, "status_code", None) in (402, 429)
    return False


def count_search_calls(response: Any) -> int:
    """How many web searches one Responses API answer ran."""
    return sum(
        1 for item in getattr(response, "output", []) or [] if _is_search_call(item)
    )


def is_cut_off(response: Any, limit: int) -> bool:
    """Whether the answer stopped at `max_tokens` instead of finishing.

    An explicit signal wins (an incomplete Responses answer, a
    length-stopped chat choice); without one, output tokens at the cap
    mean the same. A finished answer is never cut off, whatever it cost.
    """
    status = getattr(response, "status", "")
    if status:
        return status == "incomplete"
    choices = getattr(response, "choices", None) or []
    if choices:
        reason = getattr(choices[0], "finish_reason", "")
        if reason:
            return reason == "length"
    usage = getattr(response, "usage", None)
    out = 0
    if usage is not None:
        out = (
            getattr(usage, "output_tokens", 0)
            or getattr(usage, "completion_tokens", 0)
            or 0
        )
    return bool(limit) and out >= limit


def usage_from_response(response: Any, model: str, images: int = 0) -> Usage:
    """Read token and search usage off a Chat or Responses API answer."""
    usage = response.usage
    searches = count_search_calls(response)
    cached = 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", 0) or 0
    return Usage(
        model=getattr(response, "model", model),
        input_tokens=getattr(usage, "prompt_tokens", 0)
        or getattr(usage, "input_tokens", 0),
        output_tokens=getattr(usage, "completion_tokens", 0)
        or getattr(usage, "output_tokens", 0),
        cached_input_tokens=cached,
        searches=searches,
        images=images,
    )


class _ApiKeyAuth(httpx2.Auth):
    """Baseten's scheme: `Authorization: Api-Key`, not Bearer.

    Runs per request, after the OpenAI client sets its own Bearer
    header, so it wins. The `reasoning_content` the model sends back
    is never read here: only `content` reaches callers, and the log
    holds usage alone, never text.
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def auth_flow(self, request: Any) -> Any:
        request.headers["Authorization"] = f"Api-Key {self._api_key}"
        yield request


def _is_search_call(item: Any) -> bool:
    if isinstance(item, dict):
        return item.get("type") == "web_search_call"
    return getattr(item, "type", "") == "web_search_call"
