"""
Clipboard Sentry for Brahma AI.
Monitors the Windows clipboard in the background for actionable technical content:
- Tracebacks / Exceptions
- JSON structures
- URLs or SQL queries
- Code snippets
Notifies Brahma so it can offer quick contextual assistance.
"""

import time
import threading
import json
import re
from typing import Optional, Callable

_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_\-]{20,}|AIza[0-9A-Za-z_\-]{35}|gh[pousr]_[A-Za-z0-9]{25,}|xox[baprs]-[A-Za-z0-9\-]{15,}|AKIA[0-9A-Z]{16})\b"),
    re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9_\-\.=]{16,}"),
    re.compile(r"(?i)\b((?:api[_-]?key|secret|password|passwd|access[_-]?token|auth[_-]?token)\s*[:=]\s*['\"]?)([^\s'\"]{8,})"),
)


def redact_secrets(text: str) -> str:
    """Redact recognizable API keys, bearer tokens, passwords, and private keys."""
    if not text:
        return ""
    out = text
    out = _SECRET_PATTERNS[0].sub("[REDACTED_PRIVATE_KEY]", out)
    out = _SECRET_PATTERNS[1].sub("[REDACTED_KEY]", out)
    out = _SECRET_PATTERNS[2].sub(r"\1[REDACTED_TOKEN]", out)
    out = _SECRET_PATTERNS[3].sub(r"\1[REDACTED_SECRET]", out)
    return out


def contains_secret(text: str) -> bool:
    """True if `text` matches any known credential/secret pattern."""
    if not text:
        return False
    return any(p.search(text) is not None for p in _SECRET_PATTERNS)


class ClipboardSentry:
    def __init__(self, callback: Optional[Callable[[str, str], None]] = None):
        self._callback = callback
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_clip = ""

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _classify_content(self, text: str) -> Optional[str]:
        text = text.strip()
        if len(text) < 15:
            return None

        # Check for python / node / java traceback
        if "Traceback (most recent call last):" in text or "Exception:" in text or "Error:" in text and "\n" in text:
            return "error_traceback"

        # Check for JSON
        if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
            try:
                json.loads(text)
                return "json_data"
            except Exception:
                pass

        # Check for SQL queries
        if re.search(r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)\s+", text, re.IGNORECASE):
            return "sql_query"

        # Check for YouTube URL
        if re.search(r"(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[a-zA-Z0-9_-]+", text):
            return "youtube_url"

        # Check for GitHub repository URL
        if re.search(r"(https?://)?(www\.)?github\.com/[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+", text):
            return "github_url"

        # Check for code blocks
        if any(keyword in text for keyword in ["def ", "class ", "function ", "import ", "const ", "let ", "var "]) and "\n" in text:
            return "code_snippet"

        return None

    def _monitor_loop(self):
        import pyperclip
        try:
            self._last_clip = (pyperclip.paste() or "").strip()
        except Exception:
            self._last_clip = ""

        while self._running:
            time.sleep(1.2)
            try:
                current = (pyperclip.paste() or "").strip()
                if current and current != self._last_clip:
                    self._last_clip = current
                    category = self._classify_content(current)
                    if category and self._callback:
                        self._callback(category, redact_secrets(current))
            except Exception:
                pass
