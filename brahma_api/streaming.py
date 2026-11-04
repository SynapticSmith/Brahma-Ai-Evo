"""The SSE streaming encoder: chunks, comments, keepalive, and [DONE].

Chat Completions streaming is text/event-stream where every event is a `data:`
line carrying a JSON `chat.completion.chunk`. Clients parse only `data:` lines;
lines beginning with `:` are SSE comments and are ignored by spec-compliant
parsers. This is the only safe side-channel on an OpenAI-shaped stream (design
doc §5.7).

Three contracts are mandatory:
1. `choices: []` on the usage chunk (OpenAI standard; OpenRouter's choice of
   choices[0] breaks clients that index [0] unconditionally).
2. [DONE] is always emitted as the final line -- on success, denial, or error.
3. Keepalive comments (: keepalive) prevent proxy timeouts during tool runs.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional


def sse_data(payload: Any) -> str:
    """Format one SSE data event: `data: <json>\\n\\n`."""
    if isinstance(payload, str):
        raw = payload
    else:
        raw = json.dumps(payload, separators=(",", ":"))
    return f"data: {raw}\n\n"


def sse_comment(comment: str) -> str:
    """Format one SSE comment line: `: <comment>\\n\\n`."""
    return f": {comment}\n\n"


def sse_done() -> str:
    """The standard stream terminator: `data: [DONE]\\n\\n`."""
    return "data: [DONE]\n\n"


def initial_role_chunk(
    *,
    run_id: str,
    model_id: str,
    created: Optional[int] = None,
    system_fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    """The first chunk opening the assistant response."""
    return {
        "id": f"chatcmpl-{run_id}",
        "object": "chat.completion.chunk",
        "created": created or int(time.time()),
        "model": model_id,
        "system_fingerprint": system_fingerprint,
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": ""},
                "finish_reason": None,
                "logprobs": None,
            }
        ],
    }


def text_delta_chunk(
    *,
    run_id: str,
    model_id: str,
    content: str,
    created: Optional[int] = None,
    system_fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    """One incremental text content delta."""
    return {
        "id": f"chatcmpl-{run_id}",
        "object": "chat.completion.chunk",
        "created": created or int(time.time()),
        "model": model_id,
        "system_fingerprint": system_fingerprint,
        "choices": [
            {
                "index": 0,
                "delta": {"content": content},
                "finish_reason": None,
                "logprobs": None,
            }
        ],
    }


def tool_calls_delta_chunk(
    *,
    run_id: str,
    model_id: str,
    tool_calls: List[Dict[str, Any]],
    created: Optional[int] = None,
    system_fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    """Delta chunk carrying tool_calls fragments."""
    return {
        "id": f"chatcmpl-{run_id}",
        "object": "chat.completion.chunk",
        "created": created or int(time.time()),
        "model": model_id,
        "system_fingerprint": system_fingerprint,
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": tool_calls},
                "finish_reason": None,
                "logprobs": None,
            }
        ],
    }


def finish_chunk(
    *,
    run_id: str,
    model_id: str,
    finish_reason: str = "stop",
    created: Optional[int] = None,
    system_fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    """The final chunk marking choice completion."""
    return {
        "id": f"chatcmpl-{run_id}",
        "object": "chat.completion.chunk",
        "created": created or int(time.time()),
        "model": model_id,
        "system_fingerprint": system_fingerprint,
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": finish_reason,
                "logprobs": None,
            }
        ],
    }


def usage_chunk(
    *,
    run_id: str,
    model_id: str,
    usage: Optional[Dict[str, int]],
    created: Optional[int] = None,
    system_fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    """The usage chunk with choices: [] per the OpenAI wire spec."""
    return {
        "id": f"chatcmpl-{run_id}",
        "object": "chat.completion.chunk",
        "created": created or int(time.time()),
        "model": model_id,
        "system_fingerprint": system_fingerprint,
        "choices": [],
        "usage": usage,
    }


def stream_error_chunk(
    message: str,
    *,
    type: str = "invalid_request_error",
    param: Optional[str] = None,
    code: Optional[str] = None,
) -> Dict[str, Any]:
    """An in-band error chunk emitted before [DONE]."""
    return {
        "error": {
            "message": message,
            "type": type,
            "param": param,
            "code": code,
        }
    }


def split_text_chunks(text: str, chunk_size: int = 64) -> List[str]:
    """Split buffered text into manageable deltas for smooth delivery."""
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
