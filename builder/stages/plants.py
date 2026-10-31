"""Stage 8, plants: build the region's new ground plants and shrubs.

The region names its ground-cover plant models; ones the checkout
already has are reused, and at most 3 new ones per place get specs
here. Each new plant loops like an animal model: the spec is drafted
from the bilberry example and the community's plant rules, its Latin
name is verified against GBIF (a second name for a known species
reuses that spec instead), Blender builds it in a scratch copy of
the world, the world's own models verdict and a preview review feed
back, 6 rounds at most. Finals land in the job's `models/plants/`;
a plant the region marks as food, or that warns about safety, is
flagged for an expert to read later.
"""

from __future__ import annotations

import base64
import functools
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from string import Template
from typing import Any

import jsonschema
from referencing import Registry, Resource

from ..prompts import PROMPTS_DIR, fix_messages, for_children, load_prompt
from ..schemas import load_schema, validate
from ..world import check_draft, mentions, problems_by_file
from .models import build_in_sandbox
from .region import new_plant_ids
from .species_plan import taxon_same

MAX_ROUNDS_PER_PLANT = 6
PROGRESS_FILE = "progress.json"

FLORA_TOPICS = ("plant", "flower", "vegetation", "tree", "forest", "meadow", "grass")

MatchFn = Callable[[Path, str], dict[str, Any]]
ChatFn = Callable[..., Any]
BuildFn = Callable[[Path, str], dict[str, Any]]


