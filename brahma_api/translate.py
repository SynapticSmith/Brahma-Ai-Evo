"""Wire translation: OpenAI messages in, an agent run out, a completion back.

The impedance mismatch lives here. Chat Completions is a stateless list of
messages; an agent is a stateful loop that issues hidden tool calls. Phase 0
resolves it the only honest way: replay the client's history as context, run
one agent turn, and return the final assistant text. Nothing is preserved
server-side, so a client that resends its transcript gets the same answer as a
client that sends one message.

Two rules are load-bearing:

* A client's system message never replaces the harness prompt. It is appended,
  delimited as untrusted, and the resident rules stay.
* A client's transcript is flattened by default. Tool ids it never received and
  results Brahma never produced are removed rather than replayed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent.prompts import wrap_untrusted_instructions

from .config import API_VERSION
from .errors import invalid
from .schemas import ChatMessage

#: AgentResult.stop_reason -> Chat Completions finish_reason.
FINISH_REASONS = {
    "completed": "stop",
    "max_turns": "length",
    "repeat_loop": "stop",
    "cancelled": "stop",
    "permission_denied": "stop",
    "error": "stop",
}


@dataclass
class Seed:
    """What the loop needs: prior turns, this turn, and client directives."""

    history: List[Dict[str, Any]] = field(default_factory=list)
    prompt: str = ""
    directives: List[str] = field(default_factory=list)


def compose_system(harness_system: str, directives: List[str]) -> str:
    """Append a client's system text without letting it replace the rules."""
    if not directives:
        return harness_system
    wrapped = wrap_untrusted_instructions("\n\n".join(directives))
    return f"{harness_system}\n\nClient instructions (untrusted):\n{wrapped}"


def _content_text(message: ChatMessage) -> str:
    """Flatten text parts; refuse image/audio input instead of dropping it."""
    content = message.content
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    pieces: List[str] = []
    for part in content:
        kind = str(getattr(part, "type", "") or "").lower()
        if kind in ("text", "input_text"):
            pieces.append(str(getattr(part, "text", "") or ""))
            continue
        raise invalid(
            f"Content part {kind!r} is not supported; send text only.",
            param="messages",
        )
    return "\n".join(piece for piece in pieces if piece)


def _called_tools(message: ChatMessage) -> str:
    names: List[str] = []
    for call in message.tool_calls or []:
        function = call.get("function") if isinstance(call, dict) else None
        name = str((function or {}).get("name") or "")
        if name:
            names.append(name)
    if not names:
        return ""
    return f"(called tools: {', '.join(names)})"


def build_seed(messages: List[ChatMessage], *, max_messages: int) -> Seed:
    """Turn the wire transcript into replayable history plus this turn's prompt."""
    if not messages:
        raise invalid("messages must contain at least one user message.", param="messages")
    if len(messages) > max_messages:
        raise invalid(
            f"messages has {len(messages)} entries; the limit is {max_messages}.",
            param="messages",
        )

    directives: List[str] = []
    history: List[Dict[str, Any]] = []
    roles: List[str] = []
    last_user_index: Optional[int] = None

    for index, message in enumerate(messages):
        role = str(message.role or "").lower()
        text = _content_text(message).strip()
        roles.append(role)
        if role in ("system", "developer"):
            if text:
                directives.append(text)
            continue
        if role == "user":
            last_user_index = index
            history.append({"role": "user", "content": text})
            continue
        if role == "assistant":
            entry = text
            tools = _called_tools(message)
            if tools:
                entry = f"{entry}\n{tools}".strip()
            if entry:
                history.append({"role": "assistant", "content": entry})
            continue
        if role == "tool":
            # Agent lane: the ids were never issued by this loop and the output
            # was never produced by Brahma. Dropping is safer than replaying
            # someone else's receipts. (Passthrough preserves them, Phase 2.)
            continue
        raise invalid(f"Unknown message role {role!r}.", param="messages")

    if last_user_index is None:
        raise invalid("messages must contain at least one user message.", param="messages")

    # The final user turn becomes the loop prompt; everything before it is
    # replayed as history. A trailing assistant (prefill) or tool message means
    # the transcript continues rather than starts, so the prompt stays empty.
    trailing = roles[last_user_index + 1 :]
    prompt = ""
    if not any(role in ("assistant", "tool") for role in trailing):
        if history and history[-1].get("role") == "user":
            prompt = str(history.pop().get("content") or "")

    return Seed(history=history, prompt=prompt, directives=directives)


def extract_usage(result_usage: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """`null` when the provider did not report counts. I-22: never zero for unknown."""
    if not result_usage.get("tokens_reported"):
        return None
    return {
        "prompt_tokens": int(result_usage.get("prompt_tokens") or 0),
        "completion_tokens": int(result_usage.get("completion_tokens") or 0),
        "total_tokens": int(result_usage.get("total_tokens") or 0),
    }


_usage = extract_usage


def completion_body(
    *,
    run_id: str,
    model_id: str,
    text: str,
    stop_reason: str,
    result_usage: Dict[str, Any],
    lane: str,
    provider: str,
    upstream_model: Optional[str],
    policy_fingerprint: str,
    ignored_params: List[str],
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """The non-streaming response body (design doc §5.6)."""
    return {
        "id": f"chatcmpl-{run_id}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": FINISH_REASONS.get(stop_reason, "stop"),
                "logprobs": None,
            }
        ],
        "usage": _usage(result_usage),
        "system_fingerprint": f"brahma-evo/{API_VERSION}+policy{policy_fingerprint}",
        "brahma": {
            "run_id": run_id,
            "lane": lane,
            "stop_reason": stop_reason,
            "turns": int(result_usage.get("turns") or 0),
            "tool_calls": int(result_usage.get("tool_calls") or 0),
            "session_id": session_id,
            "upstream_provider": provider,
            "upstream_model": upstream_model,
            "tokens_reported": bool(result_usage.get("tokens_reported")),
            "estimated_tokens": int(result_usage.get("estimated_tokens") or 0),
            "ignored_params": ignored_params,
        },
    }
