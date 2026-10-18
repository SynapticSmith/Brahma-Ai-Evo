from core.user_paths import get_user_data_dir
import json
import logging
import requests
from pathlib import Path
from typing import Any, Dict, List, Optional
from or_client import client as openrouter_client

logger = logging.getLogger("llm_client")

def _get_base_dir() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR = _get_base_dir()
SETTINGS_PATH = get_user_data_dir() / "config" / "app_settings.json"

# OpenCode exposes an OpenAI-compatible /v1 surface; its defaults are what we
# probe when the user has not pinned a Local URL.
OPENCODE_DEFAULT_URL = "http://localhost:4096/v1"
OLLAMA_DEFAULT_URL = "http://localhost:11434/v1"

DEFAULT_LOCAL_URL = OLLAMA_DEFAULT_URL
DEFAULT_LOCAL_MODEL = "llama3.2"

# (connect, read) seconds for every chat request. Never None: an unbounded
# post() parks the caller (and the UI, which sits in THINKING until the reply
# arrives) forever when a server accepts the connection and then goes silent.
REQUEST_TIMEOUT = (10.0, 180.0)


def _local_read_timeout() -> tuple:
    """(connect, read) budget for the local provider.

    The local model may be CPU-resident, where prefill of a long system
    prompt alone can exceed REQUEST_TIMEOUT's read budget; LocalBrain
    resolves the hardware-aware profile (core.local_brain.TIMEOUT_PROFILES),
    so its link borrows that budget instead of maintaining a second,
    conflicting constant. Any failure keeps REQUEST_TIMEOUT.
    """
    try:
        from core.local_brain import local_brain

        return (REQUEST_TIMEOUT[0], local_brain.response_timeout())
    except Exception:
        return REQUEST_TIMEOUT


def _strip_json_fence(raw: str) -> str:
    """Removes markdown code fences from a model response."""
    clean = raw.strip()
    if clean.startswith("```"):
        parts = clean.split("```")
        clean = parts[1] if len(parts) > 1 else clean
        if clean.lower().startswith("json"):
            clean = clean[4:]
    return clean.strip().rstrip("`").strip()


