# Meta API: checked facts

Checked 2026-10-26 against Meta's developer docs. Each fact below names its
source page; the working examples use `$MODEL_API_KEY` and run as-is once a
key exists. Live calls (one per capability) still need a key; see the last
section.

## Client and auth

- **Base URL** `https://api.meta.ai/v1`, OpenAI-compatible. The official
  examples build the client exactly like this:
  ([quickstart](https://dev.meta.ai/docs/quickstart))

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url="https://api.meta.ai/v1",
    api_key=os.environ["MODEL_API_KEY"],
)
```

- **Auth** is `Authorization: Bearer $MODEL_API_KEY`.
  ([authentication](https://dev.meta.ai/docs/authentication))
- **List models** with `GET /v1/models`; fail clearly at start-up if the
  configured model is missing.
  ([list models](https://dev.meta.ai/docs/api-reference/models/list-models))

## Models and prices

- `muse-spark-1.3` is the newest Muse Spark, tuned for agentic work.
  Older: `muse-spark-1.2`, `muse-spark-1.1`.
  ([models](https://dev.meta.ai/docs/models))
- Standard tier, per million tokens: input $1.25, output $4.25, cached
  input $0.15.
  ([pricing](https://dev.meta.ai/docs/pricing-rate-limits#muse-spark-pricing))
- The Contributor variants (`muse-spark-1.3-contributor`,
  `muse-spark-1.2-contributor`) cost less but let Meta train on prompts and
  completions. The builder uses Standard only.
  ([contributor mode](https://dev.meta.ai/docs/pricing-rate-limits#contributor-mode))
- Context window: 1,048,576 tokens.

## Tool calling

- Chat Completions defines a tool as
  `{type: "function", function: {name, description, parameters}}`, where
  `parameters` is a JSON Schema. The model answers with a `tool_calls`
  array on the assistant message; the array can hold several calls, which
  is how parallel calls come back. We run each call and return one tool
  message per call.
  ([tool calling](https://dev.meta.ai/docs/tool-calling))
- Meta recommends the Responses API for agent loops (it keeps reasoning
  across tool turns), but tool calling is fully supported on Chat
  Completions. The builder's client supports both endpoints; the pipeline
  picks per stage.

```python
response = client.chat.completions.create(
    model="muse-spark-1.3",
    messages=[{"role": "user", "content": "What is 12 * 13?"}],
    tools=[
        {
            "type": "function",
            "function": {
                "name": "multiply",
                "description": "Multiply two numbers.",
                "parameters": {
                    "type": "object",
                    "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                    "required": ["a", "b"],
                    "additionalProperties": False,
                },
            },
        }
    ],
)
print(response.choices[0].message.tool_calls)
```

## Structured output

- Chat Completions enforces a full JSON Schema, not just JSON mode. Set
  `response_format` to `{type: "json_schema", json_schema: {name, schema}}`
  and decoding is constrained to match it exactly.
  ([structured output](https://dev.meta.ai/docs/structured-output))
- Limitation found live: schemas with `$ref` properties cannot be used
  this way. The region schema's `animals` (an object of optional `$ref`
  arrays, and similar shapes like `fish`) is silently dropped from
  constrained output, even when explicitly asked for; without the
  constraint the same prompt writes it. The animal schema hits the same
  wall: its `$ref` `diet` vanished from 18 straight drafts, each failing
  the checker identically. Both stages therefore generate plain JSON
  and validate against the world schemas in code. The small
  builder-owned schemas (species plan) work fine constrained.

```python
response = client.chat.completions.create(
    model="muse-spark-1.3",
    messages=[{"role": "user", "content": "Bag End, Hobbiton"}],
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
)
```

## Search grounding

- Add the built-in tool `{"type": "web_search"}` to a **Responses API**
  request. Sources come back as `url_citation` annotations on the
  `output_text` content parts (span plus title and URL); raw hits are opt-in with
  `include: ["web_search_call.results"]`.
  ([search grounding](https://dev.meta.ai/docs/search-grounding))
- The docs show `web_search` on Responses only. Until a live call proves
  otherwise, the survey stage uses Responses.
- Context size is tunable (low, medium, high); more context means more
  sources. No per-request search cap is documented, so the pipeline
  enforces its own survey budget (80 searches).
- Price: $2.50 per 1,000 search queries, plus tokens.
  ([pricing](https://dev.meta.ai/docs/pricing-rate-limits#muse-spark-pricing))

```python
response = client.responses.create(
    model="muse-spark-1.3",
    input="Who won the most recent Formula 1 race? Cite your source.",
    tools=[{"type": "web_search"}],
)
for block in response.output:
    for part in getattr(block, "content", []) or []:
        for ann in getattr(part, "annotations", []) or []:
            if ann.type == "url_citation":
                print(part.text[ann.start_index : ann.end_index], "-", ann.url)
```

## Image understanding

- Fully supported on Chat Completions: inline `image_url` blocks (http
  links or base64 data URLs) or `input_file` references after a Files API
  upload. Several images go in one request as several blocks.
  ([image understanding](https://dev.meta.ai/docs/image-understanding))
- Limits, enforced: at most **50 images per request** (more returns HTTP
  400); at most **50 MB per inline image**, 1 GiB per image via the Files
  API. Payload size and context window bound requests too.
- Image cost scales with resolution through input tokens; there is no
  flat per-image price. Read `usage` from every response.

## Reasoning

- Set `reasoning_effort` at the top level on Chat Completions
  (`reasoning.effort` on Responses). Levels run from `minimal` to `max`;
  `none` returns HTTP 400 on Muse Spark.
  ([reasoning](https://dev.meta.ai/docs/reasoning))
- Reasoning tokens are billed as output tokens and count toward the
  output budget (`max_tokens`). Budget for them in every estimate.
- Verified live: Chat Completions accepts `max_tokens` and the
  Responses API accepts `max_output_tokens`. The client sends one on
  every call, so each call's worst-case cost is known before it goes.

```python
response = client.chat.completions.create(
    model="muse-spark-1.3",
    reasoning_effort="high",
    messages=[{"role": "user", "content": "Prove the square root of 2 is irrational."}],
)
```

## SAM 3.1

- Model id `sam-3.1`, called over `POST /v1/responses`: media plus a
  text prompt, streaming back a box and a mask per object.
  ([segmenting](https://dev.meta.ai/docs/sam/segmenting))
- Masks arrive as binary rasters in a compact wire encoding
  (height, width, then a lossless or one-bit payload); the client
  libraries decode them to 0/1 arrays for measuring in Python.
  ([reading segmentation](https://dev.meta.ai/docs/sam/reading-segmentation))
- Price: $2.50 per 1,000 images, $0.20 per 1,000 video frames.
  Limits: 3,000 requests per minute, no token cap.
  ([pricing](https://dev.meta.ai/docs/pricing-rate-limits))

## Rate limits and retries

- Limits apply per team, not per key. Muse Spark 1.3: 3,000 requests
  and 4,000,000 tokens per minute. Past the limit the API rejects the
  request; it succeeds again once usage drops.
  ([pricing and rate limits](https://dev.meta.ai/docs/pricing-rate-limits))
- The docs say to retry 429s with exponential backoff, which is what the
  builder's client does (429 and 5xx, at most 5 tries, then the step
  fails cleanly).

## Open-weight models: Baseten serves the live builder

`BUILDER_OPEN_MODEL_HOST` stays `meta` for development: every call runs
on the Meta Model API. With `baseten`, all chat (planning, file writing,
render reviews, child-safety review, translations) runs on Muse Spark
Open on Baseten; only search grounding, image drawing and SAM 3.1 stay
on Meta. Tool calling and JSON-schema output were tried there and work.

- **Baseten, confirmed 2026-10-27 with live calls**
  ([model page](https://www.baseten.co/library/muse-spark-open/)):
  base URL `https://inference.baseten.co/v1`, model
  `meta-models/muse-spark-open-1026` (the hackathon id; the docs show
  the unpinned one). Auth is `Authorization: Api-Key $BASETEN_API_KEY`,
  not Bearer. Prices, per million tokens: input $1.25, output $4.25,
  cached input $0.15.
- **It reasons first, and the reasoning counts against `max_tokens`.**
  A two-image review burned 2,000 output tokens before answering, so
  open calls default to 8,000 (that review used 4,073); translations
  pass 1,500 with `chat_template_kwargs: {"reasoning_effort": "low"}`.
  An empty `content` is a failure: the caller retries once, then the
  job stops cleanly.
- The reasoning comes back in `message.reasoning_content`. The client
  never reads it: only `content` reaches callers, and the log holds
  usage alone, never text.
- **Money:** Baseten spend has its own pool ($100 of the $100 credit),
  so a runaway there can't touch the Meta budget. Below $20 left it
  warns on stderr (an admin-panel item lands with the panel). When
  Baseten refuses, the call falls back to the Meta Model API inside
  the Meta caps ($8 a job, $20 a day, $80 total); past those, builds
  wait. Every log line records which host ran it.
- **Dropped (#103):** the Fireworks host is gone; no host takes a
  configured URL, so every model call is Meta-only.
- **Modal:** refused until the model names are confirmed.

## Verified with live calls

`tests/test_meta_live.py` (opt-in, needs `MODEL_API_KEY`) proves one real
call each: chat with tools, structured output, search grounding with
citations, image input, and a SAM 3.1 segmentation stream. Baseten
(needs `BASETEN_API_KEY`) proves a translation and a review-shaped
call with an image and a JSON schema. Still open:

- `web_search` on Chat Completions: untested. If it works, the survey can
  use one endpoint with everything else.
