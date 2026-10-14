"""
core/provider_router.py
Vendor-agnostic provider registry and fallback chain.

Why this exists
---------------
Brahma previously hardcoded its failover path as an if/elif ladder inside
`_fallback_reply`, with OpenRouter reachable from three unrelated places (the
chat path, the Instagram reply, the clipboard reply). Two problems followed:

  1. There was no way to turn a provider off. On a machine whose CPU is already
     saturated by a local model, a background thread falling through to the
     OpenRouter free pool walks dozens of models at 60s each -- that is where the
     long stalls came from, and it was invisible in the UI.
  2. Adding a vendor meant editing branches in several files.

This module is the single place that knows which providers exist, which are
enabled, and in what order they should be tried. Two kinds of provider:

  kind="gemini"  needs the google.genai SDK (Gemini Live cannot be spoken to
                 over plain REST, so the voice path stays on the SDK)
  kind="openai"   anything implementing the OpenAI chat-completions spec:
                 OpenRouter, Ollama, LM Studio, vLLM, Groq, DeepSeek, Mistral,
                 and any custom base URL. One adapter covers all of them.

Adding a vendor is therefore a registry entry plus a base URL, not a new branch.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

_lock = threading.Lock()


# Order here is the default fallback order. A provider is skipped when the user
# has disabled it, so this list also encodes "try Gemini, then the user's
# configured vendor, then local".
PROVIDERS: List[Dict[str, Any]] = [
    {
        "id": "gemini",
        "label": "Google Gemini",
        "kind": "gemini",
        "default": True,
        "hint": "gemini-2.5-flash. Required for Gemini Live voice.",
    },
    {
        "id": "openrouter",
        "label": "OpenRouter",
        "kind": "openai",
        "default": True,
        "hint": "Free-model pool. Disable if you want zero cloud egress.",
    },
    {
        "id": "local",
        "label": "Local model",
        "kind": "openai",
        "default": True,
        "hint": "Ollama / LM Studio / vLLM via the OpenAI-compatible endpoint.",
    },
    {
        "id": "custom",
        "label": "Custom (OpenAI-compatible)",
        "kind": "openai",
        "default": True,
        "hint": "Groq, DeepSeek, Mistral, anything with a base URL + key.",
    },
]

_BY_ID = {p["id"]: p for p in PROVIDERS}


def _setting(key: str, default: Any = None) -> Any:
    try:
        from memory import config_manager

        return config_manager.get_setting(key, default)
    except Exception:
        return default


def _store(key: str, value: Any) -> None:
    try:
        from memory import config_manager

        config_manager.set_setting(key, value)
    except Exception:
        pass


def _flag(provider_id: str) -> str:
    return f"provider_enabled_{provider_id}"


def is_enabled(provider_id: str) -> bool:
    """True unless the user explicitly switched this provider off.

    Defaults to enabled so behaviour is unchanged until someone toggles it, and
    so a missing/unreadable settings file can never silently disable failover.
    """
    spec = _BY_ID.get(provider_id)
    if spec is None:
        return False
    value = _setting(_flag(provider_id), spec["default"])
    if value is None:
        return bool(spec["default"])
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off")
    return bool(value)


def set_enabled(provider_id: str, enabled: bool) -> None:
    if provider_id not in _BY_ID:
        raise KeyError(f"unknown provider: {provider_id}")
    with _lock:
        _store(_flag(provider_id), bool(enabled))


def toggle(provider_id: str) -> bool:
    """Flip a provider and return its new state."""
    new_state = not is_enabled(provider_id)
    set_enabled(provider_id, new_state)
    return new_state


def provider_spec(provider_id: str) -> Optional[Dict[str, Any]]:
    spec = _BY_ID.get(provider_id)
    return dict(spec) if spec else None


def all_providers() -> List[Dict[str, Any]]:
    """Full catalogue with live enabled state, for the settings UI."""
    out = []
    for spec in PROVIDERS:
        row = dict(spec)
        row["enabled"] = is_enabled(spec["id"])
        out.append(row)
    return out


def _configured(provider_id: str) -> bool:
    """Is this provider actually wired up, independent of the enabled flag?"""
    if provider_id == "custom":
        return bool(str(_setting("custom_ai_url", "") or "").strip())
    if provider_id == "local":
        return bool(str(_setting("local_ai_url", "") or "").strip())
    if provider_id == "gemini":
        try:
            from main import _get_api_key

            return bool(_get_api_key())
        except Exception:
            return True
    return True


def chain(only_configured: bool = False) -> List[str]:
    """Enabled providers in fallback order.

    `only_configured=True` also drops providers with nothing configured, which is
    what a caller wants before spending a network round-trip on them.
    """
    order: List[str] = []
    for spec in PROVIDERS:
        pid = spec["id"]
        if not is_enabled(pid):
            continue
        if only_configured and not _configured(pid):
            continue
        order.append(pid)
    return order


def describe() -> str:
    """One-line summary for logs, e.g. 'gemini > local'."""
    active = chain()
    return " > ".join(active) if active else "(no providers enabled)"


def guard(provider_id: str) -> None:
    """Raise immediately if a provider is switched off.

    Called at the top of every provider entry point. Without this, a disabled
    provider still walks its whole model pool before failing, which is the
    behaviour that made disabling impossible in practice.
    """
    if not is_enabled(provider_id):
        raise ProviderDisabled(
            f"provider '{provider_id}' is disabled in Settings; refusing to call it"
        )


class ProviderDisabled(RuntimeError):
    """Raised when code calls a provider the user switched off.

    Distinct from a provider error so callers can skip to the next link in the
    chain instead of reporting a failure to the user.
    """
