"""Phase 4: every pre-LLM router proves an authority and has a decline path.

The audit's rule (``docs/ROUTING-AUDIT.md`` §2) is that Plane-A code may claim
a turn only for explicit syntax, an exact user alias, state the system owns, or
a verified live target. Similarity and substrings are not authority.

These tests do three jobs:

1. pin the inventory (``core.routing.ROUTERS``) and prove each text-derived
   claim has a negative test in this module;
2. prove the shared matcher cannot reproduce the audit's substring collisions
   (``ac`` in "backup", ``phone`` in "microphone", ``fan`` in "fantastic");
3. prove the two converted routers (dynamic skills, open-app) decline on a
   near miss, and that Brahma Connect needs both a phone word and a live
   device.
"""

from __future__ import annotations

import importlib.util
import json
import types
from pathlib import Path

import pytest

from actions.open_app import _APP_ALIASES
from core import routing
from core.device_flow import DeviceFlow
from core.dynamic_registry import DynamicToolRegistry
from core.text_turn_controller import (
    BATTERY_CHECK_PHRASES,
    BRIGHTNESS_PHRASES,
    BUG_TRIGGER_PHRASES,
    CALENDAR_CHECK_PHRASES,
    DIAGNOSTIC_PHRASES,
    EMAIL_CHECK_PHRASES,
    HEAL_PHRASES,
    OFFLINE_PHRASES,
    PATCH_HISTORY_PHRASES,
    RAM_CHECK_PHRASES,
    ROLLBACK_PHRASES,
    VOLUME_PHRASES,
    TextTurnController,
    looks_like_software_request,
)
from smart_home.smart_device_manager import SmartDeviceManager

ROOT = Path(__file__).resolve().parent.parent
THIS_TEST = "tests/test_routing_authority.py"

GENERIC_PLAN = [
    "Understand the command",
    "Choose the right tool",
    "Execute the task",
    "Return the result",
]

REMAINING_PHRASES = (
    OFFLINE_PHRASES
    + EMAIL_CHECK_PHRASES
    + CALENDAR_CHECK_PHRASES
    + RAM_CHECK_PHRASES
    + BATTERY_CHECK_PHRASES
    + BRIGHTNESS_PHRASES
    + DIAGNOSTIC_PHRASES
    + BUG_TRIGGER_PHRASES
    + HEAL_PHRASES
    + ROLLBACK_PHRASES
    + PATCH_HISTORY_PHRASES
    + VOLUME_PHRASES
)

# The ordinary prompts the audit measured against the old smart-home matcher.
AUDIT_PROMPTS = (
    "Make a backup of my photos",
    "Track my parcel",
    "Practice my Spanish",
    "What is the exact time",
    "React to this message",
    "Write a light summary",
    "Highlight the important lines",
    "Explain the challenge",
    "Shall I continue",
    "Show me a fantastic idea",
    "Click the plug-in settings",
    "Compare these two contracts",
)


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


def test_claim_requires_an_authority_and_decline_does_not():
    claim = routing.RoutingClaim.claim("demo", routing.Authority.SYNTAX, "slash")
    assert claim and claim.authority is routing.Authority.SYNTAX

    decline = routing.RoutingClaim.decline("demo", "not the grammar")
    assert not decline and decline.authority is None

    with pytest.raises(ValueError):
        routing.RoutingClaim(owner="demo", claimed=True)
    with pytest.raises(ValueError):
        routing.RoutingClaim(
            owner="demo", claimed=False, authority=routing.Authority.SYNTAX
        )


def test_claim_has_no_confidence_field():
    assert "confidence" not in routing.RoutingClaim.__dataclass_fields__


def test_the_four_authorities_are_the_whole_vocabulary():
    assert {a.value for a in routing.Authority} == {
        "syntax",
        "exact_alias",
        "owned_state",
        "verified_target",
    }


# ---------------------------------------------------------------------------
# The inventory
# ---------------------------------------------------------------------------


def test_catalogue_is_sound():
    assert routing.validate() == []


def test_catalogue_keeps_the_documented_count():
    # Update this number deliberately when a Plane-A router is added or removed.
    assert len(routing.catalogue()) == 23


