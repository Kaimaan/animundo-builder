"""The build report (T38): one self-contained `report.html` per round.

Renders what a job folder holds -- sources, checks, preview sheets
before and after, reviewer notes, and the model, time and cost of
every call -- into a single file that opens offline. Images go in as
data URIs; nothing is fetched. Every job-derived string is escaped,
and only the known artefact set is read, so a family's request text
(`improve.note`) can never reach the report.
"""

from __future__ import annotations

import base64
import html
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Config


def build_report(job_dir: Path) -> Path:
    """Write `report.html` into the job folder and return its path."""
    state = _read_json(job_dir / "state.json")
    survey = _read_json(job_dir / "survey.json")
    plan = _read_json(job_dir / "species-plan.json")
    calls = _read_calls(job_dir / "log.jsonl")
    params = state.get("params", {})
    species_ids = _new_species(plan, job_dir)
    parts = [
        _head(params, job_dir.name),
        _stages(state),
        _cost(calls),
        _sources(survey),
        _checks(job_dir),
        *(_species(job_dir, species_id) for species_id in species_ids),
        _foot(),
    ]
    out = job_dir / "report.html"
    out.write_text(
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        f"<title>{html.escape('Build report: ' + job_dir.name)}</title>\n"
        "<style>"
        + _CSS
        + "</style>\n</head>\n<body>\n"
        + "\n".join(parts)
        + "\n</body>\n</html>\n",
        encoding="utf-8",
    )
    return out


_CSS = (
    "body{font-family:sans-serif;max-width:60em;margin:2em auto;padding:0 1em;"
    "line-height:1.45}table{border-collapse:collapse;margin:1em 0}"
    "th,td{border:1px solid #bbb;padding:.25em .6em;text-align:left}"
    "img{max-width:100%;height:auto;border:1px solid #bbb;margin:.5em 0}"
    ".blocking{color:#a00;font-weight:bold}pre{background:#f4f4f4;padding:.5em;"
    "overflow-x:auto}.muted{color:#555}"
)


def _head(params: dict[str, Any], job_id: str) -> str:
    area = params.get("area") or params.get("name") or "(unknown area)"
    rows = [
        ("Job", job_id),
        ("Area", str(area)),
        ("Kind", str(params.get("kind", "new_place"))),
        ("Author", str(params.get("author", "Animundo Builder"))),
    ]
    cells = "".join(
        f"<tr><th>{html.escape(key)}</th><td>{html.escape(value)}</td></tr>"
        for key, value in rows
    )
    return f"<h1>Build report: {html.escape(job_id)}</h1>\n<table>{cells}</table>"


def _stages(state: dict[str, Any]) -> str:
    done = state.get("completed", [])
    failed = state.get("failed")
    items = "".join(f"<li>{html.escape(str(name))}</li>" for name in done)
    verdict = "finished every built stage" if not failed else "stopped"
    text = f"<h2>Stages</h2>\n<p>Done: {html.escape(verdict)}.</p>\n<ul>{items}</ul>"
    if failed:
        text += f"<p><strong>Failed:</strong> {html.escape(str(failed))}</p>"
    return text


def _cost(calls: list[dict[str, Any]]) -> str:
    total = sum(call.get("cost_usd", 0.0) or 0.0 for call in calls)
    duration = sum(call.get("duration_s", 0.0) or 0.0 for call in calls)
    by_model: dict[tuple[str, str], dict[str, float]] = {}
    for call in calls:
        key = (str(call.get("call", "?")), str(call.get("model", "?")))
        row = by_model.setdefault(
            key,
            {"calls": 0.0, "in": 0.0, "out": 0.0, "other": 0.0, "s": 0.0, "usd": 0.0},
        )
        row["calls"] += 1
        row["in"] += call.get("input_tokens", 0) or 0
        row["out"] += call.get("output_tokens", 0) or 0
        row["other"] += (
            (call.get("searches", 0) or 0)
            + (call.get("images", 0) or 0)
            + (call.get("generated", 0) or 0)
        )
        row["s"] += call.get("duration_s", 0.0) or 0.0
        row["usd"] += call.get("cost_usd", 0.0) or 0.0
    lines = [
        "<tr><td>{}</td><td>{}</td><td>{:.0f}</td><td>{:.0f}</td>"
        "<td>{:.0f}</td><td>{:.0f}</td><td>{:.1f}</td><td>{:.4f}</td></tr>".format(
            html.escape(call),
            html.escape(model),
            row["calls"],
            row["in"],
            row["out"],
            row["other"],
            row["s"],
            row["usd"],
        )
        for (call, model), row in sorted(by_model.items())
    ]
    budget = Config.job_budget_usd
    return (
        "<h2>Cost</h2>\n"
        f"<p>Total <strong>${total:.4f}</strong> over {len(calls)} calls "
        f"and {duration:.0f} seconds of model time. "
        f"The default job budget is ${budget:.2f}.</p>\n"
        "<table><tr><th>Call</th><th>Model</th><th>Calls</th>"
        "<th>Tokens in</th><th>Tokens out</th><th>Searches/images</th>"
        "<th>Seconds</th><th>USD</th></tr>\n" + "\n".join(lines) + "</table>"
    )


