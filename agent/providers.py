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
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from .streaming import collect, openai_stream

Completer = Callable[[List[Dict[str, Any]], List[Dict[str, Any]]], Dict[str, Any]]

PROVIDERS = ("auto", "gemini", "local", "custom", "openrouter", "stub")

#: Gemini's OpenAI-compatible endpoint. Using the REST shim keeps this adapter
#: stdlib-only (``agent/`` must import with just pytest) and gives the loop the
#: same ``tool_calls`` shape as every other OpenAI-compatible provider.
GEMINI_OPENAI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_TIMEOUT = 300.0
#: Providers this module can actually build. ``provider_router`` may grow a
#: vendor before the harness does; an unknown id is skipped, not guessed.
HARNESS_PROVIDERS = ("gemini", "openrouter", "local", "custom", "stub")


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


def _load_app_settings() -> Dict[str, Any]:
    """Read the GUI settings file without importing PyQt.

    ``memory.config_manager`` is stdlib-only, so this stays safe in the CLI and
    in the harness CI. Any failure means "no preference", not "no provider".
    """
    try:
        from memory import config_manager

        data = config_manager.load_settings()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _provider_id_for_label(name: Any) -> Optional[str]:
    """Map ``default_ai_provider`` display names to router ids.

    Settings stores labels ("Google Gemini", "Local", "Custom
    (OpenAI-Compatible)") while the router keys on ids.
    """
    low = str(name or "").strip().lower()
    if low in ("gemini", "google gemini"):
        return "gemini"
    if low == "openrouter":
        return "openrouter"
    if low == "local":
        return "local"
    if low.startswith("custom"):
        return "custom"
    return None


def _reachable_auto_order() -> List[str]:
    """The historical local-first order, used when Settings has no preference."""
    order: List[str] = []
    try:
        from core.local_brain import local_brain

        if local_brain.is_available():
            order.append("local")
    except Exception:
        pass
    if _has_custom():
        order.append("custom")
    if _has_openrouter():
        order.append("openrouter")
    return order


def app_provider_chain() -> List[str]:
    """The app's preferred provider order, restricted to harness adapters.

    Returns an empty list when Settings has no explicit ``default_ai_provider``
    (a fresh install / CI), so ``auto`` keeps its historical local-first
    behaviour. When the user has chosen a provider, it is placed first and the
    router's enabled, configured chain follows as the fallback order.
    ``offline_mode_enabled`` removes cloud providers before they are tried.
    """
    settings = _load_app_settings()
    preferred = _provider_id_for_label(settings.get("default_ai_provider"))
    if preferred is None and "default_ai_provider" not in settings:
        return []

    enabled: List[str] = []
    try:
        from core import provider_router

        enabled = list(provider_router.chain(only_configured=True))
    except Exception:
        enabled = []

    if bool(settings.get("offline_mode_enabled", False)):
        enabled = [pid for pid in enabled if pid not in ("gemini", "openrouter")]

    order: List[str] = []
    if preferred is not None and preferred in enabled:
        order.append(preferred)
    order.extend(pid for pid in enabled if pid not in order)
    return [pid for pid in order if pid in HARNESS_PROVIDERS]


def resolve_chain(provider: str = "auto") -> List[str]:
    """Ordered providers to try, honouring Settings for ``auto``.

    An explicit provider (a token pin, ``--provider``, or
    ``BRAHMA_AGENT_PROVIDER``) is a single-element chain and is never silently
    replaced. ``auto`` uses :func:`app_provider_chain`, then falls back to the
    historical local-first probe.
    """
    requested = (provider or os.getenv("BRAHMA_AGENT_PROVIDER") or "auto").strip().lower()
    if requested not in PROVIDERS:
        raise ProviderUnavailable(f"unknown provider {requested!r}; choose from {PROVIDERS}")
    if requested != "auto":
        return [requested]
    order = app_provider_chain()
    if not order:
        order = _reachable_auto_order()
    if not order:
        raise ProviderUnavailable(
            "No model provider is reachable. Start a local OpenAI-compatible server "
            "(default http://localhost:11434/v1), configure a custom endpoint in the "
            "app, or set an OpenRouter key."
        )
    return order


def resolve_provider(provider: str = "auto") -> str:
    """The first provider ``auto`` would try (kept for callers that need one)."""
    return resolve_chain(provider)[0]


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


def _gemini_key() -> str:
    """Read the Gemini key without importing PyQt or the Google SDK."""
    for env_name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        value = (os.getenv(env_name) or "").strip()
        if value:
            return value
    try:
        from core.secret_store import load_api_keys

        data = load_api_keys()
        key = str((data or {}).get("gemini_api_key") or "").strip()
    except Exception as exc:  # noqa: BLE001 - missing file/key is a provider failure
        raise ProviderUnavailable(f"Gemini key unavailable: {exc}") from exc
    if not key:
        raise ProviderUnavailable(
            "No Gemini API key. Add gemini_api_key to the app's api_keys.json, "
            "set GEMINI_API_KEY, or pin another provider."
        )
    return key


