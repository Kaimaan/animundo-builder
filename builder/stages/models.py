"""The model loop (stage 7, steps 4-7): spec, build, review, fix.

For every new species: Muse Spark writes `models/<id>.json` from the
measurements, the reference sheet and the closest existing spec;
`tools/build_model.sh` builds it in a scratch copy of animundo-world;
the reviewer judges the preview against the reference sheet part by
part and frame by frame; a clean review after blocking issues is
verified by a second look before it counts; fixes go back into the
spec. At most six rounds. The loop ends when the review finds no
blocking issue and the world's own checks pass.

Every round keeps its spec, preview, build log and review in the job
folder: they are the build report's key shots. The scratch world is
never the real checkout; the submit step carries the final files.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import jsonschema

from ..prompts import load_prompt
from ..schemas import load_schema, validate
from ..world import check_draft, problems_by_file

MAX_ROUNDS = 6
SPEC_RETRIES = 3
CLOSEST = {"golden_eagle": "great_tit", "red_deer": "moose"}
KIND = {"golden_eagle": "bird", "red_deer": "mammal"}

ChatFn = Callable[..., Any]
BuildFn = Callable[[Path, str], dict[str, Any]]
CheckFn = Callable[[Any, Path, str, int], list[str]]


class ModelsStage:
    """Build reviewed models for every new species."""

    name = "models"

    def __init__(
        self,
        reference: str = "sheet",
        closest: dict[str, str] | None = None,
        kind: dict[str, str] | None = None,
        author: str = "Animundo Builder",
        max_rounds: int = MAX_ROUNDS,
        chat: ChatFn | None = None,
        build: BuildFn | None = None,
        check: CheckFn | None = None,
    ) -> None:
        if reference not in ("sheet", "none"):
            raise ValueError("reference must be 'sheet' or 'none'")
        self.reference = reference
        self.closest = CLOSEST if closest is None else closest
        self.kind = KIND if kind is None else kind
        self.author = author
        self.max_rounds = max_rounds
        self._chat = chat
        self._build = build or build_in_sandbox
        self._check = check or check_in_sandbox

    def run(self, ctx: Any) -> dict[str, Any]:
        if ctx.world_path is None:
            raise _Stopped("the job has no world checkout to build in")
        world = Path(ctx.world_path)
        plan = json.loads((ctx.job_dir / "species-plan.json").read_text())
        new_ids = [
            entry["species"] for entry in plan["species"] if entry["decision"] == "new"
        ]
        survey = json.loads((ctx.job_dir / "survey.json").read_text())
        surveyed = {entry["species"]: entry for entry in survey["species"]}
        sandbox = _ensure_sandbox(ctx.job_dir, world)
        files = []
        for species_id in new_ids:
            files.append(self._model_species(ctx, sandbox, world, species_id, surveyed))
        return {"model_files": sorted(files)}

    def _model_species(
        self,
        ctx: Any,
        sandbox: Path,
        world: Path,
        species_id: str,
        surveyed: dict[str, Any],
    ) -> str:
        if species_id not in self.closest or species_id not in self.kind:
            raise _Stopped(f"{species_id}: no starting spec known; add one")
        entry = surveyed.get(species_id, {})
        measurements = json.loads(
            (ctx.job_dir / "measurements" / f"{species_id}.json").read_text()
        )
        rules = (
            (world / "ART.md").read_text(encoding="utf-8")
            + "\n\n"
            + (world / "models" / "README.md").read_text(encoding="utf-8")
        )
        closest_id = self.closest[species_id]
        closest_spec = (world / "models" / f"{closest_id}.json").read_text()
        animal = _animal_title(ctx.job_dir, species_id)
        sheet = _latest_sheet(ctx.job_dir, species_id, self.reference)
        spec: dict[str, Any] | None = None
        issues: list[dict[str, Any]] = []
        errors: list[str] = []
        for round_no in range(1, self.max_rounds + 1):
            if spec is None:
                prompt = load_prompt(
                    "model_spec",
                    {
                        "SPECIES": species_id,
                        "TITLE": animal,
                        "LATIN": entry.get("latin", ""),
                        "AUTHOR": self.author,
                        "DESCRIPTION": entry.get("description", ""),
                        "MEASUREMENTS": json.dumps(measurements, indent=2),
                        "CLOSEST_NAME": closest_id,
                        "CLOSEST_SPEC": closest_spec,
                        "RULES": rules,
                    },
                )
            else:
                prompt = load_prompt(
                    "model_fix",
                    {
                        "SPEC": json.dumps(spec, indent=2),
                        "ISSUES": json.dumps(issues, indent=2),
                        "ERRORS": "\n".join(errors) or "(none)",
                        "RULES": rules,
                    },
                )
            prev = (
                ctx.job_dir / "rounds" / species_id / str(round_no - 1) / "preview.png"
            )
            prior = [prev] if round_no > 1 and prev.is_file() else []
            spec = self._write_spec(ctx, world, prompt, sheet, prior)
            (sandbox / "models" / f"{species_id}.json").write_text(
                json.dumps(spec, indent=2), encoding="utf-8"
            )
            preview = sandbox / "models" / "previews" / f"{species_id}.png"
            preview.unlink(missing_ok=True)
            built = self._build(sandbox, species_id)
            if not built["ok"] and not built["log"].strip():
                raise _Stopped(
                    "the build failed with no output: Blender itself "
                    "did not run here, not the spec. A person looks."
                )
            problems = list(self._check(ctx, sandbox, species_id, round_no))
            if not built["ok"]:
                problems = [built["log"]] + problems
            self._save_round(ctx, species_id, round_no, spec, sandbox, built)
            if preview.is_file():
                review = self._review(ctx, species_id, round_no, sheet, sandbox, built)
                issues = review["issues"]
            else:
                issues = []
            errors = problems
            if built["ok"] and not problems and not _blocking(issues):
                self._save_final(ctx, sandbox, species_id)
                return f"models/{species_id}.json"
        raise _Stopped(
            f"{species_id}: still failing after {self.max_rounds} rounds; "
            "a person looks"
        )

    def _write_spec(
        self,
        ctx: Any,
        world: Path,
        prompt: str,
        sheet: Path | None,
        prior: list[Path] | None = None,
    ) -> dict[str, Any]:
        schema = json.loads((world / "schemas" / "model.schema.json").read_text())
        images = ([sheet] if sheet else []) + list(prior or [])
        problems = ""
        for _ in range(SPEC_RETRIES):
            text = prompt if not problems else f"{prompt}\n\n{problems}"
            answer = _content(self._ask(ctx, text, images or None, None))
            try:
                spec = json.loads(_unfence(answer))
            except json.JSONDecodeError as error:
                problems = f"PREVIOUS TRY was not JSON: {error}"
                continue
            try:
                jsonschema.validate(spec, schema)
            except jsonschema.ValidationError as error:
                problems = f"PREVIOUS TRY broke the spec schema: {error.message}"
                continue
            return spec
        raise _Stopped(f"the spec writer never wrote valid JSON: {problems}")

    def _review(
        self,
        ctx: Any,
        species_id: str,
        round_no: int,
        sheet: Path | None,
        sandbox: Path,
        built: dict[str, Any],
    ) -> dict[str, Any]:
        kind = self.kind[species_id]
        closest_id = self.closest[species_id]
        bar = _bar_preview(ctx, species_id, closest_id)
        prompt = load_prompt(
            _review_prompt_name(kind, sheet is not None),
            {
                "TITLE": _animal_title(ctx.job_dir, species_id),
                "SPECIES": species_id,
                "ROUND": str(round_no),
                "BUILT": built["summary"],
                "CLOSEST_TITLE": _closest_title(Path(ctx.world_path), closest_id),
            },
        )
        previous = _previous_blocking(ctx.job_dir, species_id, round_no)
        preview = sandbox / "models" / "previews" / f"{species_id}.png"
        images = [path for path in (sheet, bar, preview) if path is not None]
        review = self._read_review(ctx, species_id, round_no, prompt, images)
        if not _blocking(review["issues"]) and previous:
            kept = ctx.job_dir / "reviews" / f"{species_id}-{round_no}-unverified.json"
            kept.parent.mkdir(exist_ok=True)
            kept.write_text(json.dumps(review, indent=2), encoding="utf-8")
            verify = load_prompt(
                "model_reverify",
                {
                    "ISSUES": json.dumps(previous, indent=2),
                    "SPECIES": species_id,
                    "ROUND": str(round_no),
                },
            )
            review = self._read_review(ctx, species_id, round_no, verify, images)
        out = ctx.job_dir / "reviews" / f"{species_id}-{round_no}.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(review, indent=2), encoding="utf-8")
        return review

    def _read_review(
        self,
        ctx: Any,
        species_id: str,
        round_no: int,
        prompt: str,
        images: list[Path],
    ) -> dict[str, Any]:
        """One review read, valid or a clean stop. Two tries, no saving."""
        schema = load_schema("review")
        problems = ""
        for _ in range(2):
            text = prompt if not problems else f"{prompt}\n\n{problems}"
            answer = _content(
                self._ask(
                    ctx,
                    text,
                    images or None,
                    {
                        "type": "json_schema",
                        "json_schema": {"name": "Review", "schema": schema},
                    },
                )
            )
            try:
                review = json.loads(_unfence(answer))
                validate("review", review)
            except (json.JSONDecodeError, jsonschema.ValidationError) as error:
                problems = f"PREVIOUS TRY was not a valid review: {error}"
                continue
            if review["species"] != species_id or review["round"] != round_no:
                problems = (
                    "PREVIOUS TRY named the wrong species or round: "
                    f"{review['species']} round {review['round']}"
                )
                continue
            return review
        raise _Stopped(f"the reviewer never wrote a valid review: {problems}")

    def _ask(
        self,
        ctx: Any,
        prompt: str,
        images: list[Path] | None,
        response_format: dict[str, Any] | None,
    ) -> Any:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for path in images or []:
            encoded = base64.b64encode(path.read_bytes()).decode()
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{encoded}"},
                }
            )
        chat = self._chat or ctx.chat
        messages = [{"role": "user", "content": content}]
        return chat(messages, response_format=response_format)

    def _save_round(
        self,
        ctx: Any,
        species_id: str,
        round_no: int,
        spec: dict[str, Any],
        sandbox: Path,
        built: dict[str, Any],
    ) -> None:
        folder = ctx.job_dir / "rounds" / species_id / str(round_no)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "spec.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
        (folder / "build.log").write_text(built["log"], encoding="utf-8")
        preview = sandbox / "models" / "previews" / f"{species_id}.png"
        if preview.is_file():
            (folder / "preview.png").write_bytes(preview.read_bytes())

    def _save_final(self, ctx: Any, sandbox: Path, species_id: str) -> None:
        folder = ctx.job_dir / "models"
        folder.mkdir(exist_ok=True)
        (folder / f"{species_id}.json").write_text(
            (sandbox / "models" / f"{species_id}.json").read_text(), encoding="utf-8"
        )
        built = sandbox / "assets" / "animals" / f"{species_id}.glb"
        if built.is_file():
            (folder / f"{species_id}.glb").write_bytes(built.read_bytes())
        preview = sandbox / "models" / "previews" / f"{species_id}.png"
        if preview.is_file():
            (folder / f"{species_id}.png").write_bytes(preview.read_bytes())


def _ensure_sandbox(job_dir: Path, world: Path) -> Path:
    """A scratch copy of the world to build in, made once per job."""
    sandbox = job_dir / "sandbox"
    if not (sandbox / "tools" / "build_model.sh").is_file():
        if sandbox.exists():
            shutil.rmtree(sandbox)
        shutil.copytree(world, sandbox, ignore=shutil.ignore_patterns(".git"))
    return sandbox


def build_in_sandbox(sandbox: Path, species_id: str) -> dict[str, Any]:
    """Run the world's build script; parse the BUILT line or keep the log."""
    done = subprocess.run(
        ["sh", "tools/build_model.sh", f"models/{species_id}.json"],
        cwd=sandbox,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    log = (done.stdout + done.stderr).strip()
    match = re.search(r"^BUILT (\S+): (.+)$", log, re.MULTILINE)
    if done.returncode == 0 and match:
        size = re.search(r"size: ([0-9.]+ m long, [0-9.]+ m tall)", log)
        return {
            "ok": True,
            "summary": f"{match.group(1)}: {match.group(2)}"
            + (f"; size {size.group(1)}" if size else ""),
            "log": log,
        }
    return {"ok": False, "summary": "the build failed", "log": log}


def check_in_sandbox(
    ctx: Any, sandbox: Path, species_id: str, round_no: int
) -> list[str]:
    """The models verdict, via the draft checker.

    The models verdict is the one the world CI runs. World-owned
    problems arrive split out as `existing`, so everything left in
    `problems` is the draft's and feeds back.
    """
    extra = {f"models/{species_id}.json": sandbox / "models" / f"{species_id}.json"}
    built = sandbox / "assets" / "animals" / f"{species_id}.glb"
    if built.is_file():
        extra[f"assets/animals/{species_id}.glb"] = built
    result = check_draft(
        Path(ctx.world_path),
        ctx.job_dir,
        f"models-{species_id}-{round_no}",
        extra=extra,
    )
    models = next(
        (
            entry
            for entry in result.get("results", [])
            if entry.get("check") == "models"
        ),
        {},
    )
    if models.get("status") != "fail":
        return []
    errors = []
    for file, lines in problems_by_file(models.get("problems", [])).items():
        for line in lines:
            text = line[2:].strip() if line.startswith("- ") else line.strip()
            errors.append(f"{file}: {text}" if file else text)
    return errors


def _animal_title(job_dir: Path, species_id: str) -> str:
    path = job_dir / "animals" / f"{species_id}.json"
    if path.is_file():
        animal = json.loads(path.read_text(encoding="utf-8"))
        title = str(animal.get("text", {}).get("title") or "").strip()
        if title:
            return title
    return species_id.replace("_", " ").capitalize()


def _latest_sheet(job_dir: Path, species_id: str, reference: str) -> Path | None:
    if reference == "none":
        return None
    sheets = sorted((job_dir / "references" / species_id).glob("sheet-*.png"))
    if not sheets:
        raise _Stopped(f"{species_id}: no reference sheet; run references first")
    return sheets[-1]


def _review_prompt_name(kind: str, sheet: bool) -> str:
    """The review prompt for this kind, with or without a sheet."""
    base = f"model_review_{kind}"
    return base if sheet else f"{base}_none"


def _bar_preview(ctx: Any, species_id: str, closest_id: str) -> Path:
    """The game's own closest preview, copied into the job as the bar."""
    src = Path(ctx.world_path) / "models" / "previews" / f"{closest_id}.png"
    if not src.is_file():
        raise _Stopped(f"no bar preview for {closest_id}: {src} is missing")
    dest = ctx.job_dir / "references" / species_id / f"closest-{closest_id}.png"
    if not dest.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    return dest


def _closest_title(world: Path, closest_id: str) -> str:
    """The closest animal's name for the review prompt."""
    data = json.loads((world / "models" / f"{closest_id}.json").read_text())
    return str(data.get("name") or closest_id.replace("_", " ").title())


def _blocking(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [issue for issue in issues if issue["severity"] == "blocking"]


def _previous_blocking(
    job_dir: Path, species_id: str, round_no: int
) -> list[dict[str, Any]]:
    """Last round's blocking issues, if that round was reviewed."""
    if round_no < 2:
        return []
    path = job_dir / "reviews" / f"{species_id}-{round_no - 1}.json"
    if not path.is_file():
        return []
    try:
        review = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return [
        issue
        for issue in review.get("issues", [])
        if issue.get("severity") == "blocking"
    ]


def _content(response: Any) -> str:
    return (response.choices[0].message.content or "").strip()


def _unfence(answer: str) -> str:
    """Strict JSON, with or without the fences models love to add."""
    text = answer.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    return match.group(1) if match else text


class _Stopped(Exception):
    """The species has no passing model. The job stops."""
