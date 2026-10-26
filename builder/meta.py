"""The only code that calls Meta models.

Everything the builder asks a model goes through `MetaClient`: agent chat
with tools and structured output on Chat Completions, search grounding and
SAM 3.1 on the Responses API. Retries, timeouts, usage and cost logging all
live here, so every stage gets them the same way.

So do the hard spending caps. Every call carries `max_tokens`, reserves
its worst-case cost against the job, day, and total caps before it is
sent, and settles the real cost after. A refused reservation or an
unreadable ledger means no call is made.

No key, no secret and no personal data is ever written to a log.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import openai

from .ledger import CapExceeded, Caps, Ledger, Receipt
from .prices import Prices, prices_for

BASE_URL = "https://api.meta.ai/v1"
MAX_TRIES = 5
TIMEOUT_TEXT = 60
TIMEOUT_LONG = 180
DEFAULT_MAX_TOKENS = 16384
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

    def cost_usd(self, prices: Prices) -> float:
        return (
            self.input_tokens * prices.input_per_million / 1_000_000
            + self.output_tokens * prices.output_per_million / 1_000_000
            + self.cached_input_tokens * prices.cached_input_per_million / 1_000_000
            + self.searches * prices.search_per_thousand_queries / 1_000
            + self.images * prices.sam_image_per_thousand / 1_000
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
    ) -> None:
        line = {
            "at": datetime.now(UTC).isoformat(),
            "call": call,
            "model": usage.model,
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
    ) -> None:
        if not api_key:
            raise MetaAuthError("MODEL_API_KEY is not set")
        if ledger is not None and caps is None:
            raise ValueError("a ledger needs caps")
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

        Default host `meta` runs them on Muse Spark. Partner hosts need
        their key and, until their URLs are confirmed, an explicit
        `BUILDER_OPEN_BASE_URL`. Anything missing fails clearly here,
        never mid-job.
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
        env_var = {"baseten": "BASETEN_API_KEY", "fireworks": "FIREWORKS_API_KEY"}[host]
        if not config.open_api_key():
            raise MetaAuthError(f"{env_var} is not set")
        if not config.open_base_url:
            raise ValueError(f"set BUILDER_OPEN_BASE_URL for host {host!r}")
        return cls(
            api_key=config.open_api_key(),
            model=config.open_model_id(),
            logger=logger,
            http_client=http_client,
            base_url=config.open_base_url,
            ledger=ledger,
            job_id=job_id,
            caps=caps,
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
    ) -> Any:
        """One Chat Completions call, with caps, retries and cost logging."""
        limit = max_tokens or self.default_max_tokens

        def call() -> Any:
            return self._client.chat.completions.create(
                model=self.model,
                messages=messages,  # type: ignore[arg-type]
                tools=tools,  # type: ignore[arg-type]
                response_format=response_format,  # type: ignore[arg-type]
                reasoning_effort=reasoning_effort,  # type: ignore[arg-type]
                timeout=timeout,
                max_tokens=limit,
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
    ) -> Any:
        """One Responses API call (search grounding, SAM), with caps."""
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
                timeout=timeout,
                max_output_tokens=limit,
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

    def _with_retries(self, call: str, call_fn: Any) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, MAX_TRIES + 1):
            try:
                return call_fn()
            except openai.AuthenticationError as error:
                raise MetaAuthError(f"{call}: not authorised: {error}") from error
            except Exception as error:  # noqa: BLE001 - sorted below
                if not _is_retriable(error) or attempt == MAX_TRIES:
                    raise MetaError(f"{call}: failed after {attempt} tries") from error
                last_error = error
                self._sleeper(_backoff_seconds(attempt))
        raise MetaError(f"{call}: failed") from last_error

    def _log(self, call: str, usage: Usage, duration_s: float) -> None:
        if self.logger is None:
            return
        self.logger.log(call, usage, self.prices, duration_s, "ok")


def _estimate_input_tokens(payload: Any) -> int:
    """Rough input size for the reservation: four characters a token."""
    return max(1, len(json.dumps(payload, default=str)) // 4)


def _is_grounded(tools: list[dict[str, Any]] | None) -> bool:
    return any(tool.get("type") == "web_search" for tool in tools or [])


def count_search_calls(response: Any) -> int:
    """How many web searches one Responses API answer ran."""
    return sum(
        1 for item in getattr(response, "output", []) or [] if _is_search_call(item)
    )


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


def _is_search_call(item: Any) -> bool:
    if isinstance(item, dict):
        return item.get("type") == "web_search_call"
    return getattr(item, "type", "") == "web_search_call"
