"""Read SAM 3.1's wire format: line grammar plus one-bit mask decode.

A streamed line carries one frame: `<Nf>` plus comma-separated records,
each an object id with one box and one mask. Mask payloads are base85 in
an alphabet of ASCII 0x21-0x7E minus the wire breakers (`"`, `\\`, `,`,
`;`, `<`, `>`, `|`) with a 5-character length prefix, then range-coded
bits under a 12-bit spatial context model.

Only the `one_bit` encoding is decoded: the builder always requests it
(`metadata.mask_encoding`), since it is tens of times smaller and all
the measurements need is a 0/1 raster. A `~` (lossless) payload is
refused with a message saying so.
"""

from __future__ import annotations

import re
from array import array
from typing import Any

_MASK_32 = 0xFFFFFFFF
_TOP = 1 << 24
_BOTTOM = 1 << 16
_INCREMENT = 14
_COUNT_LIMIT = 4096
_CONTEXT_COUNT = 1 << 12
_PADDING = 2

_EXCLUDED = frozenset({'"', "\\", ",", ";", "<", ">", "|"})
_ALPHABET = "".join(
    chr(code) for code in range(0x21, 0x7F) if chr(code) not in _EXCLUDED
)
_VALUES = {char: index for index, char in enumerate(_ALPHABET)}

_RECORD = re.compile(
    r"(?P<id>\d+)"
    r"<\|box;x1=(?P<x1>\d+);y1=(?P<y1>\d+);x2=(?P<x2>\d+);y2=(?P<y2>\d+)"
    r";w=(?P<w>\d+);h=(?P<h>\d+)(?:;c=(?P<box_c>[0-9.]+))?\|>"
    r"<\|mask;x=(?P<x>\d+);y=(?P<y>\d+)(?:;c=(?P<mask_c>[0-9.]+))?"
    r";data=(?P<mh>\d+),(?P<mw>\d+),(?P<enc>[~!])(?P<payload>.*?)\|>"
)
_FRAME = re.compile(r"<(?P<frame>\d+)f>")


class SamFormatError(Exception):
    """A SAM line or mask payload does not parse."""


def parse_line(line: str) -> list[dict[str, Any]]:
    """Split one streamed line into its object records.

    Each record holds the frame index, object id, box, and mask span.
    An empty line (zero matches) is valid and yields no records.
    """
    match = _FRAME.match(line.strip())
    if match is None:
        if not line.strip():
            return []
        raise SamFormatError(f"line has no frame marker: {line[:60]!r}")
    frame = int(match.group("frame"))
    rest = line.strip()[match.end() :]
    if not rest:
        return []
    records = []
    for part in _split_records(rest):
        found = _RECORD.fullmatch(part)
        if found is None:
            raise SamFormatError(f"record does not parse: {part[:80]!r}")
        records.append(
            {
                "frame": frame,
                "id": int(found.group("id")),
                "box": {
                    "x1": int(found.group("x1")),
                    "y1": int(found.group("y1")),
                    "x2": int(found.group("x2")),
                    "y2": int(found.group("y2")),
                    "w": int(found.group("w")),
                    "h": int(found.group("h")),
                    "c": _optional_float(found.group("box_c")),
                },
                "mask": {
                    "x": int(found.group("x")),
                    "y": int(found.group("y")),
                    "c": _optional_float(found.group("mask_c")),
                    "h": int(found.group("mh")),
                    "w": int(found.group("mw")),
                    "enc": found.group("enc"),
                    "payload": found.group("payload"),
                },
            }
        )
    return records


def _split_records(rest: str) -> list[str]:
    """Split records where a new `id<|box` starts (data holds commas)."""
    return re.split(r",(?=\d+<\|box)", rest)


def _optional_float(text: str | None) -> float | None:
    return float(text) if text is not None else None


def decode_mask(mask: dict[str, Any]) -> bytes:
    """Decode one mask span to H*W bytes of 0/1, row by row."""
    if mask["enc"] != "!":
        raise SamFormatError(
            "lossless (~) masks are not decoded; request one_bit instead"
        )
    coded = _unbase85(mask["payload"])
    return _decode_raster(coded, mask["w"], mask["h"])


