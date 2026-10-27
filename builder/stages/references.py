"""Reference sheets and measurements for the model loop (stage 7, steps 1-3).

For every new species: Muse Image draws a side and front view from the
survey's description, SAM 3.1 finds the animal in each view, and Python
measures proportions and main colours from the masks into
`measurements/<id>.json`, next to the survey's sourced numbers. Size
always comes from the sourced numbers, never from the sheet.

A sheet whose shoulder height misses the sourced number by more than
15%, or whose tail is off by more than double, is drawn again once;
then the sourced numbers win either way. Tails are small and read
noisily, so only a grossly wrong tail trips the redraw. With
`reference="none"` there is no sheet and no SAM: the measurements file
holds the sourced numbers only, and the model loop works from those.

Proportions are rough by nature: a silhouette gives exact length and
height, while torso depth, legs, head, and tail come from
projection-profile heuristics (torso rows are wide, legs hang below,
the head end holds the above-torso mass). Shoulder height is legs
plus torso depth, so antlers and ears never inflate it. A tail reads
as the small rear mass above the back plus any narrow dangling run
that ends above the feet; legs reach the feet, tails do not. A tail
drawn touching the leg all the way down is invisible to this, and
reads as missing. They only need to be sensible: the model reviewer
corrects the built shape against the sheet.

Image and SAM spend goes through the shared ledger (reserve and settle
inside the client), like every other model call. Sheets and masks stay
in the job folder; they are never committed to animundo-world.
"""

from __future__ import annotations

import base64
import io
import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import numpy
from PIL import Image

from ..prompts import load_prompt
from ..sam import SamFormatError, decode_mask, parse_line
from ..schemas import validate

MIN_CONFIDENCE = 0.5
MIN_COVERAGE = 0.05
MAX_COVERAGE = 0.95
K_COLOURS = 3
TOLERANCE = 0.15
SEASON_ORDER = ("spring", "summer", "autumn", "winter")

DrawFn = Callable[[Any, str], bytes]
SegmentFn = Callable[[Any, bytes, str], list[dict[str, Any]]]


