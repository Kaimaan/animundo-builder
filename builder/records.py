"""Record and replay model calls, per stage, as JSON lines.

`builder build --record` and `builder stage --record` wrap the live
client in a RecordingClient: every answer lands in
`records/<stage>.jsonl` next to what the stage wrote. `--replay`
runs the same stages on a ReplayClient instead: no key, no
network to a model host, no cost. Anything else a stage reaches
(GBIF, the world's tools) still runs live and free.

A recording never holds a key or a model's private reasoning:
`reasoning_content` is scrubbed on the way in, so the files are
safe to commit under `examples/`.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .meta import CostLogger, Usage
from .prices import Prices

METHODS = ("chat", "responses_create", "images_generate", "responses_stream")

#: Request strings past this are stored truncated, with their length.
MAX_REQUEST_TEXT = 2000


class ReplayError(Exception):
    """The recording ran out, or no longer matches the code."""


def save_entry(path: Path, entry: dict[str, Any]) -> None:
    """Append one recorded call to its stage file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def load_entries(path: Path) -> list[dict[str, Any]]:
    """Every recorded call of one stage, in order."""
    if not path.is_file():
        return []
    entries = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except ValueError as error:
            raise ReplayError(f"{path}:{lineno} is not JSON: {error}")
    return entries


def dump_response(response: Any) -> Any:
    """A model answer as JSON-safe data, scrubbed of private reasoning."""
    if isinstance(response, bytes):
        return {"base64": base64.b64encode(response).decode("ascii")}
    if hasattr(response, "model_dump"):
        data = response.model_dump(mode="json")
    else:
        data = _to_json(response)
    return _scrub(data)


def load_response(data: Any) -> Any:
    """A recorded answer back into attribute-access shape (or bytes)."""
    if isinstance(data, dict) and set(data) == {"base64"}:
        return base64.b64decode(data["base64"])
    return _to_namespace(data)


