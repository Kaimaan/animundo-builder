"""Pipeline stages. Each stage reads its inputs, writes its outputs."""

from .animals import AnimalsStage
from .capability import CapabilityStage
from .intake import IntakeStage
from .models import ModelsStage
from .plants import PlantsStage
from .references import ReferencesStage
from .region import RegionStage
from .species_plan import SpeciesPlanStage
from .survey import SurveyStage
from .words import WordsStage

__all__ = [
    "AnimalsStage",
    "CapabilityStage",
    "IntakeStage",
    "ModelsStage",
    "PlantsStage",
    "ReferencesStage",
    "RegionStage",
    "SpeciesPlanStage",
    "SurveyStage",
    "WordsStage",
]