class ReferencesStage:
    """Write `measurements/<id>.json` for every new species."""

    name = "references"

    def __init__(
        self,
        reference: str = "sheet",
        draw: DrawFn | None = None,
        segment: SegmentFn | None = None,
        today: Callable[[], date] | None = None,
    ) -> None:
        if reference not in ("sheet", "none"):
            raise ValueError("reference must be 'sheet' or 'none'")
        self.reference = reference
        self._draw = draw or (lambda ctx, prompt: ctx.draw(prompt))
        self._segment = segment or segment_sheet
        self._today = today or date.today

    def run(self, ctx: Any) -> dict[str, Any]:
        plan = json.loads((ctx.job_dir / "species-plan.json").read_text())
        new_ids = [
            entry["species"] for entry in plan["species"] if entry["decision"] == "new"
        ]
        survey = json.loads((ctx.job_dir / "survey.json").read_text())
        surveyed = {entry["species"]: entry for entry in survey["species"]}
        region = json.loads(next((ctx.job_dir / "regions").glob("*.json")).read_text())
        season = current_season(region.get("seasons", {}), self._today())
        files = []
        for species_id in new_ids:
            entry = surveyed.get(species_id)
            if entry is None or not entry.get("measurements"):
                raise _Stopped(
                    f"{species_id}: the survey has no measurements; re-run it"
                )
            sourced = {
                name: measured["value"]
                for name, measured in entry["measurements"].items()
            }
            if self.reference == "none":
                files.append(
                    self._write_measurements(ctx, species_id, sourced, None, [], False)
                )
                continue
            files.append(self._measure_species(ctx, species_id, entry, sourced, season))
        return {"measurement_files": sorted(files)}

    def _measure_species(
        self,
        ctx: Any,
        species_id: str,
        entry: dict[str, Any],
        sourced: dict[str, float],
        season: str,
    ) -> str:
        refs_dir = ctx.job_dir / "references" / species_id
        refs_dir.mkdir(parents=True, exist_ok=True)
        prompt = load_prompt(
            "reference_sheet",
            {"DESCRIPTION": entry["description"], "SEASON": season},
        )
        concept = _concept(ctx.job_dir, species_id)
        measured, comparison = self._draw_and_measure(
            ctx, refs_dir, concept, sourced, prompt, 1
        )
        redrawn = False
        if _contradicts(comparison):
            second = prompt + _redraw_hint(comparison)
            measured, comparison = self._draw_and_measure(
                ctx, refs_dir, concept, sourced, second, 2
            )
            redrawn = True
        return self._write_measurements(
            ctx, species_id, sourced, measured, comparison, redrawn
        )

    def _draw_and_measure(
        self,
        ctx: Any,
        refs_dir: Path,
        concept: str,
        sourced: dict[str, float],
        prompt: str,
        attempt: int,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        data = self._draw(ctx, prompt)
        (refs_dir / f"prompt-{attempt}.md").write_text(prompt, encoding="utf-8")
        sheet = refs_dir / f"sheet-{attempt}.png"
        sheet.write_bytes(data)
        kept = _keep_masks(ctx.client, self._segment, data, concept)
        if not kept:
            raise _Stopped(f"{concept}: SAM found no animal on the sheet")
        for index, item in enumerate(kept):
            kind = "side" if index == 0 else "front"
            _write_mask(refs_dir, item, kind, attempt)
        side = kept[0]
        height, width = side["record"]["mask"]["h"], side["record"]["mask"]["w"]
        grid = numpy.frombuffer(side["mask"], dtype=numpy.uint8).reshape(height, width)
        values = _silhouette(grid)
        names = ("body_height", "torso_depth", "leg_length", "head_size", "tail_length")
        proportions = {"body_length": 1.0}
        proportions.update({name: float(value) for name, value in zip(names, values)})
        if proportions["torso_depth"] <= 0:
            raise _Stopped(f"{concept}: the masks show no measurable body")
        if proportions["head_size"] <= 0:
            raise _Stopped(f"{concept}: the masks show no measurable head")
        measured = {"proportions": proportions, "colours": _colours(kept, data)}
        return measured, _compare(sourced, proportions)

    def _write_measurements(
        self,
        ctx: Any,
        species_id: str,
        sourced: dict[str, float],
        measured: dict[str, Any] | None,
        comparison: list[dict[str, Any]],
        redrawn: bool,
    ) -> str:
        measurements = {
            "species": species_id,
            "reference": self.reference,
            "sourced": sourced,
            "measured": measured,
            "comparison": comparison,
            "redrawn": redrawn,
        }
        validate("measurements", measurements)
        out = ctx.job_dir / "measurements" / f"{species_id}.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(measurements, indent=2), encoding="utf-8")
        return f"measurements/{species_id}.json"


def current_season(seasons: dict[str, Any], today: date) -> str:
    """The season whose start date has most recently passed."""

    def started(name: str) -> tuple[int, int]:
        month, day = seasons.get(name, "01-01").split("-")[:2]
        return int(month), int(day)

    now = (today.month, today.day)
    passed = [name for name in SEASON_ORDER if started(name) <= now]
    return passed[-1] if passed else "winter"


def segment_sheet(client: Any, data: bytes, concept: str) -> list[dict[str, Any]]:
    """SAM 3.1 records for the concept: one-bit masks with confidence."""
    data_url = f"data:image/png;base64,{base64.b64encode(data).decode()}"
    text = ""
    for event in client.for_sam().responses_stream(
        [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": concept},
                    {"type": "input_image", "image_url": data_url},
                ],
            }
        ],
        images=1,
        metadata={"mask_encoding": "one_bit", "include_confidence": "true"},
    ):
        if getattr(event, "type", "") == "response.output_text.delta":
            text += event.delta
    return parse_line(text)