def dump_request(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    """The call's arguments, truncated past a sane size."""
    try:
        return {
            "args": _to_json(list(args), MAX_REQUEST_TEXT),
            "kwargs": _to_json(kwargs, MAX_REQUEST_TEXT),
        }
    except (TypeError, ValueError):
        return {"args": [], "kwargs": {}, "unserializable": True}


def _to_json(value: Any, limit: int | None = None) -> Any:
    if isinstance(value, SimpleNamespace):
        return {key: _to_json(item, limit) for key, item in vars(value).items()}
    if isinstance(value, dict):
        return {str(key): _to_json(item, limit) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_json(item, limit) for item in value]
    if isinstance(value, str) and limit is not None and len(value) > limit:
        return value[:limit] + f"…<{len(value)} chars>"
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _to_namespace(value: Any) -> Any:
    if isinstance(value, dict):
        return SimpleNamespace(
            **{key: _to_namespace(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return [_to_namespace(item) for item in value]
    return value


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _scrub(item)
            for key, item in value.items()
            if key != "reasoning_content"
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


class RecordingClient:
    """A live client that writes every answer to its stage file."""

    def __init__(self, inner: Any, records_dir: Path, stage: str = "") -> None:
        self._inner = inner
        self._records_dir = records_dir
        self._stage = stage

    @property
    def model(self) -> str:
        return self._inner.model

    def set_stage(self, stage: str) -> None:
        self._stage = stage

    def for_image(self) -> RecordingClient:
        return RecordingClient(self._inner.for_image(), self._records_dir, self._stage)

    def for_sam(self) -> RecordingClient:
        return RecordingClient(self._inner.for_sam(), self._records_dir, self._stage)

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        response = self._inner.chat(*args, **kwargs)
        self._save("chat", args, kwargs, dump_response(response))
        return response

    def responses_create(self, *args: Any, **kwargs: Any) -> Any:
        response = self._inner.responses_create(*args, **kwargs)
        self._save("responses_create", args, kwargs, dump_response(response))
        return response

    def images_generate(self, *args: Any, **kwargs: Any) -> Any:
        response = self._inner.images_generate(*args, **kwargs)
        self._save("images_generate", args, kwargs, dump_response(response))
        return response

    def responses_stream(self, *args: Any, **kwargs: Any) -> Any:
        events = []
        try:
            for event in self._inner.responses_stream(*args, **kwargs):
                events.append(event)
                yield event
        finally:
            if events:
                self._save(
                    "responses_stream",
                    args,
                    kwargs,
                    [dump_response(event) for event in events],
                )

    def _save(
        self,
        method: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        response: Any,
    ) -> None:
        save_entry(
            self._records_dir / f"{self._stage or 'stage'}.jsonl",
            {
                "method": method,
                "model": getattr(self._inner, "model", "unknown"),
                "host": getattr(self._inner, "host", "meta"),
                "request": dump_request(args, kwargs),
                "response": response,
            },
        )


class ReplayClient:
    """A client that plays recorded answers back: no key, no cost.

    Responses come back in recorded order per stage; a different
    method than recorded, or no entries left, stops the run, while
    a changed request only warns (a season word moves on). Every
    call is logged at $0 with host `replay`.
    """

    replays = True

    def __init__(
        self,
        records_dir: Path,
        stage: str = "",
        logger: CostLogger | None = None,
    ) -> None:
        self._records_dir = records_dir
        self._logger = logger
        self._entries: list[dict[str, Any]] = []
        self._index = 0
        self._leftovers: dict[str, int] = {}
        self._stage = ""
        if stage:
            self.set_stage(stage)

    @property
    def model(self) -> str:
        if self._index < len(self._entries):
            return str(self._entries[self._index].get("model", "replay"))
        return "replay"

    def set_stage(self, stage: str) -> None:
        if stage == self._stage:
            # A fix round re-runs the failed stage: its answers
            # continue where the try left off, never from the
            # top (the recorder appends, so must the replay).
            return
        self._flush()
        self._stage = stage
        path = self._records_dir / f"{stage}.jsonl"
        self._entries = load_entries(path) if path.is_file() else []
        self._index = 0

    def verify_consumed(self) -> None:
        """Every recorded answer was played, or the code drifted."""
        self._flush()
        if self._leftovers:
            detail = ", ".join(
                f"{stage}: {count} unplayed"
                for stage, count in sorted(self._leftovers.items())
            )
            raise ReplayError(f"the recording no longer matches: {detail}")

    def _flush(self) -> None:
        if self._entries and self._index < len(self._entries):
            self._leftovers[self._stage] = len(self._entries) - self._index
        self._entries = []
        self._index = 0

    def for_image(self) -> ReplayClient:
        return self

    def for_sam(self) -> ReplayClient:
        return self

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        return self._play("chat", args, kwargs)

    def responses_create(self, *args: Any, **kwargs: Any) -> Any:
        return self._play("responses_create", args, kwargs)

    def images_generate(self, *args: Any, **kwargs: Any) -> Any:
        return self._play("images_generate", args, kwargs)

    def responses_stream(self, *args: Any, **kwargs: Any) -> Any:
        events = self._play("responses_stream", args, kwargs)
        yield from events

    def _play(self, method: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        if self._index >= len(self._entries):
            raise ReplayError(
                f"[{self._stage}] the recording is out of answers "
                f"at {method} call {self._index + 1}"
            )
        entry = self._entries[self._index]
        self._index += 1
        if entry.get("method") != method:
            raise ReplayError(
                f"[{self._stage}] recorded {entry.get('method')}, "
                f"the code asked {method}"
            )
        if entry.get("request") != dump_request(args, kwargs):
            print(
                f"[{self._stage}] warning: this {method} call differs "
                "from the recorded one; playing the recording anyway",
                file=sys.stderr,
            )
        self._log(method)
        return load_response(entry["response"])

    def _log(self, method: str) -> None:
        if self._logger is None:
            return
        answer = self._entries[self._index - 1]
        self._logger.log(
            method,
            Usage(model=str(answer.get("model", "replay"))),
            Prices(),
            0.0,
            "replayed",
            host="replay",
        )
