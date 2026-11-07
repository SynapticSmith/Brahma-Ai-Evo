from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock
import pytest

PyQt6 = pytest.importorskip("PyQt6.QtWidgets")
QApplication = PyQt6.QApplication

from core.audio_io import AudioIO  # noqa: E402
from core.identity import IdentityService  # noqa: E402
from core.ui_chat import ChatBubble, EventCard  # noqa: E402

_APP = None


def _app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication(sys.argv[:1])
    return _APP


def test_proactive_speech_respects_identity_setting(monkeypatch):
    host = MagicMock()
    audio = AudioIO.__new__(AudioIO)
    audio._host = host

    # When proactive is disabled
    monkeypatch.setattr(IdentityService, "is_proactive", lambda self: False)
    audio.speak("System alert: CPU high", proactive=True)
    assert host.set_speaking.call_count == 0

    # When proactive is enabled
    monkeypatch.setattr(IdentityService, "is_proactive", lambda self: True)
    monkeypatch.setattr(host, "_voice_backend", lambda: "local_loop")
    monkeypatch.setattr(host, "session", None)
    monkeypatch.setattr("actions.attention_monitor._speak_edge_native", MagicMock())
    audio.speak("System alert: CPU high", proactive=True)
    assert host.set_speaking.call_count > 0


def test_speak_does_not_inject_a_live_turn_while_a_tool_is_pending(monkeypatch):
    """A client turn before send_tool_response starts a second model turn."""
    from core import audio_io as audio_mod

    host = MagicMock()
    host._voice_backend = lambda: "gemini_live"
    host.session = object()
    host._loop = object()

    audio = AudioIO.__new__(AudioIO)
    audio._host = host
    audio._live_tool_pending = True

    sent = []

    def fake_run(coro, loop):
        sent.append(coro)
        coro.close()

    monkeypatch.setattr(audio_mod.asyncio, "run_coroutine_threadsafe", fake_run)

    audio.speak("Working on web search...")
    assert sent == []
    assert host.set_speaking.call_count == 0

    # Once the tool response is away the live path is usable again.
    audio._live_tool_pending = False
    audio.speak("Working on web search...")
    assert len(sent) == 1


def test_chat_bubble_and_event_card_layout_spacing():
    _app()
    detail = "A" * 300
    card = EventCard("File Audit", detail, "12:00 PM")
    title_lbl = None
    for child in card.findChildren(object):
        if getattr(child, "text", None) and detail in child.text():
            title_lbl = child
            break
    assert title_lbl is not None, "EventCard should preserve full detail without 160-char truncation"

    bubble = ChatBubble("assistant", "Brahma", "Test message content", "12:00 PM")
    assert bubble.maximumWidth() >= 200


def test_on_file_selected_attaches_cleanly(tmp_path: Path):
    from ui import MainWindow
    win = MainWindow.__new__(MainWindow)
    win._on_chat_event = MagicMock()
    win.write_log = MagicMock()

    dummy_file = tmp_path / "data.csv"
    dummy_file.write_text("a,b,c\n1,2,3")

    win._on_file_selected(str(dummy_file))
    win._on_chat_event.assert_called_once()
    event = win._on_chat_event.call_args[0][0]
    assert event["role"] == "file"
    assert event["attachments"][0]["name"] == "data.csv"
