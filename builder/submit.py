"""`builder submit`: a person sends a finished draft on as a pull request.

An admin runs this on their own computer. It takes the draft's
newest round (the local job's pack and model files; the Storage
download lands with T25), lays it out like animundo-world on a
branch `builder/<draft_id>` with the real place id, runs the
world's draft checker on exactly that content, and opens the pull
request with the admin's own `gh` login. The PR link goes back to
the job's `submit.json` (and to Firestore once T22 exists).

The PR body carries the area, the sources, every check's result,
the new species with their preview sheets and the reviewer's
notes. It never names the family and never carries their notes:
this module only reads pack, model, review and check files.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .report import build_report, report_text
from .world import check_draft


class SubmitFailed(Exception):
    """The draft can't go on as a pull request. A person looks."""


def run_submit(
    draft_id: str,
    jobs_dir: Path,
    world: Path,
    place_id: str | None = None,
    base: str = "main",
    gh: str = "gh",
    cost_usd: float | None = None,
) -> dict[str, Any]:
    """Submit one draft. Returns the submit record (with `pr_url`).

    `cost_usd` is the job's ledger total for the report; without
    it the report falls back to the calls the folder logged.
    """
    job_dir = jobs_dir / draft_id
    manifest = _read_manifest(job_dir)
    if manifest["draft_id"] != draft_id:
        raise SubmitFailed(f"the pack is for {manifest['draft_id']}, not {draft_id}")
    place = place_id or manifest["place_id"]
    branch = f"builder/{draft_id}"
    if _submitted(job_dir):
        raise SubmitFailed(
            f"already submitted: {_submitted(job_dir)}; a new round needs "
            "a new pack first"
        )
    _require_git(world)
    if _branch_exists(world, branch):
        return _continue(world, job_dir, draft_id, branch, base, gh, place)
    _require_clean(world)
    update = _is_update(job_dir)
    region = _read_region(job_dir)
    files = _layout(world, job_dir, manifest, region, place, branch, update)
    gate = _gate(world, job_dir, manifest, region, place, files, branch)
    build_report(job_dir, cost_usd=cost_usd)
    pr_body = _pr_body(job_dir, manifest, region, place, gate, cost_usd)
    pr_path = job_dir / "pr.md"
    pr_path.write_text(pr_body, encoding="utf-8")
    _commit(world, branch, region, place, files, update)
    return _open_pr(world, job_dir, draft_id, branch, base, gh, place, files)


def _read_manifest(job_dir: Path) -> dict[str, Any]:
    path = job_dir / "pack" / "manifest.json"
    if not path.is_file():
        raise SubmitFailed(f"no pack in {job_dir}; the pack stage must pass first")
    return json.loads(path.read_text(encoding="utf-8"))


def _is_update(job_dir: Path) -> bool:
    """Whether the draft reuses a built place instead of making one."""
    brief_path = job_dir / "home.json"
    if not brief_path.is_file():
        return False
    return bool(
        json.loads(brief_path.read_text(encoding="utf-8")).get("reuse_place_id")
    )


def _submitted(job_dir: Path) -> str | None:
    path = job_dir / "submit.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("pr_url")


def _require_git(world: Path) -> None:
    proc = _git(world, "rev-parse", "--is-inside-work-tree")
    if proc.returncode != 0 or proc.stdout.strip() != "true":
        raise SubmitFailed(f"{world} is not a git checkout")


def _branch_exists(world: Path, branch: str) -> bool:
    proc = _git(world, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}")
    return proc.returncode == 0


def _require_clean(world: Path) -> None:
    proc = _git(world, "status", "--porcelain")
    dirty = [line for line in proc.stdout.splitlines() if not line.startswith("??")]
    if dirty:
        raise SubmitFailed(
            "the world checkout has uncommitted changes; commit or stash them first"
        )


def _read_region(job_dir: Path) -> dict[str, Any]:
    regions = sorted((job_dir / "regions").glob("*.json"))
    if len(regions) != 1:
        raise SubmitFailed(f"the job has {len(regions)} region files, submit needs one")
    return json.loads(regions[0].read_text(encoding="utf-8"))


def _new_plants(manifest: dict[str, Any]) -> list[str]:
    return sorted(
        Path(entry["path"]).stem
        for entry in manifest["files"]
        if entry["path"].startswith("models/plants/") and entry["path"].endswith(".glb")
    )


