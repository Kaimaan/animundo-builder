"""Settings for Animundo Builder.

Values come from an optional `builder.toml` file, overruled by environment
variables. Secrets come from the environment only, never from the file.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from .prices import Prices

DEFAULT_MODEL = "muse-spark-1.3"
BASETEN_OPEN_MODEL = "meta-models/muse-spark-open-1026"
OPEN_HOSTS = ("meta", "baseten", "modal-glimmer")

ENV_PREFIX = "BUILDER_"


@dataclass(frozen=True)
class Config:
    """Every setting the builder runs with."""

    model: str = DEFAULT_MODEL
    open_model_host: str = "meta"
    open_model: str | None = None
    reference: str = "sheet"
    safety: bool = False
    world_path: str | None = None
    daily_budget_usd: float = 20.0
    job_budget_usd: float = 8.0
    total_budget_usd: float = 80.0
    ledger_path: str = "spend-ledger.json"
    species_cache_dir: str | None = None
    baseten_total_usd: float = 100.0
    max_new_species: int = 3
    max_new_plants: int = 3
    model_api_key: str | None = field(default=None, repr=False)
    baseten_api_key: str | None = field(default=None, repr=False)
    prices: Prices = field(default_factory=Prices)

    def open_model_id(self) -> str:
        """The model id to use for open-weights jobs on the chosen host."""
        if self.open_model_host == "meta":
            return self.model
        if self.open_model is not None:
            return self.open_model
        if self.open_model_host == "baseten":
            return BASETEN_OPEN_MODEL
        raise ValueError(f"set BUILDER_OPEN_MODEL for host {self.open_model_host!r}")

    def open_api_key(self) -> str | None:
        """The key for the chosen open-weights host, if it needs one."""
        if self.open_model_host == "baseten":
            return self.baseten_api_key
        if self.open_model_host == "modal-glimmer":
            return None
        return self.model_api_key

    def baseten_ledger_path(self) -> str:
        """The Baseten pool's own ledger file, next to the Meta one."""
        path = Path(self.ledger_path)
        return str(path.with_name(f"{path.stem}-baseten{path.suffix}"))

    def allowance_path(self) -> str:
        """The shared job allowance file, next to the Meta one (#156)."""
        path = Path(self.ledger_path)
        return str(path.with_name(f"{path.stem}-allowance{path.suffix}"))


def _from_env(prefix: str = ENV_PREFIX) -> dict[str, str]:
    names = (
        "MODEL",
        "OPEN_MODEL_HOST",
        "OPEN_MODEL",
        "REFERENCE",
        "SAFETY",
        "WORLD_PATH",
        "DAILY_BUDGET_USD",
        "JOB_BUDGET_USD",
        "TOTAL_BUDGET_USD",
        "LEDGER_PATH",
        "SPECIES_CACHE_DIR",
        "MAX_NEW_SPECIES",
        "MAX_NEW_PLANTS",
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
    A `.env` file fills what the environment lacks (never overriding
    it); its values are never printed.
    """
    # From the working directory upward (a tool reads the project's
    # file, not its own install folder); missing files are fine.
    env_file = find_dotenv(usecwd=True)
    if env_file:
        load_dotenv(env_file)
    values: dict[str, object] = {}
    if path is not None:
        candidate = Path(path)
        if candidate.is_dir():
            candidate /= "builder.toml"
        if candidate.is_file():
            with candidate.open("rb") as handle:
                values.update(tomllib.load(handle))
    values.update(_from_env())
    for money in ("daily_budget_usd", "job_budget_usd", "total_budget_usd"):
        if money in values:
            values[money] = float(values[money])
    if "max_new_species" in values:
        values["max_new_species"] = int(values["max_new_species"])
    if "max_new_plants" in values:
        values["max_new_plants"] = int(values["max_new_plants"])
    if "safety" in values:
        values["safety"] = str(values["safety"]).lower() in ("1", "true", "yes")
    if "open_model_host" in values and values["open_model_host"] not in OPEN_HOSTS:
        raise ValueError(f"open_model_host must be one of {', '.join(OPEN_HOSTS)}")
    if "prices" in values and isinstance(values["prices"], dict):
        values["prices"] = Prices(**values["prices"])
    return Config(
        model_api_key=os.environ.get("MODEL_API_KEY"),
        baseten_api_key=os.environ.get("BASETEN_API_KEY"),
        **values,  # type: ignore[arg-type]
    )
