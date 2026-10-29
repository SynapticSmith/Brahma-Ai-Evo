"""Incremental model output: assemble stream deltas into one message.

The loop's contract is unchanged — one assistant message per model call — but
a slow local model can take a minute to produce the first token, and a HUD that
shows nothing for a minute looks broken. Streaming changes *when* the text is
visible, not what is stored or sent back.

Two responsibilities live here, both stdlib-only so the harness keeps its
dependency-free CI:

* :class:`StreamAssembler` merges OpenAI-style ``choices[0].delta`` chunks
  (text plus fragmented ``tool_calls``) into the exact message shape
  :func:`agent.loop.normalise_assistant_message` expects. Tool-call fragments
  are merged by ``index`` and their ``arguments`` strings concatenated, because
  that is what the protocol means: each delta carries a *piece* of the JSON.
* :func:`openai_stream` reads a server-sent-event response over ``urllib`` so a
  custom endpoint or OpenRouter can stream without pulling ``requests`` into
  the harness.

The assembler is deliberately strict about what it emits and lenient about
what it accepts: a chunk with no ``choices`` (some servers send a usage-only
tail chunk) updates usage and produces nothing; a ``message`` instead of a
``delta`` is treated as a one-piece delta, which is how a few local runtimes
implement "streaming".
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence

#: One normalised incremental update, handed to the caller's sink.
#: ``{"kind": "text", "text": "..."}`` or
#: ``{"kind": "tool_call", "index": 0, "id": ..., "name": ..., "arguments": ...}``
Delta = Dict[str, Any]
DeltaSink = Callable[[Delta], None]


class StreamError(RuntimeError):
    """A streaming transport failed. Classified by :mod:`agent.retry`."""


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


class StreamAssembler:
    """Accumulate stream chunks into one OpenAI assistant message."""

    def __init__(self) -> None:
        self._text: List[str] = []
        self._calls: Dict[int, Dict[str, Any]] = {}
        self._order: List[int] = []
        #: ``finish_reason`` from the last chunk that carried one.
        self.finish_reason: Optional[str] = None
        #: Provider usage from any chunk that carried it (usually the tail).
        self.usage: Dict[str, Any] = {}

    # -- ingestion ---------------------------------------------------------

    def apply_chunk(self, chunk: Any) -> List[Delta]:
        """Merge one chunk; return the deltas a UI should see, in order."""
        events: List[Delta] = []
        if not isinstance(chunk, Mapping):
            return events

        usage = chunk.get("usage")
        if isinstance(usage, Mapping):
            self.usage = dict(usage)

        error = chunk.get("error")
        if error:
            detail = error.get("message") if isinstance(error, Mapping) else error
            raise StreamError(str(detail)[:400])

        choices = chunk.get("choices")
        if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes)) or not choices:
            return events
        choice = choices[0] if isinstance(choices[0], Mapping) else {}

        finish = choice.get("finish_reason")
        if finish:
            self.finish_reason = str(finish)

        delta = choice.get("delta")
        if not isinstance(delta, Mapping):
            message = choice.get("message")
            delta = message if isinstance(message, Mapping) else {}

        text = _as_text(delta.get("content"))
        if text:
            self._text.append(text)
            events.append({"kind": "text", "text": text})

        fragments = delta.get("tool_calls")
        if isinstance(fragments, Mapping):
            fragments = [fragments]
        if isinstance(fragments, Sequence) and not isinstance(fragments, (str, bytes)):
            for position, fragment in enumerate(fragments):
                if isinstance(fragment, Mapping):
                    events.extend(self._apply_tool_fragment(fragment, position))

        legacy = delta.get("function_call")
        if isinstance(legacy, Mapping):
            events.extend(
                self._apply_tool_fragment(
                    {"index": 0, "function": dict(legacy)}, 0
                )
            )
        return events

    def _apply_tool_fragment(self, fragment: Mapping[str, Any], position: int) -> List[Delta]:
        raw_index = fragment.get("index")
        index = raw_index if isinstance(raw_index, int) and raw_index >= 0 else position
        entry = self._calls.get(index)
        if entry is None:
            entry = {
                "id": "",
                "type": "function",
                "function": {"name": "", "arguments": ""},
            }
            self._calls[index] = entry
            self._order.append(index)

        event: Delta = {"kind": "tool_call", "index": index}

        call_id = _as_text(fragment.get("id"))
        if call_id and call_id != entry["id"]:
            entry["id"] = call_id
            event["id"] = call_id

        function = fragment.get("function")
        if not isinstance(function, Mapping):
            function = {}
        name = _as_text(function.get("name"))
        if name:
            # Deltas are *fragments*. Appending is what the protocol specifies;
            # a server that repeats the whole name (observed in the wild) must
            # not end up with "get_weatherget_weather".
            if name != entry["function"]["name"]:
                entry["function"]["name"] += name
            event["name"] = name

        arguments = _as_text(function.get("arguments"))
        if arguments:
            entry["function"]["arguments"] += arguments
            event["arguments"] = arguments
        return [event]

    # -- result ------------------------------------------------------------

    def message(self) -> Dict[str, Any]:
        """The final assistant message, in the loop's normalised shape."""
        out: Dict[str, Any] = {
            "role": "assistant",
            "content": "".join(self._text),
        }
        if self._order:
            calls = []
            # ``index`` is the protocol's position, so sort by it rather than by
            # first appearance; servers stream them in order, but that is not a
            # guarantee worth depending on.
            for index in sorted(self._order):
                entry = self._calls[index]
                calls.append(
                    {
                        "id": entry["id"],
                        "type": "function",
                        "function": {
                            "name": entry["function"]["name"] or "unknown",
                            "arguments": entry["function"]["arguments"] or "{}",
                        },
                    }
                )
            out["tool_calls"] = calls
        if self.usage:
            out["usage"] = dict(self.usage)
        return out