def _sources(survey: dict[str, Any]) -> str:
    if not survey:
        return '<h2>Sources</h2>\n<p class="muted">No survey yet.</p>'
    facts = "".join(
        '<li><strong>{}</strong>: {} <span class="muted">({})</span></li>'.format(
            html.escape(str(fact.get("topic", "?"))),
            html.escape(str(fact.get("text", ""))),
            html.escape(", ".join(fact.get("sources", []))),
        )
        for fact in survey.get("facts", [])
    )
    species = "".join(_survey_species(entry) for entry in survey.get("species", []))
    return (
        "<h2>Sources</h2>\n"
        f"<p>Every fact below carries the source that states it.</p>\n"
        f"<ul>{facts}</ul>\n<ul>{species}</ul>"
    )


def _survey_species(entry: dict[str, Any]) -> str:
    measured = "".join(
        "<li>{}: {} ({})</li>".format(
            html.escape(name),
            html.escape(str(found.get("value", "?"))),
            html.escape(str(found.get("source", "?"))),
        )
        for name, found in (entry.get("measurements") or {}).items()
        if isinstance(found, dict)
    )
    looks = ""
    if entry.get("description"):
        looks = " Looks: {}.".format(html.escape(str(entry.get("description"))))
    return (
        "<li><strong>{} ({})</strong>, {}: {}{} "
        '<span class="muted">({})</span><ul>{}</ul></li>'.format(
            html.escape(str(entry.get("species", "?"))),
            html.escape(str(entry.get("latin", "?"))),
            html.escape(str(entry.get("role", "?"))),
            html.escape(str(entry.get("evidence", ""))),
            looks,
            html.escape(", ".join(entry.get("sources", []))),
            measured,
        )
    )


def _checks(job_dir: Path) -> str:
    files = sorted((job_dir / "checks").glob("*.json"))
    if not files:
        return '<h2>Checks</h2>\n<p class="muted">No draft checks recorded yet.</p>'
    sections = []
    for path in files:
        data = _read_json(path)
        rows = []
        for entry in data.get("results", []):
            found = "".join(
                f"<li>{html.escape(str(item))}</li>"
                for item in entry.get("problems", [])
            )
            rows.append(
                "<tr><td>{}</td><td>{}</td><td>{}</td><td><ul>{}</ul></td></tr>".format(
                    html.escape(str(entry.get("check", "?"))),
                    html.escape(str(entry.get("status", "?"))),
                    html.escape(str(entry.get("reason", ""))),
                    found,
                )
            )
        verdict = "passed" if data.get("ok") else "failing"
        sections.append(
            f"<h3>{html.escape(path.stem)} ({verdict})</h3>\n"
            + "<table><tr><th>Check</th><th>Status</th><th>Skipped"
            + " why</th><th>Problems</th></tr>\n"
            + "\n".join(rows)
            + "</table>"
        )
    return "<h2>Checks</h2>\n" + "\n".join(sections)


def _species(job_dir: Path, species_id: str) -> str:
    measured_file = job_dir / "measurements" / f"{species_id}.json"
    measured = _read_json(measured_file)
    refs = _references(job_dir, species_id, measured)
    rounds = _rounds(job_dir, species_id)
    final = _final(job_dir, species_id)
    return f"<h2>{html.escape(species_id)}</h2>\n" + refs + rounds + final


def _references(job_dir: Path, species_id: str, measured: dict[str, Any]) -> str:
    if not measured:
        return '<p class="muted">No measurements yet.</p>'
    sourced = "".join(
        f"<li>{html.escape(name)}: {html.escape(str(value))}</li>"
        for name, value in measured.get("sourced", {}).items()
    )
    comparison = "".join(
        "<li>{}: sourced {:.3f}, measured {:.3f} ({})</li>".format(
            html.escape(str(row.get("proportion", "?"))),
            row.get("sourced", 0.0) or 0.0,
            row.get("measured", 0.0) or 0.0,
            "within 15%" if row.get("within_15_percent") else "off",
        )
        for row in measured.get("comparison", [])
    )
    mode = measured.get("reference", "?")
    redrawn = "drawn again once" if measured.get("redrawn") else "first sheet kept"
    sheets = "".join(
        _figure(path, f"{species_id} reference {path.stem}")
        for path in sorted((job_dir / "references" / species_id).glob("sheet-*.png"))
    )
    masks = "".join(
        _figure(path, f"{species_id} mask {path.stem}")
        for path in sorted((job_dir / "references" / species_id).glob("mask-*.png"))
    )
    return (
        f"<h3>Reference ({html.escape(str(mode))}, {redrawn})</h3>\n"
        f"<p>Size always comes from the sourced numbers:</p>\n<ul>{sourced}</ul>\n"
        + (f"<ul>{comparison}</ul>\n" if comparison else "")
        + sheets
        + masks
    )