class PlantsStage:
    """Build `models/plants/<id>.json` for the region's new plants."""

    name = "plants"

    def __init__(
        self,
        max_new_plants: int = 3,
        author: str = "Animundo Builder",
        match: MatchFn | None = None,
        chat: ChatFn | None = None,
        build: BuildFn | None = None,
    ) -> None:
        self.max_new_plants = max_new_plants
        self.author = author
        self._match = match or _match_plant
        self._chat = chat
        self._build = build or _build_plant

    def run(self, ctx: Any) -> dict[str, Any]:
        if ctx.world_path is None:
            raise ValueError("plants needs --world")
        world = Path(ctx.world_path)
        regions = sorted((ctx.job_dir / "regions").glob("*.json"))
        if not regions:
            return self._nothing()
        region_path = regions[0]
        region = json.loads(region_path.read_text(encoding="utf-8"))
        entries = {
            plant.get("model", ""): plant
            for plant in region.get("ground_cover", {}).get("plants", [])
        }
        entries.pop("", None)
        new_ids = sorted(new_plant_ids(region, world))
        if len(new_ids) > self.max_new_plants:
            raise _Stopped(
                f"the region names {len(new_ids)} new plants "
                f"({', '.join(new_ids)}), at most {self.max_new_plants}"
            )
        if not new_ids:
            # Items that name no plants skip the survey, the
            # sandbox and every model call (T76's item pipeline).
            return self._nothing()
        survey = json.loads((ctx.job_dir / "survey.json").read_text(encoding="utf-8"))
        sandbox = _ensure_sandbox(ctx.job_dir, world)
        facts = _flora_facts(survey)
        progress = _load_progress(ctx.job_dir)
        rounds: dict[str, int] = dict(progress.get("rounds", {}))
        passed = set(progress.get("passed", [])) & set(new_ids)
        reused: dict[str, str] = dict(progress.get("reused", {}))
        expert: set[str] = set(progress.get("expert", []))
        files = []
        for plant_id in new_ids:
            if plant_id in passed:
                files.append(f"models/plants/{plant_id}.json")
                continue
            target, seconds = self._plant(
                ctx, world, sandbox, region, entries[plant_id], facts, plant_id
            )
            rounds[plant_id] = seconds
            if target != plant_id:
                reused[plant_id] = target
                _rewrite_model(region_path, plant_id, target)
            else:
                passed.add(plant_id)
                files.append(f"models/plants/{plant_id}.json")
                if entries[plant_id].get("food") or _needs_expert(
                    world, ctx.job_dir, plant_id
                ):
                    expert.add(plant_id)
            _save_progress(ctx.job_dir, new_ids, rounds, passed, reused, expert)
        return {
            "plant_files": sorted(files),
            "rounds": {i: rounds[i] for i in new_ids if i in rounds},
            "reused": reused,
            "expert_review": sorted(expert),
        }

    @staticmethod
    def _nothing() -> dict[str, Any]:
        """No plants named, nothing built, $0."""
        return {"plant_files": [], "rounds": {}, "reused": {}, "expert_review": []}

    def _plant(
        self,
        ctx: Any,
        world: Path,
        sandbox: Path,
        region: dict[str, Any],
        entry: dict[str, Any],
        facts: str,
        plant_id: str,
    ) -> tuple[str, int]:
        names = ", ".join(entry.get("names", [])) or plant_id.replace("_", " ")
        spec: dict[str, Any] | None = None
        previous: str | None = None
        issues: list[dict[str, Any]] = []
        errors: list[str] = []
        for round_no in range(1, MAX_ROUNDS_PER_PLANT + 1):
            spec, previous = self._draft(
                ctx,
                world,
                region,
                facts,
                plant_id,
                names,
                spec,
                issues,
                errors,
                previous,
            )
            if spec.get("id") != plant_id:
                renamed = spec.get("id")
                raise _Stopped(f"{plant_id}: the draft renamed itself {renamed!r}")
            try:
                _validate_plant(world, spec)
            except jsonschema.ValidationError as error:
                errors = [f"broke the plant schema: {error.message}"]
                issues = []
                self._save_round(ctx, plant_id, round_no, spec, sandbox, _NO_BUILD)
                continue
            found = self._match(world, spec.get("latin", ""))
            target = _reuse_target(found)
            if target is not None:
                return target, round_no
            spec["taxon"] = found["taxon"]
            (sandbox / "models" / "plants" / f"{plant_id}.json").write_text(
                json.dumps(spec, indent=2), encoding="utf-8"
            )
            built = self._build(sandbox, plant_id)
            if not built["ok"] and not built["log"].strip():
                raise _Stopped(
                    "the build failed with no output: Blender itself "
                    "did not run here, not the spec. A person looks."
                )
            problems = self._check(ctx, sandbox, plant_id, round_no)
            if not built["ok"]:
                problems = [built["log"]] + problems
            self._save_round(ctx, plant_id, round_no, spec, sandbox, built)
            preview = sandbox / "models" / "previews" / "plants" / f"{plant_id}.png"
            if preview.is_file():
                issues = self._review(ctx, plant_id, round_no, spec, preview, built)[
                    "issues"
                ]
            else:
                issues = []
            errors = problems
            if built["ok"] and not problems and not _blocking(issues):
                self._save_final(ctx, sandbox, plant_id)
                return plant_id, round_no
        raise _Stopped(
            f"{plant_id}: still failing after {MAX_ROUNDS_PER_PLANT} rounds; "
            "a person looks"
        )

    def _draft(
        self,
        ctx: Any,
        world: Path,
        region: dict[str, Any],
        facts: str,
        plant_id: str,
        names: str,
        spec: dict[str, Any] | None,
        issues: list[dict[str, Any]],
        errors: list[str],
        previous: str | None,
    ) -> tuple[dict[str, Any], str]:
        ctx.check_budget()
        template = (PROMPTS_DIR / "plant_write.md").read_text(encoding="utf-8")
        bush = (world / "models" / "plants" / "bilberry.json").read_text()
        ground = (world / "models" / "plants" / "wood_anemone.json").read_text()
        examples = (
            f"A bush (bilberry):\n{bush}\n\nA ground plant (wood anemone):\n{ground}"
        )
        prompt = Template(template).substitute(
            {
                "PLANT_ID": plant_id,
                "NAMES": names,
                "REGION_NAME": region.get("name", region.get("id", "")),
                "REGION_COUNTRY": region.get("country", ""),
                "FACTS": facts or "(the survey names no plants)",
                "EXAMPLES": examples.replace("$", "$$"),
                "RULES": _plant_rules(world),
                "AUTHOR": self.author,
                "FOR_CHILDREN": for_children(),
            }
        )
        first = {"role": "user", "content": prompt}
        if spec is not None and previous is not None:
            notes = "\n".join(
                [f"- {error}" for error in errors]
                + [
                    f"- {issue['part']}: {issue['problem']} Fix: {issue['fix']}"
                    for issue in issues
                ]
            )
            messages = fix_messages(first, previous, notes)
        else:
            messages = [first]
        response = self._ask_text(ctx, messages)
        if not response:
            # An empty answer retries once, like a cut one
            # (#140.4): the kelp draft came back with nothing.
            response = self._ask_text(ctx, messages)
        try:
            return json.loads(response), response
        except json.JSONDecodeError as error:
            raise _Stopped(f"{plant_id}: the draft was not JSON: {error}")

    def _check(
        self, ctx: Any, sandbox: Path, plant_id: str, round_no: int
    ) -> list[str]:
        extra = {
            f"models/plants/{plant_id}.json": sandbox
            / "models"
            / "plants"
            / f"{plant_id}.json"
        }
        built = sandbox / "assets" / "plants" / f"{plant_id}.glb"
        if built.is_file():
            extra[f"assets/plants/{plant_id}.glb"] = built
        preview = sandbox / "models" / "previews" / "plants" / f"{plant_id}.png"
        if preview.is_file():
            # The plant rules require the preview sheet, so it layers too.
            extra[f"models/previews/plants/{plant_id}.png"] = preview
        result = check_draft(
            Path(ctx.world_path),
            ctx.job_dir,
            f"plants-{plant_id}-{round_no}",
            extra=extra,
        )
        from ..enrich import (
            place_tiles,
            sort_problems,
            sort_region_problems,
            split_not_built,
            write_places,
        )

        rid_to_tile = place_tiles(ctx.job_dir)
        verdicts = {
            entry.get("check", ""): entry for entry in result.get("results", [])
        }
        if rid_to_tile:
            # An enrich plant checks in its tiles' places: the
            # food web and the region verdict land per tile for
            # the report, never the fix round, and anything
            # outside the new plant is the place's (#161.2).
            animals = verdicts.get("animals", {})
            _, tiled, general = sort_problems(
                (
                    animals.get("problems", [])
                    if animals.get("status") == "fail"
                    else []
                ),
                plant_id,
                rid_to_tile,
            )
            region = verdicts.get("region", {})
            if region.get("status") == "fail":
                real, unbuilt = split_not_built(region.get("problems", []))
                region_tiled, region_general = sort_region_problems(real, rid_to_tile)
                for tile, reasons in region_tiled.items():
                    tiled.setdefault(tile, []).extend(reasons)
                general.extend(region_general)
            else:
                unbuilt = []
            notes = []
            if region.get("status") == "skip" and region.get("reason"):
                notes.append(region["reason"])
            if unbuilt:
                notes.append(f"models not built in this checkout: {', '.join(unbuilt)}")
            region_note = "; ".join(notes) or None

            def record() -> None:
                write_places(ctx.job_dir, rid_to_tile, tiled, general, region_note)

        else:
            tiled, general = {}, []

            def record() -> None:
                pass

        models = verdicts.get("models", {})
        if models.get("status") != "fail":
            record()
            return []
        errors = []
        for file, lines in problems_by_file(models.get("problems", [])).items():
            for line in lines:
                owner = (
                    _file_owner(file, plant_id)
                    if file
                    else (plant_id if mentions(line, plant_id) else None)
                )
                if owner is None:
                    if rid_to_tile:
                        general.append(
                            f"{file}: {line.strip()}" if file else line.strip()
                        )
                        continue
                    raise _Stopped(
                        "the checker failed outside the new plant: "
                        + (f"{file}: {line.strip()}" if file else line.strip())
                    )
                if "the preview sheet is missing" in line:
                    # The draft checker never overlays previews, so the
                    # plant rules always miss ours; the build just wrote
                    # it (a failed build reports its own log instead).
                    continue
                text = line[2:].strip() if line.startswith("- ") else line.strip()
                errors.append(f"{file}: {text}" if file else text)
        record()
        return errors

    def _review(
        self,
        ctx: Any,
        plant_id: str,
        round_no: int,
        spec: dict[str, Any],
        preview: Path,
        built: dict[str, Any],
    ) -> dict[str, Any]:
        bar_id = "bilberry" if spec.get("kind") == "bush" else "wood_anemone"
        bar = _bar_preview(ctx, plant_id, bar_id)
        prompt = load_prompt(
            "model_review_plant",
            {
                "TITLE": str(spec.get("name", plant_id)),
                "BAR_TITLE": _bar_title(Path(ctx.world_path), bar_id),
                "SPECIES": plant_id,
                "ROUND": str(round_no),
                "BUILT": built["summary"],
                "YEAR_JSON": json.dumps(
                    {
                        "latin": spec.get("latin"),
                        "kind": spec.get("kind"),
                        "year": spec.get("year"),
                    },
                    indent=2,
                ),
            },
        )
        review = self._read_review(ctx, plant_id, round_no, prompt, [bar, preview])
        out = ctx.job_dir / "reviews" / f"{plant_id}-{round_no}.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(review, indent=2), encoding="utf-8")
        return review

    def _read_review(
        self,
        ctx: Any,
        plant_id: str,
        round_no: int,
        prompt: str,
        images: list[Path],
    ) -> dict[str, Any]:
        """One review read, valid or a clean stop. Two tries, no saving."""
        problems = ""
        previous: str | None = None
        for _ in range(2):
            answer = self._ask(ctx, prompt, images, problems, previous)
            previous = answer
            try:
                review = json.loads(_unfence(answer))
                validate("review", review)
            except (json.JSONDecodeError, jsonschema.ValidationError) as error:
                problems = f"PREVIOUS TRY was not a valid review: {error}"
                continue
            if review["species"] != plant_id or review["round"] != round_no:
                problems = (
                    "PREVIOUS TRY named the wrong plant or round: "
                    f"{review['species']} round {review['round']}"
                )
                continue
            return review
        raise _Stopped(f"the reviewer never wrote a valid review: {problems}")

    def _ask(
        self,
        ctx: Any,
        prompt: str,
        images: list[Path],
        problems: str = "",
        previous: str | None = None,
    ) -> str:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for path in images:
            encoded = base64.b64encode(path.read_bytes()).decode()
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{encoded}"},
                }
            )
        first = {"role": "user", "content": content}
        messages = (
            fix_messages(first, previous, problems)
            if problems and previous is not None
            else [first]
        )
        chat = self._chat or ctx.chat
        response = chat(
            messages,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "Review", "schema": load_schema("review")},
            },
        )
        return (response.choices[0].message.content or "").strip()

    def _ask_text(self, ctx: Any, messages: list[dict[str, Any]]) -> str:
        chat = self._chat or ctx.chat
        response = chat(messages)
        return (response.choices[0].message.content or "").strip()

    def _save_round(
        self,
        ctx: Any,
        plant_id: str,
        round_no: int,
        spec: dict[str, Any],
        sandbox: Path,
        built: dict[str, Any],
    ) -> None:
        folder = ctx.job_dir / "rounds" / plant_id / str(round_no)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "spec.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
        (folder / "build.log").write_text(built["log"], encoding="utf-8")
        preview = sandbox / "models" / "previews" / "plants" / f"{plant_id}.png"
        if preview.is_file():
            (folder / "preview.png").write_bytes(preview.read_bytes())

    def _save_final(self, ctx: Any, sandbox: Path, plant_id: str) -> None:
        folder = ctx.job_dir / "models" / "plants"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{plant_id}.json").write_text(
            (sandbox / "models" / "plants" / f"{plant_id}.json").read_text(),
            encoding="utf-8",
        )
        built = sandbox / "assets" / "plants" / f"{plant_id}.glb"
        if built.is_file():
            (folder / f"{plant_id}.glb").write_bytes(built.read_bytes())
        preview = sandbox / "models" / "previews" / "plants" / f"{plant_id}.png"
        if preview.is_file():
            (folder / f"{plant_id}.png").write_bytes(preview.read_bytes())


