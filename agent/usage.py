"""Provider-reported token usage, normalised.

``AgentLoop.usage["estimated_tokens"]`` is a local estimate from the transcript
size. That is enough to trigger compaction, but it cannot answer "what did this
run cost?". Providers already return exact counts; this module reads them
without inventing anything.

Three shapes are accepted:

* OpenAI-compatible — ``{"usage": {"prompt_tokens": 12, ...}}``
* Gemini — ``{"usage_metadata": {"prompt_token_count": 12, ...}}``
* SDK objects with attributes instead of dict keys (``response.usage``).

The result is ``{}`` when the provider reported nothing, so callers can tell
"zero tokens" from "unknown".
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

_PROMPT_KEYS = ("prompt_tokens", "input_tokens", "prompt_token_count", "promptTokenCount")
_COMPLETION_KEYS = (
    "completion_tokens",
    "output_tokens",
    "candidates_token_count",
    "candidatesTokenCount",
)
_TOTAL_KEYS = ("total_tokens", "total_token_count", "totalTokenCount")

_USAGE_CONTAINERS = ("usage", "usage_metadata", "usageMetadata")


def _get(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _as_count(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _first(container: Any, keys: tuple) -> Optional[int]:
    for key in keys:
        count = _as_count(_get(container, key))
        if count is not None:
            return count
    return None


def extract_usage(response: Any) -> Dict[str, int]:
    """Return ``prompt_tokens``/``completion_tokens``/``total_tokens`` or ``{}``.

    Totals are taken as reported. Only when the provider omitted the total but
    gave both halves is the sum used; otherwise the total is left out rather
    than guessed.
    """
    container = None
    for key in _USAGE_CONTAINERS:
        candidate = _get(response, key)
        if candidate is not None:
            container = candidate
            break
    if container is None:
        return {}

    prompt = _first(container, _PROMPT_KEYS)
    completion = _first(container, _COMPLETION_KEYS)
    total = _first(container, _TOTAL_KEYS)
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    if prompt is None and completion is None and total is None:
        return {}

    usage: Dict[str, int] = {}
    if prompt is not None:
        usage["prompt_tokens"] = prompt
    if completion is not None:
        usage["completion_tokens"] = completion
    if total is not None:
        usage["total_tokens"] = total
    return usage


def add_usage(total: Dict[str, int], reported: Mapping[str, Any]) -> None:
    """Accumulate one response's counts into a running total (in place)."""
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = _as_count(reported.get(key))
        if value is not None:
            total[key] = total.get(key, 0) + value
