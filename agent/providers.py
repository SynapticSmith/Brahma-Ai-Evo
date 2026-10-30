"""Provider adapters for the loop and the CLI.

The loop only knows one shape::

    complete(messages, tool_schemas) -> assistant message dict

Everything provider-specific lives here. That is the second half of decoupling
(the first is :mod:`agent.spec`): the harness does not care whether the model
is Ollama on localhost, a custom OpenAI-compatible endpoint, or OpenRouter.

A completer *may* also expose a ``stream`` attribute::

    complete.stream(messages, tool_schemas, on_delta) -> assistant message dict

The loop uses it only when streaming was requested, so the two-argument shape
stays the contract and a third-party completer without ``stream`` still works
(the loop emits one ``stream_fallback`` event and calls it normally).

``auto`` resolution order is local -> custom -> openrouter. Local first is
deliberate: an agent loop makes many calls per task, and the cheapest one that
works should win.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional

from .streaming import collect, openai_stream

Completer = Callable[[List[Dict[str, Any]], List[Dict[str, Any]]], Dict[str, Any]]

PROVIDERS = ("auto", "local", "custom", "openrouter", "stub")


def _endpoint_base(url: str) -> str:
    """A base URL even when the caller configured the full chat endpoint.

    ``or_client.API_URL`` is ``.../chat/completions`` while the shared
    OpenAI transport appends that path itself; without this the OpenRouter
    agent path requested ``.../chat/completions/chat/completions``.
    """
    base = (url or "").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    return base


def _chunked(text: str, size: int = 24) -> List[str]:
    return [text[index : index + size] for index in range(0, len(text), size)] or [""]


class ProviderUnavailable(RuntimeError):
    pass


def _has_custom() -> bool:
    try:
        from llm_client import client as ai_client

        ai_client.reload_settings()
        return bool(getattr(ai_client, "_custom_url", "") and getattr(ai_client, "_custom_model", ""))
    except Exception:
        return False


def _has_openrouter() -> bool:
    try:
        import or_client

        return bool(or_client._load_api_key())
    except Exception:
        return False


def resolve_provider(provider: str = "auto") -> str:
    requested = (provider or os.getenv("BRAHMA_AGENT_PROVIDER") or "auto").strip().lower()
    if requested not in PROVIDERS:
        raise ProviderUnavailable(f"unknown provider {requested!r}; choose from {PROVIDERS}")
    if requested != "auto":
        return requested
    try:
        from core.local_brain import local_brain

        if local_brain.is_available():
            return "local"
    except Exception:
        pass
    if _has_custom():
        return "custom"
    if _has_openrouter():
        return "openrouter"
    raise ProviderUnavailable(
        "No model provider is reachable. Start a local OpenAI-compatible server "
        "(default http://localhost:11434/v1), configure a custom endpoint in the "
        "app, or set an OpenRouter key."
    )


def custom_stream(
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    on_delta: Optional[Callable[[Dict[str, Any]], None]],
    *,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """Stream one completion from the configured custom endpoint.

    Shared by the CLI adapter and the GUI text path so the two cannot drift:
    both build the same payload and normalise a URL that may already end in
    ``/chat/completions``. Settings are reloaded per call, matching
    ``chat_as_completion`` — a saved endpoint change needs no restart.
    """
    from llm_client import client as ai_client

    ai_client.reload_settings()
    payload: Dict[str, Any] = {
        "model": ai_client._custom_model,  # noqa: SLF001 - configured endpoint
        "messages": messages,
        "temperature": temperature,
        "stream": True,
        # Ask for the final usage chunk; servers that do not support it
        # ignore the field, and usage simply stays unknown.
        "stream_options": {"include_usage": True},
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if tools:
        payload["tools"] = tools
    url = f"{_endpoint_base(ai_client._custom_url)}/chat/completions"  # noqa: SLF001
    headers = ai_client._auth_headers(ai_client._custom_api_key)  # noqa: SLF001
    return collect(openai_stream(url, payload, headers=headers), on_delta)


def build_completer(
    provider: str = "auto",
    model: Optional[str] = None,
    *,
    temperature: float = 0.2,
) -> Completer:
    """Return a loop-compatible completer for the requested provider."""
    pid = resolve_provider(provider)

    if pid == "local":
        from core.local_brain import local_brain

        def complete_local(messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
            return local_brain.chat_complete(
                messages,
                model=model or local_brain.default_model,
                tools=tools or None,
                temperature=temperature,
            )

        def stream_local(messages, tools, on_delta):
            chunks = local_brain.generate_chat_stream(
                messages,
                model=model or local_brain.default_model,
                tools=tools or None,
                temperature=temperature,
            )
            return collect(chunks, on_delta)

        complete_local.stream = stream_local
        return complete_local

    if pid == "custom":
        from llm_client import client as ai_client

        def complete_custom(messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
            return ai_client.chat_as_completion(
                "custom",
                "",
                full_messages=messages,
                tools=tools or None,
                temperature=temperature,
            )

        def stream_custom(messages, tools, on_delta):
            return custom_stream(
                messages, tools, on_delta, temperature=temperature
            )

        complete_custom.stream = stream_custom
        return complete_custom

    if pid == "openrouter":
        # OpenRouter is OpenAI-compatible, but ``chat_as_completion`` walks it
        # through or_client, which drops the tools array. Use the shared
        # OpenAI transport directly so tool calling survives.
        import or_client
        from llm_client import client as ai_client

        def complete_openrouter(messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
            message = ai_client._openai_chat_completion(  # noqa: SLF001 - documented transport reuse
                messages,
                url=_endpoint_base(or_client.API_URL),
                model=model or or_client.TEXT_MODELS[0],
                api_key=or_client._load_api_key(),  # noqa: SLF001
                temperature=temperature,
                max_tokens=8192,
                label="OpenRouter (agent)",
                tools=tools or None,
                return_message=True,
            )
            if not message:
                raise ProviderUnavailable("OpenRouter returned no assistant message.")
            return message

        def stream_openrouter(messages, tools, on_delta):
            payload: Dict[str, Any] = {
                "model": model or or_client.TEXT_MODELS[0],
                "messages": messages,
                "temperature": temperature,
                "max_tokens": 8192,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if tools:
                payload["tools"] = tools
            headers = {"Authorization": f"Bearer {or_client._load_api_key()}"}  # noqa: SLF001
            url = f"{_endpoint_base(or_client.API_URL)}/chat/completions"
            return collect(openai_stream(url, payload, headers=headers), on_delta)

        complete_openrouter.stream = stream_openrouter
        return complete_openrouter

    if pid == "stub":
        def complete_stub(messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
            prompt = ""
            for message in reversed(messages):
                if message.get("role") == "user":
                    prompt = str(message.get("content") or "")
                    break
            return {
                "role": "assistant",
                "content": f"[stub provider] tools={len(tools or [])} prompt={prompt[:200]!r}",
            }

        def stream_stub(messages, tools, on_delta):
            message = complete_stub(messages, tools)
            for piece in _chunked(str(message.get("content") or "")):
                if on_delta is not None:
                    on_delta({"kind": "text", "text": piece})
            return message

        complete_stub.stream = stream_stub
        return complete_stub

    raise ProviderUnavailable(f"provider {pid!r} is not implemented")


def build_summarizer(completer: Completer) -> Callable[[str], str]:
    """A compaction summariser backed by the same model, minus tools.

    The prompt asks for facts that make resumption possible, not prose. If the
    call fails the caller falls back to the deterministic summary in
    :mod:`agent.context`.
    """

    def summarise(rendered: str) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    "Compress this agent transcript into the minimum needed to continue the task. "
                    "Keep: the user's goal, decisions made, files created or edited (paths), "
                    "commands run and their verdicts, and what is still unfinished. "
                    "Drop pleasantries and repeated tool output. Use terse bullet points."
                ),
            },
            {"role": "user", "content": rendered},
        ]
        response = completer(messages, [])
        if isinstance(response, dict) and "choices" in response:
            content = (response["choices"][0].get("message") or {}).get("content") or ""
        elif isinstance(response, dict):
            content = response.get("content") or ""
        else:
            content = str(response or "")
        return str(content).strip()

    return summarise


def describe(provider: str = "auto") -> Dict[str, Any]:
    pid = resolve_provider(provider)
    detail: Dict[str, Any] = {"provider": pid}
    if pid == "local":
        from core.local_brain import local_brain

        detail["endpoint"] = getattr(local_brain, "endpoint", "")
        detail["model"] = getattr(local_brain, "default_model", "")
    elif pid == "custom":
        from llm_client import client as ai_client

        detail["endpoint"] = getattr(ai_client, "_custom_url", "")
        detail["model"] = getattr(ai_client, "_custom_model", "")
    elif pid == "openrouter":
        import or_client

        detail["endpoint"] = or_client.API_URL
        detail["model"] = or_client.TEXT_MODELS[0]
    return detail


def dumps(message: Any) -> str:  # pragma: no cover - debugging helper
    return json.dumps(message, indent=2, ensure_ascii=False, default=str)