class _Stopped(Exception):
    """The stage ran out of rounds, or the checkout itself fails."""


def _match_plant(world: Path, latin: str) -> dict[str, Any]:
    return taxon_same(world, latin, plant=True)


def _build_plant(sandbox: Path, plant_id: str) -> dict[str, Any]:
    """Run the world's build script on one plant spec, in the sandbox."""
    return build_in_sandbox(sandbox, f"models/plants/{plant_id}.json")


def _ensure_sandbox(job_dir: Path, world: Path) -> Path:
    """A scratch copy of the world to build in, shared with the models."""
    sandbox = job_dir / "sandbox"
    if not (sandbox / "tools" / "build_model.sh").is_file():
        if sandbox.exists():
            shutil.rmtree(sandbox)
        shutil.copytree(world, sandbox, ignore=shutil.ignore_patterns(".git"))
    return sandbox


def _flora_facts(survey: dict[str, Any]) -> str:
    lines = []
    for fact in survey.get("facts", []):
        topic = str(fact.get("topic", "")).lower()
        if any(word in topic for word in FLORA_TOPICS):
            lines.append(f"- {fact.get('text', '')}")
    return "\n".join(lines)


def _plant_rules(world: Path) -> str:
    contributing = (world / "CONTRIBUTING.md").read_text(encoding="utf-8")
    plants = contributing.find("## Adding plants")
    start = contributing.find("### The rules", plants)
    end = contributing.find("\n## ", start + 1)
    rules = contributing[start:end].strip()
    recipe = rules.find("> Add a **[PLANT]**")
    if recipe > 0:
        rules = rules[:recipe].strip()
    guide = (world / "models" / "README.md").read_text(encoding="utf-8")
    gstart = guide.find("## Plants")
    return rules + "\n\n" + guide[gstart:].strip()


