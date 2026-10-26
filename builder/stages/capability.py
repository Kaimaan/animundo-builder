"""Stage 3, capability check: no agent, just code.

Decides whether the surveyed place can be shown honestly with what the
game does today. At this stage only trees are knowable (from the survey's
fact texts): species diets and behaviours are decided by later stages, so
food-first and archetype checks live there, not here.

Two jobs: refuse places whose main trees have no honest stand-in (palm,
mangrove), and snapshot the supported lists later stages build on (food
kinds, archetypes) into `capabilities.json`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..schemas import validate

TREE_TOPICS = ("tree", "forest", "woodland", "vegetation", "plant", "taiga", "jungle")

# Tree words with no honest stand-in among the game's five shapes
# (spruce, pine, birch, round, rowan). The region stage owns fine
# mapping; this gate only stops what clearly cannot be shown.
UNSHOWABLE_TREES = (
    "palm",
    "mangrove",
    "baobab",
    "cactus",
    "saguaro",
    "bamboo",
    "eucalyptus",
    "acacia",
    "sequoia",
    "redwood",
    "tree fern",
    "cypress",
)

NEGATIONS = re.compile(r"\b(no|not|without|never|none|absent|free of|lack of)\b")


class CapabilityStage:
    """Write `capabilities.json`, or stop the job with `not_possible_yet`."""

    name = "capability"

    def run(self, ctx: Any) -> dict[str, Any]:
        survey = json.loads((ctx.job_dir / "survey.json").read_text(encoding="utf-8"))
        needs_code = self._tree_needs(survey)
        possible = not self._blocks_build(survey, needs_code)
        if ctx.world_path is None:
            raise ValueError("capability needs --world")
        capabilities = {
            "area_id": survey["area_id"],
            "possible": possible,
            "supported": {
                "tree_shapes": ["spruce", "pine", "birch", "round", "rowan"],
                "food_kinds": _food_kinds(ctx.world_path),
                "behaviours": _archetypes(ctx.world_path),
            },
            "needs_code": needs_code,
        }
        validate("capabilities", capabilities)
        out = ctx.job_dir / "capabilities.json"
        out.write_text(json.dumps(capabilities, indent=2), encoding="utf-8")
        if not possible:
            raise _NotPossible(
                "the game's trees cannot show this place honestly: "
                + ", ".join(item["item"] for item in needs_code)
            )
        return {"capabilities_file": "capabilities.json", "possible": True}

    def _tree_needs(self, survey: dict[str, Any]) -> list[dict[str, str]]:
        found = self._tree_words(survey)
        return [
            {
                "item": f"{word} trees",
                "reason": f"the survey names {word} trees and no game shape shows them",
            }
            for word in sorted(found)
        ]

    def _blocks_build(
        self, survey: dict[str, Any], needs_code: list[dict[str, str]]
    ) -> bool:
        if not needs_code:
            return False
        tree_text = " ".join(
            fact["text"]
            for fact in survey.get("facts", [])
            if any(topic in fact.get("topic", "").lower() for topic in TREE_TOPICS)
        )
        words = {item["item"].removesuffix(" trees") for item in needs_code}
        return any(_mentions(tree_text, word) for word in words)

    def _tree_words(self, survey: dict[str, Any]) -> set[str]:
        texts = [fact.get("text", "") for fact in survey.get("facts", [])]
        texts += [species.get("evidence", "") for species in survey.get("species", [])]
        found = set()
        for word in UNSHOWABLE_TREES:
            if any(_mentions(text, word) for text in texts):
                found.add(word)
        return found


def _mentions(text: str, word: str) -> bool:
    """The word appears outside a negated sentence ("no palms" is fine)."""
    for sentence in re.split(r"[.!?]\s+", text.lower()):
        if re.search(rf"\b{re.escape(word)}s?\b", sentence) and not NEGATIONS.search(
            sentence
        ):
            return True
    return False


def _food_kinds(world: Path) -> list[str]:
    foods = json.loads((world / "locale" / "en" / "food.json").read_text())
    kinds = [key for key in foods if key.startswith("food.kind.")]
    return sorted(key.removeprefix("food.kind.") for key in kinds)


def _archetypes(world: Path) -> list[str]:
    archetypes = json.loads((world / "animals" / "archetypes.json").read_text())
    if isinstance(archetypes, dict):
        return sorted(archetypes)
    return sorted(a["id"] if isinstance(a, dict) else a for a in archetypes)


class _NotPossible(Exception):
    """The place needs game code first. The job stops with `not_possible_yet`."""