def _resolve(node_id: str):
    path, _, name = node_id.partition("::")
    if path == THIS_TEST:
        return globals().get(name)
    assert path, node_id
    target = ROOT / path
    spec = importlib.util.spec_from_file_location(f"_decline_{path}_{name}", target)
    assert spec and spec.loader, node_id
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, name, None)


def test_every_decline_test_named_in_the_catalogue_exists():
    missing = [
        record.decline_test
        for record in routing.catalogue()
        if record.decline_test and _resolve(record.decline_test) is None
    ]
    assert missing == []


def test_every_text_claim_names_a_decline_test():
    missing = [
        record.id
        for record in routing.catalogue()
        if record.status in routing.CLAIMING_STATUSES
        and record.authority in routing.TEXT_AUTHORITIES
        and not record.decline_test
    ]
    assert missing == []


def test_dormant_routers_are_not_imported_by_app_code():
    dormant = ("core.intent_dispatcher", "core.execution_reflection")
    skip_dirs = {"tests", ".venv", ".git", "__pycache__", "docs", "node_modules"}
    offenders = []
    for path in ROOT.rglob("*.py"):
        if any(part in skip_dirs for part in path.parts):
            continue
        if path.name in {"intent_dispatcher.py", "execution_reflection.py"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for module in dormant:
            tail = module.split(".")[-1]
            if f"import {module}" in text or f"from {module} import" in text:
                offenders.append(f"{path.relative_to(ROOT)} imports {module}")
            elif f"from core import {tail}" in text:
                offenders.append(f"{path.relative_to(ROOT)} imports {module}")
    assert offenders == []


# ---------------------------------------------------------------------------
# Shared matching: word boundaries, not substrings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "word,ordinary",
    [
        ("ac", "make a backup of my photos"),
        ("phone", "open the microphone settings"),
        ("fan", "that was fantastic"),
        ("light", "highlight the important lines"),
        ("hall", "explain the challenge"),
        ("room", "mushroom risotto recipe"),
    ],
)
def test_has_phrase_cannot_match_inside_another_word(word, ordinary):
    assert routing.has_phrase(ordinary, word) is False


@pytest.mark.parametrize("phrase", REMAINING_PHRASES)
def test_every_fixed_phrase_is_word_bounded(phrase):
    glued = phrase.replace(" ", "") + "ish"
    assert routing.has_phrase(f"please {glued} now", phrase) is False


def test_the_audit_corpus_matches_no_remaining_phrase_gate():
    for prompt in AUDIT_PROMPTS:
        assert routing.any_phrase(prompt, REMAINING_PHRASES) is False, prompt


def test_exact_alias_strips_at_most_one_imperative():
    aliases = ("chrome", "google chrome", "visual studio code")

    assert routing.exact_alias("chrome", aliases) == "chrome"
    assert routing.exact_alias("Google Chrome", aliases) == "google chrome"
    assert routing.exact_alias("open chrome", aliases, strip_prefixes=("open",)) == "chrome"
    assert routing.exact_alias("chrome browser", aliases) is None
    assert routing.exact_alias("open the chrome", aliases, strip_prefixes=("open",)) is None
    assert routing.exact_alias("", aliases) is None


# ---------------------------------------------------------------------------
# The converted / live routers
# ---------------------------------------------------------------------------


def test_session_commands_need_the_slash():
    controller = TextTurnController(types.SimpleNamespace())
    assert controller.handle_agent_session_command("sessions") is False
    assert controller.handle_agent_session_command("resume please") is False
    assert controller.handle_agent_session_command("skills") is False
    assert controller.handle_agent_session_command("scripts") is False
    assert controller.handle_agent_session_command("cookbook") is False
    assert controller.handle_agent_session_command("") is False