def _reuse_target(found: dict[str, Any]) -> str | None:
    """The existing plant spec for this taxon, if the world has one."""
    for file in found.get("files", []):
        if file.startswith("models/plants/") and file.endswith(".json"):
            return file[len("models/plants/") : -len(".json")]
    return None


def _rewrite_model(region_path: Path, plant_id: str, target: str) -> None:
    region = json.loads(region_path.read_text(encoding="utf-8"))
    for plant in region.get("ground_cover", {}).get("plants", []):
        if plant.get("model") == plant_id:
            plant["model"] = target
    region_path.write_text(json.dumps(region, indent=2), encoding="utf-8")


def _file_owner(file: str, plant_id: str) -> str | None:
    return plant_id if file == f"models/plants/{plant_id}.json" else None


def _bar_preview(ctx: Any, plant_id: str, bar_id: str) -> Path:
    """The game's own plant of this kind, copied into the job as the bar."""
    src = Path(ctx.world_path) / "models" / "previews" / "plants" / f"{bar_id}.png"
    if not src.is_file():
        raise _Stopped(f"no bar preview for {bar_id}: {src} is missing")
    dest = ctx.job_dir / "references" / plant_id / f"closest-{bar_id}.png"
    if not dest.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    return dest


def _bar_title(world: Path, bar_id: str) -> str:
    """The bar plant's name for the review prompt."""
    data = json.loads((world / "models" / "plants" / f"{bar_id}.json").read_text())
    return str(data.get("name") or bar_id.replace("_", " ").title())


