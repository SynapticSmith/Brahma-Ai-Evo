"""One authority contract for the pre-LLM routing plane.

Plane A (see ``docs/ROUTING-AUDIT.md`` §2) may claim a turn before the model
runs only when it can prove one of four authorities:

* ``syntax``          — the user wrote the control grammar (``/resume``,
                        ``open <app>``, ``email <recipient>``);
* ``exact_alias``     — the normalised utterance equals a name the user saved;
* ``owned_state``     — the system is the source of truth for the mode being
                        changed (offline mode, reply mode, active device);
* ``verified_target`` — a real device/entity resolves uniquely *now*.

Similarity, token overlap, substring presence and a confidence score are **not**
authorities. A router that cannot prove one of the four must decline and let
the model choose a tool. ``RoutingClaim`` deliberately has no ``confidence``
field: the field invites the bug back.

The second half of this module is the inventory of every router that still runs
before the model. It is the single list the audit asked for; adding a router
without a record (and, for a text-derived claim, a negative test) fails
``tests/test_routing_authority.py``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional, Sequence, Tuple


class Authority(str, Enum):
    """The four claims a pre-LLM router is allowed to make."""

    SYNTAX = "syntax"
    EXACT_ALIAS = "exact_alias"
    OWNED_STATE = "owned_state"
    VERIFIED_TARGET = "verified_target"


@dataclass(frozen=True)
class RoutingClaim:
    """A pre-LLM decision: either a proven claim or an explicit decline."""

    owner: str
    claimed: bool
    reason: str = ""
    authority: Optional[Authority] = None

    def __post_init__(self) -> None:
        if self.claimed and self.authority is None:
            raise ValueError(f"{self.owner} claimed without an authority")
        if not self.claimed and self.authority is not None:
            raise ValueError(f"{self.owner} declined but named an authority")

    @classmethod
    def claim(
        cls, owner: str, authority: Authority, reason: str = ""
    ) -> "RoutingClaim":
        return cls(owner=owner, claimed=True, reason=reason, authority=authority)

    @classmethod
    def decline(cls, owner: str, reason: str = "") -> "RoutingClaim":
        return cls(owner=owner, claimed=False, reason=reason)

    def __bool__(self) -> bool:
        return self.claimed


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> list[str]:
    """Lowercase word tokens; punctuation and case are not authority."""
    return _TOKEN_RE.findall((text or "").lower())


def normalize(text: str) -> str:
    """A stable token string used for exact comparisons."""
    return " ".join(tokens(text))


def has_phrase(text: str, phrase: str) -> bool:
    """True only when ``phrase`` appears as whole words.

    This is the replacement for ``phrase in text.lower()``. It keeps the old
    convenience (a phrase may sit anywhere in a sentence) and removes the
    collision class that hijacked "backup" for "ac" and "microphone" for
    "phone" (ROUTING-AUDIT §4, R1).
    """
    needle = tokens(phrase)
    if not needle:
        return False
    hay = tokens(text)
    size = len(needle)
    return any(hay[i : i + size] == needle for i in range(len(hay) - size + 1))


def any_phrase(text: str, phrases: Iterable[str]) -> bool:
    """``has_phrase`` over a collection; the caller keeps the phrase list."""
    return any(has_phrase(text, phrase) for phrase in phrases)


def is_phrase(text: str, phrase: str) -> bool:
    """Whole-utterance equality, ignoring case and punctuation."""
    return bool(normalize(text)) and normalize(text) == normalize(phrase)


def exact_alias(
    text: str, aliases: Iterable[str], *, strip_prefixes: Sequence[str] = ()
) -> Optional[str]:
    """Return the configured alias the utterance names exactly, else ``None``.

    At most one imperative prefix ("open ", "run ", ...) is stripped; the
    remainder must equal an alias. This is deliberately not a fuzzy match: a
    near miss goes to the model, which sees the same tool surface.
    """
    spoken = normalize(text)
    for prefix in strip_prefixes:
        head = normalize(prefix)
        if head and (spoken == head or spoken.startswith(head + " ")):
            spoken = spoken[len(head) :].strip()
            break
    if not spoken:
        return None
    for alias in aliases:
        if normalize(alias) == spoken or normalize(str(alias).replace("_", " ")) == spoken:
            return alias
    return None


# ---------------------------------------------------------------------------
# The inventory
# ---------------------------------------------------------------------------

LIVE = "live"
CONVERTED = "converted"
DELEGATED = "delegated"
DORMANT = "dormant"
REMOVED = "removed"

STATUSES = frozenset({LIVE, CONVERTED, DELEGATED, DORMANT, REMOVED})
CLAIMING_STATUSES = frozenset({LIVE, CONVERTED})
#: Text-derived claims need a negative test. An ``owned_state`` claim is
#: guarded by the state flag itself, which its feature tests exercise.
TEXT_AUTHORITIES = frozenset(
    {Authority.SYNTAX, Authority.EXACT_ALIAS, Authority.VERIFIED_TARGET}
)


@dataclass(frozen=True)
class RouterRecord:
    """One pre-LLM consumer, its authority, and how it declines."""

    id: str
    entry: str
    status: str
    authority: Optional[Authority]
    note: str
    decline_test: str = ""


ROUTERS: Tuple[RouterRecord, ...] = (
    RouterRecord(
        id="A1",
        entry="core/text_turn_controller.py:handle_agent_session_command",
        status=LIVE,
        authority=Authority.SYNTAX,
        note="Anchored slash grammar; anything else returns False.",
        decline_test="tests/test_routing_authority.py::test_session_commands_need_the_slash",
    ),
    RouterRecord(
        id="A2",
        entry="core/text_turn_controller.py:on_text_command[plugin]",
        status=DELEGATED,
        authority=None,
        note="Third-party hook owns its own claim; the host only honours True.",
    ),
    RouterRecord(
        id="A3",
        entry="core/text_turn_controller.py:on_text_command[modal reply modes]",
        status=LIVE,
        authority=Authority.OWNED_STATE,
        note="Only when _reply_mode/_ig_reply_mode/_email_mode is already set.",
    ),
    RouterRecord(
        id="A4",
        entry="core/text_turn_controller.py:on_text_command[offline mode]",
        status=LIVE,
        authority=Authority.OWNED_STATE,
        note="Changes a system-owned mode with an explicit phrase.",
        decline_test="tests/test_routing_authority.py::test_offline_phrases_are_word_bounded",
    ),
    RouterRecord(
        id="A5",
        entry="main.py:_extract_skill_creation_goal",
        status=LIVE,
        authority=Authority.SYNTAX,
        note="Anchored 'make a skill' grammar; no goal means a prompt, not a turn.",
        decline_test="tests/test_routing_authority.py::test_skill_creation_needs_the_grammar",
    ),
    RouterRecord(
        id="A6",
        entry="core/dynamic_registry.py:find_matching_skill",
        status=CONVERTED,
        authority=Authority.EXACT_ALIAS,
        note="Was a >=70 fuzzy score; now the whole utterance must name a trigger/alias. The model still sees every active skill as a dynamic_skill tool.",
        decline_test="tests/test_routing_authority.py::test_dynamic_skills_need_an_exact_trigger",
    ),
    RouterRecord(
        id="A7",
        entry="core/text_turn_controller.py:on_text_command[email/calendar]",
        status=LIVE,
        authority=Authority.SYNTAX,
        note="Anchored imperative ('email ...') or a fixed phrase list matched on word boundaries.",
        decline_test="tests/test_routing_authority.py::test_email_and_calendar_phrases_are_word_bounded",
    ),
    RouterRecord(
        id="A7b",
        entry="main.py:_looks_like_daily_briefing_request",
        status=LIVE,
        authority=Authority.SYNTAX,
        note="Fixed briefing phrasings, matched on word boundaries.",
        decline_test="tests/test_routing_authority.py::test_daily_briefing_phrases_are_word_bounded",
    ),
    RouterRecord(
        id="A8",
        entry="main.py:_looks_like_{presentation,spreadsheet,website,code}_request",
        status=LIVE,
        authority=Authority.SYNTAX,
        note="Requires an action verb plus a domain noun, both word-bounded.",
        decline_test="tests/test_routing_authority.py::test_builder_requests_need_an_action_and_a_noun",
    ),
    RouterRecord(
        id="A9",
        entry="core/text_turn_controller.py:on_text_command[system fast paths]",
        status=LIVE,
        authority=Authority.SYNTAX,
        note="Local-machine diagnostics with fixed phrases; no filesystem or network claim.",
        decline_test="tests/test_routing_authority.py::test_system_phrases_are_word_bounded",
    ),
    RouterRecord(
        id="A9b",
        entry="core/text_turn_controller.py:on_text_command[auto-heal]",
        status=LIVE,
        authority=Authority.SYNTAX,
        note="Developer/self-heal grammar with fixed phrases and anchored 'always/never' rules.",
        decline_test="tests/test_routing_authority.py::test_heal_phrases_are_word_bounded",
    ),
    RouterRecord(
        id="A10",
        entry="core/text_turn_controller.py:on_text_command[volume]",
        status=LIVE,
        authority=Authority.SYNTAX,
        note="Local-machine control; a phone/mobile target declines first.",
        decline_test="tests/test_routing_authority.py::test_volume_phrases_are_word_bounded",
    ),
    RouterRecord(
        id="A11",
        entry="core/text_turn_controller.py:on_text_command[open app]",
        status=CONVERTED,
        authority=Authority.EXACT_ALIAS,
        note="Was a substring search over the alias table (and matched an empty candidate); now exact alias only.",
        decline_test="tests/test_routing_authority.py::test_open_app_needs_an_exact_alias",
    ),
    RouterRecord(
        id="A12",
        entry="core/text_turn_controller.py:on_text_command[instagram source]",
        status=LIVE,
        authority=Authority.OWNED_STATE,
        note="The source is set by the Instagram channel, not inferred from words.",
    ),
    RouterRecord(
        id="A13",
        entry="core/text_turn_controller.py:on_text_command[meeting mode]",
        status=LIVE,
        authority=Authority.SYNTAX,
        note="Exact set of meeting-mode phrasings.",
        decline_test="tests/test_routing_authority.py::test_meeting_mode_phrases_are_exact",
    ),
    RouterRecord(
        id="A14",
        entry="core/attention_flow.py:handle_attention_response",
        status=LIVE,
        authority=Authority.OWNED_STATE,
        note="Only inside an open attention window owned by the app.",
    ),
    RouterRecord(
        id="A15",
        entry="main.py:_build_task_plan",
        status=REMOVED,
        authority=None,
        note="Domain guessing deleted; the preview is a neutral four-step placeholder until the model speaks.",
    ),
    RouterRecord(
        id="A16",
        entry="core/device_flow.py:handle_brahma_connect_command",
        status=CONVERTED,
        authority=Authority.VERIFIED_TARGET,
        note="Phone words are now token-bounded and a paired device must resolve; no device declines.",
        decline_test="tests/test_routing_authority.py::test_brahma_connect_requires_a_phone_word_and_a_device",
    ),
    RouterRecord(
        id="A16b",
        entry="smart_home/smart_device_manager.py:route_command",
        status=LIVE,
        authority=Authority.OWNED_STATE,
        note="Rewrites a bare 'turn on' style command to the active device; no active device means no rewrite.",
        decline_test="tests/test_routing_authority.py::test_active_device_rewrite_needs_an_active_device",
    ),
    RouterRecord(
        id="A17",
        entry="core/device_flow.py:handle_smart_home_command",
        status=REMOVED,
        authority=None,
        note="Deleted in Phase 0; smart_home_control is a model tool choice.",
    ),
    RouterRecord(
        id="A18",
        entry="main.py:_looks_like_screen_request",
        status=LIVE,
        authority=Authority.SYNTAX,
        note="Fixed screen phrasings plus a screen noun + request verb; no bare 'error' claim.",
        decline_test="tests/test_routing_authority.py::test_screen_phrases_are_word_bounded",
    ),
    RouterRecord(
        id="A20",
        entry="core/intent_dispatcher.py",
        status=DORMANT,
        authority=None,
        note="Imported by tests only. Do not wire it as a fourth pre-LLM router (ROUTING-AUDIT §7 Q6).",
    ),
    RouterRecord(
        id="A21",
        entry="core/execution_reflection.py",
        status=DORMANT,
        authority=None,
        note="Post-execution idea with no caller; not a router and not a second dispatch path.",
    ),
)


def catalogue() -> Tuple[RouterRecord, ...]:
    """The documented Plane-A inventory, in the order the audit lists it."""
    return ROUTERS


def validate() -> list[str]:
    """Return a list of catalogue problems; empty means the inventory is sound."""
    problems: list[str] = []
    seen: set[str] = set()
    for record in ROUTERS:
        if record.id in seen:
            problems.append(f"{record.id}: duplicate id")
        seen.add(record.id)
        if not record.entry:
            problems.append(f"{record.id}: missing entry point")
        if not record.note:
            problems.append(f"{record.id}: missing note")
        if record.status not in STATUSES:
            problems.append(f"{record.id}: unknown status {record.status!r}")
            continue
        if record.status in CLAIMING_STATUSES:
            if record.authority is None:
                problems.append(f"{record.id}: claims a turn without an authority")
            if record.authority in TEXT_AUTHORITIES and not record.decline_test:
                problems.append(f"{record.id}: text claim without a decline test")
        elif record.authority is not None:
            problems.append(f"{record.id}: {record.status} record still declares an authority")
    return problems
