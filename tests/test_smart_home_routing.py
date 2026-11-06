"""Phase 0 routing regressions: smart home is a tool, not a pre-LLM claim.

The old `DeviceFlow.handle_smart_home_command` matched substrings before the
model ran and returned `True` even when the device action failed, so a false
positive could never fall through. These tests keep the removal honest and
keep the remaining tool path able to serve real commands.

See docs/ROUTING-AUDIT.md §1 and §6 (Phase 0).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import desktop_tools
from smart_home.service import SmartHomeService

ROOT = Path(__file__).resolve().parent.parent


class _FakeSmartHome:
    def execute_command(self, _text: str):
        raise ValueError("No smart home devices are connected yet.")


class _FakeHost:
    smart_home = _FakeSmartHome()


class _Context:
    extra = {"host": _FakeHost()}


def test_pre_llm_smart_home_router_is_gone():
    device_flow = (ROOT / "core" / "device_flow.py").read_text(encoding="utf-8")
    assert "handle_smart_home_command" not in device_flow

    controller = (ROOT / "core" / "text_turn_controller.py").read_text(
        encoding="utf-8"
    )
    assert "_handle_smart_home_command" not in controller

    main = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "handle_smart_home_command" not in main


def test_smart_home_tool_reports_no_devices_instead_of_raising():
    result = desktop_tools._smart_home_control(
        {"command": "turn off the bedroom light"}, _Context()
    )
    assert result == "No smart home devices are connected yet."


def test_service_distinguishes_no_devices_from_an_unknown_action():
    service = object.__new__(SmartHomeService)

    class _EmptyStorage:
        def list_devices(self, *_args, **_kwargs):
            return []

    service._storage = _EmptyStorage()
    with pytest.raises(ValueError, match="No smart home devices"):
        service.execute_command("turn off the bedroom light")


def test_target_selection_still_serves_real_commands():
    service = object.__new__(SmartHomeService)
    devices = [
        {"name": "Bedroom Lamp", "room": "Bedroom", "device_type": "light"},
        {"name": "Living Fan", "room": "Living Room", "device_type": "fan"},
    ]

    bedroom = service._select_targets("turn off the bedroom light", devices)
    assert [d["name"] for d in bedroom] == ["Bedroom Lamp"]

    everything = service._select_targets("turn off all devices", devices)
    assert {d["name"] for d in everything} == {"Bedroom Lamp", "Living Fan"}


def test_preview_no_longer_guesses_smart_home():
    from main import _build_task_plan

    generic = [
        "Understand the command",
        "Choose the right tool",
        "Execute the task",
        "Return the result",
    ]
    assert _build_task_plan("Mushroom risotto recipe") == generic
    assert _build_task_plan("Open Microsoft Office") == generic