def _blocking(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [issue for issue in issues if issue.get("severity") == "blocking"]


def _unfence(answer: str) -> str:
    text = answer.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _needs_expert(world: Path, job_dir: Path, plant_id: str) -> bool:
    """A poisonous plant, by the world's list or its own notes."""
    data = json.loads((job_dir / "models" / "plants" / f"{plant_id}.json").read_text())
    notes = str(data.get("year", {}).get("notes", "")).lower()
    if "poison" in notes or "toxic" in notes:
        return True
    key = data.get("taxon", {}).get("gbif_key")
    listed = json.loads((world / "poisonous-plants.json").read_text()).get("plants", [])
    return any(entry.get("gbif_key") == key for entry in listed)


_NO_BUILD: dict[str, Any] = {"ok": False, "summary": "no build", "log": "skipped"}


@functools.lru_cache(maxsize=8)
def _plant_validator(world: Path) -> jsonschema.Draft202012Validator:
    """The world's model schema, with cross-file refs resolved locally."""
    schemas = {}
    for path in sorted((world / "schemas").glob("*.schema.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if "$id" in data:
            schemas[data["$id"]] = Resource.from_contents(data)
    registry = Registry().with_resources(schemas.items())
    model = json.loads((world / "schemas" / "model.schema.json").read_text())
    return jsonschema.Draft202012Validator(model, registry=registry)


def _validate_plant(world: Path, spec: dict[str, Any]) -> None:
    """The world's own plant schema, in code, before any build."""
    _plant_validator(world).validate(spec)


def _load_progress(job_dir: Path) -> dict[str, Any]:
    path = job_dir / "plants" / PROGRESS_FILE
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_progress(
    job_dir: Path,
    new_ids: list[str],
    rounds: dict[str, int],
    passed: set[str],
    reused: dict[str, str],
    expert: set[str],
) -> None:
    folder = job_dir / "plants"
    folder.mkdir(exist_ok=True)
    (folder / PROGRESS_FILE).write_text(
        json.dumps(
            {
                "rounds": {i: rounds[i] for i in new_ids if i in rounds},
                "passed": sorted(passed),
                "reused": reused,
                "expert": sorted(expert),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