def test_slash_commands_skills_and_cookbooks():
    logs = []
    host = types.SimpleNamespace(ui=types.SimpleNamespace(write_log=logs.append))
    controller = TextTurnController(host)

    assert controller.handle_agent_session_command("/skills") is True
    assert any("Available skills" in line for line in logs)

    logs.clear()
    assert controller.handle_agent_session_command("/cookbook inversion-thinking") is True
    assert any("COOKBOOK: inversion-thinking" in line for line in logs)

    logs.clear()
    assert controller.handle_agent_session_command("/scripts") is True
    assert any("Verified scripts" in line for line in logs)

    logs.clear()
    assert controller.handle_agent_session_command("/domains") is True
    assert any("Taxonomy Domain" in line for line in logs)

    logs.clear()
    assert controller.handle_agent_session_command("/brain buddhism") is True
    assert any("Second Brain" in line for line in logs)


def test_offline_phrases_are_word_bounded():
    assert routing.any_phrase("go offline", OFFLINE_PHRASES) is True
    assert routing.any_phrase("i am going offline for a while", OFFLINE_PHRASES) is False
    assert routing.any_phrase("make a backup", OFFLINE_PHRASES) is False


def test_skill_creation_needs_the_grammar():
    from main import _extract_skill_creation_goal

    assert _extract_skill_creation_goal("write a letter to Sam") is None
    assert _extract_skill_creation_goal("build a website") is None
    assert _extract_skill_creation_goal("make a skill that tidies my desktop") is not None


def _skill(name="daily_report", triggers=("daily report",), aliases=("status report",)):
    return types.SimpleNamespace(
        name=name,
        description="Builds a daily report from tracked work.",
        active=True,
        triggers=list(triggers),
        aliases=list(aliases),
        parameters={"properties": {}, "required": []},
        created_at=0,
    )


@pytest.fixture
def one_dynamic_skill(monkeypatch):
    monkeypatch.setattr(DynamicToolRegistry, "_skills", {"daily_report": _skill()})
    monkeypatch.setattr(DynamicToolRegistry, "_initialized", True)
    return DynamicToolRegistry


def test_dynamic_skills_need_an_exact_trigger(one_dynamic_skill):
    assert one_dynamic_skill.find_matching_skill("daily report") == ("daily_report", {})
    assert one_dynamic_skill.find_matching_skill("run status report") == (
        "daily_report",
        {},
    )
    # Near misses used to clear the >=70 fuzzy threshold. The model still sees
    # the skill as a dynamic_skill declaration; it just is not a pre-LLM claim.
    assert one_dynamic_skill.find_matching_skill("give me the daily report for today") is None
    assert one_dynamic_skill.find_matching_skill("report my daily progress") is None


def test_open_app_needs_an_exact_alias():
    assert routing.exact_alias("spotify", _APP_ALIASES) == "spotify"
    assert routing.exact_alias("spotify please", _APP_ALIASES) is None
    # The old substring check matched every alias for an empty candidate.
    assert routing.exact_alias("", _APP_ALIASES) is None
    assert routing.exact_alias("chrome browser", _APP_ALIASES) is None


class _UI:
    muted = True

    def __init__(self):
        self.logs = []

    def update_task_workspace(self, **kwargs):
        pass

    def write_log(self, message, *args, **kwargs):
        self.logs.append(str(message))

    def set_state(self, *args, **kwargs):
        pass


class _ConnectHost:
    def __init__(self):
        self.ui = _UI()

    def speak(self, *args, **kwargs):
        pass


@pytest.fixture
def connected_phone(monkeypatch):
    calls = []
    devices = json.dumps(
        {"devices": [{"name": "Pixel", "device_id": "pixel-1", "platform": "android"}]}
    )
    monkeypatch.setattr(
        "core.device_flow.connect_list_devices", lambda **kwargs: devices
    )

    def fake_execute(parameters=None, player=None):
        calls.append(parameters)
        return json.dumps({"success": True, "detail": "Done."})

    monkeypatch.setattr("core.device_flow.connect_execute", fake_execute)
    return calls


def test_brahma_connect_requires_a_phone_word_and_a_device(connected_phone):
    flow = DeviceFlow(_ConnectHost())

    # "microphone" contains "phone" but is not a phone reference. The old
    # matcher would have resolved the phone and launched "microphone settings".
    assert flow.handle_brahma_connect_command("open microphone settings") is False
    assert connected_phone == []

    assert flow.handle_brahma_connect_command("open settings on my phone") is True
    assert connected_phone and connected_phone[0]["action"] == "launch_app"


