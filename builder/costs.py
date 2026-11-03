"""What a job really cost, measured for later estimates (#259.2).

When a job ends, the worker writes `cost` on its doc: the quote it
ran under, the ledger total, and the spend split into parts. The
same record goes to the `cost_log` collection, where later quotes
read their medians from (#259.3, #259.4). Nothing here is personal:
job ids, species, tiles and dollars only.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


def _lines(job_dir: Path) -> list[dict[str, Any]]:
    """Every usage line: the job's log plus each item's (#259.2)."""
    import json

    found: list[dict[str, Any]] = []
    paths = [job_dir / "log.jsonl", *sorted(job_dir.glob("*/log.jsonl"))]
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for raw in text.splitlines():
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            if isinstance(entry, dict):
                found.append(entry)
    return found


def _blank_part() -> dict[str, Any]:
    return {"usd": 0.0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "models": []}


def _add(line: dict[str, Any], part: dict[str, Any], usd: float) -> None:
    part["usd"] = round(part["usd"] + usd, 6)
    part["calls"] += 1
    for key in ("input_tokens", "output_tokens"):
        value = line.get(key, 0)
        part[key] += value if isinstance(value, int) else 0
    model = line.get("model")
    if isinstance(model, str) and model and model not in part["models"]:
        part["models"].append(model)


def _split_search_images(
    line: dict[str, Any], prices_for: Any
) -> tuple[float, float, float]:
    """A line's dollars: tokens, search calls, image calls (#259.5).

    Unknown models keep the whole line on the semantic part.
    """
    usd = line.get("cost_usd", 0.0)
    usd = usd if isinstance(usd, (int, float)) else 0.0
    model = line.get("model")
    if not isinstance(model, str) or not model:
        return usd, 0.0, 0.0
    try:
        prices = prices_for(model)
    except Exception:  # noqa: BLE001 - unknown model, no split
        return usd, 0.0, 0.0
    searches = line.get("searches", 0)
    searches = searches if isinstance(searches, int) else 0
    images = line.get("images", 0)
    images = images if isinstance(images, int) else 0
    generated = line.get("generated", 0)
    generated = generated if isinstance(generated, int) else 0
    search_usd = searches * prices.search_per_thousand_queries / 1000
    image_usd = (
        images * prices.sam_image_per_thousand / 1000
        + generated * prices.image_per_thousand / 1000
    )
    return max(0.0, usd - search_usd - image_usd), search_usd, image_usd


def rollup_parts(job_dir: Path) -> dict[str, Any]:
    """The job's spend by part, from its usage lines (#259.2).

    Item lines carry `kind:what` tags; research, review and tile
    surveys tag themselves; failed attempts count as retries.
    Untagged lines land in `other`. Search and image dollars get
    their own parts (#259.5); the semantic parts keep the tokens.
    """
    from .prices import prices_for

    parts: dict[str, Any] = {
        "research": _blank_part(),
        "tiles": _blank_part(),
        "review": _blank_part(),
        "retries": _blank_part(),
        "search": _blank_part(),
        "images": _blank_part(),
        "animals": {},
        "plants": {},
        "homes": {},
        "other": _blank_part(),
    }
    for line in _lines(job_dir):
        token_usd, search_usd, image_usd = _split_search_images(line, prices_for)
        bare = {**line, "input_tokens": 0, "output_tokens": 0}
        if search_usd > 0:
            _add(bare, parts["search"], search_usd)
        if image_usd > 0:
            _add(bare, parts["images"], image_usd)
        bucket: dict[str, Any]
        if line.get("result") != "ok":
            bucket = parts["retries"]
        else:
            tag = line.get("part")
            if not isinstance(tag, str) or not tag:
                bucket = parts["other"]
            elif ":" in tag:
                kind, what = tag.split(":", 1)
                homes = parts["homes"]
                if kind == "animal":
                    bucket = parts["animals"].setdefault(what, _blank_part())
                elif kind == "plant":
                    bucket = parts["plants"].setdefault(what, _blank_part())
                elif kind == "home":
                    bucket = homes.setdefault(what, _blank_part())
                else:
                    bucket = parts["other"]
            elif tag in parts and isinstance(parts[tag], dict):
                bucket = parts[tag]
            else:
                bucket = parts["other"]
        _add(line, bucket, token_usd)
    for key in ("research", "tiles", "review", "retries", "search", "images", "other"):
        parts[key]["models"] = sorted(parts[key]["models"])
    for group in ("animals", "plants", "homes"):
        for entry in parts[group].values():
            entry["models"] = sorted(entry["models"])
    return parts


def ledger_actual(job_id: str, ledger_path: str, baseten_ledger_path: str) -> float:
    """The job's ledger total across both pools (#259.2).

    The job's own namespace plus its items' (`job/item`); the
    survey namespaces stay out (shared across jobs — the job's
    own survey marker covers this run's surveys). Unreadable
    ledgers read as zero: costing never breaks a publish.
    """
    from .ledger import FileLedger, LedgerError

    total = 0.0
    for path in (ledger_path, baseten_ledger_path):
        try:
            ledger = FileLedger(path)
            total += ledger.job_spent(job_id)
            for namespace in ledger.namespaces(f"{job_id}/"):
                total += ledger.job_spent(namespace)
        except (LedgerError, OSError, ValueError):
            continue
    return round(total, 6)


def job_estimate(job: dict[str, Any]) -> dict[str, Any]:
    """The quote the job ran under (#259.2).

    A place ran under its research price parts; anything else
    under the best number on the doc, named.
    """
    research = job.get("research") or {}
    quote = research.get("price_parts")
    if isinstance(quote, dict) and quote:
        total = quote.get("total", 0.0)
        total = total if isinstance(total, (int, float)) else 0.0
        return {"total": total, "parts": quote, "from": "research.price_parts"}
    for key in ("price", "estimate_usd", "budget_usd"):
        value = job.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return {"total": value, "parts": {}, "from": f"job.{key}"}
    return {"total": 0.0, "parts": {}, "from": "none"}


def median_estimates(store: Any) -> dict[str, tuple[float, str]]:
    """Per-part estimates from measured medians (#259.4).

    Each part medians its matching `cost_log` rows once 3 have
    it, else today's fixed number. The source rides along so
    the quote says which it used. The per-tile survey median
    reads single-tile rows only, so cached tiles can't dilute
    it; the place tile price shares that source.
    """
    import statistics

    from .prices import (
        ITEM_ESTIMATES,
        PLACE_BUILD_USD_PER_TILE,
        SURVEY_ESTIMATE_USD,
    )

    try:
        rows = [record for _, record in store.list_cost_log()]
    except Exception:  # noqa: BLE001 - unreadable log reads fixed
        rows = []
    out: dict[str, tuple[float, str]] = {}
    for part, fixed in (
        ("animal", ITEM_ESTIMATES["animal"]),
        ("plant", ITEM_ESTIMATES["plant"]),
        ("home", ITEM_ESTIMATES["home"]),
    ):
        group = part + "s"
        samples = [
            entry["usd"]
            for record in rows
            if isinstance(record, dict)
            for entry in (
                ((record.get("cost") or {}).get("parts") or {}).get(group, {})
            ).values()
            if isinstance(entry, dict) and isinstance(entry.get("usd"), (int, float))
        ]
        hit_rows = sum(
            1
            for record in rows
            if isinstance(record, dict)
            and ((record.get("cost") or {}).get("parts") or {}).get(group)
        )
        if hit_rows >= 3 and samples:
            out[part] = (round(statistics.median(samples), 4), f"log, {hit_rows} rows")
        else:
            out[part] = (fixed, "fixed")
    singles = [
        ((record.get("cost") or {}).get("parts") or {}).get("tiles", {}).get("usd", 0.0)
        for record in rows
        if isinstance(record, dict)
        and len(record.get("tiles") or []) == 1
        and ((record.get("cost") or {}).get("parts") or {})
        .get("tiles", {})
        .get("usd", 0.0)
        > 0
    ]
    if len(singles) >= 3:
        value = round(statistics.median(singles), 4)
        source = f"log, {len(singles)} rows"
        out["survey"] = (value, source)
        out["tile"] = (value, source)
    else:
        out["survey"] = (SURVEY_ESTIMATE_USD, "fixed")
        out["tile"] = (PLACE_BUILD_USD_PER_TILE, "fixed")
    return out


def wall_seconds(job: dict[str, Any], ended_at: str) -> float | None:
    """Seconds the job ran on the worker, or nothing unstamped."""
    started = job.get("started_at")
    if not isinstance(started, str) or not started:
        return None
    try:
        seconds = (
            datetime.fromisoformat(ended_at) - datetime.fromisoformat(started)
        ).total_seconds()
    except ValueError:
        return None
    return round(max(0.0, seconds), 3)


def rollup_cost(
    job_id: str,
    job: dict[str, Any],
    jobs_dir: Path,
    ledger_path: str,
    baseten_ledger_path: str,
    ended_at: str,
) -> dict[str, Any]:
    """`cost` for a job that just ended (#259.2)."""
    parts = rollup_parts(jobs_dir / job_id)
    survey = job.get("survey_usd", 0.0)
    survey = survey if isinstance(survey, (int, float)) else 0.0
    actual = round(
        ledger_actual(job_id, ledger_path, baseten_ledger_path) + survey,
        6,
    )
    if survey > 0:
        parts["tiles"]["usd"] = round(parts["tiles"]["usd"] + survey, 6)
    return {
        "estimate": job_estimate(job),
        "actual": actual,
        "parts": parts,
        "wall_s": wall_seconds(job, ended_at),
    }
