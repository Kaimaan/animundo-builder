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
from pathlib import Path

import httpx2
import pytest

from builder.config import Config
from builder.config import load as load_config
from builder.meta import CostLogger, MetaClient
from builder.prompts import load_prompt
from builder.schemas import load_schema

LIVE = os.environ.get("BUILDER_LIVE_TEST") == "1" and bool(
    os.environ.get("MODEL_API_KEY")
)
BASETEN = os.environ.get("BUILDER_LIVE_TEST") == "1" and bool(
    os.environ.get("BASETEN_API_KEY")
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


def test_live_images_generate(tmp_path) -> None:
    client = make_live_client(tmp_path, model="muse-image-1.0")
    data = client.images_generate("a small red deer silhouette, flat icon")
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"expected PNG, got {data[:16]!r}"
    print(f"\nimage bytes: {len(data)}")


def test_live_reviewer_passes_approved_deer(tmp_path) -> None:
    """Inbox #13: the loosened reviewer must pass the approved deer.

    Needs the deer run's round 5 (kept locally, never committed)
    and a world checkout next to this repo for the moose bar.
    """
    repo = Path(__file__).resolve().parent.parent
    sheet = repo / "jobs" / "deer-demo" / "references" / "red_deer" / "sheet-1.png"
    bar = repo.parent / "animundo-world" / "models" / "previews" / "moose.png"
    preview = repo / "jobs" / "deer-demo" / "rounds" / "red_deer" / "5" / "preview.png"
    for path in (sheet, bar, preview):
        if not path.is_file():
            pytest.skip(f"needs {path}")
    prompt = load_prompt(
        "model_review_mammal",
        {
            "TITLE": "Red deer",
            "SPECIES": "red_deer",
            "ROUND": "5",
            "BUILT": "red_deer: 1400 triangles, 12 bones; size 2.2 m long",
            "CLOSEST_TITLE": "Moose",
        },
    )
    content: list = [{"type": "text", "text": prompt}]
    for path in (sheet, bar, preview):
        encoded = base64.b64encode(path.read_bytes()).decode()
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{encoded}"},
            }
        )
    client = make_live_client(tmp_path)
    response = client.chat(
        [{"role": "user", "content": content}],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "Review", "schema": load_schema("review")},
        },
    )
    review = json.loads(response.choices[0].message.content or "{}")
    blocking = [i for i in review.get("issues", []) if i.get("severity") == "blocking"]
    assert not blocking, f"approved deer still blocked: {blocking!r}"
    print(f"\nissues: {len(review.get('issues', []))}, blocking: 0")


def test_live_safety_sixty(tmp_path) -> None:
    """T18: the reviewer catches 30 bad texts, flags at most 2 good ones."""
    from builder.stages.safety import REVIEW_SCHEMA, review_items

    cases = json.loads(
        (Path(__file__).resolve().parent / "safety_cases.json").read_text()
    )
    items = [
        {
            "key": case["key"],
            "en": case["en"],
            "fi": case.get("fi"),
            "sources": case.get("sources", []),
        }
        for case in [*cases["good"], *cases["bad"]]
    ]
    client = make_live_client(tmp_path)

    def ask(prompt: str, schema: dict | None) -> str:
        response = client.chat(
            [{"role": "user", "content": prompt}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "Safety", "schema": schema or REVIEW_SCHEMA},
            },
        )
        return (response.choices[0].message.content or "").strip()

    verdicts = review_items(
        ask, items, "(no sensitive species in this place)", "title: Moose"
    )
    missed = [
        key
        for case in cases["bad"]
        for key in [case["key"]]
        if verdicts[key]["verdict"] == "pass"
    ]
    flagged = [
        key
        for case in cases["good"]
        for key in [case["key"]]
        if verdicts[key]["verdict"] != "pass"
    ]
    print(f"\nmissed bad: {missed}\nflagged good: {flagged}")
    assert not missed, f"reviewer passed {len(missed)} bad texts: {missed}"
    assert len(flagged) <= 2, f"reviewer flagged {len(flagged)} good texts: {flagged}"


def make_baseten_client(tmp_path) -> MetaClient:
    key = os.environ.get("BASETEN_API_KEY")
    assert key, "BASETEN_API_KEY is not set"
    config = Config(open_model_host="baseten", baseten_api_key=key)
    return MetaClient.for_open_weights(
        config, logger=CostLogger(tmp_path / "baseten-cost.jsonl")
    )


@pytest.mark.skipif(
    not BASETEN, reason="live test: needs BUILDER_LIVE_TEST=1 and BASETEN_API_KEY"
)
def test_live_baseten_translate(tmp_path) -> None:
    client = make_baseten_client(tmp_path)
    response = client.chat(
        [
            {
                "role": "user",
                "content": "Translate to Finnish: The eagle circles above the cliffs.",
            }
        ],
        max_tokens=1500,
        extra_body={"chat_template_kwargs": {"reasoning_effort": "low"}},
    )
    content = response.choices[0].message.content or ""
    assert content.strip(), "empty content counts as a failure"
    usage = response.usage
    print(f"\nbaseten translation: {usage.prompt_tokens} in")
    print(f"completion tokens: {usage.completion_tokens}")


@pytest.mark.skipif(
    not BASETEN, reason="live test: needs BUILDER_LIVE_TEST=1 and BASETEN_API_KEY"
)
def test_live_baseten_review_shaped(tmp_path) -> None:
    client = make_baseten_client(tmp_path)
    response = client.chat(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "List visible flaws as JSON."},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{red_pixel_png_b64()}"
                        },
                    },
                ],
            }
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "Review",
                "schema": {
                    "type": "object",
                    "properties": {
                        "issues": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["issues"],
                    "additionalProperties": False,
                },
            },
        },
    )
    content = response.choices[0].message.content or ""
    assert content.strip(), "empty content counts as a failure"
    assert "issues" in json.loads(content)
    usage = response.usage
    print(f"\nbaseten review: {usage.prompt_tokens} in, {usage.completion_tokens} out")
