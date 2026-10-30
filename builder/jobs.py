"""Assemble a pipeline run the same way for the CLI and the worker.

A job is a folder with `state.json`: the pipeline skips finished
stages, so carrying on is just running again.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Config
from .ledger import Caps, CombinedLedger, FileLedger, Ledger
from .meta import CostLogger, MetaAuthError, MetaClient
from .pipeline import BudgetTracker, JobContext
from .prices import UnknownModelError
from .stages import (
    AnimalMakerStage,
    AnimalsStage,
    CapabilityStage,
    HomeMakerStage,
    HomeStage,
    LooksStage,
    MinimumStage,
    ModelsStage,
    PackStage,
    PlantsStage,
    ReferencesStage,
    RegionStage,
    ResearchStage,
    ReuseStage,
    SafetyStage,
    SpeciesPlanStage,
    SurveyStage,
    WordsStage,
)


def pipeline_stages(
    author: str,
    max_new_species: int,
    reference: str,
    max_new_plants: int = 3,
    pack_version: int = 1,
) -> list:
    """Every stage in run order, with the pack stamped for its round."""
    return [
        ResearchStage(),
        MinimumStage(),
        LooksStage(),
        HomeMakerStage(),
        HomeStage(),
        ReuseStage(),
        AnimalMakerStage(),
        SurveyStage(),
        CapabilityStage(),
        RegionStage(author=author),
        SpeciesPlanStage(max_new_species=max_new_species),
        AnimalsStage(),
        ReferencesStage(reference=reference),
        ModelsStage(reference=reference, author=author),
        PlantsStage(max_new_plants=max_new_plants, author=author),
        WordsStage(),
        SafetyStage(),
        PackStage(version=pack_version),
    ]


def caps(config: Config) -> Caps:
    return Caps(
        job_usd=config.job_budget_usd,
        day_usd=config.daily_budget_usd,
        total_usd=config.total_budget_usd,
    )


def allowance_ledger(config: Config) -> Ledger:
    """The one job allowance both providers reserve against (#156)."""
    return FileLedger(config.allowance_path())


def allowance_caps(config: Config) -> Caps:
    """Only the job cap binds the allowance; pools stay provider-side."""
    return Caps(
        job_usd=config.job_budget_usd,
        day_usd=float("inf"),
        total_usd=float("inf"),
    )


def open_client(
    config: Config,
    job_dir: Path,
    job_id: str,
    allowance: Ledger | None = None,
) -> MetaClient | None:
    """The open-weights client, or None when reviews stay on Muse Spark."""
    if config.open_model_host == "meta":
        return None
    pool: Ledger = FileLedger(config.baseten_ledger_path())
    if allowance is not None:
        pool = CombinedLedger(pool, allowance, allowance_caps(config))
    try:
        return MetaClient.for_open_weights(
            config,
            logger=CostLogger(job_dir / "log.jsonl"),
            ledger=pool,
            job_id=job_id,
            caps=Caps(
                job_usd=config.job_budget_usd,
                day_usd=config.daily_budget_usd,
                total_usd=config.baseten_total_usd,
            ),
        )
    except (MetaAuthError, ValueError, UnknownModelError) as error:
        print(
            f"open weights off ({config.open_model_host}): {error}; "
            "reviews run on Muse Spark"
        )
        return None


def make_context(
    config: Config, job_id: str, job_dir: Path, world: Path | None
) -> JobContext:
    """A job context with the Meta client, budget and open host."""
    allowance = allowance_ledger(config)
    client = None
    if config.model_api_key:
        client = MetaClient(
            api_key=config.model_api_key,
            model=config.model,
            logger=CostLogger(job_dir / "log.jsonl"),
            ledger=CombinedLedger(
                FileLedger(config.ledger_path), allowance, allowance_caps(config)
            ),
            job_id=job_id,
            caps=caps(config),
        )
    return JobContext(
        job_id=job_id,
        job_dir=job_dir,
        client=client,
        tracker=BudgetTracker(budget_usd=config.job_budget_usd),
        world_path=world,
        open_client=open_client(config, job_dir, job_id, allowance),
    )


def fresh_state(params: dict[str, Any]) -> dict[str, Any]:
    """A new `state.json` for a job that starts from intake."""
    return {"params": params, "completed": [], "results": {}, "failed": None}
