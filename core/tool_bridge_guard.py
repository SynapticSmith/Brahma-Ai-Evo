"""Recursion and repetition guard for the dynamic tool bridge."""
from __future__ import annotations

import threading
from typing import List, Tuple


class BridgeGuard:
    """Bound bridge recursion and repeated identical calls across threads."""

    def __init__(self) -> None:
        self._depth = 0
        self._recent: List[Tuple[str, str]] = []
        self._lock = threading.Lock()

    def enter(self, key: Tuple[str, str]) -> str:
        """Register one call; return a refusal string when it must not run."""
        with self._lock:
            if self._depth >= 3:
                return "Max recursion exceeded"
            self._depth += 1
            self._recent.append(key)
            if len(self._recent) > 100:
                self._recent = self._recent[-30:]
            if self._recent.count(key) > 5:
                self._depth -= 1
                return (
                    f"Tool '{key[0]}' skipped to prevent loop "
                    "(repeated calls detected)."
                )
            return ""

    def leave(self) -> None:
        """Release one call slot."""
        with self._lock:
            self._depth -= 1
