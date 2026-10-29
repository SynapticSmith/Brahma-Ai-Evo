"""Transient-failure retries for the agent loop.

A coding loop makes many provider calls per task. One 429, one dropped socket,
or one slow proxy is not a reason to abandon the turn — but retrying a malformed
request, a bad key, or a refused local port only wastes the user's time. This
module keeps the two apart: :func:`is_transient` classifies an exception,
:func:`call_with_retry` backs off with jitter and gives up at a bound.

Deliberately stdlib-only and provider-agnostic: no HTTP client is imported, so
the classifier works on whatever exception type the transport happens to raise
(``requests``, ``urllib``, ``httpx``, an SDK wrapper). Tests inject the sleeper.

Cancellation is honoured while waiting: the loop passes its ``threading.Event``
so a Ctrl-C (or the GUI stop shortcut) interrupts the backoff instead of
sleeping through it.
"""
from __future__ import annotations

import random
import re
import time
from typing import Any, Callable, Optional

#: Statuses worth another attempt. 401/403/404/422 are not here on purpose:
#: retrying them cannot change the answer.
TRANSIENT_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

_STATUS_RE = re.compile(r"\b(?:http(?: status| error)?|status|error code)\D{0,4}(\d{3})\b", re.IGNORECASE)

#: Substrings used when the exception carries no status. "connection refused"
#: is absent on purpose: a dead local server should fail fast, not after three
#: backoffs.
_TRANSIENT_HINTS = (
    "timeout",
    "timed out",
    "temporarily unavailable",
    "temporarily overloaded",
    "connection reset",
    "connection aborted",
    "connection broken",
    "remote end closed",
    "server disconnected",
    "bad gateway",
    "service unavailable",
    "gateway time",
    "too many requests",
    "rate limit",
    "overloaded",
)

#: OS-level errors that are always worth retrying.
_TRANSIENT_ERRORS = (TimeoutError, ConnectionResetError, ConnectionAbortedError)


class Cancelled(Exception):
    """Raised when the cancel event is set during a backoff wait."""


def http_status(exc: BaseException) -> Optional[int]:
    """Best-effort HTTP status from an exception, or ``None``.

    Looks at the shapes the common transports use (``.status``,
    ``.status_code``, ``.code``, ``.response.status_code``) before falling back
    to a status code in the message, which is what a plain ``RuntimeError`` from
    an SDK wrapper usually carries.
    """
    for attr in ("status", "status_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int) and 100 <= status <= 599:
        return status
    match = _STATUS_RE.search(str(exc) or "")
    return int(match.group(1)) if match else None


def is_transient(exc: BaseException) -> bool:
    """True when retrying the same call could plausibly succeed."""
    if isinstance(exc, Cancelled):
        return False
    status = http_status(exc)
    if status is not None:
        return status in TRANSIENT_STATUS
    if isinstance(exc, _TRANSIENT_ERRORS):
        return True
    reason = getattr(exc, "reason", None)  # urllib.error.URLError
    if isinstance(reason, BaseException):
        return is_transient(reason)
    text = str(exc).lower()
    return any(hint in text for hint in _TRANSIENT_HINTS)


def backoff_delay(attempt: int, *, base_delay: float = 0.8, max_delay: float = 8.0) -> float:
    """Exponential delay for ``attempt`` (1-based), capped at ``max_delay``.

    ``attempt=1`` is the wait *after* the first failure.
    """
    if base_delay <= 0:
        return 0.0
    return min(float(max_delay), float(base_delay) * (2 ** max(0, int(attempt) - 1)))


def call_with_retry(
    operation: Callable[[], Any],
    *,
    attempts: int = 3,
    base_delay: float = 0.8,
    max_delay: float = 8.0,
    jitter: float = 0.25,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Optional[Callable[[int, float, BaseException], None]] = None,
    cancel: Optional[Any] = None,
    random_source: Callable[[], float] = random.random,
) -> Any:
    """Run ``operation`` once, then retry only transient failures.

    ``on_retry(attempt, delay, error)`` is called before each wait, so the loop
    can emit telemetry. ``cancel`` is any object with ``is_set()`` and
    ``wait(timeout)`` — a ``threading.Event`` in practice.
    """
    attempts = max(1, int(attempts))
    last: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        try:
            return operation()
        except Exception as exc:  # noqa: BLE001 - classified below
            last = exc
            if attempt >= attempts or not is_transient(exc):
                raise
            delay = backoff_delay(attempt, base_delay=base_delay, max_delay=max_delay)
            if delay and jitter:
                delay += delay * float(jitter) * float(random_source())
            if on_retry is not None:
                try:
                    on_retry(attempt, delay, exc)
                except Exception:
                    pass
            if cancel is not None:
                if cancel.wait(delay):
                    raise Cancelled() from exc
            elif delay:
                sleep(delay)
    raise last if last is not None else RuntimeError("retry loop produced no result")
