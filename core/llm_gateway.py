"""
core/llm_gateway.py -- single egress point for auxiliary LLM calls.

Why this exists
---------------
``provider_router``'s kill switches only governed the chat reply path. Dozens
of tool-internal calls (memory extraction, web search, PDF/document analysis,
skill synthesis, intent parsing, auto-heal, planner/executor, ...) constructed
their own ``genai.Client`` or called OpenRouter directly, so a "local only"
configuration still sent Google/OpenRouter traffic from background threads --
invisible to the user and unbounded when a server was wedged.

Routing contract
----------------
``generate_content()`` mirrors ``google.genai``'s
``client.models.generate_content(model=, contents=, config=)`` call shape so
call sites migrate by swapping the client for this function:

* When the Gemini link runs (enabled, configured, working) the request is
  forwarded to the real SDK unchanged -- same models, same config, same
  response object.
* When Gemini is disabled or fails, the same request is routed through the
  remaining enabled links in ``provider_router`` chain order (OpenRouter ->
  local -> custom; cloud links dropped in offline mode) and returned as an
  ``AuxResponse`` carrying ``.text`` and a one-part ``.candidates`` tree, so
  parsers written against the SDK keep working.
* Every link failure is logged with its reason, and when a later link
  answers, the log names it -- cloud egress stays visible.
* When *every* enabled link fails, ``GatewayError`` is raised with all
  reasons joined; callers keep their existing ``except`` handling and can
  surface a real message instead of a silent empty string.

The gateway only ever calls providers that are *enabled*: that is the whole
point. A disabled provider is never contacted, not even as a fallback.
"""
from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any, List, Optional, Tuple

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_SYSTEM = (
    "You are Brahma Evo, a concise, helpful desktop assistant. "
    "Reply naturally and briefly."
)


class GatewayError(RuntimeError):
    """Every enabled provider failed; the message names each link's reason."""


class AuxResponse:
    """Minimal stand-in for ``google.genai``'s GenerateContentResponse.

    ``.text`` plus a single-part ``.candidates`` tree covers how call sites
    actually consume a response, so a non-Gemini link is indistinguishable
    to them. ``.provider`` records which link answered (useful in logs/tests).
    """

    def __init__(self, text: str, provider: str):
        self.text = text
        self.provider = provider
        part = SimpleNamespace(text=text, inline_data=None)
        self.candidates = [SimpleNamespace(content=SimpleNamespace(parts=[part]))]


# --------------------------------------------------------------- chain state


def _offline() -> bool:
    try:
        from memory import config_manager

        return bool((config_manager.load_settings() or {}).get("offline_mode_enabled", False))
    except Exception:
        return False


def _selected_id() -> Optional[str]:
    """The provider the user selected in Settings, as a router id.

    Same rule as the reply ladder: internal LLM work follows the selection
    first and treats the rest of the chain as failover, so a local-first
    configuration extracts memory and parses intents on the local model
    instead of quietly walking the cloud links.
    """
    try:
        from main import _provider_id_for_name

        from memory import config_manager

        name = (config_manager.load_settings() or {}).get("default_ai_provider", "Gemini")
        return _provider_id_for_name(name)
    except Exception:
        return None


def _chain() -> List[str]:
    """Enabled providers in failover order; offline mode drops the cloud links.

    The provider selected in Settings is placed first (when it is enabled),
    mirroring the reply ladder's ordering.
    """
    try:
        from core import provider_router

        chain = list(provider_router.chain())
    except Exception:
        chain = ["gemini", "openrouter", "local", "custom"]
    if _offline():
        chain = [p for p in chain if p in ("local", "custom")]
    selected = _selected_id()
    if selected in chain:
        chain = [selected] + [p for p in chain if p != selected]
    return chain


def _gemini_key(api_key: Optional[str]) -> str:
    if api_key:
        return api_key
    try:
        from main import _get_api_key

        return _get_api_key() or ""
    except Exception:
        return ""