def _rounds(job_dir: Path, species_id: str) -> str:
    folders = sorted(
        (job_dir / "rounds" / species_id).glob("*"),
        key=lambda path: int(path.name) if path.name.isdigit() else 0,
    )
    if not folders:
        return '<p class="muted">No model rounds yet.</p>'
    return "<h3>Rounds</h3>\n" + "".join(
        _round(job_dir, species_id, folder) for folder in folders
    )


def _round(job_dir: Path, species_id: str, folder: Path) -> str:
    spec = _read_json(folder / "spec.json")
    log = (
        (folder / "build.log").read_text(encoding="utf-8")
        if (folder / "build.log").is_file()
        else ""
    )
    built = next(
        (line for line in log.splitlines() if line.startswith("BUILT")),
        "the build failed" if log else "no build log",
    )
    review = _read_json(job_dir / "reviews" / f"{species_id}-{folder.name}.json")
    issues = "".join(
        '<li><span class="{}">[{}]</span> <strong>{}</strong>: {} '
        "(fix: {})</li>".format(
            "blocking" if issue.get("severity") == "blocking" else "muted",
            html.escape(str(issue.get("severity", "?"))),
            html.escape(str(issue.get("part", "?"))),
            html.escape(str(issue.get("problem", ""))),
            html.escape(str(issue.get("fix", ""))),
        )
        for issue in review.get("issues", [])
    )
    verdict = (
        "<p>No issues: the reviewer passed this round.</p>"
        if review and not review.get("issues")
        else (f"<ul>{issues}</ul>" if issues else "")
    )
    return (
        f"<h4>Round {html.escape(folder.name)}</h4>\n"
        f"<p>Spec <code>{html.escape(str(spec.get('id', species_id)))}</code>: "
        f"{html.escape(_spec_summary(spec))}. Build: {html.escape(built)}</p>\n"
        + _figure(folder / "preview.png", f"{species_id} round {folder.name}")
        + verdict
    )


def _spec_summary(spec: dict[str, Any]) -> str:
    bits = []
    for key, value in spec.items():
        if key == "id":
            continue
        if isinstance(value, list):
            bits.append(f"{len(value)} {key}")
        elif isinstance(value, dict):
            bits.append(f"{key} with {len(value)} entries")
        else:
            bits.append(f"{key} {value}")
    return ", ".join(bits[:6]) if bits else "empty spec"


def _final(job_dir: Path, species_id: str) -> str:
    files = sorted((job_dir / "models").glob(f"{species_id}.*"))
    if not files:
        return ""
    items = "".join(
        f"<li>{html.escape(path.name)} ({path.stat().st_size} bytes)</li>"
        for path in files
    )
    preview = job_dir / "models" / f"{species_id}.png"
    bar = ""
    bars = sorted((job_dir / "references" / species_id).glob("closest-*.png"))
    if bars:
        bar = _figure(
            bars[0],
            "the bar: the game's own "
            + bars[0].stem.removeprefix("closest-").replace("_", " "),
        )
    return (
        "<h3>Final model</h3>\n"
        + bar
        + _figure(preview, f"{species_id} final preview")
        + f"<ul>{items}</ul>"
    )


def _foot() -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f'<hr>\n<p class="muted">Written {html.escape(now)} from the job folder '
        "only. This file makes no network requests and holds no personal data.</p>"
    )


def _figure(path: Path, caption: str) -> str:
    if not path.is_file():
        return ""
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return (
        f'<figure><img alt="{html.escape(caption)}" '
        f'src="data:image/png;base64,{encoded}">'
        f"<figcaption>{html.escape(caption)}</figcaption></figure>"
    )


def _new_species(plan: dict[str, Any], job_dir: Path) -> list[str]:
    """New species from the plan, or whatever has measurements."""
    ids = [
        str(entry.get("species", ""))
        for entry in plan.get("species", [])
        if entry.get("decision") == "new"
    ]
    if ids:
        return ids
    return sorted(path.stem for path in (job_dir / "measurements").glob("*.json"))


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _read_calls(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    calls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            calls.append(data)
    return calls
