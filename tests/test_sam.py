"""SAM wire format: line grammar and one-bit mask decode. No network."""

from __future__ import annotations

from pathlib import Path

import pytest

from builder.sam import SamFormatError, decode_mask, parse_line

VECTOR = Path(__file__).parent / "vectors" / "sam_red_deer_onebit.txt"


def test_parse_real_line() -> None:
    (record,) = parse_line(VECTOR.read_text())
    assert record["frame"] == 0
    assert record["id"] == 0
    assert record["box"] == {
        "x1": 486,
        "y1": 432,
        "x2": 2314,
        "y2": 1400,
        "w": 3320,
        "h": 2072,
        "c": pytest.approx(0.9416, abs=1e-4),
    }
    assert record["mask"]["h"] == 543
    assert record["mask"]["w"] == 1024
    assert record["mask"]["enc"] == "!"
    assert record["mask"]["c"] == pytest.approx(0.9416, abs=1e-4)


def test_parse_empty_line_is_zero_matches() -> None:
    assert parse_line("") == []
    assert parse_line("<3f>") == []


def test_parse_two_records() -> None:
    line = (
        "<0f>0<|box;x1=1;y1=2;x2=3;y2=4;w=10;h=10|>"
        "<|mask;x=0;y=0;data=1,1,!abc|>,"
        "2<|box;x1=5;y1=6;x2=7;y2=8;w=10;h=10|>"
        "<|mask;x=0;y=0;data=1,1,!def|>"
    )
    records = parse_line(line)
    assert [record["id"] for record in records] == [0, 2]
    assert records[1]["box"]["x1"] == 5


def test_parse_rejects_garbage() -> None:
    with pytest.raises(SamFormatError, match="frame marker"):
        parse_line("hello")
    with pytest.raises(SamFormatError, match="does not parse"):
        parse_line("<0f>0<|box;broken|>")


def test_decode_real_mask_is_binary_and_sane() -> None:
    (record,) = parse_line(VECTOR.read_text())
    raster = decode_mask(record["mask"])
    assert len(raster) == 543 * 1024
    assert set(raster) <= {0, 1}
    fraction = sum(raster) / len(raster)
    assert 0.1 < fraction < 0.9


def test_decode_matches_official_package() -> None:
    official = pytest.importorskip("meta_sam_parser")
    (record,) = parse_line(VECTOR.read_text())
    mask = official.SegmentationMask(
        encoding="one_bit",
        height=record["mask"]["h"],
        width=record["mask"]["w"],
        payload="!" + record["mask"]["payload"],
    )
    assert decode_mask(record["mask"]) == bytes(official.decode_mask_to_raster(mask))


def test_decode_refuses_lossless() -> None:
    with pytest.raises(SamFormatError, match="one_bit"):
        decode_mask({"enc": "~", "h": 1, "w": 1, "payload": "x"})


def test_decode_rejects_truncated_payload() -> None:
    (record,) = parse_line(VECTOR.read_text())
    record["mask"]["payload"] = record["mask"]["payload"][:100]
    with pytest.raises(SamFormatError, match="[Ll]ength|prefix|truncated|completed"):
        decode_mask(record["mask"])