def _concept(job_dir: Path, species_id: str) -> str:
    """What SAM looks for: the animal's title, or its id in words."""
    path = job_dir / "animals" / f"{species_id}.json"
    if path.is_file():
        animal = json.loads(path.read_text(encoding="utf-8"))
        title = str(animal.get("text", {}).get("title") or "").strip().lower()
        if title:
            return title
    return species_id.replace("_", " ")


def _concepts(title: str) -> list[str]:
    """Full title first, then simpler words: SAM grounds some better."""
    words = title.split()
    chain = [title]
    if len(words) > 1:
        chain.append(words[-1])
    if "animal" not in chain:
        chain.append("animal")
    return chain


def _keep_masks(
    client: Any,
    segment: SegmentFn,
    data: bytes,
    concept: str,
) -> list[dict[str, Any]]:
    """Up to two confident masks, side view first.

    Tries the full title, then its last word, then "animal": SAM
    grounds a sheet under one phrasing and misses it under another.
    """
    for wording in _concepts(concept):
        kept = _try_masks(client, segment, data, wording)
        if kept:
            kept.sort(key=_side_first)
            return kept[:2]
    return []


def _try_masks(
    client: Any,
    segment: SegmentFn,
    data: bytes,
    concept: str,
) -> list[dict[str, Any]]:
    def usable(record: dict[str, Any]) -> dict[str, Any] | None:
        if record["mask"]["c"] is None or record["mask"]["c"] < MIN_CONFIDENCE:
            return None
        try:
            mask = decode_mask(record["mask"])
        except SamFormatError:
            return None
        if not _covers(mask, record):
            return None
        return {"record": record, "mask": mask}

    try:
        records = segment(client, data, concept)
    except (OSError, ValueError, SamFormatError):
        return []
    return [item for item in (usable(r) for r in records) if item is not None]


def _side_first(item: dict[str, Any]) -> tuple[float, float]:
    """Side views are wide; between similar shapes the bigger wins."""
    box = item["record"]["box"]
    width = box["x2"] - box["x1"]
    height = max(1, box["y2"] - box["y1"])
    return (-width / height, -(width * height))


def _covers(mask: bytes, record: dict[str, Any]) -> bool:
    """The mask fills a sane fraction of its frame: animal, not speck."""
    box = record["box"]
    frame = box["w"] * box["h"]
    box_area = (box["x2"] - box["x1"]) * (box["y2"] - box["y1"])
    raster = record["mask"]["h"] * record["mask"]["w"]
    if frame <= 0 or raster <= 0:
        return False
    if not MIN_COVERAGE < box_area / frame < MAX_COVERAGE:
        return False
    return MIN_COVERAGE < sum(mask) / raster < MAX_COVERAGE


def _write_mask(refs_dir: Path, item: dict[str, Any], kind: str, attempt: int) -> None:
    height, width = item["record"]["mask"]["h"], item["record"]["mask"]["w"]
    pixels = bytes(value * 255 for value in item["mask"])
    Image.frombytes("L", (width, height), pixels).save(
        refs_dir / f"mask-{kind}-{attempt}.png"
    )


def _compare(
    sourced: dict[str, float], proportions: dict[str, float]
) -> list[dict[str, Any]]:
    """Sourced ratios against measured ones; within 15% passes."""
    body = sourced["body_cm"]
    checks = {"tail_length": (sourced["tail_cm"] / body, proportions["tail_length"])}
    if "shoulder_cm" in sourced:
        checks["shoulder_height"] = (
            sourced["shoulder_cm"] / body,
            proportions["leg_length"] + proportions["torso_depth"],
        )
    rows = []
    for name, (ratio, measured) in checks.items():
        rows.append(
            {
                "proportion": name,
                "sourced": ratio,
                "measured": measured,
                "within_15_percent": abs(measured - ratio) / ratio <= TOLERANCE,
            }
        )
    return rows


