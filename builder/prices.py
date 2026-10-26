"""The one place model prices live.

Every spending cap prices calls from this table. A model without a
price here cannot be called: `prices_for` fails instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass

PRICE_CHECKED = "2026-10-26"
PRICING_URL = "https://dev.meta.ai/docs/pricing-rate-limits"


@dataclass(frozen=True)
class Prices:
    """US dollars per million tokens, unless said otherwise."""

    input_per_million: float = 1.25
    output_per_million: float = 4.25
    cached_input_per_million: float = 0.15
    search_per_thousand_queries: float = 2.50
    sam_image_per_thousand: float = 2.50
    sam_frame_per_thousand: float = 0.20


PRICES: dict[str, Prices] = {
    "muse-spark-1.3": Prices(),
}


class UnknownModelError(Exception):
    """The model has no checked price, so it cannot be called."""


def prices_for(model: str) -> Prices:
    """The checked prices for one model id, or a refusal to guess."""
    try:
        return PRICES[model]
    except KeyError:
        raise UnknownModelError(
            f"model {model!r} has no price in builder/prices.py "
            f"(checked {PRICE_CHECKED})"
        ) from None
