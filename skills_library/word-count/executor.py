"""Executable skill: count words, lines and characters in a block of text.

The library imports this module lazily — only when the skill is actually
invoked — and calls ``execute(**kwargs)`` with the model-supplied arguments.
It may be synchronous or async; the library handles both.
"""

from __future__ import annotations

import re
from typing import Any, Dict


def execute(text: str = "", **kwargs: Any) -> Dict[str, Any]:
    """Return simple size statistics for ``text``."""
    text = str(text or "")
    words = re.findall(r"\S+", text)
    lines = text.splitlines() or ([] if not text else [text])
    return {
        "summary": (
            f"{len(words)} words, {len(lines)} lines, {len(text)} characters "
            f"({len(text.replace(' ', ''))} without spaces)."
        ),
        "words": len(words),
        "lines": len(lines),
        "characters": len(text),
        "characters_no_spaces": len(text.replace(" ", "")),
    }