def _contradicts(comparison: list[dict[str, Any]]) -> bool:
    """A shoulder past 15% off, or a tail past double, trips the redraw."""
    return any(_trips(row) for row in comparison)


def _trips(row: dict[str, Any]) -> bool:
    if row["proportion"] == "tail_length":
        ratio = row["measured"] / row["sourced"]
        return ratio <= 0.5 or ratio >= 2.0
    return not row["within_15_percent"]


def _redraw_hint(comparison: list[dict[str, Any]]) -> str:
    """Name the faults for the redraw: directions only, never numbers."""
    hints = []
    for row in comparison:
        if not _trips(row):
            continue
        small = row["measured"] < row["sourced"]
        if row["proportion"] == "tail_length":
            hints.append(
                "The tail came out too small; draw it clearly visible."
                if small
                else "The tail came out too long; draw it smaller."
            )
        else:
            hints.append(
                "The legs came out too short; draw them longer."
                if small
                else "The legs came out too long and the body too deep;"
                " draw the legs shorter and the belly higher."
            )
    faults = "\n".join(f"- {hint}" for hint in hints)
    return f"\n\nThe previous sheet had faults:\n{faults}"


def _silhouette(grid: Any) -> tuple[float, float, float, float, float]:
    """Height, torso depth, leg, head, and tail ratios from one silhouette."""
    rows = numpy.where(grid.sum(axis=1) > 0)[0]
    cols = numpy.where(grid.sum(axis=0) > 0)[0]
    length = float(cols[-1] - cols[0] + 1)
    height = float(rows[-1] - rows[0] + 1)
    widths = grid.sum(axis=1).astype(float)
    torso = widths > 0.45 * widths.max()
    torso_rows = numpy.where(torso)[0]
    torso_bottom = float(torso_rows[-1])
    torso_top = float(torso_rows[0])
    depth = (torso_bottom - torso_top + 1) / length
    leg = max(0.0, float(rows[-1]) - torso_bottom) / length
    above = grid[: int(torso_top), cols[0] : cols[-1] + 1]
    third = max(1, above.shape[1] // 3)
    left = float(above[:, :third].sum())
    right = float(above[:, -third:].sum())
    head_end, rear_end = (0, 2) if left >= right else (2, 0)
    head_mass = left if head_end == 0 else right
    head = (head_mass**0.5) / length if head_mass > 0 else 0.0
    rear = above[:, :third] if rear_end == 0 else above[:, -third:]
    tail_mass = float(rear.sum()) + _dangling_mass(
        grid, rows, cols, rear_end, int(torso_top), int(torso_bottom), length
    )
    torso_mass = float(grid.sum())
    tail = (tail_mass**0.5) / length if 0 < tail_mass < 0.25 * torso_mass else 0.0
    return height / length, depth, leg, head, tail


def _dangling_mass(
    grid: Any,
    rows: Any,
    cols: Any,
    rear_end: int,
    torso_top: int,
    torso_bottom: int,
    length: float,
) -> float:
    """Mass of narrow rear runs that end above the feet: hanging tails.

    Segments are read off the full row: a rump row is one wide span,
    while a dangling tail is a narrow span with a gap before the body.
    """
    low, high = int(cols[0]), int(cols[-1])
    third = max(1, (high - low + 1) // 3)
    inner, outer = (low, low + third) if rear_end == 0 else (high - third, high)
    feet = int(rows[-1])
    runs: list[list[int]] = []
    for y in range(torso_top, feet + 1):
        segments = _segments(grid[y])
        if not segments:
            continue
        first = segments[0] if rear_end == 0 else segments[-1]
        if first[0] < inner or first[1] > outer:
            continue
        narrow = first[1] - first[0] <= length // 8
        if rear_end == 0:
            separated = len(segments) > 1 and segments[1][0] - first[1] >= 2
        else:
            separated = len(segments) > 1 and first[0] - segments[-2][1] >= 2
        straight_back = (
            len(segments) == 1
            and torso_top + length // 30 <= y <= torso_bottom
            and first[1] - first[0] <= length // 12
        )
        if narrow and (separated or straight_back):
            if runs and y - runs[-1][3] <= 2 and _near(runs[-1], first, length):
                runs[-1][3] = y
                runs[-1][0] = min(runs[-1][0], first[0])
                runs[-1][1] = max(runs[-1][1], first[1])
                runs[-1][4] += first[1] - first[0]
            else:
                runs.append([first[0], first[1], y, y, first[1] - first[0]])
    mass = 0
    for run in runs:
        if run[3] < feet - length // 15:
            mass += run[4]
    return float(mass)


def _segments(row: Any) -> list[tuple[int, int]]:
    """Spans of filled cells in one row, with at least a 2-cell gap."""
    spans = []
    start = None
    gap = 0
    for x, value in enumerate(row):
        if value:
            if start is None:
                start = x
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= 2:
                spans.append((start, x - gap))
                start = None
    if start is not None:
        spans.append((start, len(row) - 1))
    return spans


def _near(run: list[int], segment: tuple[int, int], length: float) -> bool:
    """The segment touches the run's span, give or take a hoof's width."""
    slack = max(2, length // 100)
    return segment[0] <= run[1] + slack and run[0] - slack <= segment[1]


def _colours(kept: list[dict[str, Any]], data: bytes) -> list[dict[str, Any]]:
    """Main colours over the kept masks: k-means, biggest first."""
    photo = Image.open(io.BytesIO(data)).convert("RGB")
    pixels: list[Any] = []
    for item in kept:
        box = item["record"]["box"]
        left = max(0, min(box["x1"], photo.width - 1))
        upper = max(0, min(box["y1"], photo.height - 1))
        right = max(left + 1, min(box["x2"], photo.width))
        lower = max(upper + 1, min(box["y2"], photo.height))
        crop = photo.crop((left, upper, right, lower))
        height, width = item["record"]["mask"]["h"], item["record"]["mask"]["w"]
        small = numpy.asarray(crop.resize((width, height)))
        mask = (
            numpy.frombuffer(item["mask"], dtype=numpy.uint8)
            .reshape(height, width)
            .astype(bool)
        )
        pixels.append(small[mask])
    if not pixels:
        raise _Stopped("masks cover no pixels")
    return _kmeans(numpy.concatenate(pixels).astype(float), K_COLOURS)


def _kmeans(data: Any, clusters: int) -> list[dict[str, Any]]:
    """A small fixed-seed k-means: hex colour plus share, biggest first."""
    rng = numpy.random.default_rng(0)
    picks = rng.choice(len(data), size=min(clusters, len(data)), replace=False)
    centres = data[picks]
    for _ in range(25):
        distance = ((data[:, None, :] - centres[None, :, :]) ** 2).sum(axis=2)
        nearest = distance.argmin(axis=1)
        moved = False
        for index in range(len(centres)):
            members = data[nearest == index]
            if len(members) == 0:
                continue
            mean = members.mean(axis=0)
            if numpy.any(mean != centres[index]):
                moved = True
                centres[index] = mean
        if not moved:
            break
    distance = ((data[:, None, :] - centres[None, :, :]) ** 2).sum(axis=2)
    nearest = distance.argmin(axis=1)
    found = []
    for index, centre in enumerate(centres):
        share = float((nearest == index).sum()) / len(data)
        if share > 0:
            found.append(
                {
                    "hex": "#{:02x}{:02x}{:02x}".format(*centre.astype(int)),
                    "fraction": share,
                }
            )
    found.sort(key=lambda entry: entry["fraction"], reverse=True)
    return found


class _Stopped(Exception):
    """The species has no measurable references. The job stops."""