def _unbase85(payload: str) -> bytes:
    """The wire base85: 5-char length prefix, then 5 chars per 4 bytes."""
    if len(payload) < 5:
        raise SamFormatError("mask payload is missing its length prefix")
    try:
        values = [_VALUES[char] for char in payload]
    except KeyError as error:
        raise SamFormatError("mask payload has an invalid character") from error
    if any(value >= 85 for value in values):
        raise SamFormatError("mask payload has an out-of-range character")
    length = 0
    for value in values[:5]:
        length = length * 85 + value
    body, tail = divmod(length, 4)
    if len(payload) != 5 + body * 5 + (tail + 1 if tail else 0):
        raise SamFormatError("mask payload length does not match its prefix")
    output = bytearray(length)
    source = 5
    for block in range(body):
        value = 0
        for digit in values[source : source + 5]:
            value = value * 85 + digit
        if value > _MASK_32:
            raise SamFormatError("mask payload has an out-of-range group")
        output[block * 4 : block * 4 + 4] = value.to_bytes(4, "big")
        source += 5
    if tail:
        value = 0
        digits = values[source:] + [84] * (4 - tail)
        for digit in digits:
            value = value * 85 + digit
        if value > _MASK_32:
            raise SamFormatError("mask payload has an out-of-range tail")
        output[body * 4 :] = value.to_bytes(4, "big")[:tail]
    return bytes(output)


class _RangeDecoder:
    """A range decoder over the unpacked bytes."""

    def __init__(self, data: bytes) -> None:
        if len(data) < 4:
            raise SamFormatError("mask payload is missing its finalization")
        self._data = data
        self._position = 4
        self._low = 0
        self._range = _MASK_32
        self._code = int.from_bytes(data[:4], "big")
        self._scaled = 0

    def frequency(self, total: int) -> int:
        self._scaled = self._range // total
        if self._scaled == 0:
            raise SamFormatError("mask payload has invalid coding state")
        value = (self._code - self._low) // self._scaled
        return total - 1 if value >= total else value

    def decode(self, cumulative: int, frequency: int) -> None:
        self._low = (self._low + self._scaled * cumulative) & _MASK_32
        self._range = self._scaled * frequency
        while True:
            top = (self._low ^ ((self._low + self._range) & _MASK_32)) < _TOP
            if not top and self._range < _BOTTOM:
                self._range = (-self._low) & (_BOTTOM - 1)
            elif not top:
                break
            self._code = ((self._code << 8) | self._read()) & _MASK_32
            self._low = (self._low << 8) & _MASK_32
            self._range = (self._range << 8) & _MASK_32

    def _read(self) -> int:
        if self._position >= len(self._data):
            raise SamFormatError("mask payload ended before decoding completed")
        value = self._data[self._position]
        self._position += 1
        return value


def _context_at(raster: bytearray, at: int, up: int, upper: int) -> int:
    return (
        raster[at - 1]
        | (raster[at - 2] << 1)
        | (raster[up - 2] << 2)
        | (raster[up - 1] << 3)
        | (raster[up] << 4)
        | (raster[up + 1] << 5)
        | (raster[up + 2] << 6)
        | (raster[upper - 2] << 7)
        | (raster[upper - 1] << 8)
        | (raster[upper] << 9)
        | (raster[upper + 1] << 10)
        | (raster[upper + 2] << 11)
    )


def _decode_raster(coded: bytes, width: int, height: int) -> bytes:
    """Range-coded bits under adaptive 12-bit spatial contexts."""
    decoder = _RangeDecoder(coded)
    counts = array("H", [1]) * (_CONTEXT_COUNT * 2)
    stride = width + 2 * _PADDING
    padded = bytearray(stride * (height + _PADDING))
    output = bytearray(width * height)
    for y in range(height):
        row = (y + _PADDING) * stride + _PADDING
        for x in range(width):
            at = row + x
            context = _context_at(padded, at, at - stride, at - 2 * stride)
            slot = context * 2
            zero, one = counts[slot], counts[slot + 1]
            bit = 0 if decoder.frequency(zero + one) < zero else 1
            decoder.decode(0 if bit == 0 else zero, zero if bit == 0 else one)
            counts[slot + bit] += _INCREMENT
            if counts[slot] + counts[slot + 1] >= _COUNT_LIMIT:
                counts[slot] = (counts[slot] >> 1) or 1
                counts[slot + 1] = (counts[slot + 1] >> 1) or 1
            padded[at] = bit
            output[y * width + x] = bit
    return bytes(output)