# ----------------------------------------------------------- request parsing


def _cfg(config: Any, key: str, default=None) -> Any:
    """Read a config field from either a plain dict or a types.* config object."""
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _system_text(config: Any) -> Optional[str]:
    system = _cfg(config, "system_instruction", None)
    if system is None or isinstance(system, str):
        return system or None
    # types.Content-shaped system instruction: flatten its parts.
    parts = getattr(system, "parts", None) or []
    texts = [getattr(p, "text", "") for p in parts]
    joined = "\n".join(t for t in texts if t)
    return joined or None


def _split_contents(contents: Any) -> Tuple[str, List[Tuple[bytes, str]]]:
    """Flatten google-genai request contents into (text, [(bytes, mime)]).

    Handles plain strings, lists mixing strings and ``types.Part`` objects,
    and dict-shaped parts -- both request-side (bytes) and JSON-side
    (base64 str) inline data.
    """
    text_parts: List[str] = []
    images: List[Tuple[bytes, str]] = []

    def _eat(item: Any) -> None:
        if isinstance(item, str):
            text_parts.append(item)
            return
        inline = getattr(item, "inline_data", None)
        if inline is None and isinstance(item, dict):
            inline = item.get("inline_data")
        if inline is not None:
            # Request-side parts carry attribute-style inline_data (bytes);
            # JSON-side parts carry a plain dict ({'data': b64, ...}).
            if isinstance(inline, dict):
                data = inline.get("data")
            else:
                data = getattr(inline, "data", None)
            if data:
                if isinstance(inline, dict):
                    mime = inline.get("mime_type")
                else:
                    mime = getattr(inline, "mime_type", None)
                mime = mime or (
                    item.get("mime_type") if isinstance(item, dict) else None
                ) or "image/png"
                if isinstance(data, str):
                    try:
                        data = base64.b64decode(data)
                    except Exception:
                        data = data.encode("utf-8", "replace")
                images.append((bytes(data), mime))
                return
        text = getattr(item, "text", None)
        if text is None and isinstance(item, dict):
            text = item.get("text")
        if text:
            text_parts.append(str(text))
            return
        # Unknown part shape: stringify rather than drop it silently.
        text_parts.append(str(item))

    if isinstance(contents, (list, tuple)):
        for item in contents:
            _eat(item)
    else:
        _eat(contents)
    return "\n".join(t for t in text_parts if t).strip(), images


# ------------------------------------------------------------------- links


def _merged_config(config: Any, system: Any, temperature: Any, max_tokens: Any) -> Any:
    """Config for the Gemini link with explicit kwargs layered on top.

    Dict configs are copied and updated; ``types.*`` configs go through
    pydantic's ``model_copy`` when available. When nothing was passed
    explicitly the original config object is returned untouched, so a
    plain call stays byte-identical to a direct SDK request.
    """
    if system is None and temperature is None and max_tokens is None:
        return config
    if config is None or isinstance(config, dict):
        merged = dict(config or {})
        if system is not None:
            merged["system_instruction"] = system
        if temperature is not None:
            merged["temperature"] = temperature
        if max_tokens is not None:
            merged["max_output_tokens"] = max_tokens
        return merged
    updates = {}
    if system is not None:
        updates["system_instruction"] = system
    if temperature is not None:
        updates["temperature"] = temperature
    if max_tokens is not None:
        updates["max_output_tokens"] = max_tokens
    try:
        return config.model_copy(update=updates)
    except Exception:
        return config


def _call_gemini(contents: Any, model: str, config: Any, api_key: Optional[str]) -> str:
    from google import genai  # local import: only paid for when the link runs

    key = _gemini_key(api_key)
    if not key:
        raise GatewayError("no Gemini API key configured (Settings > API Keys)")
    client = genai.Client(api_key=key, http_options={"api_version": "v1beta"})
    kwargs = {"model": model, "contents": contents}
    if config is not None:
        kwargs["config"] = config
    response = client.models.generate_content(**kwargs)
    try:
        text = response.text
    except Exception as exc:  # blocked/empty candidates surface here
        raise GatewayError(f"{model}: unusable response ({exc})") from exc
    if not (text or "").strip():
        raise GatewayError(f"{model}: empty response")
    return text.strip()