class UnifiedAIClient:
    def __init__(self):
        self._provider = "OpenRouter"
        self._local_url = DEFAULT_LOCAL_URL
        self._local_model = DEFAULT_LOCAL_MODEL
        self._local_api_key = ""
        self._custom_url = ""
        self._custom_model = ""
        self._custom_api_key = ""
        self.reload_settings()

    def reload_settings(self):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._provider = data.get("default_ai_provider", "OpenRouter")
            self._local_url = (data.get("local_ai_url") or DEFAULT_LOCAL_URL).rstrip("/")
            self._local_model = data.get("local_ai_model") or DEFAULT_LOCAL_MODEL
            self._local_api_key = (data.get("local_api_key") or "").strip()
            self._custom_url = (data.get("custom_ai_url") or "").rstrip("/")
            self._custom_model = (data.get("custom_ai_model") or "").strip()
            self._custom_api_key = (data.get("custom_api_key") or "").strip()
        except Exception as e:
            logger.error(f"[LLM Client] Failed to load settings: {e}")

    # ------------------------------------------------------- OpenAI-compatible

    @staticmethod
    def _auth_headers(api_key: str) -> Dict[str, str]:
        """Only attach Authorization when a key is actually configured.

        Local servers such as Ollama and LM Studio reject (or mis-route) an
        unexpected bearer token, so an empty key must mean "send nothing".
        """
        headers = {"Content-Type": "application/json"}
        key = (api_key or "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _openai_chat_completion(
        self,
        messages: List[Dict[str, Any]],
        url: str,
        model: str,
        api_key: str = "",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
        label: str = "OpenAI-compatible",
        timeout: tuple = REQUEST_TIMEOUT,
    ) -> Optional[str]:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if response_format:
            payload["response_format"] = response_format

        endpoint = f"{url}/chat/completions"
        try:
            resp = requests.post(
                endpoint,
                headers=self._auth_headers(api_key),
                json=payload,
                timeout=timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                return content.strip() if content else None
            logger.error(f"[LLM Client] {label} error {resp.status_code}: {resp.text[:400]}")
            return None
        except requests.Timeout as e:
            logger.error(
                f"[LLM Client] {label} timed out after "
                f"{timeout[1]:.0f}s reading {endpoint}: {e}"
            )
            return None
        except Exception as e:
            logger.error(f"[LLM Client] {label} request failed: {e}")
            return None

    def _local_chat_completion(self, messages: List[dict], temperature: float = 0.7, response_format: Optional[dict] = None) -> Optional[str]:
        """Backwards-compatible wrapper around the local OpenAI-compatible path."""
        return self._openai_chat_completion(
            messages,
            url=self._local_url,
            model=self._local_model,
            api_key=self._local_api_key,
            temperature=temperature,
            response_format=response_format,
            label="Local AI",
        )

    def _custom_chat_completion(
        self,
        messages: List[dict],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
    ) -> Optional[str]:
        return self._openai_chat_completion(
            messages,
            url=self._custom_url,
            model=self._custom_model,
            api_key=self._custom_api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            label="Custom provider",
        )

    def _require_custom(self) -> None:
        if not self._custom_url:
            raise RuntimeError(
                "Custom provider is selected but no URL is set. Add one in Settings > AI Providers."
            )
        if not self._custom_model:
            raise RuntimeError(
                "Custom provider is selected but no model is set. Add one in Settings > AI Providers."
            )

    # ------------------------------------------------------- OpenCode discovery

    @staticmethod
    def poll_opencode_models(
        url: str = OPENCODE_DEFAULT_URL,
        timeout: float = 3.0,
    ) -> List[str]:
        """Returns model IDs from an OpenCode (or other) /v1/models endpoint.

        Returns an empty list on any failure — discovery is best-effort and must
        never raise into the UI or the chat path. A local URL is only meaningful
        for the "Local" provider, so the caller decides how to apply the result.
        """
        endpoint = (url or "").rstrip("/")
        if not endpoint:
            return []
        if not endpoint.endswith("/models"):
            endpoint = f"{endpoint}/models"
        try:
            resp = requests.get(endpoint, timeout=timeout)
            if resp.status_code != 200:
                logger.warning(f"[LLM Client] Model discovery {resp.status_code} at {endpoint}")
                return []
            data = resp.json()
            entries = data.get("data") if isinstance(data, dict) else None
            if not isinstance(entries, list):
                # Tolerate a bare {"models": [...]} shape.
                entries = data.get("models") if isinstance(data, dict) else None
            if not isinstance(entries, list) and isinstance(data, list):
                # Tolerate a bare top-level list.
                entries = data
            if not isinstance(entries, list):
                return []
            models: List[str] = []
            for entry in entries:
                if isinstance(entry, str) and entry.strip():
                    models.append(entry.strip())
                elif isinstance(entry, dict):
                    model_id = entry.get("id") or entry.get("name") or entry.get("model")
                    if model_id and str(model_id).strip():
                        models.append(str(model_id).strip())
            # Preserve order, drop duplicates.
            seen = set()
            unique = []
            for m in models:
                if m not in seen:
                    seen.add(m)
                    unique.append(m)
            return unique
        except Exception as e:
            logger.warning(f"[LLM Client] Model discovery failed at {endpoint}: {e}")
            return []

    # --------------------------------------------------------------- dispatch

    def _is_local(self) -> bool:
        return str(self._provider).strip().lower() == "local"

    def _is_custom(self) -> bool:
        return str(self._provider).strip().lower().startswith("custom")

    def _route_chat(self, messages: List[dict], temperature: float, max_tokens: int, response_format: Optional[dict] = None) -> str:
        """Sends messages to whichever OpenAI-compatible provider is selected."""
        # Honour the per-provider kill switch before any network call, so a
        # disabled provider fails in microseconds rather than walking a model
        # pool with a long per-request timeout.
        try:
            from core import provider_router
        except Exception:
            provider_router = None
        if provider_router is not None:
            target = "local" if self._is_local() else (
                "custom" if self._is_custom() else "openrouter"
            )
            provider_router.guard(target)

        if self._is_local():
            result = self._openai_chat_completion(
                messages,
                url=self._local_url,
                model=self._local_model,
                api_key=self._local_api_key,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                label="Local AI",
            )
            if result:
                return result
            raise RuntimeError(
                "Local AI request failed. Check that the server is running and that "
                "the URL and model in Settings > AI Providers are correct."
            )

        self._require_custom()
        result = self._custom_chat_completion(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )
        if result:
            return result
        raise RuntimeError(
            f"Custom provider request failed. Verify {self._custom_url}/chat/completions "
            "and the configured model/API key in Settings > AI Providers."
        )

    # ------------------------------------------------------------------ API

    def chat(self, prompt: str, system: str = "You are a helpful assistant.", history: Optional[list[dict]] = None, model: Optional[str] = None, max_tokens: int = 4096, temperature: float = 0.7) -> str:
        self.reload_settings()
        if self._is_local() or self._is_custom():
            messages = [{"role": "system", "content": system}]
            if history:
                messages.extend(history)
            messages.append({"role": "user", "content": prompt})
            return self._route_chat(messages, temperature, max_tokens)
        return openrouter_client.chat(prompt, system, history, model, max_tokens, temperature)

    def chat_as(
        self,
        provider_id: str,
        prompt: str,
        system: str = "You are a helpful assistant.",
        history: Optional[list[dict]] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        image_b64: Optional[str] = None,
        image_mime: str = "image/png",
    ) -> str:
        """Route one call to an *explicit* provider id.

        Unlike ``chat``, which follows whatever Settings has selected, this is
        for callers that iterate the provider chain and need each link to
        behave exactly as that link (the reply ladder's failover and the
        LLM gateway's auxiliary routing). Optional image attachment uses the
        OpenAI ``image_url`` content shape, which every link here speaks.
        Raises RuntimeError with an actionable message when the provider is
        disabled, unconfigured, or returns nothing -- never an empty string.
        """
        self.reload_settings()
        pid = (provider_id or "").strip().lower()
        try:
            from core import provider_router
        except ImportError:
            provider_router = None
        if provider_router is not None:
            provider_router.guard(pid)

        if pid == "openrouter":
            if image_b64:
                return openrouter_client.vision(
                    prompt, image_b64, image_mime, system, None, max_tokens
                )
            return openrouter_client.chat(
                prompt, system, history, None, max_tokens, temperature
            )

        timeout = REQUEST_TIMEOUT
        if pid == "local":
            url, model, key = self._local_url, self._local_model, self._local_api_key
            label = "Local AI"
            # CPU-resident inference needs the hardware-aware patience;
            # LocalBrain resolves it (see _local_read_timeout).
            timeout = _local_read_timeout()
            hint = (
                "Local AI request failed. Check that the server is running and "
                "that the URL and model in Settings > AI Providers are correct."
            )
        elif pid == "custom":
            self._require_custom()
            url, model, key = self._custom_url, self._custom_model, self._custom_api_key
            label = "Custom provider"
            hint = (
                f"Custom provider request failed. Verify {self._custom_url}"
                "/chat/completions and the configured model/API key in "
                "Settings > AI Providers."
            )
        else:
            raise ValueError(
                f"chat_as: unsupported provider '{provider_id}' "
                "(expected openrouter/local/custom)"
            )

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]
        if history:
            messages.extend(history)
        if image_b64:
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{image_mime};base64,{image_b64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            })
        else:
            messages.append({"role": "user", "content": prompt})
        result = self._openai_chat_completion(
            messages,
            url=url,
            model=model,
            api_key=key,
            temperature=temperature,
            max_tokens=max_tokens,
            label=label,
            timeout=timeout,
        )
        if result:
            return result
        raise RuntimeError(hint)

    def chat_json(self, prompt: str, system: str = "Return ONLY valid JSON.", model: Optional[str] = None, max_tokens: int = 4096) -> dict:
        self.reload_settings()
        if self._is_local() or self._is_custom():
            messages = [
                {"role": "system", "content": system + " Output valid JSON only, without any markdown formatting."},
                {"role": "user", "content": prompt}
            ]
            raw = self._route_chat(messages, 0.2, max_tokens, response_format={"type": "json_object"})
            clean = _strip_json_fence(raw)
            try:
                return json.loads(clean)
            except json.JSONDecodeError as e:
                raise ValueError(f"Model returned unparseable JSON: {e}\nRaw output: {raw[:200]}")
        return openrouter_client.chat_json(prompt, system, model, max_tokens)

    def vision(self, prompt: str, image_b64: str, mime: str = "image/png", system: str = "Analyze the image.", model: Optional[str] = None, max_tokens: int = 1024) -> str:
        self.reload_settings()
        if self._is_local() or self._is_custom():
            messages = [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                        {"type": "text", "text": prompt}
                    ]
                }
            ]
            return self._route_chat(messages, 0.2, max_tokens)
        return openrouter_client.vision(prompt, image_b64, mime, system, model, max_tokens)

    def vision_from_file(self, prompt: str, image_path: str, system: str = "Analyze the image.", model: Optional[str] = None, max_tokens: int = 1024) -> str:
        self.reload_settings()
        if self._is_local() or self._is_custom():
            import base64
            path = Path(image_path)
            mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
            mime = mime_map.get(path.suffix.lower(), "image/png")
            with open(path, "rb") as f:
                image_b64 = base64.b64encode(f.read()).decode("utf-8")
            return self.vision(prompt, image_b64, mime, system, model, max_tokens)
        return openrouter_client.vision_from_file(prompt, image_path, system, model, max_tokens)

    def multi_turn(self, messages: list[dict], model: Optional[str] = None, max_tokens: int = 4096, temperature: float = 0.7) -> str:
        self.reload_settings()
        if self._is_local() or self._is_custom():
            return self._route_chat(messages, temperature, max_tokens)
        return openrouter_client.multi_turn(messages, model, max_tokens, temperature)


client = UnifiedAIClient()
LLMClient = UnifiedAIClient