def _layout(
    world: Path,
    job_dir: Path,
    manifest: dict[str, Any],
    region: dict[str, Any],
    place: str,
    branch: str,
    update: bool = False,
) -> list[str]:
    """Lay the round out on a new branch. Returns the touched paths."""
    _git(world, "checkout", "-b", branch, check=True)
    touched: list[str] = []
    try:
        region_path = f"regions/{place}.json"
        if update:
            # The pull request goes against the built region file (#82.3).
            if not (world / region_path).is_file():
                raise SubmitFailed(
                    f"{region_path} is gone from the world; a person looks"
                )
            (world / region_path).write_text(
                json.dumps(dict(region, id=place), indent=2), encoding="utf-8"
            )
        else:
            _write(world, region_path, dict(region, id=place))
        touched.append(region_path)
        for species in manifest["new_species"]:
            for job_rel, world_rel in (
                (f"animals/{species}.json", f"animals/{species}.json"),
                (f"models/{species}.json", f"models/{species}.json"),
            ):
                _copy(job_dir, world, job_rel, world_rel)
                touched.append(world_rel)
            preview = job_dir / "models" / f"{species}.png"
            if preview.is_file():
                _copy(
                    job_dir,
                    world,
                    f"models/{species}.png",
                    f"models/previews/{species}.png",
                )
                touched.append(f"models/previews/{species}.png")
        for plant in _new_plants(manifest):
            for job_rel, world_rel in (
                (
                    f"models/plants/{plant}.json",
                    f"models/plants/{plant}.json",
                ),
            ):
                _copy(job_dir, world, job_rel, world_rel)
                touched.append(world_rel)
            preview = job_dir / "models" / "plants" / f"{plant}.png"
            if preview.is_file():
                _copy(
                    job_dir,
                    world,
                    f"models/plants/{plant}.png",
                    f"models/previews/plants/{plant}.png",
                )
                touched.append(f"models/previews/plants/{plant}.png")
        for source in sorted((job_dir / "locale").rglob("*.json")):
            rel = f"locale/{source.relative_to(job_dir / 'locale')}"
            _merge_locale(world, rel, json.loads(source.read_text()))
            touched.append(rel)
    except Exception:
        _roll_back(world, branch, touched)
        raise
    return touched


def _write(world: Path, rel: str, data: dict[str, Any]) -> None:
    if (world / rel).exists():
        raise SubmitFailed(f"{rel} already exists in the world; a person looks")
    path = world / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _copy(job_dir: Path, world: Path, job_rel: str, world_rel: str) -> None:
    source = job_dir / job_rel
    if not source.is_file():
        raise SubmitFailed(f"the round has no {job_rel}; a person looks")
    if (world / world_rel).exists():
        raise SubmitFailed(f"{world_rel} already exists in the world; a person looks")
    dest = world / world_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)


def _merge_locale(world: Path, rel: str, new_keys: dict[str, str]) -> None:
    """Add the round's keys. Changing a shipped text stops for a person."""
    dest = world / rel
    if dest.is_file():
        shipped = json.loads(dest.read_text(encoding="utf-8"))
        changed = sorted(
            key
            for key, text in new_keys.items()
            if key in shipped and shipped[key] != text
        )
        if changed:
            raise SubmitFailed(
                f"{rel} would change shipped text ({changed[0]}...); a person looks"
            )
        merged = dict(shipped)
        merged.update(new_keys)
        if merged == shipped:
            return
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        merged = new_keys
    dest.write_text(json.dumps(merged, indent=2), encoding="utf-8")


def _roll_back(world: Path, branch: str, touched: list[str]) -> None:
    for rel in touched:
        path = world / rel
        if _git(world, "ls-files", "--error-unmatch", rel).returncode == 0:
            _git(world, "checkout", "--", rel)
        elif path.is_file():
            path.unlink()
    _git(world, "checkout", "-")
    _git(world, "branch", "-D", branch)