def _chat_link(
    pid: str,
    text: str,
    images: List[Tuple[bytes, str]],
    system: Optional[str],
    temperature: Optional[float],
    max_tokens: Optional[int],
) -> str:
    """OpenAI-spec links (openrouter/local/custom) through llm_client.chat_as.

    One image is attached when present (call sites here never pass more);
    JSON-mode is deliberately *not* signalled to these links -- every JSON
    call site already parses defensively (fence stripping + fallback), which
    works regardless of how the model formats its answer.
    """
    import base64 as _b64

    from llm_client import client as unified

    kwargs: dict = {
        "system": system or DEFAULT_SYSTEM,
        "temperature": 0.7 if temperature is None else float(temperature),
    }
    if max_tokens:
        kwargs["max_tokens"] = int(max_tokens)
    if images:
        raw, mime = images[0]
        kwargs["image_b64"] = _b64.b64encode(raw).decode("ascii")
        kwargs["image_mime"] = mime
    return unified.chat_as(pid, text, **kwargs)


# ---------------------------------------------------------------- public API


def generate_content(
    contents: Any,
    *,
    model: Optional[str] = None,
    config: Any = None,
    api_key: Optional[str] = None,
    system: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    links: Optional[tuple] = None,
    purpose: str = "auxiliary task",
) -> AuxResponse:
    """Run one auxiliary LLM request against the enabled provider chain.

    Mirrors ``client.models.generate_content(model=, contents=, config=)``;
    the Gemini link forwards everything verbatim, later links get the
    extracted text/image/system/temperature. The explicit ``system`` /
    ``temperature`` / ``max_tokens`` arguments are conveniences for callers
    that have no genai config object -- they override whatever the config
    carries. ``links`` restricts which providers may serve the call (for
    grounded tasks such as search, where a non-grounded model answering from
    memory would be a lie). Raises ``GatewayError`` (a RuntimeError) naming
    every link's failure when nothing answered.
    """
    chain = _chain()
    if links:
        chain = [p for p in chain if p in links]
        if not chain:
            raise GatewayError(
                f"{purpose}: {'/'.join(links)} is disabled in Settings > AI Providers"
            )
    if not chain:
        raise GatewayError(
            f"{purpose}: no providers are enabled in Settings > AI Providers"
        )

    text, images = _split_contents(contents)
    system = system or _system_text(config)
    if temperature is None:
        temperature = _cfg(config, "temperature", None)
    if max_tokens is None:
        max_tokens = _cfg(config, "max_output_tokens", None)
    errors: List[str] = []

    for pid in chain:
        try:
            if pid == "gemini":
                result = _call_gemini(
                    contents,
                    model or DEFAULT_GEMINI_MODEL,
                    _merged_config(config, system, temperature, max_tokens),
                    api_key,
                )
            else:
                result = _chat_link(pid, text, images, system, temperature, max_tokens)
        except Exception as exc:
            reason = str(exc).strip() or exc.__class__.__name__
            errors.append(f"{pid}: {reason}")
            print(f"[LLM Gateway] {purpose}: {pid} failed -> {reason}")
            continue

        result = (result or "").strip()
        if not result:
            errors.append(f"{pid}: empty response")
            print(f"[LLM Gateway] {purpose}: {pid} returned an empty response")
            continue
        if pid != chain[0]:
            print(f"[LLM Gateway] {purpose}: answered by {pid}")
        return AuxResponse(result, pid)

    raise GatewayError(f"{purpose}: all providers failed ({'; '.join(errors)})")
