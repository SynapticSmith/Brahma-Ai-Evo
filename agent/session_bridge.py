"""Bind the GUI text path to durable :class:`~agent.session.AgentSession` rows.

The CLI owns its session lifecycle in ``agent/__main__.py``: it creates a
session, hands it to the loop, and resumes by opening the row. The GUI rebuilds
a fresh ``[system, user]`` seed on every turn and never created a row, so a HUD
restart was a hard reset even though the SQLite tables were already there.

These helpers are deliberately free of Qt and of any provider import: ``main``
imports this module lazily inside the turn, and the tests exercise it on a bare
``py -3.11`` with only pytest installed.

Two invariants keep a resumed transcript valid for OpenAI-compatible servers:

* the system prompt is never persisted. It is rebuilt for the current workspace
  and endpoint on every run, exactly like the CLI path; and
* only messages that are *new* relative to the stored history are appended.
  Re-seeding a resumed history must not duplicate the earlier turns.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .context import ContextBudget, ConversationContext
from .session import AgentSession, SessionRecord

#: The picker/label width used by the GUI. Kept here so the CLI and the HUD
#: agree on what "the same session" looks like in a list.
TITLE_LIMIT = 60


def title_for(prompt: str, limit: int = TITLE_LIMIT) -> str:
    """A short, single-line session title derived from the first request."""
    text = " ".join(str(prompt or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "\u2026"


def split_system(
    messages: Iterable[Dict[str, Any]],
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """Return ``(system, rest)`` for an assembled OpenAI message list.

    Only a leading system message counts. A system message anywhere else would
    be a protocol violation, and the harness never produces one, but leaving it
    in the transcript is safer than silently dropping it.
    """
    items = list(messages or [])
    if items and items[0].get("role") == "system":
        system = items[0].get("content")
        return (str(system) if system else None), items[1:]
    return None, items


def history(session: Optional[AgentSession]) -> List[Dict[str, Any]]:
    """The persisted turn history, without the leading system message.

    A session created by an older build (or by a caller that did store a system
    message) still loads: the system row is stripped, because the GUI builds a
    fresh prompt for the workspace it is currently running in.
    """
    if session is None:
        return []
    try:
        stored = session.messages()
    except Exception:
        return []
    _system, rest = split_system(stored)
    return rest


def seed_context(
    messages: Iterable[Dict[str, Any]],
    *,
    system: Optional[str] = None,
    budget: Optional[ContextBudget] = None,
) -> ConversationContext:
    """Build a context from an assembled message list.

    ``system`` wins over a stored leading system message: the caller has just
    built the prompt for *this* workspace and endpoint.
    """
    stored_system, rest = split_system(messages)
    context = ConversationContext(
        system=system if system is not None else stored_system,
        budget=budget or ContextBudget(),
    )
    for message in rest:
        context.append(dict(message))
    return context


def messages_after(
    history_messages: Sequence[Dict[str, Any]],
    seeded: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """The suffix of ``seeded`` that is not already in ``history_messages``.

    The GUI seeds ``history + [new user]``. Comparing the common prefix means a
    resumed turn appends one user message, not the whole transcript. If the
    history was compacted between reading and seeding the prefix simply stops
    earlier; the caller then appends the tail, which is still the new material.
    """
    stored = list(history_messages or [])
    items = list(seeded or [])
    prefix = 0
    for index, message in enumerate(items[: len(stored)]):
        if message != stored[index]:
            break
        prefix = index + 1
    return items[prefix:]


def transcript(messages: Iterable[Dict[str, Any]]) -> List[Dict[str, str]]:
    """User/assistant text for the HUD feed.

    Tool calls and tool results are execution detail, not conversation: the
    replay shows what the user asked and what Brahma answered. An assistant
    turn that only called tools has no text and is skipped.
    """
    out: List[Dict[str, str]] = []
    for message in messages or []:
        role = str(message.get("role") or "")
        if role not in ("user", "assistant"):
            continue
        text = str(message.get("content") or "").strip()
        if not text:
            continue
        # A compaction marker is a user-shaped synthetic message, not something
        # the user typed. Show it as a system-style note rather than a bubble.
        if role == "user" and text.startswith("[Earlier context"):
            out.append({"role": "system", "text": "Earlier context was compacted."})
            continue
        out.append({"role": role, "text": text})
    return out


def picker_label(record: SessionRecord) -> str:
    """One line for a modal session picker.

    The id is shortened to 8 characters only for display; the picker resolves
    the full row by index, and ``/resume <id>`` accepts the full id.
    """
    title = title_for(record.title or "(untitled)", 48)
    status = (record.status or "unknown")[:12]
    return f"{record.turn_count:>3} turns  {status:<12}  {title}  [{record.id[:8]}]"


def session_summary(record: SessionRecord) -> str:
    """The line ``/sessions`` writes for one row, including the full id."""
    return (
        f"{record.id}  {record.status:<12} {record.turn_count:>3} turns  "
        f"{title_for(record.title or '(untitled)', TITLE_LIMIT)}"
    )
