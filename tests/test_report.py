"""The build report: whole story, offline, escaped, no personal data."""

from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image

from builder.report import build_report


def _png() -> bytes:
    canvas = Image.new("RGB", (8, 8), (122, 82, 48))
    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()


def _job(job_dir: Path) -> None:
    (job_dir / "state.json").write_text(
        json.dumps(
            {
                "params": {
                    "area": "xx_eagle",
                    "kind": "new_place",
                    "author": "Animundo Builder",
                },
                "completed": ["intake", "survey", "references", "models"],
                "failed": None,
            }
        )
    )
    (job_dir / "survey.json").write_text(
        json.dumps(
            {
                "area_id": "xx_eagle",
                "facts": [
                    {
                        "topic": "Cliffs",
                        "text": "Tall cliffs.",
                        "sources": ["https://example.org/cliffs"],
                    }
                ],
                "species": [
                    {
                        "species": "eagle",
                        "latin": "Aquila chrysaetos",
                        "role": "predator",
                        "evidence": "Nests here.",
                        "sources": ["https://example.org/eagle"],
                        "description": "Big bird <b>Bold</b>.",
                        "measurements": {
                            "body_cm": {
                                "value": 77,
                                "source": "https://example.org/size",
                            }
                        },
                    }
                ],
            }
        )
    )
    (job_dir / "species-plan.json").write_text(
        json.dumps({"species": [{"species": "eagle", "decision": "new"}]})
    )
    measured = job_dir / "measurements"
    measured.mkdir()
    (measured / "eagle.json").write_text(
        json.dumps(
            {
                "species": "eagle",
                "reference": "sheet",
                "sourced": {"body_cm": 77.0},
                "measured": None,
                "comparison": [
                    {
                        "proportion": "tail_length",
                        "sourced": 0.4,
                        "measured": 0.02,
                        "within_15_percent": False,
                    }
                ],
                "redrawn": True,
            }
        )
    )
    refs = job_dir / "references" / "eagle"
    refs.mkdir(parents=True)
    (refs / "sheet-1.png").write_bytes(_png())
    (refs / "mask-side-1.png").write_bytes(_png())
    first = job_dir / "rounds" / "eagle" / "1"
    first.mkdir(parents=True)
    (first / "spec.json").write_text(json.dumps({"id": "eagle", "parts": [1, 2]}))
    (first / "build.log").write_text("BUILT eagle: 900 triangles\n")
    (first / "preview.png").write_bytes(_png())
    reviews = job_dir / "reviews"
    reviews.mkdir()
    (reviews / "eagle-1.json").write_text(
        json.dumps(
            {
                "species": "eagle",
                "round": 1,
                "issues": [
                    {
                        "part": "tail",
                        "problem": "Too thin.",
                        "severity": "blocking",
                        "fix": "Fan it.",
                    }
                ],
            }
        )
    )
    (reviews / "eagle-2.json").write_text(
        json.dumps({"species": "eagle", "round": 2, "issues": []})
    )
    second = job_dir / "rounds" / "eagle" / "2"
    second.mkdir()
    (second / "spec.json").write_text(json.dumps({"id": "eagle"}))
    (second / "build.log").write_text("BUILT eagle: 910 triangles\n")
    (second / "preview.png").write_bytes(_png())
    models = job_dir / "models"
    models.mkdir()
    (models / "eagle.json").write_text(json.dumps({"id": "eagle"}))
    (job_dir / "log.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "at": "2026-10-27T00:00:00+00:00",
                        "call": "images",
                        "model": "muse-image-1.0",
                        "generated": 1,
                        "duration_s": 5.0,
                        "cost_usd": 0.01,
                        "result": "ok",
                    }
                ),
                json.dumps(
                    {
                        "at": "2026-10-27T00:01:00+00:00",
                        "call": "chat",
                        "model": "muse-spark-1.3",
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "duration_s": 10.0,
                        "cost_usd": 0.0338,
                        "result": "ok",
                    }
                ),
            ]
        )
    )
    (job_dir / "improve.note").write_text("NOT-FOR-REPORT family words")


def test_report_tells_the_whole_story(tmp_path: Path) -> None:
    _job(tmp_path)
    out = build_report(tmp_path)
    assert out == tmp_path / "report.html"
    text = out.read_text(encoding="utf-8")
    assert "$0.0438" in text
    assert "muse-spark-1.3" in text and "muse-image-1.0" in text
    assert "https://example.org/cliffs" in text, "sources as text"
    assert "77" in text and "drawn again once" in text
    assert "BUILT eagle: 900 triangles" in text
    assert "Too thin." in text and "No issues" in text
    assert "data:image/png;base64," in text
    assert "eagle.json" in text, "final files listed"


def test_report_is_offline_safe(tmp_path: Path) -> None:
    _job(tmp_path)
    text = build_report(tmp_path).read_text(encoding="utf-8")
    for banned in ('src="http', 'href="http', "<script", "<link"):
        assert banned not in text


def test_report_escapes_job_text_and_skips_the_family_note(tmp_path: Path) -> None:
    _job(tmp_path)
    text = build_report(tmp_path).read_text(encoding="utf-8")
    assert "&lt;b&gt;Bold&lt;/b&gt;" in text
    assert "<b>Bold</b>" not in text
    assert "NOT-FOR-REPORT" not in text


def test_partial_job_still_reports(tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text(
        json.dumps({"params": {}, "completed": ["intake"], "failed": None})
    )
    text = build_report(tmp_path).read_text(encoding="utf-8")
    assert "No survey yet." in text
    assert "$0.0000" in text
