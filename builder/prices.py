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
    image_per_thousand: float = 10.0


PRICES: dict[str, Prices] = {
    "muse-spark-1.3": Prices(),
    # SAM bills per image and frame, with no token cap; token prices are
    # unlisted and treated as zero until the pricing page says otherwise.
    "sam-3.1": Prices(
        input_per_million=0.0,
        output_per_million=0.0,
        cached_input_per_million=0.0,
    ),
    # Muse Image bills a flat $0.01 per generated image, prompt or tools
    # not changing it.
    "muse-image-1.0": Prices(
        input_per_million=0.0,
        output_per_million=0.0,
        cached_input_per_million=0.0,
    ),
    # Muse Spark Open on Baseten, checked 2026-10-27 on Baseten's own
    # model page. The hackathon pins the -1026 snapshot; same rates.
    "meta-models/muse-spark-open-1026": Prices(
        input_per_million=1.25,
        output_per_million=4.25,
        cached_input_per_million=0.15,
    ),
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


#: Measured build costs per plan item kind in US dollars, first
#: Measured medians from the first border batches' spend ledger
#: (#141.5): Helsinki's home cost $0.15 to $0.49, its kelp $0.17.
#: The old $0.02 guesses failed the second Helsinki build on
#: `budget`, since the job's budget is the plan's estimate. Food
#: is a code-only home edit. Re-measure when there is more data.
ITEM_ESTIMATES: dict[str, float] = {
    "home": 0.20,
    "animal": 0.15,
    "plant": 0.15,
    "food": 0.0,
}
