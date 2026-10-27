"""Reference sheets: draw, segment, measure, compare, redraw once. No network."""

from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path
from typing import Any

import numpy
import pytest
from PIL import Image

from builder.pipeline import BudgetTracker, JobContext, Pipeline, StageFailedError
from builder.sam import parse_line
from builder.stages.references import (
    ReferencesStage,
    _compare,
    _side_first,
    _silhouette,
    current_season,
)

VECTOR = Path(__file__).parent / "vectors" / "sam_red_deer_onebit.txt"
SEASONS = {
    "spring": "04-15",
    "summer": "06-01",
    "autumn": "09-15",
    "winter": "11-20",
    "snow": True,
}

# Regression values off the vector mask: it shows a grazing deer, head
# down, so head and rear read flipped and the tail number is plumbing,
# not biology. Real tail behaviour is pinned by the synthetic test below.
CLOSE = {"body_cm": 200.0, "tail_cm": 18.8, "shoulder_cm": 101.0, "weight_kg": 150.0}
FAR = {"body_cm": 200.0, "tail_cm": 70.0, "shoulder_cm": 101.0, "weight_kg": 150.0}


def _photo() -> bytes:
    canvas = Image.new("RGB", (1024, 1024), (122, 82, 48))
    pixels = canvas.load()
    assert pixels is not None
    for x in range(100):
        for y in range(100):
            pixels[x, y] = (232, 220, 200)
    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()


def _inputs(tmp_path: Path, sourced: dict[str, float]) -> None:
    (tmp_path / "species-plan.json").write_text(
        json.dumps({"species": [{"species": "red_deer", "decision": "new"}]})
    )
    (tmp_path / "survey.json").write_text(
        json.dumps(
            {
                "area_id": "xx_test",
                "facts": [],
                "species": [
                    {
                        "species": "red_deer",
                        "description": "Large deer, reddish-brown summer coat.",
                        "measurements": {
                            name: {
                                "value": value,
                                "source": "https://example.org/size",
                            }
                            for name, value in sourced.items()
                        },
                    }
                ],
            }
        )
    )
    regions = tmp_path / "regions"
    regions.mkdir()
    (regions / "xx_test.json").write_text(json.dumps({"seasons": SEASONS}))
    animals = tmp_path / "animals"
    animals.mkdir()
    (animals / "red_deer.json").write_text(json.dumps({"text": {"title": "Red deer"}}))


def _ctx(tmp_path: Path) -> JobContext:
    return JobContext(
        job_id="job-1",
        job_dir=tmp_path,
        client=None,
        tracker=BudgetTracker(budget_usd=8.0),
    )


def _record() -> dict[str, Any]:
    (record,) = parse_line(VECTOR.read_text())
    return record


def _segment_ok(client: Any, data: bytes, concept: str) -> list[dict[str, Any]]:
    assert concept == "red deer"
    return [_record()]


def test_sheet_measures_and_matches(tmp_path: Path) -> None:
    _inputs(tmp_path, CLOSE)
    prompts: list[str] = []

    def draw(ctx: Any, prompt: str) -> bytes:
        prompts.append(prompt)
        return _photo()

    result = ReferencesStage(
        draw=draw, segment=_segment_ok, today=lambda: date(2026, 10, 26)
    ).run(_ctx(tmp_path))
    assert result == {"measurement_files": ["measurements/red_deer.json"]}
    assert len(prompts) == 1, "one draw when the sheet matches"
    assert "reddish-brown" in prompts[0], "prompted with the survey description"
    assert "autumn" in prompts[0], "October wears the autumn coat"
    refs = tmp_path / "references" / "red_deer"
    assert (refs / "sheet-1.png").is_file()
    assert (refs / "prompt-1.md").read_text() == prompts[0]
    assert (refs / "mask-side-1.png").is_file()
    written = json.loads((tmp_path / "measurements" / "red_deer.json").read_text())
    assert written["reference"] == "sheet"
    assert written["sourced"] == CLOSE
    assert written["redrawn"] is False
    assert written["measured"]["proportions"]["body_length"] == 1.0
    assert written["measured"]["proportions"]["tail_length"] == pytest.approx(
        0.0944, abs=0.001
    )
    assert all(row["within_15_percent"] for row in written["comparison"])
    assert {row["proportion"] for row in written["comparison"]} == {
        "tail_length",
        "shoulder_height",
    }
    colours = written["measured"]["colours"]
    assert colours, "at least one colour"
    assert sum(entry["fraction"] for entry in colours) == pytest.approx(1.0)
    assert [entry["fraction"] for entry in colours] == sorted(
        [entry["fraction"] for entry in colours], reverse=True
    )


def test_contradiction_redraws_once_then_sourced_win(tmp_path: Path) -> None:
    _inputs(tmp_path, FAR)
    calls = []

    def draw(ctx: Any, prompt: str) -> bytes:
        calls.append(prompt)
        return _photo()

    result = ReferencesStage(
        draw=draw, segment=_segment_ok, today=lambda: date(2026, 10, 26)
    ).run(_ctx(tmp_path))
    assert result == {"measurement_files": ["measurements/red_deer.json"]}
    assert len(calls) == 2, "at most one redraw"
    assert "tail" in calls[1], "the redraw names the fault"
    assert "legs and body" not in calls[1], "passing proportions stay quiet"
    refs = tmp_path / "references" / "red_deer"
    assert (refs / "sheet-1.png").is_file()
    assert (refs / "sheet-2.png").is_file()
    assert (refs / "prompt-2.md").read_text() == calls[1]
    written = json.loads((tmp_path / "measurements" / "red_deer.json").read_text())
    assert written["redrawn"] is True
    assert written["sourced"] == FAR, "the sourced numbers are kept either way"
    tail = next(
        row for row in written["comparison"] if row["proportion"] == "tail_length"
    )
    assert tail["within_15_percent"] is False