def collect(chunks: Iterable[Any], on_delta: Optional[DeltaSink] = None) -> Dict[str, Any]:
    """Consume a chunk iterator, forward deltas, return the assistant message."""
    assembler = StreamAssembler()
    for chunk in chunks:
        for delta in assembler.apply_chunk(chunk):
            if on_delta is not None:
                try:
                    on_delta(delta)
                except Exception:
                    # A broken UI must not abort a paid-for model call.
                    pass
    message = assembler.message()
    if assembler.finish_reason:
        message["finish_reason"] = assembler.finish_reason
    return message


# -- transports ------------------------------------------------------------ #


def iter_sse(response: Iterable[bytes]) -> Iterator[Dict[str, Any]]:
    """Parse OpenAI-style server-sent events from a line iterator."""
    for raw in response:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        line = line.strip()
        if not line or line.startswith(":"):
            continue
        # ``event:``/``id:``/``retry:`` fields carry nothing the assembler needs.
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data:
            continue
        if data == "[DONE]":
            return
        try:
            parsed = json.loads(data)
        except ValueError:
            continue
        if isinstance(parsed, Mapping):
            yield dict(parsed)


def _error_body(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", "replace")[:400]
    except Exception:
        return ""


def openai_stream(
    url: str,
    payload: Mapping[str, Any],
    *,
    headers: Optional[Mapping[str, str]] = None,
    timeout: float = 600.0,
) -> Iterator[Dict[str, Any]]:
    """POST ``payload`` and yield parsed SSE chunks.

    The request is opened lazily (generators do not run until iterated), so the
    loop's retry wrapper sees transport failures instead of a silent empty
    stream. HTTP errors carry the response body because that is where an
    OpenAI-compatible server puts the actual reason ("model not found", ...).
    """
    body = json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")
    merged = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    for key, value in (headers or {}).items():
        if value:
            merged[str(key)] = str(value)
    request = urllib.request.Request(url, data=body, headers=merged, method="POST")
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = _error_body(exc)
        raise StreamError(f"HTTP {exc.code} from {url}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise StreamError(f"{url}: {exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise StreamError(f"{url}: {exc}") from exc
    try:
        yield from iter_sse(response)
    finally:
        try:
            response.close()
        except Exception:
            pass
