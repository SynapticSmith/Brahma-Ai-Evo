"""Parsing for the cosmetic-popup and approval timers.

Three independent timers used to decide how long a surface stayed visible:
the task card hid after 4 s and reset after 5 s, the daily briefing counted
down from 30 s, and an approval banner expired after 90 s. They now share one
Settings group (``popup_auto_dismiss_enabled`` / ``popup_dismiss_seconds``) and
one separate, capped approval budget (``approval_timeout_seconds``).

Keeping the parsing here means the three call sites cannot drift on what a
blank, zero or malformed value means. Approval is deliberately *not* covered by
the cosmetic toggle: an unattended prompt must still expire.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

#: Per-surface defaults when ``popup_dismiss_seconds`` is 0/blank. The task
#: card is a receipt; the briefing is something to read.
DEFAULT_TASK_CARD_SECONDS = 4.0
DEFAULT_BRIEFING_SECONDS = 30.0
MAX_DISMISS_SECONDS = 600.0

#: Approval is security-relevant, so it has its own floor and ceiling. It is
#: never tied to the cosmetic toggle and never defaults to infinite.
APPROVAL_DEFAULT_SECONDS = 90.0
APPROVAL_MIN_SECONDS = 10.0
APPROVAL_MAX_SECONDS = 600.0


def _settings() -> Dict[str, Any]:
    try:
        from memory import config_manager

        data = config_manager.load_settings()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off", "")
    if value is None:
        return default
    return bool(value)


def auto_dismiss_enabled() -> bool:
    """False keeps cosmetic popups open until the next task or a manual clear."""
    return _as_bool(_settings().get("popup_auto_dismiss_enabled", True), True)


def dismiss_seconds(surface_default: float) -> Optional[float]:
    """Seconds before a cosmetic popup hides, or ``None`` when it should stay.

    ``popup_dismiss_seconds <= 0`` means "use this surface's default", so the
    task card stays a quick receipt while the briefing gets time to be read.
    """
    if not auto_dismiss_enabled():
        return None
    raw = _settings().get("popup_dismiss_seconds", 0)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 0.0
    if value <= 0:
        value = float(surface_default)
    return max(1.0, min(value, MAX_DISMISS_SECONDS))


def approval_timeout(default: float = APPROVAL_DEFAULT_SECONDS) -> float:
    """The Settings approval budget, clamped to a sane, finite range."""
    raw = _settings().get("approval_timeout_seconds", default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = float(default)
    return max(APPROVAL_MIN_SECONDS, min(value, APPROVAL_MAX_SECONDS))


def clamp_approval_timeout(timeout: Optional[float]) -> float:
    """Clamp an explicitly supplied approval timeout (the API passes one).

    Unlike :func:`approval_timeout` this does not impose the 10 s Settings
    floor, because a caller may deliberately use a shorter budget; it still
    enforces the shared ceiling so no prompt can hang forever.
    """
    if timeout is None:
        return approval_timeout()
    try:
        value = float(timeout)
    except (TypeError, ValueError):
        return approval_timeout()
    return max(1.0, min(value, APPROVAL_MAX_SECONDS))