def _gate(
    world: Path,
    job_dir: Path,
    manifest: dict[str, Any],
    region: dict[str, Any],
    place: str,
    files: list[str],
    branch: str,
) -> dict[str, Any]:
    """Run the world's draft checker on exactly the branch content.

    The specs come from the branch; the built `.glb` files come from
    the job, since the world builds (and ignores) its own.
    """
    with tempfile.TemporaryDirectory(prefix="submit-") as tmp:
        staged = Path(tmp)
        (staged / "regions").mkdir()
        (staged / "regions" / f"{place}.json").write_text(
            (world / "regions" / f"{place}.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (staged / "animals").mkdir()
        for species in manifest["new_species"]:
            shutil.copy2(
                world / "animals" / f"{species}.json",
                staged / "animals" / f"{species}.json",
            )
        models = staged / "models"
        models.mkdir()
        for species in manifest["new_species"]:
            shutil.copy2(
                world / "models" / f"{species}.json",
                models / f"{species}.json",
            )
            built = job_dir / "models" / f"{species}.glb"
            if built.is_file():
                shutil.copy2(built, models / f"{species}.glb")
        plants = models / "plants"
        if _new_plants(manifest):
            plants.mkdir()
            for plant in _new_plants(manifest):
                shutil.copy2(
                    world / "models" / "plants" / f"{plant}.json",
                    plants / f"{plant}.json",
                )
                built = job_dir / "models" / "plants" / f"{plant}.glb"
                if built.is_file():
                    shutil.copy2(built, plants / f"{plant}.glb")
        locale_src = job_dir / "locale"
        if locale_src.is_dir():
            shutil.copytree(locale_src, staged / "locale")
        try:
            result = check_draft(world, staged, "submit")
        except Exception as error:
            _roll_back(world, branch, files)
            raise SubmitFailed(f"the submit gate did not run: {error}") from error
        dest = job_dir / "checks" / "submit.json"
        dest.parent.mkdir(exist_ok=True)
        dest.write_text(json.dumps(result, indent=2), encoding="utf-8")
    failed = [
        entry for entry in result.get("results", []) if entry.get("status") == "fail"
    ]
    if failed:
        names = ", ".join(entry.get("check", "?") for entry in failed)
        _roll_back(world, branch, files)
        raise SubmitFailed(
            f"the submit gate failed on {names}; the branch is removed, "
            "fix the round and submit again"
        )
    return result


def _commit(
    world: Path,
    branch: str,
    region: dict[str, Any],
    place: str,
    files: list[str],
    update: bool = False,
) -> None:
    _git(world, "add", "--", *files, check=True)
    name = region.get("name", place)
    verb = "Update" if update else "Add"
    proc = _git(
        world,
        "commit",
        "-s",
        "-m",
        f"{verb} {name} ({place}) via builder submit",
    )
    if proc.returncode != 0:
        raise SubmitFailed(
            f"the commit failed ({_short(proc)}); the branch {branch} holds the files"
        )


def _continue(
    world: Path,
    job_dir: Path,
    draft_id: str,
    branch: str,
    base: str,
    gh: str,
    place: str,
) -> dict[str, Any]:
    """Finish a submit whose branch is committed but has no PR yet."""
    _require_clean(world)
    _git(world, "checkout", branch, check=True)
    diff = _git(world, "diff", "--name-only", f"{base}...{branch}")
    files = [line for line in diff.stdout.splitlines() if line.strip()]
    return _open_pr(world, job_dir, draft_id, branch, base, gh, place, files)


def _open_pr(
    world: Path,
    job_dir: Path,
    draft_id: str,
    branch: str,
    base: str,
    gh: str,
    place: str,
    files: list[str],
) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            [
                gh,
                "pr",
                "create",
                "--head",
                branch,
                "--base",
                base,
                "--title",
                _title(job_dir, place),
                "--body-file",
                str(job_dir / "pr.md"),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SubmitFailed(
            f"gh did not run ({error}); the branch {branch} is committed, "
            "run submit again once gh works"
        ) from error
    if proc.returncode != 0:
        raise SubmitFailed(
            f"gh refused the pull request ({_short(proc)}); the branch "
            f"{branch} is committed, fix it and run submit again"
        )
    record = {
        "draft_id": draft_id,
        "place_id": place,
        "branch": branch,
        "base": base,
        "pr_url": proc.stdout.strip(),
        "files": files,
        "at": datetime.now(UTC).isoformat(),
    }
    (job_dir / "submit.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def _title(job_dir: Path, place: str) -> str:
    regions = sorted((job_dir / "regions").glob("*.json"))
    name = place
    if regions:
        name = json.loads(regions[0].read_text(encoding="utf-8")).get("name", place)
    if _is_update(job_dir):
        return f"Update place: {name} ({place})"
    return f"New place: {name} ({place})"


def _pr_body(
    job_dir: Path,
    manifest: dict[str, Any],
    region: dict[str, Any],
    place: str,
    gate: dict[str, Any],
    cost_usd: float | None = None,
) -> str:
    name = region.get("name", place)
    country = region.get("country", "")
    area = f"{name} ({manifest['place_id']})"
    if country and not name.endswith(country):
        area += f", {country}"
    rounds = manifest.get("version", 1) - 1
    if rounds <= 0:
        submitted = "played and submitted without asking for changes"
    elif rounds == 1:
        submitted = "submitted after 1 improvement round"
    else:
        submitted = f"submitted after {rounds} improvement rounds"
    update = _is_update(job_dir)
    heading = "Update place" if update else "New place"
    if update:
        made = f"Updated for a family in {area}."
    else:
        made = f"Built for a family for {area}."
    lines = [
        f"# {heading}: {name} ({place})",
        "",
        made,
        "",
        "Made by Animundo Builder, played by the family that asked for "
        f"it, {submitted}, and sent on by a person.",
        "",
        "## Sources",
        "",
        *["- " + source for source in _sources(job_dir, manifest, region)],
        "",
        "## Checks",
        "",
        *[
            "- {}: {}".format(
                entry.get("check", "?"),
                entry.get("status", "?")
                + (
                    " (" + "; ".join(entry.get("problems", [])[:3]) + ")"
                    if entry.get("problems")
                    else ""
                ),
            )
            for entry in gate.get("results", [])
        ],
        "",
        "## New species",
        "",
        *[
            f"- {species} (`animals/{species}.json`, "
            f"`models/{species}.json`)"
            + (
                f"\n\n  ![{species} preview](models/previews/{species}.png)"
                if (job_dir / "models" / f"{species}.png").is_file()
                else ""
            )
            for species in manifest["new_species"]
        ],
        *[
            f"- {plant} (`models/plants/{plant}.json`)"
            + (
                f"\n\n  ![{plant} preview](models/previews/plants/{plant}.png)"
                if (job_dir / "models" / "plants" / f"{plant}.png").is_file()
                else ""
            )
            for plant in _new_plants(manifest)
        ],
        *(
            ["- Nothing new: this place reuses the library."]
            if not manifest["new_species"] and not _new_plants(manifest)
            else []
        ),
        "",
        "## Build report",
        "",
        *report_text(job_dir, cost_usd=cost_usd).splitlines(),
        "",
        "## Reviewer's notes",
        "",
        *_review_notes(job_dir, manifest),
        "",
        "## For an expert",
        "",
        *_expert_flags(job_dir),
    ]
    return "\n".join(lines) + "\n"


def _sources(
    job_dir: Path, manifest: dict[str, Any], region: dict[str, Any]
) -> list[str]:
    found: list[str] = []
    found.extend(region.get("sources", []))
    for species in manifest["new_species"]:
        path = job_dir / "animals" / f"{species}.json"
        if path.is_file():
            found.extend(json.loads(path.read_text()).get("sources", []))
    for plant in _new_plants(manifest):
        path = job_dir / "models" / "plants" / f"{plant}.json"
        if path.is_file():
            found.extend(json.loads(path.read_text()).get("sources", []))
    return list(dict.fromkeys(found)) or ["(no sources recorded)"]


def _final_review(reviews: Path, species: str) -> dict[str, Any] | None:
    """The highest round's verified review, if any notes were kept."""
    candidates: list[tuple[int, bool, Path]] = []
    if reviews.is_dir():
        for path in reviews.glob(f"{species}-*.json"):
            rest = path.stem[len(species) + 1 :]
            num, _, tag = rest.partition("-")
            if num.isdigit():
                candidates.append((int(num), tag != "unverified", path))
    if not candidates:
        return None
    candidates.sort()
    return json.loads(candidates[-1][2].read_text(encoding="utf-8"))


def _review_notes(job_dir: Path, manifest: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    reviews = job_dir / "reviews"
    for species in manifest["new_species"]:
        final = _final_review(reviews, species)
        if final is None:
            lines.append(f"- {species}: no review notes kept.")
            continue
        issues = final.get("issues", [])
        if not issues:
            lines.append(
                f"- {species}: the final review (round {final.get('round')}) "
                "found nothing."
            )
        for issue in issues:
            lines.append(
                "- {} ({}): {} {}".format(
                    species,
                    issue.get("severity", "note"),
                    issue.get("problem", ""),
                    f"Fix: {issue['fix']}" if issue.get("fix") else "",
                ).rstrip()
            )
    approved = job_dir / "models" / "APPROVED.txt"
    if approved.is_file():
        lines.append("")
        lines.append("A person approved the models:")
        lines.append("")
        lines.append(approved.read_text(encoding="utf-8").strip())
    safety = job_dir / "checks" / "safety.json"
    if safety.is_file():
        record = json.loads(safety.read_text(encoding="utf-8"))
        rewritten = record.get("rewritten", [])
        if rewritten:
            lines.append("")
            lines.append(
                f"Child-safety review rewrote {len(rewritten)} texts, "
                "which then passed."
            )
    return lines or ["- No review notes kept."]


def _expert_flags(job_dir: Path) -> list[str]:
    flagged: list[str] = []
    for rel in ("animals/progress.json", "plants/progress.json"):
        path = job_dir / rel
        if path.is_file():
            flagged.extend(json.loads(path.read_text()).get("expert", []))
    if not flagged:
        return ["Nothing is waiting for an expert."]
    return [f"- {name}: needs an expert look." for name in sorted(set(flagged))]


def _git(world: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=world,
        timeout=120,
    )
    if check and proc.returncode != 0:
        raise SubmitFailed(f"git {' '.join(args)} failed: {_short(proc)}")
    return proc


def _short(proc: subprocess.CompletedProcess) -> str:
    text = (proc.stderr or proc.stdout or "").strip().splitlines()
    return text[-1][:200] if text else f"exit {proc.returncode}"
