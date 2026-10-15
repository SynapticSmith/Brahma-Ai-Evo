"""Gemini→OpenRouter failover *timing* policy, split out of ``BrahmaLive``.

Distinct from ``core.provider_router`` (the provider registry / per-provider
kill switches): this module owns only the "Gemini just throttled, so prefer
OpenRouter for a cooldown" decision.

That policy used to live as two attributes and two methods scattered through
``BrahmaLive`` (``main.py``). It is pure timestamp state with no other
dependencies, so it lives here and is unit-tested in isolation.
"""
from __future__ import annotations

import time

DEFAULT_COOLDOWN_SECONDS = 300.0


class ProviderFailover:
    """Tracks whether a recent Gemini throttle should keep OpenRouter first."""

    def __init__(self, default_cooldown_s: float = DEFAULT_COOLDOWN_SECONDS) -> None:
        self._default_cooldown_s = default_cooldown_s
        self._use_openrouter_first = False
        self._openrouter_until = 0.0

    def prefer_openrouter(self, now: float | None = None) -> bool:
        """True while a recent Gemini throttle should keep OpenRouter first."""
        if not self._use_openrouter_first:
            return False
        return (time.time() if now is None else now) < self._openrouter_until

    def throttle_to_openrouter(
        self, seconds: float | None = None, now: float | None = None
    ) -> None:
        """Record a Gemini throttle and route to OpenRouter for a cooldown."""
        window = self._default_cooldown_s if seconds is None else seconds
        self._use_openrouter_first = True
        self._openrouter_until = (time.time() if now is None else now) + window

    def reset(self) -> None:
        """Clear the throttle (e.g. the operator switched providers deliberately)."""
        self._use_openrouter_first = False
        self._openrouter_until = 0.0
