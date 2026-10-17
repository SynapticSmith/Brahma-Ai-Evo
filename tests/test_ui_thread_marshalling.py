"""Regression tests for GUI-thread marshalling of UI mixin delegates.

Delegates on BrahmaUI/MainWindow are reachable from worker threads (Discord,
wake word, mic/audio loop, screen processor, meeting listener, daily briefing,
phone gateway). Touching QWidgets off the GUI thread raises
``QObject::setParent: Cannot set parent, new parent is in a different thread``,
so the delegates now funnel direct widget calls through
``core.ui_thread.run_on_ui`` -> ``MainWindow._run_on_ui``.

These tests use lightweight doubles (no Qt widget construction), so they run
headless and verify the *wiring*: the widget call is deferred to the host's
GUI-thread marshaller rather than executing inline.
"""
from __future__ import annotations

import importlib
import subprocess
import sys
import textwrap
import types
from pathlib import Path


# --------------------------------------------------------------------------- #
# core.ui_thread.run_on_ui
# --------------------------------------------------------------------------- #
def test_run_on_ui_inline_without_runner():
    from core.ui_thread import run_on_ui

    calls = []
    run_on_ui(object(), lambda: calls.append("ran"))
    assert calls == ["ran"]


def test_run_on_ui_routes_through_marshaller():
    from core.ui_thread import run_on_ui

    class Win:
        def __init__(self):
            self.queued = []

        def _run_on_ui(self, fn):
            self.queued.append(fn)

    win = Win()
    ran = []
    run_on_ui(win, lambda: ran.append(True))
    assert ran == []
    assert len(win.queued) == 1
    win.queued[0]()
    assert ran == [True]


# --------------------------------------------------------------------------- #
# _ShellDelegatesMixin: direct widget delegates must defer to _run_on_ui
# --------------------------------------------------------------------------- #
class _FakeWin:
    """Records widget calls; defers everything queued via _run_on_ui."""

    def __init__(self):
        self.calls = []
        self.queued = []
        self._muted = False
        self._state = "LISTENING"

    def _run_on_ui(self, fn):
        self.queued.append(fn)

    def _drain(self):
        while self.queued:
            self.queued.pop(0)()

    def _toggle_mute(self):
        self.calls.append("_toggle_mute")

    def set_muted_state(self, muted, *, wakeword=False):
        self.calls.append(("set_muted_state", muted, wakeword))

    def set_audio_level(self, level):
        self.calls.append(("set_audio_level", level))

    def show_daily_briefing(self, text):
        self.calls.append(("show_daily_briefing", text))

    def hide_daily_briefing(self):
        self.calls.append("hide_daily_briefing")

    def schedule_daily_briefing_hide(self):
        self.calls.append("schedule_daily_briefing_hide")

    def notify_phone_connected(self):
        self.calls.append("notify_phone_connected")

    def set_scanning(self, enabled, text=""):
        self.calls.append(("set_scanning", enabled, text))

    def set_meeting_mode(self, enabled, title="", summary="", answer="", speech=""):
        self.calls.append(("set_meeting_mode", enabled))


class _Host:
    def __init__(self, win):
        self._win = win


def _make_host():
    from core.ui_shell_delegates import _ShellDelegatesMixin

    class Host(_ShellDelegatesMixin, _Host):
        pass

    win = _FakeWin()
    return Host(win), win


def test_delegates_defer_widget_calls_to_gui_thread():
    host, win = _make_host()

    host.set_audio_level(0.5)
    host.set_scanning(True, "SCANNING")
    host.show_daily_briefing("hi")
    host.hide_daily_briefing()
    host.schedule_daily_briefing_hide()
    host.notify_phone_connected()
    host.set_meeting_mode(True, "t", "s", "a", "sp")
    host.set_muted(True)
    host.set_muted_state(True, wakeword=True)

    # Nothing touched the widgets yet -- every call was queued.
    assert win.calls == []
    assert len(win.queued) == 9

    win._drain()

    assert win.calls == [
        ("set_audio_level", 0.5),
        ("set_scanning", True, "SCANNING"),
        ("show_daily_briefing", "hi"),
        "hide_daily_briefing",
        "schedule_daily_briefing_hide",
        "notify_phone_connected",
        ("set_meeting_mode", True),
        ("set_muted_state", True, False),
        ("set_muted_state", True, True),
    ]


