"""
Brahma Local Brain Engine (v2.1)
Provides full offline local LLM execution using an OpenAI-compatible local runtime
(Ollama, LM Studio, vLLM, OpenCode, or LocalAI) with automatic tool-calling.

Configuration contract
----------------------
``local_ai_url`` and ``local_ai_model`` in Settings are the single source of
truth. They are re-read before every operation (the same contract
``llm_client.UnifiedAIClient`` follows), so changing the endpoint or model in
Settings takes effect without restarting the app. The Ollama defaults only
apply when Settings has nothing configured.

Networking contract
-------------------
Every socket operation carries a finite timeout. An unbounded ``urlopen`` is
what let a wedged server leave the UI stuck in THINKING with no compute
happening; a timeout turns that into a normal provider failure the reply
ladder can report and fail over from.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import threading
from typing import Any, Dict, Generator, List, Optional

DEFAULT_ENDPOINT = "http://localhost:11434/v1"
DEFAULT_MODEL = "qwen2.5:3b"

# urllib takes a single socket timeout (connect + each recv), not a
# (connect, read) tuple like requests. Never None.
#
# Non-streaming: the whole body arrives after generation finishes, and prompt
# evaluation of a bounded tool surface on a busy CPU can take a while before
# the first byte -> generous.
RESPONSE_TIMEOUT = 180.0
# Streaming: a healthy stream yields chunks continuously; this is the maximum
# allowed silence between chunks before the server counts as wedged.
STREAM_IDLE_TIMEOUT = 60.0
# Liveness probe: fail fast, the UI polls this.
PROBE_TIMEOUT = 3.0

# Context window requested from the local runtime. Ollama's OpenAI-compatible
# endpoint silently truncates to its own default (2048-4096 tokens), which is
# smaller than the system prompt this app actually sends: core/prompt.txt plus
# the skill-context block and a bounded tool surface comfortably exceeds 4K
# tokens, so without an explicit budget the model would lose the operating
# instructions before the user's turn. 8192 leaves headroom for a real answer
# on top; the bundled 3B models (qwen2.5, llama3.2) support 32K natively, and
# non-Ollama OpenAI-compatible servers ignore the unknown `options` field.
LOCAL_NUM_CTX = 8192

CORE_LOCAL_TOOL_NAMES = {
    "open_app", "computer_settings", "system_diagnostics", "spotify_controller",
    "youtube_video", "web_search", "weather_report", "file_controller",
    "smart_organizer", "desktop_control", "execute_protocol", "reminder",
    "word_document", "pdf_document", "dev_agent", "recall_memory",
    "save_memory", "shutdown_brahma", "undo"
}


class LocalBrainError(RuntimeError):
    """A local-endpoint failure with a message that is safe to surface.

    Raised instead of letting a timeout, an HTTP error or a malformed body
    fall through as ``None``/empty content: the reply ladder logs this text
    verbatim, so it has to say *which* endpoint failed and *why*.
    """


def convert_schema_to_lowercase(schema: Any) -> Any:
    """Recursively converts uppercase Gemini schema types (e.g. STRING, OBJECT) to lowercase (string, object)."""
    if not isinstance(schema, dict):
        return schema
    res = {}
    for k, v in schema.items():
        if k == "type" and isinstance(v, str):
            res[k] = v.lower()
        elif isinstance(v, dict):
            res[k] = convert_schema_to_lowercase(v)
        elif isinstance(v, list):
            res[k] = [convert_schema_to_lowercase(item) if isinstance(item, dict) else item for item in v]
        else:
            res[k] = v
    return res


def _load_settings() -> Dict[str, Any]:
    try:
        from memory import config_manager

        return config_manager.load_settings() or {}
    except Exception:
        return {}


class LocalBrain:
    def __init__(self, endpoint: Optional[str] = None, default_model: Optional[str] = None):
        self.enabled = False
        self._cached_models: List[str] = []
        # An explicit constructor argument pins the value for this instance;
        # with none given, Settings (falling back to the Ollama defaults) wins.
        self._pinned_endpoint = (endpoint or "").strip() or None
        self._pinned_model = (default_model or "").strip() or None
        self.endpoint = DEFAULT_ENDPOINT
        self._default_model = DEFAULT_MODEL
        self.reload_settings()

    # ------------------------------------------------------------- settings

    def reload_settings(self) -> None:
        """Re-read ``local_ai_url`` / ``local_ai_model`` from Settings.

        Called at the top of every public operation so a Settings change
        takes effect immediately instead of only after a restart.
        """
        data = _load_settings()
        if self._pinned_endpoint:
            self.endpoint = self._pinned_endpoint.rstrip("/")
        else:
            endpoint = str(data.get("local_ai_url") or "").strip() or DEFAULT_ENDPOINT
            self.endpoint = endpoint.rstrip("/")
        if self._pinned_model:
            # An explicit constructor argument pins the model for this
            # instance; without this branch the pin only blocked Settings
            # while _default_model stayed at the Ollama default, so
            # LocalBrain(default_model=...) silently ignored its argument.
            self._default_model = self._pinned_model
        else:
            model = str(data.get("local_ai_model") or "").strip()
            if model:
                self._default_model = model

    @property
    def default_model(self) -> str:
        return self._default_model

    @default_model.setter
    def default_model(self, value: str) -> None:
        """Set the active model and persist it, so Settings stays the source
        of truth and the next ``reload_settings`` does not clobber a runtime
        change (e.g. the ``manage_local_model`` tool's ``set_active``)."""
        clean = (value or "").strip() or DEFAULT_MODEL
        self._default_model = clean
        self._pinned_model = None
        try:
            from memory import config_manager

            config_manager.set_setting("local_ai_model", clean)
        except Exception:
            pass

    @property
    def ollama_base(self) -> str:
        """Server root derived from the configured endpoint.

        Ollama's native admin API (``/api/tags``, ``/api/pull``) lives on the
        root, the OpenAI-compatible surface on ``/v1``. Other runtimes expose
        neither; callers treat a failure there as "not supported".
        """
        base = self.endpoint
        if base.endswith("/v1"):
            base = base[:-3]
        return base.rstrip("/")

    # ------------------------------------------------------------- probing

    @staticmethod
    def _parse_model_ids(data: Any) -> Optional[List[str]]:
        """Accept both the OpenAI shape (``data[].id``) and Ollama's native
        shape (``models[].name``) so one parser covers every runtime."""
        if isinstance(data, dict):
            entries = data.get("data")
            if isinstance(entries, list):
                ids = [str(e.get("id") or e.get("name") or "") for e in entries if isinstance(e, dict)]
                models = [i.strip() for i in ids if i.strip()]
                if models or entries == []:
                    return models
            entries = data.get("models")
            if isinstance(entries, list):
                return [str(m.get("name") or m.get("id") or "").strip()
                        for m in entries if isinstance(m, dict)
                        if (m.get("name") or m.get("id"))]
        return None

    def _fetch_models(self) -> List[str]:
        """Probe the configured endpoint for its model list.

        Primary: ``GET {local_ai_url}/models`` - the OpenAI-compatible
        endpoint served by Ollama, LM Studio, vLLM, LocalAI and OpenCode.
        Fallback: Ollama's native ``GET /api/tags`` when the configured
        endpoint is the Ollama ``/v1`` surface and the primary probe failed.

        Raises LocalBrainError naming the endpoint on total failure; the
        message is what ``is_available``'s callers surface.
        """
        candidates = [f"{self.endpoint}/models"]
        if self.endpoint.endswith("/v1") or self.endpoint.endswith("/api"):
            candidates.append(f"{self.ollama_base}/api/tags")

        last_error = ""
        for url in candidates:
            try:
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                models = self._parse_model_ids(data)
                if models is None:
                    last_error = f"{url}: unexpected response shape"
                    continue
                self._cached_models = models
                return models
            except urllib.error.HTTPError as e:
                last_error = f"{url}: HTTP {e.code}"
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                reason = getattr(e, "reason", e)
                last_error = f"{url}: {reason}"
            except ValueError as e:
                last_error = f"{url}: {e}"

        raise LocalBrainError(
            f"local endpoint unreachable ({last_error or 'no endpoint configured'}); "
            f"check Settings > AI Providers > Local URL ({self.endpoint})"
        )

    def is_available(self) -> bool:
        """True when the configured local server answers its model probe.

        Probes *the configured endpoint* - the old implementation hardcoded
        ``localhost:11434`` regardless of Settings, so any non-Ollama or
        non-default-port server reported "unavailable" and the reply ladder
        skipped Local entirely.
        """
        self.reload_settings()
        try:
            self._fetch_models()
            return True
        except LocalBrainError:
            return False

    def list_installed_models(self) -> List[str]:
        """Returns all downloaded models on the local runtime."""
        self.is_available()
        return self._cached_models

    def pull_model_async(self, model_name: str, progress_callback=None):
        """Pulls a model from the local runtime library in the background.

        Ollama-only (``POST /api/pull``); other runtimes return an error
        which is delivered through the callback instead of raising.
        """
        def _pull():
            try:
                url = f"{self.ollama_base}/api/pull"
                payload = json.dumps({"name": model_name}).encode("utf-8")
                req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=1200) as resp:
                    for line in resp:
                        if line:
                            try:
                                chunk = json.loads(line.decode("utf-8"))
                                if progress_callback:
                                    progress_callback(chunk)
                            except Exception:
                                pass
            except Exception as e:
                if progress_callback:
                    progress_callback({"error": str(e)})

        threading.Thread(target=_pull, daemon=True).start()

    # ------------------------------------------------------------- requests

    @staticmethod
    def _open(req: urllib.request.Request, timeout: float) -> str:
        """POST/GET one request with a finite timeout, mapping every transport
        failure onto LocalBrainError so callers get one error type with the
        endpoint in the message."""
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                detail = ""
            raise LocalBrainError(f"{req.full_url}: HTTP {e.code} {e.reason} {detail}".strip()) from e
        except urllib.error.URLError as e:
            raise LocalBrainError(f"{req.full_url}: {e.reason}") from e
        except TimeoutError as e:
            raise LocalBrainError(
                f"{req.full_url}: timed out after {timeout:.0f}s"
            ) from e
        except OSError as e:
            raise LocalBrainError(f"{req.full_url}: {e}") from e

    def _post_json(self, payload: Dict[str, Any], timeout: float) -> str:
        self.reload_settings()
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.endpoint}/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        return self._open(req, timeout)

    # ---------------------------------------------------------- tool format

    def format_tools_for_local(
        self,
        tool_declarations: List[Dict[str, Any]],
        focus_core: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Formats and converts Gemini or OpenAI-style tool declarations into
        strictly compliant OpenAI tools with lowercased JSON schema types.

        focus_core is accepted for backwards compatibility but the core-name
        filter is intentionally disabled: the tool index now supplies a bounded,
        relevance-ranked surface instead (see resolve_tools), which is a better
        filter than a static name list and also makes MCP tools reachable.
        """
        formatted = []
        for t in tool_declarations:
            name = t.get("name") or (t.get("function", {}).get("name") if isinstance(t.get("function"), dict) else None)
            if not name:
                continue

            # Already formatted OpenAI tool
            if "type" in t and t.get("type") == "function" and "function" in t:
                fn = dict(t["function"])
                if "parameters" in fn:
                    fn["parameters"] = convert_schema_to_lowercase(fn["parameters"])
                formatted.append({"type": "function", "function": fn})
            else:
                desc = t.get("description", "")
                raw_params = t.get("parameters", {"type": "object", "properties": {}})
                params = convert_schema_to_lowercase(raw_params)
                formatted.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": desc,
                        "parameters": params,
                    }
                })
        return formatted

    def resolve_tools(
        self,
        intent: str,
        static_declarations: List[Dict[str, Any]],
    ) -> tuple:
        """Tool surface for one local turn, plus any JIT instruction context.

        Gemini Live advertises a bounded, relevance-ranked tool surface built by
        core.tool_bridge, so the rest of the catalogue stays reachable through
        search_tools / call_tool. Local models previously got the raw static
        TOOL_DECLARATIONS list, which neither exposes MCP tools nor fits a 3B
        context window. This routes the local path through the same bridge.

        Returns (declarations, skill_context). Falls back to `static_declarations`
        whenever the index is unavailable, so a missing index degrades to the
        previous behaviour instead of breaking the local path.
        """
        fallback = list(static_declarations or [])
        try:
            from core import tool_bridge

            payload = tool_bridge.build_session_payload(intent or "")
        except Exception:
            return fallback, ""
        picked = payload.get("declarations") or []
        if not picked:
            return fallback, ""
        return picked, (payload.get("skill_context") or "")

    # ------------------------------------------------------------- chat

    def generate_chat_stream(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Streams completions from the local runtime.
        Yields parsed stream delta chunks or tool calls.
        """
        active_model = model or self.default_model
        self.reload_settings()
        payload: Dict[str, Any] = {
            "model": active_model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            # See LOCAL_NUM_CTX: without this the runtime truncates the system
            # prompt before the user's turn.
            "options": {"num_ctx": LOCAL_NUM_CTX},
        }

        if tools:
            payload["tools"] = self.format_tools_for_local(tools, focus_core=False)

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.endpoint}/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with self._open_stream(req) as resp:
            for line in resp:
                line_str = line.decode("utf-8").strip()
                if line_str.startswith("data: "):
                    content = line_str[6:].strip()
                    if content == "[DONE]":
                        break
                    try:
                        chunk = json.loads(content)
                        yield chunk
                    except Exception:
                        pass

    def _open_stream(self, req: urllib.request.Request):
        """Open a streaming response with a finite inter-chunk timeout."""
        try:
            return urllib.request.urlopen(req, timeout=STREAM_IDLE_TIMEOUT)
        except urllib.error.HTTPError as e:
            raise LocalBrainError(f"{req.full_url}: HTTP {e.code} {e.reason}") from e
        except urllib.error.URLError as e:
            raise LocalBrainError(f"{req.full_url}: {e.reason}") from e
        except TimeoutError as e:
            raise LocalBrainError(
                f"{req.full_url}: timed out after {STREAM_IDLE_TIMEOUT:.0f}s"
            ) from e

    def chat_complete(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.1,
        focus_core: bool = True,
    ) -> Dict[str, Any]:
        """Non-streaming completion for fast single-turn tool calls and structured responses.

        Raises LocalBrainError on any transport or protocol failure (timeout,
        HTTP error, non-JSON body, ``error`` field, missing ``choices``) so a
        silent empty response can never masquerade as an answer upstream.
        """
        active_model = model or self.default_model
        payload: Dict[str, Any] = {
            "model": active_model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
            # See LOCAL_NUM_CTX: without this the runtime truncates the system
            # prompt before the user's turn.
            "options": {"num_ctx": LOCAL_NUM_CTX},
        }
        if tools:
            payload["tools"] = self.format_tools_for_local(tools, focus_core=focus_core)

        body = self._post_json(payload, RESPONSE_TIMEOUT)
        try:
            res = json.loads(body)
        except ValueError as e:
            raise LocalBrainError(
                f"{self.endpoint}/chat/completions returned a non-JSON body: {body[:200]!r}"
            ) from e
        if not isinstance(res, dict):
            raise LocalBrainError(
                f"{self.endpoint}/chat/completions returned an unexpected payload: {body[:200]!r}"
            )
        if res.get("error"):
            raise LocalBrainError(f"local model error: {res['error']}")
        if not res.get("choices"):
            raise LocalBrainError(
                f"local model '{active_model}' returned no choices: {body[:200]!r}"
            )
        return res


# Global singleton instance
local_brain = LocalBrain()
