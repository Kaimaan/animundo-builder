"""Stage 5, species plan: reuse, new, food, or candidate for every species.

The code matches survey species to existing animal files by GBIF taxon
id, using the world's own `tools/taxon.py` (never file ids or Latin
spellings). Small invertebrates become food, never files, unless they
are playable. The model ranks the rest best-first and writes reasons;
the code builds at most `max_new_species` of them and saves the rest
as candidates for `too_few_animals` and the submit pull request. The
region is rewritten to the final cast (reuse + new) with food kinds
for the invertebrates, then validated again.

Nothing here touches the world's `wanted.json`: wanted additions go
in the submit pull request only.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Collection
from pathlib import Path
from typing import Any

import jsonschema

from ..prompts import load_prompt
from ..reference import PICK_SCHEMA, impossible_match, pick_row, read_table
from ..schemas import load_schema
from ..world import check_draft
from .region import draft_region_errors, sensitive_place_errors

DEFAULT_MAX_NEW_SPECIES = 3
MAX_FIX_ROUNDS = 3

# Surveyed ids whose larvae live in water: food kind `water_bugs`
# instead of `insects`.
WATER_BREEDERS = frozenset({"gnat", "midge", "mosquito"})

MatchFn = Callable[[Path, str], dict[str, Any]]
ClassifyFn = Callable[[int], str]
RecordedFn = Callable[[float, float, int], bool]


TAXON_TRIES = 3


def taxon_same(world: Path, latin: str, plant: bool = False) -> dict[str, Any]:
    """Resolve one Latin name with the world's `tools/taxon.py`.

    GBIF is a network call away, so transient failures are retried;
    only a stable failure stops the job for a person.
    """
    args = [sys.executable, "tools/taxon.py"]
    if plant:
        args.append("--plant")
    args += ["--same", latin]
    last_error = ""
    for attempt in range(1, TAXON_TRIES + 1):
        try:
            proc = subprocess.run(
                args,
                cwd=world,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            last_error = str(error)
            time.sleep(2.0**attempt)
            continue
        if proc.returncode != 0:
            last_error = _last_line(proc.stderr or proc.stdout)
            time.sleep(2.0**attempt)
            continue
        try:
            found = json.loads(proc.stdout)
        except json.JSONDecodeError as error:
            raise _Stopped(f"taxon.py gave no JSON for {latin!r}: {error}")
        if "taxon" not in found or "gbif_key" not in found["taxon"]:
            raise _Stopped(f"taxon.py gave no GBIF key for {latin!r}")
        return found
    raise _Stopped(f"could not resolve {latin!r} ({last_error}); GBIF may be down")


def _last_line(text: str) -> str:
    """The cause, not the traceback: taxon.py failures end with it."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else "no output"


def gbif_phylum(gbif_key: int) -> str:
    """The GBIF phylum for a matched key: Chordata or an invertebrate.

    This answers classification, not identity: the key always comes from
    `taxon.py`, so no matching rule is duplicated here.
    """
    last_error = ""
    for attempt in range(1, TAXON_TRIES + 1):
        try:
            url = f"https://api.gbif.org/v1/species/{gbif_key}"
            with urllib.request.urlopen(url, timeout=20) as response:
                return json.load(response).get("phylum", "")
        except (OSError, ValueError) as error:
            last_error = str(error)
            time.sleep(2.0**attempt)
    raise _Stopped(f"could not read the GBIF phylum for key {gbif_key} ({last_error})")


OCCURRENCE_SPAN = 0.5  # degrees around the center: recorded here means living here


def recorded_near(lat: float, lon: float, gbif_key: int) -> bool:
    """Whether GBIF holds an occurrence of the taxon near the place.

    A count query over a one-degree box around the center: real
    sightings, not a range guess. Transient failures are retried;
    a stable failure raises, and the backfill skips that animal
    visibly (it lands in `backfill_unchecked`, never silently).
    """
    south = max(-90.0, lat - OCCURRENCE_SPAN)
    north = min(90.0, lat + OCCURRENCE_SPAN)
    west = max(-180.0, lon - OCCURRENCE_SPAN)
    east = min(180.0, lon + OCCURRENCE_SPAN)
    box = (
        f"POLYGON(({west} {south}, {east} {south}, {east} {north}, "
        f"{west} {north}, {west} {south}))"
    )
    query = urllib.parse.urlencode({"taxon_key": gbif_key, "geometry": box, "limit": 0})
    last_error = ""
    for attempt in range(1, TAXON_TRIES + 1):
        try:
            url = f"https://api.gbif.org/v1/occurrence/search?{query}"
            with urllib.request.urlopen(url, timeout=20) as response:
                return json.load(response).get("count", 0) > 0
        except (OSError, ValueError) as error:
            last_error = str(error)
            time.sleep(2.0**attempt)
    raise _Stopped(f"could not read GBIF occurrences for key {gbif_key} ({last_error})")


