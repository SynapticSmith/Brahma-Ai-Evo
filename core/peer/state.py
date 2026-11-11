"""Wire task state machine and transition rules for BTE-1 (ADR-030, ADR-032)."""

from __future__ import annotations

from enum import Enum
import logging
from typing import Optional, Set

logger = logging.getLogger(__name__)


class WireState(str, Enum):
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input_required"
    AUTH_REQUIRED = "auth_required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


WIRE_STATES: Set[str] = {s.value for s in WireState}

TERMINAL_STATES: Set[str] = {
    WireState.COMPLETED.value,
    WireState.FAILED.value,
    WireState.CANCELED.value,
    WireState.REJECTED.value,
}

VALID_TRANSITIONS: dict[str, Set[str]] = {
    # initial state
    "": {WireState.SUBMITTED.value, WireState.REJECTED.value},
    WireState.SUBMITTED.value: {
        WireState.WORKING.value,
        WireState.CANCELED.value,
        WireState.REJECTED.value,
    },
    WireState.WORKING.value: {
        WireState.COMPLETED.value,
        WireState.FAILED.value,
        WireState.CANCELED.value,
        WireState.INPUT_REQUIRED.value,
        WireState.AUTH_REQUIRED.value,
        WireState.UNKNOWN.value,
    },
    WireState.UNKNOWN.value: {
        WireState.COMPLETED.value,
        WireState.FAILED.value,
        WireState.CANCELED.value,
    },
    WireState.INPUT_REQUIRED.value: {
        WireState.WORKING.value,
        WireState.CANCELED.value,
        WireState.FAILED.value,
    },
    WireState.AUTH_REQUIRED.value: {
        WireState.WORKING.value,
        WireState.CANCELED.value,
        WireState.FAILED.value,
    },
    # Terminal states are strictly immutable
    WireState.COMPLETED.value: set(),
    WireState.FAILED.value: set(),
    WireState.CANCELED.value: set(),
    WireState.REJECTED.value: set(),
}


def is_terminal(state: str) -> bool:
    """Check if the given wire state is terminal."""
    return state.lower() in TERMINAL_STATES


def validate_transition(current: str, next_state: str) -> bool:
    """Validate whether transitioning from current to next_state is valid.

    Returns True if allowed. If current is terminal, returns False (late event).
    """
    cur = (current or "").lower()
    nxt = (next_state or "").lower()

    if cur in TERMINAL_STATES:
        logger.warning("Ignoring late transition to %r for terminal state %r", nxt, cur)
        return False

    allowed = VALID_TRANSITIONS.get(cur, set())
    return nxt in allowed


def is_retry_permitted(effect: str, state: str, started_at: Optional[str] = None) -> bool:
    """Invariant S-05 and S-07: check if a task can be retried.

    - S-05: A retry of a mutating task is refused unless the worker proves it never started.
    - S-07: A lease expiry yields unknown, not failed and not a retry.
    """
    eff = (effect or "mutating").lower()
    st = (state or "").lower()
    if eff in ("none", "idempotent"):
        return True
    # For mutating tasks:
    # If the task was started (started_at is set or state reached working/unknown/completed), retry is strictly refused.
    if started_at or st in (WireState.WORKING.value, WireState.UNKNOWN.value, WireState.COMPLETED.value):
        return False
    # If unstarted submitted or rejected:
    if st in (WireState.SUBMITTED.value, WireState.REJECTED.value):
        return True
    return False

