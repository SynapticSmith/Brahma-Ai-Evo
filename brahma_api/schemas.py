"""Request models and the fatal-parameter checks.

Pydantic validates shape. It does not decide which parameters Brahma can
honour, because "unknown to us" and "impossible for us" are different: a client
may send a field a later phase implements, and rejecting it now would be a
compatibility bug. ``validate_request`` is the explicit list of parameters whose
semantics we cannot fake (design doc §5.5).

``extra="allow"`` is deliberate. OpenAI adds fields; a strict schema would turn
every new client into a 400.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from .errors import invalid


class ContentPart(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str = "text"
    text: Optional[str] = None


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: Optional[Union[str, List[ContentPart]]] = None
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = ""
    messages: List[ChatMessage] = Field(default_factory=list)
    stream: bool = False
    stream_options: Optional[Dict[str, Any]] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    n: Optional[int] = None
    max_tokens: Optional[int] = None
    max_completion_tokens: Optional[int] = None
    stop: Any = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Any = None
    parallel_tool_calls: Optional[bool] = None
    response_format: Optional[Dict[str, Any]] = None
    logprobs: Optional[bool] = None
    top_logprobs: Optional[int] = None
    audio: Any = None
    modalities: Optional[List[str]] = None
    seed: Optional[int] = None
    user: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


#: Parameters accepted but not implemented in Phase 0. They are echoed in
#: ``brahma.ignored_params`` rather than silently dropped, because "accepted and
#: ignored" is the failure mode that makes compatibility a lie.
ADVISORY_PHASE0 = ("max_tokens", "max_completion_tokens", "top_p", "presence_penalty",
                   "frequency_penalty", "logit_bias", "service_tier", "seed", "user",
                   "metadata")


def validate_request(request: ChatRequest, *, tools_enabled: bool) -> List[str]:
    """Raise ``ApiError`` for fatal parameters; return the advisory ones used.

    Failing is the honest answer when the semantics cannot be honoured. The
    alternative -- accepting `n=3` and returning one choice -- is the bug that
    makes a client silently misbehave one line after a 200.
    """
    if request.n is not None and request.n != 1:
        raise invalid(
            f"n={request.n} is not supported; this model returns one choice.",
            param="n",
        )
    if request.logprobs:
        raise invalid("logprobs are not supported.", param="logprobs")
    if request.top_logprobs is not None:
        raise invalid("top_logprobs is not supported.", param="top_logprobs")
    if request.audio is not None:
        raise invalid("audio output is not supported.", param="audio")
    if request.modalities and request.modalities != ["text"]:
        raise invalid(
            "only text output is supported.", param="modalities"
        )
    if request.response_format and request.response_format.get("type") not in (None, "text"):
        raise invalid(
            "response_format is not supported; ask for the shape in the prompt.",
            param="response_format",
        )
    if request.stop not in (None, "", []):
        raise invalid(
            "stop sequences are not supported; the agent decides when to stop.",
            param="stop",
        )
    if request.stream:
        raise invalid(
            "streaming is not enabled in this build; send stream=false.",
            param="stream",
        )
    if request.tools:
        if not tools_enabled:
            raise invalid(
                "This model runs its own tools and cannot accept client tools. "
                "Use model 'brahma-evo:passthrough' (Phase 2) to supply your own.",
                param="tools",
                code="tools_not_supported",
            )
        raise invalid(
            "Client tools are not supported in the agent lane.",
            param="tools",
            code="tools_not_supported",
        )
    if request.tool_choice is not None and not request.tools:
        raise invalid(
            "tool_choice requires tools, and this model does not accept them.",
            param="tool_choice",
        )
    if request.parallel_tool_calls:
        raise invalid(
            "parallel_tool_calls is not supported in the agent lane.",
            param="parallel_tool_calls",
        )

    used: List[str] = []
    for name in ADVISORY_PHASE0:
        if getattr(request, name, None) is not None:
            used.append(name)
    return used


__all__ = ["ChatMessage", "ChatRequest", "ContentPart", "validate_request"]
