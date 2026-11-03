"""
Secrets Redaction Engine (WS-7).

Part of Security Architecture Phase 2.
Scans text, dictionaries, prompts, logs, and error traces to scrub sensitive
credentials, API keys, tokens, and DPAPI payloads before they can leak.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Set

# Compiled regular expressions for well-known API keys and credentials
REDACTION_PATTERNS = [
    # Google / Gemini API Keys (AIzaSy...)
    ("gemini_api_key", re.compile(r"\bAIza[0-9A-Za-z\-_]{30,45}\b")),
    # OpenRouter API Keys (sk-or-v1-...)
    ("openrouter_key", re.compile(r"sk-or-v1-[0-9a-fA-F]{64}")),
    # OpenAI / Anthropic / Generic API Keys (sk-...)
    ("generic_api_key", re.compile(r"\bsk-(?:proj-|ant-|live-)?[a-zA-Z0-9_\-]{32,}\b")),
    # Groq / Mistral Keys (gsk_...)
    ("groq_key", re.compile(r"\bgsk_[a-zA-Z0-9_\-]{32,}\b")),
    # Authorization Bearer Tokens (Bearer ...)
    ("bearer_token", re.compile(r"(?i)\bBearer\s+([a-zA-Z0-9_\-\.:]{24,})\b")),
    # Windows DPAPI Ciphertext Envelopes
    ("dpapi_envelope", re.compile(r"BRAHMA_DPAPI\x01:[^\s'\"<>\\]+")),
    # Fernet 32-byte urlsafe-base64 keys
    ("fernet_key", re.compile(r"\b[A-Za-z0-9_\-]{43}=\b")),
]

# Sensitive dictionary keys to redact in structured logging or JSON payloads
SENSITIVE_DICT_KEYS: Set[str] = {
    "api_key",
    "apikey",
    "gemini_api_key",
    "openrouter_api_key",
    "anthropic_api_key",
    "secret",
    "client_secret",
    "device_secret",
    "password",
    "passwd",
    "token",
    "access_token",
    "refresh_token",
    "sessionid",
    "private_key",
    "fernet_key",
}


def redact_text(text: str) -> str:
    """Scans and redacts known API key patterns from text strings."""
    if not text or not isinstance(text, str):
        return "" if text is None else str(text)

    redacted = text
    for pattern_name, regex in REDACTION_PATTERNS:
        if pattern_name == "bearer_token":
            # Preserve 'Bearer ' prefix, redact the token payload
            redacted = regex.sub(r"Bearer [REDACTED:bearer_token]", redacted)
        else:
            redacted = regex.sub(f"[REDACTED:{pattern_name}]", redacted)

    return redacted


def redact_data(obj: Any) -> Any:
    """Recursively traverses dictionaries, lists, and primitives, redacting sensitive fields and patterns."""
    if isinstance(obj, str):
        return redact_text(obj)
    elif isinstance(obj, dict):
        cleaned: Dict[str, Any] = {}
        for key, val in obj.items():
            key_str = str(key).lower()
            if any(sens in key_str for sens in SENSITIVE_DICT_KEYS):
                cleaned[key] = "[REDACTED]"
            else:
                cleaned[key] = redact_data(val)
        return cleaned
    elif isinstance(obj, (list, tuple)):
        cleaned_list = [redact_data(item) for item in obj]
        return type(obj)(cleaned_list)
    elif isinstance(obj, (bytes, bytearray)):
        try:
            decoded = obj.decode("utf-8", errors="ignore")
            redacted = redact_text(decoded)
            return redacted.encode("utf-8")
        except Exception:
            return obj
    return obj


def contains_unredacted_secret(text: str) -> bool:
    """Returns True if the text contains any raw unredacted credential patterns."""
    if not text or not isinstance(text, str):
        return False

    for _, regex in REDACTION_PATTERNS:
        if regex.search(text):
            return True

    return False