class SpeciesPlanStage:
    """Write `species-plan.json` and shrink the region to its final cast."""

    name = "species_plan"

    def __init__(
        self,
        max_new_species: int = DEFAULT_MAX_NEW_SPECIES,
        match: MatchFn | None = None,
        classify: ClassifyFn | None = None,
        recorded: RecordedFn | None = None,
    ) -> None:
        self.max_new_species = max_new_species
        self._match = match or taxon_same
        self._classify = classify or gbif_phylum
        self._recorded = recorded or recorded_near

    def run(self, ctx: Any) -> dict[str, Any]:
        if ctx.world_path is None:
            raise ValueError("species plan needs --world")
        world = Path(ctx.world_path)
        survey = json.loads((ctx.job_dir / "survey.json").read_text(encoding="utf-8"))
        region_path = self._region_path(ctx)
        table = read_table(world)
        fixed, rank = self._match_all(world, survey, table)
        unchecked = self._backfill(world, region_path, fixed, rank)
        plan = self._write_with_model(ctx, survey, region_path, fixed, rank)
        self._attach_references(ctx, table, survey, plan)
        jsonschema.validate(plan, load_schema("species-plan"))
        self._rewrite_region(world, ctx.job_dir, region_path, survey, fixed, plan)
        out = ctx.job_dir / "species-plan.json"
        out.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        if not plan["food_web_ok"]:
            raise _Stopped(
                "food first fails: " + "; ".join(plan.get("missing_food", ["unknown"]))
            )
        decisions = [entry["decision"] for entry in plan["species"]]
        return {
            "species_plan_file": "species-plan.json",
            "reuse": sum(1 for d in decisions if d == "reuse"),
            "new": sum(1 for d in decisions if d == "new"),
            "food": sum(1 for d in decisions if d == "food"),
            "candidate": sum(1 for d in decisions if d == "candidate"),
            "needs_code": sum(1 for d in decisions if d == "needs_code"),
            "added_young": plan.get("added_young", []),
            "backfilled": sorted(
                survey_id
                for survey_id, record in fixed.items()
                if record.get("backfilled")
            ),
            "backfill_unchecked": sorted(unchecked),
        }

    def _region_path(self, ctx: Any) -> Path:
        regions = sorted((ctx.job_dir / "regions").glob("*.json"))
        if len(regions) != 1:
            raise ValueError(f"expected one region file, found {len(regions)}")
        return regions[0]

    def _match_all(
        self, world: Path, survey: dict[str, Any], table: list[dict[str, str]]
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        """Split surveyed species into fixed decisions and ones to rank.

        Returns (`fixed`, `rank`): fixed maps an id to its reuse, food
        or needs_code record, rank maps an id to its GBIF taxon for the
        model to order.
        """
        taxa = _file_taxa(world)
        fixed: dict[str, dict[str, Any]] = {}
        rank: dict[str, dict[str, Any]] = {}
        for species in survey.get("species", []):
            survey_id = species["species"]
            latin = (species.get("latin") or "").strip()
            if not latin:
                raise _Stopped(f"{survey_id} has no Latin name to match")
            found = self._match(world, latin)
            taxon = found["taxon"]
            files = found.get("files", [])
            if files:
                fixed[survey_id] = {
                    "decision": "reuse",
                    "file": _grown_form(world, files),
                    "taxon": taxon,
                }
                continue
            _refuse_collision(survey_id, taxon, taxa)
            _refuse_other_subspecies(survey_id, taxon, taxa)
            if species.get("role") != "playable" and self._is_food(taxon):
                fixed[survey_id] = {
                    "decision": "food",
                    "kind": _food_kind(survey_id),
                    "taxon": taxon,
                }
                continue
            blocked = impossible_match(table, species)
            if blocked is not None:
                fixed[survey_id] = {
                    "decision": "needs_code",
                    "reason": (
                        f"the reference table lists {blocked['animal']} "
                        f"as not possible yet: {blocked['change']}"
                    ),
                    "taxon": taxon,
                }
                continue
            rank[survey_id] = taxon
        return fixed, rank

    def _backfill(
        self,
        world: Path,
        region_path: Path,
        fixed: dict[str, dict[str, Any]],
        rank: dict[str, dict[str, Any]],
    ) -> set[str]:
        """Reuse the world's animals GBIF records at the place.

        The survey misses residents (a cut-off species call listed one
        animal for a whole park); reusing costs nothing, so every
        existing animal with an occurrence near the center joins the
        plan as reuse, in its file's name. Skipped without a sound:
        young forms, surveyed species, files already reused, taxa the
        survey already covers, the unrecorded, and animals the cast
        gives no part. Returns the stems GBIF could not answer for.
        """
        region = json.loads(region_path.read_text(encoding="utf-8"))
        center = region.get("center") or {}
        lat, lon = center.get("lat"), center.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            return set()
        taxa = _file_taxa(world)
        taken_files = {
            record["file"]
            for record in fixed.values()
            if record.get("decision") == "reuse"
        }
        taken_keys = {record["taxon"]["gbif_key"] for record in fixed.values()}
        taken_keys |= {taxon["gbif_key"] for taxon in rank.values()}
        surveyed = set(fixed) | set(rank)
        cast = set(taken_files)
        for stem in _cast_files(region):
            if (world / "animals" / f"{stem}.json").is_file():
                cast.add(stem)
        hunts = {
            tag
            for stem in cast
            for tag in _animal_file(world, stem).get("hunts", []) or []
        }
        unchecked = set()
        for stem in sorted(taxa):
            if stem in surveyed or stem in taken_files:
                continue
            taxon = taxa[stem]
            if taxon.get("gbif_key") is None or taxon["gbif_key"] in taken_keys:
                continue
            data = _animal_file(world, stem)
            if data.get("stage_of"):
                continue
            role = _backfill_role(data, hunts)
            if role is None:
                continue
            try:
                recorded = self._recorded(float(lat), float(lon), taxon["gbif_key"])
            except Exception:
                unchecked.add(stem)
                continue
            if not recorded:
                continue
            fixed[stem] = {
                "decision": "reuse",
                "file": stem,
                "taxon": taxon,
                "backfilled": True,
                "role": role,
            }
        return unchecked

    def _is_food(self, taxon: dict[str, Any]) -> bool:
        """A non-chordate is food: small invertebrates, never files.

        Never a name match: slow_worm is a reptile, bee_eater a bird, and
        only GBIF's phylum tells them from real invertebrates.
        """
        return self._classify(taxon["gbif_key"]) != "Chordata"

    def _write_with_model(
        self,
        ctx: Any,
        survey: dict[str, Any],
        region_path: Path,
        fixed: dict[str, dict[str, Any]],
        rank: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        schema = load_schema("species-plan")
        lines = []
        for survey_id in sorted(set(fixed) | set(rank)):
            if survey_id in fixed:
                record = fixed[survey_id]
                if record["decision"] == "reuse":
                    taxon = record["taxon"]
                    line = (
                        f"- {survey_id}: reuse the file {record['file']} "
                        f"(GBIF {taxon['gbif_key']} {taxon['rank']} "
                        f"{taxon['name']})."
                    )
                    if record.get("backfilled"):
                        line += (
                            " The survey missed it; GBIF records it at "
                            "the place, so it joins the cast."
                        )
                    lines.append(line)
                elif record["decision"] == "food":
                    lines.append(
                        f"- {survey_id}: food ({record['kind']}), never a file. "
                        f"(GBIF {record['taxon']['gbif_key']} "
                        f"{record['taxon']['rank']} {record['taxon']['name']})."
                    )
                else:
                    lines.append(
                        f"- {survey_id}: needs game code first "
                        f"({record['reason']}), never a file. "
                        f"(GBIF {record['taxon']['gbif_key']} "
                        f"{record['taxon']['rank']} {record['taxon']['name']})."
                    )
            else:
                taxon = rank[survey_id]
                lines.append(
                    f"- {survey_id}: rank me "
                    f"(GBIF {taxon['gbif_key']} {taxon['rank']} {taxon['name']})."
                )
        base = load_prompt(
            "species_plan",
            {
                "SURVEY_JSON": json.dumps(survey, indent=2),
                "MATCH_TABLE": "\n".join(lines),
                "MAX_NEW": str(self.max_new_species),
            },
        )
        problems = ""
        for _ in range(MAX_FIX_ROUNDS + 1):
            ctx.check_budget()
            prompt = base if not problems else f"{base}\n\n{problems}"
            response = ctx.chat(
                [{"role": "user", "content": prompt}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "SpeciesPlan", "schema": schema},
                },
            )
            text = (response.choices[0].message.content or "").strip()
            problems, plan = self._settle(
                text, survey, region_path, fixed, rank, schema
            )
            if not problems:
                return plan
        raise _Stopped("species plan did not settle: " + problems)

    def _attach_references(
        self,
        ctx: Any,
        table: list[dict[str, str]],
        survey: dict[str, Any],
        plan: dict[str, Any],
    ) -> None:
        """Match every new species to its reference-table row, in place.

        The match is the backstop too: a species the word match missed
        but the table lists as not possible yet is saved as needs_code
        (its cap slot is not refilled).
        """
        by_id = {entry["species"]: entry for entry in survey.get("species", [])}

        def ask(text: str) -> str:
            ctx.check_budget()
            response = ctx.chat(
                [{"role": "user", "content": text}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "ReferencePick", "schema": PICK_SCHEMA},
                },
            )
            return (response.choices[0].message.content or "").strip()

        for entry in plan["species"]:
            if entry["decision"] != "new":
                continue
            species = by_id.get(entry["species"], {"species": entry["species"]})
            ref = pick_row(table, species, ask)
            if not ref["start"]:
                entry["decision"] = "needs_code"
                entry["reason"] = (
                    entry.get("reason", "")
                    + f" Saved: the reference table lists {ref['row']} "
                    "as not possible yet."
                ).strip()
                continue
            entry["reference"] = ref

    def _settle(
        self,
        text: str,
        survey: dict[str, Any],
        region_path: Path,
        fixed: dict[str, dict[str, Any]],
        rank: dict[str, dict[str, Any]],
        schema: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        try:
            plan = json.loads(text)
        except json.JSONDecodeError as error:
            return f"PREVIOUS TRY was not JSON: {error}", {}
        entries = plan.get("species", [])
        if {entry.get("species") for entry in entries} != set(fixed) | set(rank):
            return "PREVIOUS TRY must list every match-table species once.", {}
        for entry in entries:
            survey_id = entry["species"]
            if survey_id in fixed:
                want = fixed[survey_id]["decision"]
                if entry.get("decision") != want:
                    return (
                        f"PREVIOUS TRY moved {survey_id} from {want} to "
                        f"{entry.get('decision')}. Keep the match; write reasons.",
                        {},
                    )
            elif entry.get("decision") not in ("new", "candidate"):
                return (
                    f"PREVIOUS TRY gave {survey_id} "
                    f"{entry.get('decision')}; rank-me species are new or "
                    "candidate.",
                    {},
                )
        kept = 0
        for entry in entries:
            if entry.get("decision") != "new":
                continue
            kept += 1
            if kept > self.max_new_species:
                entry["decision"] = "candidate"
                entry["reason"] = (
                    entry.get("reason", "")
                    + f" Saved for later: over the cap of {self.max_new_species}."
                ).strip()
        self._enforce_wild_and_prey(entries, survey, fixed)
        for entry in entries:
            survey_id = entry["species"]
            taxon = fixed[survey_id]["taxon"] if survey_id in fixed else rank[survey_id]
            entry["taxon"] = taxon
            if entry["decision"] == "food":
                entry["food_kind"] = fixed[survey_id]["kind"]
        try:
            jsonschema.validate(plan, schema)
        except jsonschema.ValidationError as error:
            return f"PREVIOUS TRY broke the schema: {error.message}", {}
        sensitive = list(plan.get("sensitive_species", []))
        region = json.loads(region_path.read_text(encoding="utf-8"))
        errors = sensitive_place_errors(
            survey.get("species", []), region.get("places", []), sensitive
        )
        if errors:
            raise _Stopped(
                "the region names real sites for sensitive species: "
                + "; ".join(errors)
            )
        return "", plan

    def _enforce_wild_and_prey(
        self,
        entries: list[dict[str, Any]],
        survey: dict[str, Any],
        fixed: dict[str, dict[str, Any]],
    ) -> None:
        """Demote unbuildable new species, refilling from candidates.

        A new species the model itself calls not wild (herded, domestic,
        farmed, stocked) is saved for a person, and a new predator with
        no prey-role species in the cast waits until its prey is built.
        Each demotion promotes the next suitable candidate, in order, so
        the cast keeps the model's ranking up to the cap. Backfilled
        reuse counts by the part the cast gives it, so a recorded hare
        feeds a new fox.
        """
        roles = {s["species"]: s.get("role") for s in survey.get("species", [])}
        for record in fixed.values():
            if record.get("backfilled"):
                roles[record["file"]] = record["role"]
        while True:
            final = [e for e in entries if e["decision"] in ("reuse", "new")]
            prey = {e["species"] for e in final if roles.get(e["species"]) == "prey"}
            demoted = False
            for entry in entries:
                if entry["decision"] != "new":
                    continue
                if entry.get("wild", True) is False:
                    entry["decision"] = "candidate"
                    entry["reason"] = (
                        entry.get("reason", "")
                        + " Saved for a person: not built as a wild species."
                    ).strip()
                    demoted = True
                elif roles.get(entry["species"]) == "predator" and not prey:
                    entry["decision"] = "candidate"
                    entry["reason"] = (
                        entry.get("reason", "")
                        + " Saved until its prey is built: no prey in the cast."
                    ).strip()
                    demoted = True
            new_count = sum(1 for e in entries if e["decision"] == "new")
            promoted = False
            for entry in entries:
                if new_count >= self.max_new_species:
                    break
                if entry["decision"] != "candidate":
                    continue
                if entry.get("wild", True) is False:
                    continue
                if roles.get(entry["species"]) == "predator" and not prey:
                    continue
                entry["decision"] = "new"
                entry["reason"] = (
                    entry.get("reason", "")
                    + f" Built within the cap of {self.max_new_species}."
                ).strip()
                new_count += 1
                promoted = True
                if roles.get(entry["species"]) == "prey":
                    prey.add(entry["species"])
            if not (demoted or promoted):
                return

    @staticmethod
    def _place_backfilled(
        world: Path,
        region: dict[str, Any],
        fixed: dict[str, dict[str, Any]],
    ) -> None:
        """Seat backfilled reuse in the cast, in place.

        Fish swim, hunters hunt, the hunted are prey, the playable
        play: the file says which, the same way the built places do.
        Before the food kinds and the heal, so the rewrite's own
        check proves the fuller cast.
        """
        for record in fixed.values():
            if not record.get("backfilled"):
                continue
            stem = record["file"]
            data = _animal_file(world, stem)
            if data.get("archetype") == "fish":
                fish = region.setdefault("fish", [])
                if stem not in {entry.get("kind") for entry in fish}:
                    fish.append({"kind": stem, "count": 5})
            animals = region.setdefault("animals", {})
            if data.get("playable"):
                group = animals.setdefault("playable", [])
                if stem not in group:
                    group.append(stem)
            group_name = None
            if record["role"] == "predator":
                group_name = "predators"
            elif record["role"] == "prey" and data.get("archetype") != "fish":
                group_name = "prey"
            if group_name is not None:
                group = animals.setdefault(group_name, [])
                if stem not in group:
                    group.append(stem)

    def _heal_cast(self, world: Path, region: dict[str, Any]) -> set[str]:
        """Feed the reuse cast: missing food kinds and young prey forms.

        Every diet kind of a reused cast animal must be food the region
        has: its `food` list, its ground-cover plants, or nectar from
        its flowers (the same count the animal checker makes). A missing
        kind the region checker allows is added to `food`; one it does
        not (mushrooms with no mushroom plant) stops for a person, since
        growing it invents content. Every tag its hunters need must sit
        on a cast animal: a missing tag is fixed by adding the young
        form (`stage_of`) of a cast animal that carries it, young fish
        with their parent's count. Returns the young forms added. Tags
        no young form carries are left for the new species to provide
        (the animal stage tells the model which).
        """
        allowed = _region_food_kinds(world)
        added: set[str] = set()
        for _ in range(3):
            cast = _cast_files(region)
            known = cast | added
            files = {stem: _animal_file(world, stem) for stem in known}
            kinds = _available_food(region)
            changed = False
            for stem, data in files.items():
                for kind in data.get("diet") or []:
                    if kind in kinds:
                        continue
                    if allowed is not None and kind not in allowed:
                        raise _Stopped(
                            f"{stem} eats {kind}, but no food or plant "
                            "here provides it; a person must decide"
                        )
                    kinds.add(kind)
                    region.setdefault("food", []).append(
                        {"kind": kind, "amount": 10, "names": [kind]}
                    )
                    changed = True
            tags = {tag for data in files.values() for tag in data.get("tags", [])}
            hunts = {tag for data in files.values() for tag in data.get("hunts", [])}
            for tag in sorted(hunts - tags):
                young = _young_carrying(world, known, tag)
                if young is None:
                    continue
                added.add(young)
                parent = _animal_file(world, young).get("stage_of", "")
                placed = False
                for fish in region.get("fish", []):
                    if fish.get("kind") == parent:
                        region["fish"].append(
                            {"kind": young, "count": fish.get("count", 5)}
                        )
                        placed = True
                        break
                if not placed:
                    region["animals"].setdefault("prey", []).append(young)
                changed = True
            if not changed:
                break
        return added

    def _rewrite_region(
        self,
        world: Path,
        job_dir: Path,
        region_path: Path,
        survey: dict[str, Any],
        fixed: dict[str, dict[str, Any]],
        plan: dict[str, Any],
    ) -> None:
        region = json.loads(region_path.read_text(encoding="utf-8"))
        rename = {
            survey_id: record["file"]
            for survey_id, record in fixed.items()
            if record["decision"] == "reuse" and survey_id != record["file"]
        }
        drop = {
            entry["species"]
            for entry in plan["species"]
            if entry["decision"] in ("food", "candidate", "needs_code")
        }
        for group in region.get("animals", {}).values():
            kept = []
            for animal_id in group:
                animal_id = rename.get(animal_id, animal_id)
                if animal_id not in drop:
                    kept.append(animal_id)
            group[:] = kept
        region["fish"] = [
            fish
            for fish in region.get("fish", [])
            if rename.get(fish.get("kind"), fish.get("kind")) not in drop
        ]
        for fish in region.get("fish", []):
            fish["kind"] = rename.get(fish["kind"], fish["kind"])
        self._place_backfilled(world, region, fixed)
        kinds = {food.get("kind", "") for food in region.get("food", [])}
        for entry in plan["species"]:
            if entry["decision"] != "food" or entry["food_kind"] in kinds:
                continue
            kinds.add(entry["food_kind"])
            region.setdefault("food", []).append(
                {
                    "kind": entry["food_kind"],
                    "amount": 10,
                    "names": [entry["species"].replace("_", " ")],
                }
            )
        added_young = self._heal_cast(world, region)
        region_path.write_text(json.dumps(region, indent=2), encoding="utf-8")
        new_ids = {
            entry["species"] for entry in plan["species"] if entry["decision"] == "new"
        }
        result = check_draft(world, job_dir, "species-plan")
        known = {
            path.stem
            for path in (world / "animals").glob("*.json")
            if path.stem != "archetypes"
        }
        errors = draft_region_errors(result, new_ids, _cast_files(region), known)
        if errors:
            raise _Stopped("the rewritten region fails: " + "; ".join(errors))
        plan["added_young"] = sorted(added_young)


def _cast_files(region: dict[str, Any]) -> set[str]:
    cast = set()
    for group in region.get("animals", {}).values():
        cast.update(group)
    cast.update(fish.get("kind", "") for fish in region.get("fish", []))
    return cast - {""}


def _animal_file(world: Path, stem: str) -> dict[str, Any]:
    path = world / "animals" / f"{stem}.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _available_food(region: dict[str, Any]) -> set[str]:
    """Food kinds the region has: `food`, plant food, flower nectar."""
    kinds = {food.get("kind", "") for food in region.get("food", [])} - {""}
    ground = region.get("ground_cover", {})
    if not isinstance(ground, dict):
        return kinds
    for plant in ground.get("plants", []):
        food = plant.get("food")
        if isinstance(food, str):
            kinds.add(food)
        elif isinstance(food, dict) and food.get("kind"):
            kinds.add(food["kind"])
    flowers = ground.get("flowers", 0.0)
    if (
        isinstance(flowers, (int, float))
        and flowers > 0
        and ground.get("flower_colors")
    ):
        kinds.add("nectar")
    return kinds


def _region_food_kinds(world: Path) -> set[str] | None:
    """The region checker's FOOD_KINDS, read from the checkout.

    Never copied: the world's lists have one home. Test doubles have no
    checker, so they get None and heal anything.
    """
    path = world / "tools" / "validate_region.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("animundo_validate_region", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        return None
    kinds = getattr(module, "FOOD_KINDS", None)
    return set(kinds) if kinds else None


def _young_carrying(world: Path, cast: set[str], tag: str) -> str | None:
    """A young form of a cast animal that carries `tag`, if one exists."""
    for path in sorted((world / "animals").glob("*.json")):
        if path.stem == "archetypes" or path.stem in cast:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("stage_of") in cast and tag in data.get("tags", []):
            return path.stem
    return None


def _file_taxa(world: Path) -> dict[str, dict[str, Any]]:
    """GBIF taxon block per animal file stem (skipping the index)."""
    taxa = {}
    for path in (world / "animals").glob("*.json"):
        if path.stem == "archetypes":
            continue
        taxon = json.loads(path.read_text(encoding="utf-8")).get("taxon")
        if taxon:
            taxa[path.stem] = taxon
    return taxa


def _grown_form(world: Path, files: Collection[str]) -> str:
    """The file to reuse: a grown form over a `stage_of` young one."""
    grown = []
    for name in files:
        data = json.loads((world / name).read_text(encoding="utf-8"))
        if not data.get("stage_of"):
            grown.append(Path(name).stem)
    return sorted(grown or [Path(n).stem for n in files], key=len)[0]


def _backfill_role(data: dict[str, Any], hunts: set[str]) -> str | None:
    """The cast part for a backfilled animal, if the cast gives it one.

    Read off the file the way the built places do: the `predator` tag
    hunts, fish swim, hunters without the tag play, the hunted are
    prey, the playable play, and the rest (unplayable and unhunted)
    sit out.
    """
    tags = set(data.get("tags", []) or [])
    if "predator" in tags:
        return "predator"
    if data.get("archetype") == "fish":
        return "prey" if tags & hunts else "fish"
    if data.get("hunts"):
        return "playable" if data.get("playable") else None
    if tags & hunts:
        return "prey"
    if data.get("playable"):
        return "playable"
    return None


def _refuse_collision(
    survey_id: str, taxon: dict[str, Any], taxa: dict[str, dict[str, Any]]
) -> None:
    same_name = taxa.get(survey_id)
    if same_name is not None and same_name["gbif_key"] != taxon["gbif_key"]:
        raise _Stopped(
            f"{survey_id} is a different species from the file with its "
            "name; a person must rename one"
        )


def _refuse_other_subspecies(
    survey_id: str, taxon: dict[str, Any], taxa: dict[str, dict[str, Any]]
) -> None:
    words = str(taxon.get("name", "")).split()[:2]
    if len(words) < 2:
        return
    for stem, other in taxa.items():
        if (
            str(other.get("name", "")).split()[:2] == words
            and other["gbif_key"] != taxon["gbif_key"]
        ):
            raise _Stopped(
                f"{survey_id} ({taxon['name']}) is a different subspecies "
                f"from {stem} ({other['name']}); a person must decide"
            )


def _food_kind(survey_id: str) -> str:
    if set(survey_id.split("_")) & WATER_BREEDERS:
        return "water_bugs"
    return "insects"


class _Stopped(Exception):
    """The plan needs a person. The job stops with a reason."""
