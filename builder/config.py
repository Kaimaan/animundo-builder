"""Settings for Animundo Builder.

Values come from an optional `builder.toml` file, overruled by environment
variables. Secrets come from the environment only, never from the file.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MODEL = "muse-spark-1.3"
BASENTEN_OPEN_MODEL = "meta-models/muse-spark-open-1026"
OPEN_HOSTS = ("baseten", "modal", "meta")

ENV_PREFIX = "BUILDER_"


@dataclass(frozen=True)
class Prices:
    """US dollars per million tokens, unless said otherwise."""

    input_per_million: float = 1.25
    output_per_million: float = 4.25
    cached_input_per_million: float = 0.15
    search_per_thousand_queries: float = 2.50
    sam_image_per_thousand: float = 2.50
    sam_frame_per_thousand: float = 0.20


@dataclass(frozen=True)
class Config:
    """Every setting the builder runs with."""

    model: str = DEFAULT_MODEL
    open_model_host: str = "meta"
    world_path: str | None = None
    daily_budget_usd: float = 20.0
    job_budget_usd: float = 8.0
    model_api_key: str | None = None
    baseten_api_key: str | None = None
    prices: Prices = field(default_factory=Prices)

    def open_model(self) -> str:
        """The model id and host to use for open-weights jobs."""
        if self.open_model_host == "baseten":
            return BASENTEN_OPEN_MODEL
        if self.open_model_host == "modal":
            raise ValueError("the Modal model name is not confirmed yet")
        return self.model


def _from_env(prefix: str = ENV_PREFIX) -> dict[str, str]:
    names = (
        "MODEL",
        "OPEN_MODEL_HOST",
        "WORLD_PATH",
        "DAILY_BUDGET_USD",
        "JOB_BUDGET_USD",
    )
    found = {}
    for name in names:
        value = os.environ.get(prefix + name)
        if value is not None:
            found[name.lower()] = value
    return found


def load(path: str | Path | None = None) -> Config:
    """Load settings from `builder.toml` plus the environment.

    `path` may point at the file itself or at the directory holding it.
    Missing files are fine; the environment alone is a complete config.
    """
    values: dict[str, object] = {}
    if path is not None:
        candidate = Path(path)
        if candidate.is_dir():
            candidate /= "builder.toml"
        if candidate.is_file():
            with candidate.open("rb") as handle:
                values.update(tomllib.load(handle))
    values.update(_from_env())
    for money in ("daily_budget_usd", "job_budget_usd"):
        if money in values:
            values[money] = float(values[money])
    if "open_model_host" in values and values["open_model_host"] not in OPEN_HOSTS:
        raise ValueError(f"open_model_host must be one of {', '.join(OPEN_HOSTS)}")
    if "prices" in values and isinstance(values["prices"], dict):
        values["prices"] = Prices(**values["prices"])
    return Config(
        model_api_key=os.environ.get("MODEL_API_KEY"),
        baseten_api_key=os.environ.get("BASETEN_API_KEY"),
        **values,  # type: ignore[arg-type]
    )