def test_none_writes_sourced_only_and_draws_nothing(tmp_path: Path) -> None:
    _inputs(tmp_path, CLOSE)

    def draw(ctx: Any, prompt: str) -> bytes:
        raise AssertionError("none mode draws nothing")

    def segment(client: Any, data: bytes, concept: str) -> list[dict[str, Any]]:
        raise AssertionError("none mode segments nothing")

    result = ReferencesStage(reference="none", draw=draw, segment=segment).run(
        _ctx(tmp_path)
    )
    assert result == {"measurement_files": ["measurements/red_deer.json"]}
    assert not (tmp_path / "references").exists()
    written = json.loads((tmp_path / "measurements" / "red_deer.json").read_text())
    assert written["reference"] == "none"
    assert written["sourced"] == CLOSE
    assert written["measured"] is None
    assert written["comparison"] == []
    assert written["redrawn"] is False


def test_missing_survey_measurements_stops(tmp_path: Path) -> None:
    _inputs(tmp_path, CLOSE)
    survey = json.loads((tmp_path / "survey.json").read_text())
    del survey["species"][0]["measurements"]
    (tmp_path / "survey.json").write_text(json.dumps(survey))
    with pytest.raises(StageFailedError, match="stage references failed"):
        Pipeline([ReferencesStage()], tmp_path / "state.json").run(_ctx(tmp_path))
    state = json.loads((tmp_path / "state.json").read_text())
    assert "no measurements" in state["failed"]["error"]


def test_empty_sheet_stops(tmp_path: Path) -> None:
    _inputs(tmp_path, CLOSE)

    def segment(client: Any, data: bytes, concept: str) -> list[dict[str, Any]]:
        return []

    with pytest.raises(StageFailedError, match="stage references failed"):
        Pipeline(
            [
                ReferencesStage(
                    draw=lambda ctx, prompt: _photo(),
                    segment=segment,
                    today=lambda: date(2026, 10, 26),
                )
            ],
            tmp_path / "state.json",
        ).run(_ctx(tmp_path))


def test_bad_reference_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="sheet.*none"):
        ReferencesStage(reference="photos")


def test_current_season_picks_latest_start() -> None:
    assert current_season(SEASONS, date(2026, 10, 26)) == "autumn"
    assert current_season(SEASONS, date(2026, 7, 1)) == "summer"
    assert current_season(SEASONS, date(2026, 1, 15)) == "winter"


def test_compare_boundary() -> None:
    (row,) = [
        entry
        for entry in _compare(
            {"body_cm": 100.0, "tail_cm": 10.0, "weight_kg": 1.0},
            {"tail_length": 0.115},
        )
        if entry["proportion"] == "tail_length"
    ]
    assert row["within_15_percent"] is True
    (row,) = [
        entry
        for entry in _compare(
            {"body_cm": 100.0, "tail_cm": 10.0, "weight_kg": 1.0},
            {"tail_length": 0.116},
        )
        if entry["proportion"] == "tail_length"
    ]
    assert row["within_15_percent"] is False


def test_compare_skips_height_for_birds() -> None:
    rows = _compare(
        {"body_cm": 100.0, "tail_cm": 10.0, "wingspan_cm": 200.0, "weight_kg": 1.0},
        {"tail_length": 0.1, "body_height": 0.9},
    )
    assert [row["proportion"] for row in rows] == ["tail_length"]


def _synthetic(tail: bool) -> Any:
    """A head-up side silhouette: body, head, two legs, maybe a tail."""
    grid = numpy.zeros((100, 300), dtype=numpy.uint8)
    grid[20:61, 50:251] = 1  # body
    grid[5:20, 200:251] = 1  # head, above the back at the right
    grid[60:96, 80:101] = 1  # legs reach the feet
    grid[60:96, 180:201] = 1
    if tail:
        grid[40:71, 30:46] = 1  # dangle at the rear, clear of the legs
    return grid


def test_hanging_tail_found_legs_ignored() -> None:
    height, depth, leg, head, tail = _silhouette(_synthetic(tail=True))
    assert leg == pytest.approx((95 - 60) / 221, abs=0.01)
    assert head > 0
    assert tail == pytest.approx((16 * 31) ** 0.5 / 221, abs=0.005)


def test_no_tail_reads_zero() -> None:
    *_, tail = _silhouette(_synthetic(tail=False))
    assert tail == 0.0


def test_sam_concept_falls_back_to_last_word(tmp_path: Path) -> None:
    _inputs(tmp_path, CLOSE)
    seen: list[str] = []

    def segment(client: Any, data: bytes, concept: str) -> list[dict[str, Any]]:
        seen.append(concept)
        return [] if concept == "red deer" else [_record()]

    ReferencesStage(
        draw=lambda ctx, prompt: _photo(),
        segment=segment,
        today=lambda: date(2026, 10, 26),
    ).run(_ctx(tmp_path))
    assert seen == ["red deer", "deer"]


def test_side_view_sorts_first() -> None:
    def item(x2: int, y2: int) -> dict[str, Any]:
        return {"record": {"box": {"x1": 0, "y1": 0, "x2": x2, "y2": y2}}}

    wide = item(400, 200)
    tall = item(200, 400)
    assert sorted([tall, wide], key=_side_first) == [wide, tall]