def _post_json(
    url: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    timeout: float,
    *,
    label: str = "Provider",
) -> Any:
    """POST one JSON body with stdlib urllib and a finite timeout."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:400]
        except Exception:
            detail = ""
        raise ProviderUnavailable(f"{label} HTTP {exc.code} {exc.reason} {detail}".strip()) from exc
    except urllib.error.URLError as exc:
        raise ProviderUnavailable(f"{label} request failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ProviderUnavailable(f"{label} timed out after {timeout:.0f}s") from exc


def _gemini_message(body: Any) -> Dict[str, Any]:
    """Normalise Gemini's OpenAI-shaped response to the loop's message dict."""
    if not isinstance(body, dict):
        raise ProviderUnavailable("Gemini returned a non-object response.")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderUnavailable("Gemini returned no choices.")
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message")
    if not isinstance(message, dict):
        raise ProviderUnavailable("Gemini returned no assistant message.")
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict)
        )
    out: Dict[str, Any] = {"role": "assistant", "content": str(content or "")}
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        out["tool_calls"] = tool_calls
    return out


def _gemini_complete(
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    *,
    model: Optional[str],
    temperature: float,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": model or DEFAULT_GEMINI_MODEL,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
    body = _post_json(
        GEMINI_OPENAI_URL,
        payload,
        {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_gemini_key()}",
        },
        GEMINI_TIMEOUT,
        label="Gemini",
    )
    return _gemini_message(body)


def _gemini_stream(
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    on_delta: Optional[Callable[[Dict[str, Any]], None]],
    *,
    model: Optional[str],
    temperature: float,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": model or DEFAULT_GEMINI_MODEL,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }
    if tools:
        payload["tools"] = tools
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_gemini_key()}",
    }
    return collect(openai_stream(GEMINI_OPENAI_URL, payload, headers=headers), on_delta)


class _ChainCompleter:
    """Try providers in order; fail over only before the first output.

    A failure after a delta has reached the caller is re-raised: switching
    providers then would duplicate or contradict text the user already saw.
    """

    def __init__(self, entries: List[Any]) -> None:
        self._entries = list(entries)
        self.last_provider = self._entries[0][0]
        self.last_error = ""

    def __call__(
        self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        errors: List[str] = []
        for pid, completer in self._entries:
            try:
                message = completer(messages, tools)
            except Exception as exc:  # noqa: BLE001 - try the next provider
                errors.append(f"{pid}: {exc}")
                continue
            self.last_provider = pid
            self.last_error = ""
            return message
        self.last_error = "; ".join(errors)
        raise ProviderUnavailable(f"All providers failed ({self.last_error})")

    def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        on_delta: Optional[Callable[[Dict[str, Any]], None]],
    ) -> Dict[str, Any]:
        errors: List[str] = []
        for pid, completer in self._entries:
            streamer = getattr(completer, "stream", None)
            sent = [False]

            def wrapped(delta: Any) -> None:
                sent[0] = True
                if on_delta is not None:
                    on_delta(delta)

            try:
                if callable(streamer):
                    message = streamer(messages, tools, wrapped)
                else:
                    message = completer(messages, tools)
            except Exception as exc:  # noqa: BLE001 - try the next provider
                if sent[0]:
                    raise
                errors.append(f"{pid}: {exc}")
                continue
            self.last_provider = pid
            self.last_error = ""
            return message
        self.last_error = "; ".join(errors)
        raise ProviderUnavailable(f"All providers failed ({self.last_error})")


def _build_one(
    pid: str, model: Optional[str], temperature: float
) -> Completer:
    """Construct one adapter. Connectivity is checked on the first call.

    No kill-switch guard here: ``auto`` already builds from
    ``provider_router.chain(only_configured=True)``, which honours the switches,
    while an explicit ``--provider`` / token pin is a direct request and must
    keep working even if the GUI toggle is off.
    """
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

    if pid == "gemini":
        def complete_gemini(messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
            return _gemini_complete(
                messages, tools, model=model, temperature=temperature
            )

        def stream_gemini(messages, tools, on_delta):
            return _gemini_stream(
                messages, tools, on_delta, model=model, temperature=temperature
            )

        complete_gemini.stream = stream_gemini
        return complete_gemini

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


def build_completer(
    provider: str = "auto",
    model: Optional[str] = None,
    *,
    temperature: float = 0.2,
) -> Completer:
    """Return a loop-compatible completer for the requested provider.

    ``auto`` honours the app's ``default_ai_provider`` and the router's enabled
    chain, then falls back to the historical local-first probe. When more than
    one provider is available the returned object fails over on the first call
    (and before the first stream delta), and exposes ``last_provider`` so the
    caller can audit which one answered.
    """
    chain = resolve_chain(provider)
    entries: List[Any] = []
    errors: List[str] = []
    for pid in chain:
        try:
            entries.append((pid, _build_one(pid, model, temperature)))
        except Exception as exc:  # noqa: BLE001 - a broken candidate is skipped
            errors.append(f"{pid}: {exc}")

    if not entries:
        detail = f" ({'; '.join(errors)})" if errors else "."
        raise ProviderUnavailable(f"No model provider could be built{detail}")

    if len(entries) == 1:
        pid, single = entries[0]
        try:
            single.last_provider = pid  # type: ignore[attr-defined]
        except Exception:
            pass
        return single
    return _ChainCompleter(entries)


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
