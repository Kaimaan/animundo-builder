"""The only code that calls Meta models.

Everything the builder asks a model goes through `MetaClient`: agent chat
with tools and structured output on Chat Completions, search grounding and
SAM 3.1 on the Responses API. Retries, timeouts, usage and cost logging all
live here, so every stage gets them the same way.

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

from .config import Prices

BASE_URL = "https://api.meta.ai/v1"
MAX_TRIES = 5
TIMEOUT_TEXT = 60
TIMEOUT_LONG = 180


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
    ) -> None:
        if not api_key:
            raise MetaAuthError("MODEL_API_KEY is not set")
        self.model = model
        self.prices = prices or Prices()
        self.logger = logger
        self._sleeper = sleeper
        self._client = openai.OpenAI(
            base_url=BASE_URL,
            api_key=api_key,
            max_retries=0,  # retries are explicit below, and logged
            http_client=http_client,
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
    ) -> Any:
        """One Chat Completions call, with retries and cost logging."""

        def call() -> Any:
            return self._client.chat.completions.create(
                model=self.model,
                messages=messages,  # type: ignore[arg-type]
                tools=tools,  # type: ignore[arg-type]
                response_format=response_format,  # type: ignore[arg-type]
                reasoning_effort=reasoning_effort,  # type: ignore[arg-type]
                timeout=timeout,
            )

        start = time.monotonic()
        response = self._with_retries("chat", call)
        self._log("chat", response, time.monotonic() - start)
        return response

    def responses_create(
        self,
        model_input: Any,
        tools: list[dict[str, Any]] | None = None,
        include: list[str] | None = None,
        images: int = 0,
        timeout: float = TIMEOUT_LONG,
    ) -> Any:
        """One Responses API call (search grounding, SAM), with retries."""

        def call() -> Any:
            return self._client.responses.create(
                model=self.model,
                input=model_input,
                tools=tools,  # type: ignore[arg-type]
                include=include,  # type: ignore[arg-type]
                timeout=timeout,
            )

        start = time.monotonic()
        response = self._with_retries("responses", call)
        self._log("responses", response, time.monotonic() - start, images=images)
        return response

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

    def _log(
        self,
        call: str,
        response: Any,
        duration_s: float,
        images: int = 0,
    ) -> None:
        if self.logger is None:
            return
        self.logger.log(
            call,
            usage_from_response(response, self.model, images),
            self.prices,
            duration_s,
            "ok",
        )


def usage_from_response(response: Any, model: str, images: int = 0) -> Usage:
    """Read token and search usage off a Chat or Responses API answer."""
    usage = response.usage
    searches = sum(
        1 for item in getattr(response, "output", []) or [] if _is_search_call(item)
    )
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
