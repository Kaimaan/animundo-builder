"""Live smoke tests against the Meta Model API. Paid calls, opt-in only.

Skipped unless BUILDER_LIVE_TEST=1 with MODEL_API_KEY set. Never runs in
CI. Prints usage and cost only, never the key.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import zlib

import httpx2
import pytest

from builder.config import load as load_config
from builder.meta import CostLogger, MetaClient

LIVE = os.environ.get("BUILDER_LIVE_TEST") == "1" and bool(
    os.environ.get("MODEL_API_KEY")
)

pytestmark = pytest.mark.skipif(
    not LIVE, reason="live test: needs BUILDER_LIVE_TEST=1 and MODEL_API_KEY"
)


def red_pixel_png_b64(size: int = 64) -> str:
    def chunk(tag: bytes, data: bytes) -> bytes:
        out = struct.pack(">I", len(data)) + tag + data
        return out + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * size for _ in range(size))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode()


def make_live_client(tmp_path, model: str | None = None) -> MetaClient:
    config = load_config()
    assert config.model_api_key, "MODEL_API_KEY is not set"
    return MetaClient(
        api_key=config.model_api_key,
        model=model or config.model,
        logger=CostLogger(tmp_path / "live-cost.jsonl"),
    )


def test_live_models_list(tmp_path) -> None:
    client = make_live_client(tmp_path)
    client.ensure_model()
    print(f"\nmodel on API: {client.model}")


def test_live_chat_with_tools(tmp_path) -> None:
    client = make_live_client(tmp_path)
    response = client.chat(
        [{"role": "user", "content": "What is 12 times 13? Use multiply."}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "multiply",
                    "description": "Multiply two numbers.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "a": {"type": "number"},
                            "b": {"type": "number"},
                        },
                        "required": ["a", "b"],
                        "additionalProperties": False,
                    },
                },
            }
        ],
        reasoning_effort="minimal",
    )
    calls = response.choices[0].message.tool_calls or []
    assert calls, "expected the model to call multiply"
    assert calls[0].function.name == "multiply"
    args = json.loads(calls[0].function.arguments)
    assert args["a"] * args["b"] == 156
    print(f"\ntool call: multiply{args}")


def test_live_structured_output(tmp_path) -> None:
    client = make_live_client(tmp_path)
    response = client.chat(
        [{"role": "user", "content": "Bag End, Hobbiton"}],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "Place",
                "schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                    "additionalProperties": False,
                },
            },
        },
        reasoning_effort="minimal",
    )
    place = json.loads(response.choices[0].message.content or "{}")
    assert place.get("name"), f"expected a name, got: {place!r}"
    print(f"\nstructured name: {place['name']}")


def test_live_search_grounding(tmp_path) -> None:
    client = make_live_client(tmp_path)
    response = client.responses_create(
        "Who won the most recent Formula 1 race? Cite your source.",
        tools=[{"type": "web_search"}],
        include=["web_search_call.results"],
    )
    cites = [
        ann.url
        for block in response.output
        for part in getattr(block, "content", []) or []
        for ann in getattr(part, "annotations", []) or []
        if getattr(ann, "type", "") == "url_citation"
    ]
    assert cites, "expected at least one cited source"
    print(f"\ncitations: {cites[:3]}")


def test_live_image_input(tmp_path) -> None:
    client = make_live_client(tmp_path)
    data_url = f"data:image/png;base64,{red_pixel_png_b64()}"
    response = client.chat(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Reply with only the colour."},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        reasoning_effort="minimal",
    )
    answer = (response.choices[0].message.content or "").strip().lower()
    assert "red" in answer, f"expected RED, got: {answer!r}"
    print(f"\nvision answer: {answer}")


def test_live_sam_segments_a_photo(tmp_path) -> None:
    photo = httpx2.get("https://picsum.photos/320", follow_redirects=True).content
    assert len(photo) > 1000, "test photo did not download"
    data_url = f"data:image/jpeg;base64,{base64.b64encode(photo).decode()}"
    client = make_live_client(tmp_path, model="sam-3.1")
    events: list = []
    with client._client.responses.stream(  # noqa: SLF001 - smoke only
        model="sam-3.1",
        input=[
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "the main foreground object"},
                    {"type": "input_image", "image_url": data_url},
                ],
            }
        ],
    ) as stream:
        for event in stream:
            events.append(event)
    kinds = sorted({getattr(event, "type", "?") for event in events})
    assert events, "expected a completed SAM stream"
    print(f"\nSAM events: {len(events)}, kinds: {kinds[:6]}")


def test_live_baseten_open_weights() -> None:
    pytest.skip("Baseten call lands with T33 (needs key and endpoint URL)")