def test_active_device_rewrite_needs_an_active_device():
    manager = SmartDeviceManager()
    devices = [{"name": "Bedroom Lamp"}]
    manager.set_active_device(None, None)
    try:
        assert manager.route_command("turn on", devices) == "turn on"
        manager.set_active_device("lamp-1", "Bedroom Lamp")
        assert manager.route_command("turn on", devices) == "turn on of Bedroom Lamp"
        assert (
            manager.route_command("turn on the bedroom lamp", devices)
            == "turn on the bedroom lamp"
        )
    finally:
        manager.set_active_device(None, None)


def test_email_and_calendar_phrases_are_word_bounded():
    assert routing.any_phrase("check my inbox", EMAIL_CHECK_PHRASES) is True
    assert routing.any_phrase("what is on my calendar", CALENDAR_CHECK_PHRASES) is False
    assert routing.any_phrase("what's on my calendar", CALENDAR_CHECK_PHRASES) is True
    assert routing.any_phrase("calendar mechanics", CALENDAR_CHECK_PHRASES) is False


def test_daily_briefing_phrases_are_word_bounded():
    from main import _looks_like_daily_briefing_request

    assert _looks_like_daily_briefing_request("morning update") is True
    assert _looks_like_daily_briefing_request("update my morning routine") is False
    assert routing.has_phrase("dailybriefing", "daily briefing") is False


def test_builder_requests_need_an_action_and_a_noun():
    from main import (
        _looks_like_code_request,
        _looks_like_presentation_request,
        _looks_like_website_request,
    )

    assert _looks_like_presentation_request("make a pitch deck") is True
    assert _looks_like_presentation_request("write a light summary") is False
    assert _looks_like_code_request("that was fantastic") is False
    assert _looks_like_code_request("write a python script") is True
    assert looks_like_software_request("write a script for a play") is False
    assert looks_like_software_request("write a python script") is True
    assert looks_like_software_request("build me a website") is True
    assert _looks_like_website_request("build me a landing page") is True
    assert _looks_like_website_request("the cobweb in the corner") is False


def test_system_phrases_are_word_bounded():
    assert routing.any_phrase("check ram", RAM_CHECK_PHRASES) is True
    assert routing.any_phrase("check the program", RAM_CHECK_PHRASES) is False
    assert routing.any_phrase("dim screen", BRIGHTNESS_PHRASES) is True
    assert routing.any_phrase("system status", DIAGNOSTIC_PHRASES) is True


def test_heal_phrases_are_word_bounded():
    assert routing.any_phrase("fix that bug", HEAL_PHRASES) is True
    assert routing.any_phrase("fix the buggy parser", HEAL_PHRASES) is False
    assert routing.any_phrase("undo last patch", ROLLBACK_PHRASES) is True
    assert routing.any_phrase("patch history", PATCH_HISTORY_PHRASES) is True


def test_volume_phrases_are_word_bounded():
    assert routing.any_phrase("turn the volume up", VOLUME_PHRASES) is True
    assert routing.any_phrase("the volume of the box", VOLUME_PHRASES) is False
    assert routing.any_phrase("volume up", VOLUME_PHRASES) is True


def test_meeting_mode_phrases_are_exact():
    assert routing.is_phrase("stop meeting mode", "stop meeting mode") is True
    assert routing.is_phrase("stop meeting mode now", "stop meeting mode") is False
    assert routing.is_phrase("end meeting mode", "end meeting mode") is True


def test_screen_phrases_are_word_bounded():
    from main import _looks_like_screen_request

    assert _looks_like_screen_request("explain this error") is True
    assert _looks_like_screen_request("read the screenwriter's notes") is False
    assert _looks_like_screen_request("check my screen") is True


def test_preview_never_guesses():
    from main import _build_task_plan

    assert _build_task_plan("Make a budget spreadsheet") == GENERIC_PLAN
    assert _build_task_plan("Write a report about mushrooms") == GENERIC_PLAN
    assert _build_task_plan("Turn off the bedroom light") == GENERIC_PLAN
