"""Provider adapters for the loop and the CLI.

The loop only knows one shape::

    complete(messages, tool_schemas) -> assistant message dict

Everything provider-specific lives here. That is the second half of decoupling
(the first is :mod:`agent.spec`): the harness does not care whether the model
is Ollama on localhost, a custom OpenAI-compatible endpoint, or OpenRouter.

``auto`` resolution order is local -> custom -> openrouter. Local first is
deliberate: an agent loop makes many calls per task, and the cheapest one that
works should win.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional

Completer = Callable[[List[Dict[str, Any]], List[Dict[str, Any]]], Dict[str, Any]]

PROVIDERS = ("auto", "local", "custom", "openrouter", "stub")


class ProviderUnavailable(RuntimeError):
    pass


def _has_custom() -> bool:
    try:
        from llm_client import ai_client

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

        return complete_local

    if pid == "custom":
        from llm_client import ai_client

        def complete_custom(messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
            return ai_client.chat_as_completion(
                "custom",
                "",
                full_messages=messages,
                tools=tools or None,
                temperature=temperature,
            )

        return complete_custom

    if pid == "openrouter":
        # OpenRouter is OpenAI-compatible, but ``chat_as_completion`` walks it
        # through or_client, which drops the tools array. Use the shared
        # OpenAI transport directly so tool calling survives.
        import or_client
        from llm_client import ai_client

        def complete_openrouter(messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
            message = ai_client._openai_chat_completion(  # noqa: SLF001 - documented transport reuse
                messages,
                url=or_client.API_URL,
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
        from llm_client import ai_client

        detail["endpoint"] = getattr(ai_client, "_custom_url", "")
        detail["model"] = getattr(ai_client, "_custom_model", "")
    elif pid == "openrouter":
        import or_client

        detail["endpoint"] = or_client.API_URL
        detail["model"] = or_client.TEXT_MODELS[0]
    return detail


def dumps(message: Any) -> str:  # pragma: no cover - debugging helper
    return json.dumps(message, indent=2, ensure_ascii=False, default=str)
