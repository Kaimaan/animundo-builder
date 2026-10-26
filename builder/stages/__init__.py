"""Pipeline stages. Each stage reads its inputs, writes its outputs."""

from .animals import AnimalsStage
from .capability import CapabilityStage
from .intake import IntakeStage
from .region import RegionStage
from .species_plan import SpeciesPlanStage
from .survey import SurveyStage

__all__ = [
    "AnimalsStage",
    "CapabilityStage",
    "IntakeStage",
    "RegionStage",
    "SpeciesPlanStage",
    "SurveyStage",
]