def test_muted_setter_defers_toggle():
    host, win = _make_host()
    host.muted = True
    assert win.calls == []
    win._drain()
    assert win.calls == ["_toggle_mute"]


def test_delegates_run_inline_without_marshaller():
    from core.ui_shell_delegates import _ShellDelegatesMixin

    class Host(_ShellDelegatesMixin, _Host):
        pass

    class PlainWin:
        """No _run_on_ui: the delegate must call the widget method inline."""

        def __init__(self):
            self.calls = []

        def set_audio_level(self, level):
            self.calls.append(("set_audio_level", level))

    win = PlainWin()
    host = Host(win)
    host.set_audio_level(0.25)
    assert win.calls == [("set_audio_level", 0.25)]


# --------------------------------------------------------------------------- #
# _MuteSendMixin.submit_command: widget ops must defer to the GUI thread
# --------------------------------------------------------------------------- #
def _load_mute_send(monkeypatch):
    """Import core.ui_mute_send with a fake sound_manager (QtMultimedia hang)."""
    fake = types.ModuleType("sound_manager")
    fake.sound_mgr = types.SimpleNamespace(
        play_listening_start=lambda: None,
        play_listening_stop=lambda: None,
    )
    monkeypatch.setitem(sys.modules, "sound_manager", fake)
    sys.modules.pop("core.ui_mute_send", None)
    return importlib.import_module("core.ui_mute_send")


def test_submit_command_defers_widget_ops(monkeypatch):
    from collections import deque

    mod = _load_mute_send(monkeypatch)

    class Sink:
        def __init__(self):
            self.emitted = []

        def emit(self, text):
            self.emitted.append(text)

    class FakeMainWin(mod._MuteSendMixin):
        def __init__(self):
            self.queued = []
            self._log_sig = Sink()
            self._chat_source_queue = deque()
            self.on_text_command = None
            self.card_ops = []

        def _run_on_ui(self, fn):
            self.queued.append(fn)

        def _restart_card_hide_timer(self):
            self.card_ops.append("restart_timer")

    win = FakeMainWin()
    win.submit_command("hello", source="discord")

    # Deferred: nothing ran inline.
    assert win.queued != []
    assert win._log_sig.emitted == []
    assert win._chat_source_queue == deque()

    while win.queued:
        win.queued.pop(0)()

    assert win._log_sig.emitted == ["You: hello"]
    assert list(win._chat_source_queue) == ["discord"]
    assert win.card_ops == ["restart_timer"]
    assert win.on_text_command is None  # no thread spawned when unset


# --------------------------------------------------------------------------- #
# Startup regression: MainWindow.__init__ must build before the GUI exists
# --------------------------------------------------------------------------- #
def test_main_window_builds_in_subprocess():
    """A local ``import threading`` inside ``MainWindow.__init__`` used to shadow
    the module-level import, so the marshaller's ``threading.get_ident()`` (added
    earlier in ``__init__``) raised ``UnboundLocalError`` and the whole app died
    at startup. Build the real window in an isolated subprocess (offscreen) so
    this is caught without inheriting the known flaky in-process Qt teardown."""
    root = Path(__file__).resolve().parent.parent
    script = textwrap.dedent(
        """
        import os, sys
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        sys.path.insert(0, sys.argv[1])
        from core.qt_bootstrap import bootstrap
        bootstrap()
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        app.setStyle("Fusion")  # matches BrahmaUI.__init__ before MainWindow
        from ui import MainWindow
        win = MainWindow(os.path.join(sys.argv[1], "assets", "Brahma_Lite_Logo.png"))
        assert win._gui_thread_id is not None, "marshaller captured no GUI thread id"
        assert callable(win._run_on_ui), "run_on_ui missing"
        ran = []
        win._run_on_ui(lambda: ran.append(True))
        assert ran == [True], "run_on_ui did not run inline on the GUI thread"
        sys.stdout.write("BUILD_OK\\n")
        sys.stdout.flush()
        os._exit(0)
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, str(root)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert "BUILD_OK" in proc.stdout, (
        f"MainWindow failed to build (rc={proc.returncode})\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr[-3000:]}"
    )
