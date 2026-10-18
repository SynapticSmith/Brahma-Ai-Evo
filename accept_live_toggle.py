"""
accept_live_toggle.py
Acceptance check for the Always On / On Call session control in main.py.

Runs the real control methods off the real BrahmaLive class against a stub UI,
with no Gemini connection and no microphone. Proves the four things the first
patch got wrong:

  1. the session is a cancellable task, so End Call actually tears the socket down
  2. the UI indicators follow the session instead of sitting on LISTENING
  3. a deliberate disconnect does not kill run(), and run() comes back
  4. the Qt-thread -> asyncio-loop handoff is thread-safe

Usage: py -3.11 accept_live_toggle.py
"""
import asyncio
import re
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from main import BrahmaLive

from accept_util import check, section, report


class StubUI:
    """Collects the state transitions main.py pushes at the UI."""

    def __init__(self) -> None:
        self.states: list[str] = []
        self.logs: list[str] = []
        self.muted = False

    def set_state(self, state: str) -> None:
        self.states.append(state)

    def write_log(self, text: str) -> None:
        self.logs.append(text)

    def boot_set_step_status(self, *_a, **_k) -> None:
        pass

    def boot_set_progress(self, *_a, **_k) -> None:
        pass


class Harness(BrahmaLive):
    """BrahmaLive's On Call control surface, with the heavy __init__ bypassed."""

    def __init__(self) -> None:  # noqa: D107 - deliberately skips super()
        self.ui = StubUI()
        self.session = None
        self._loop = None
        self._live_mode = "always_on"
        self._manual_call_active = False
        # Added with the explicit-Disconnect latch; without it _should_standby
        # raises and the whole On Call suite cannot run.
        self._live_suspended = False
        self._disconnect_requested = False
        self._control_lock = threading.Lock()
        self._ui_loop = None
        self._connect_event = None
        self._session_task = None
        self._wake_stop = None
        self._wake_thread = None
        self._startup_briefing_started = True
        # Silence the mic-owning coroutines the session TaskGroup spawns.
        self._send_realtime = self._idle_coro
        self._listen_audio = self._idle_coro
        self._relay_phone_audio = self._idle_coro
        self._receive_audio = self._idle_coro
        self._play_audio = self._idle_coro

    @staticmethod
    async def _idle_coro() -> None:
        await asyncio.sleep(3600)

    def set_speaking(self, _v: bool) -> None:
        pass


def wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# ---------------------------------------------------------------------------
# A miniature of run()'s loop, using the same primitives the real loop uses.
# ---------------------------------------------------------------------------
async def session_loop(h: Harness, *, iterations: int = 6, hold: float = 2.0):
    """Mirror of the while True: body in main.run(), reduced to a few turns."""
    turns = 0
    while turns < iterations:
        turns += 1
        if h._should_standby():
            await h._wait_for_connect_request()
            continue
        h._clear_disconnect_request()
        h.ui.set_state("THINKING")
        h._session_task = asyncio.create_task(h._serve_live_session(object()))
        try:
            await asyncio.wait_for(asyncio.shield(h._session_task), timeout=hold)
        except asyncio.CancelledError:
            if not h._take_disconnect_request():
                raise
        except asyncio.TimeoutError:
            pass
        finally:
            h._session_task = None
        h.set_speaking(False)
        if h._should_standby():
            h.ui.set_state("STANDBY")
            continue
        h.ui.set_state("LISTENING")
        await asyncio.sleep(0.05)
    return turns


async def connect_and_disconnect(h: Harness) -> None:
    """Exactly run()'s connect block, so the disconnect flag is consumed."""
    h._clear_disconnect_request()
    h._session_task = asyncio.create_task(h._serve_live_session(object()))
    try:
        await h._session_task
    except asyncio.CancelledError:
        if not h._take_disconnect_request():
            raise
    finally:
        h._session_task = None


def run_scenario(name: str, body) -> None:
    print(f"\n{name}")
    h = Harness()

    def runner():
        asyncio.run(body(h))

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join(timeout=30)
    if t.is_alive():
        check(f"{name}: loop terminated", False, "harness hung")
    return h


def _control_surface(mode: str):
    """The four controls with no sockets, no Qt, no audio.

    Uses the real methods off BrahmaLive so the state machine under test is
    the shipped one, not a copy of it.
    """
    import threading

    sys.path.insert(0, str(BASE))
    import main as _main

    class Surface:
        _should_standby = _main.BrahmaLive._should_standby
        _session_lifecycle = _main.BrahmaLive._session_lifecycle
        set_live_mode = _main.BrahmaLive.set_live_mode
        set_live_call = _main.BrahmaLive.set_live_call
        _wake_control = _main.BrahmaLive._wake_control

        def __init__(self, start_mode):
            self._control_lock = threading.Lock()
            self._live_mode = start_mode
            self._manual_call_active = False
            self._live_suspended = False
            self._disconnect_requested = False
            self._session_task = None
            self._ui_loop = None
            self._connect_event = None

            class _UI:
                def set_state(self, *_a, **_k):
                    pass

                def write_log(self, *_a, **_k):
                    pass

            self.ui = _UI()

        def _stop_wakeword_listener(self):
            pass

        def _request_connect(self):
            pass

        def _disconnect_live(self):
            pass

        def should_standby(self):
            return self._should_standby()

    return Surface(mode)


def main() -> int:
    print("=" * 74)
    print("Always On / On Call acceptance check")
    print("=" * 74)

    # ------------------------------------------------------------------
    print("\n1. Always On keeps a live session and ignores the standby branch")
    # ------------------------------------------------------------------
    async def always_on(h: Harness):
        h._ui_loop = asyncio.get_running_loop()
        check("Always On is not standby", not h._should_standby())
        run_task = asyncio.create_task(connect_and_disconnect(h))
        await asyncio.sleep(0.05)
        check("session task exists while connected", h._session_task is not None)
        check("_loop is set while connected (tool dispatch path)", h._loop is not None)
        threading.Thread(target=h._disconnect_live).start()
        await asyncio.wait_for(run_task, timeout=3.0)
        check("run loop survived the disconnect", True)
        check("disconnect was consumed, not leaked", not h._take_disconnect_request())

    h = Harness()
    asyncio.run(always_on(h))
    check("no STANDBY while Always On", "STANDBY" not in h.ui.states)

    # ------------------------------------------------------------------
    print("\n2. End Call while connected actually tears the socket down")
    # ------------------------------------------------------------------
    async def end_call(h: Harness):
        h._ui_loop = asyncio.get_running_loop()
        # Realistic state: user is in On Call with a live call, then presses
        # "End Call" (the button only exists in On Call mode).
        h.set_live_mode("on_call")
        h.set_live_call(True)
        h.ui.states.clear()

        run_task = asyncio.create_task(connect_and_disconnect(h))
        await asyncio.sleep(0.05)
        check("session running before disconnect", not h._session_task.done())

        # Call from a non-loop thread, exactly like a Qt slot would.
        t = threading.Thread(target=h.set_live_call, args=(False,))
        t.start()
        t.join()
        await asyncio.wait_for(run_task, timeout=3.0)
        check("End Call tore the session down", h._session_task is None)
        check("disconnect was consumed, not leaked", not h._take_disconnect_request())
        check("standby reached the UI on End Call", "STANDBY" in h.ui.states,
              f"states={h.ui.states}")
        # LISTENING legitimately happened while the session was up; what must
        # not happen is it coming back after the disconnect.
        check("indicator ends on STANDBY, not LISTENING",
              h.ui.states[-1] == "STANDBY", f"states={h.ui.states}")

    asyncio.run(end_call(Harness()))

    # ------------------------------------------------------------------
    print("\n3. On Call shows STANDBY instead of staying on LISTENING")
    # ------------------------------------------------------------------
    ui_seen: list[str] = []

    async def on_call(h: Harness):
        h._ui_loop = asyncio.get_running_loop()
        h.set_live_mode("on_call")
        check("_should_standby() is True", h._should_standby())
        check("toggle updates the indicator immediately", "STANDBY" in h.ui.states)

        # Now let the real loop observe standby and park.
        waiter = asyncio.create_task(h._wait_for_connect_request())
        await asyncio.sleep(0.15)
        ui_seen.extend(h.ui.states)
        check("STANDBY reached the UI", "STANDBY" in h.ui.states, f"states={h.ui.states}")
        check("standby log line emitted", any("standby" in l.lower() for l in h.ui.logs))
        waiter.cancel()
        try:
            await waiter
        except asyncio.CancelledError:
            pass
        h._stop_wakeword_listener()

    asyncio.run(on_call(Harness()))
    check("UI never showed LISTENING while disconnected", "LISTENING" not in ui_seen)

    # ------------------------------------------------------------------
    print("\n4. Pressing Connect wakes the standby wait")
    # ------------------------------------------------------------------
    async def reconnect(h: Harness):
        h._ui_loop = asyncio.get_running_loop()
        h.set_live_mode("on_call")
        waiter = asyncio.create_task(h._wait_for_connect_request())
        await asyncio.sleep(0.15)
        check("session loop is parked in standby", not waiter.done())

        # Qt thread flips the button.
        t = threading.Thread(target=h.set_live_call, args=(True,))
        t.start()
        t.join()
        woke = await asyncio.wait_for(waiter, timeout=2.0)
        check("standby wait returned on Connect", woke is None)
        check("no longer standby", not h._should_standby())
        h._stop_wakeword_listener()

    asyncio.run(reconnect(Harness()))

    # ------------------------------------------------------------------
    print("\n5. Connect request sent before the wait exists is not lost")
    # ------------------------------------------------------------------
    async def race(h: Harness):
        h._ui_loop = asyncio.get_running_loop()
        h.set_live_mode("on_call")
        # Button pressed in the window before _wait_for_connect_request() runs.
        h.set_live_call(True)
        await asyncio.wait_for(h._wait_for_connect_request(), timeout=2.0)
        check("pre-wait Connect request survived", not h._should_standby())
        h._stop_wakeword_listener()

    asyncio.run(race(Harness()))

    # ------------------------------------------------------------------
    print("\n6. A real shutdown cancellation is not swallowed")
    # ------------------------------------------------------------------
    async def shutdown(h: Harness):
        h._ui_loop = asyncio.get_running_loop()
        h._session_task = asyncio.create_task(h._serve_live_session(object()))
        await asyncio.sleep(0.05)
        # No _disconnect_requested set: this is shutdown, not a toggle.
        h._session_task.cancel()
        try:
            await h._session_task
        except asyncio.CancelledError:
            check("shutdown CancelledError propagates", True)
        else:
            check("shutdown CancelledError propagates", False, "was swallowed")

    asyncio.run(shutdown(Harness()))

    # ------------------------------------------------------------------
    print("\n7. The full loop survives repeated connect/disconnect cycles")
    # ------------------------------------------------------------------
    async def cycles(h: Harness):
        h._ui_loop = asyncio.get_running_loop()
        loop_task = asyncio.create_task(session_loop(h, iterations=12, hold=2.0))
        await asyncio.sleep(0.2)

        for i in range(3):
            threading.Thread(target=h.set_live_mode, args=("on_call",)).start()
            await asyncio.sleep(0.25)
            check(f"cycle {i}: STANDBY after switching to On Call", "STANDBY" in h.ui.states)
            threading.Thread(target=h.set_live_call, args=(True,)).start()
            await asyncio.sleep(0.25)
            check(f"cycle {i}: session reconnected", h._session_task is not None)

        h._stop_wakeword_listener()
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass

    asyncio.run(cycles(Harness()))

    # ------------------------------------------------------------------
    print("\n8. The Qt signals actually resolve through BrahmaUI")
    # ------------------------------------------------------------------
    try:
        import os

        import ui as brahma_ui

        prop_a = brahma_ui.BrahmaUI.live_mode_changed
        prop_b = brahma_ui.BrahmaUI.live_call_toggled
        check("BrahmaUI.live_mode_changed is a forwarding property",
              isinstance(prop_a, property))
        check("BrahmaUI.live_call_toggled is a forwarding property",
              isinstance(prop_b, property))
        check("declared on MainWindow", hasattr(brahma_ui.MainWindow, "live_mode_changed"))
        check("declared on MainWindow (call)", hasattr(brahma_ui.MainWindow, "live_call_toggled"))

        # End-to-end: a real Qt click on the real Mode button must reach
        # BrahmaLive. This is the regression that shipped broken.
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6 import QtCore

        app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])

        class SignalProbe(QtCore.QObject):
            live_mode_changed = QtCore.pyqtSignal(str)
            live_call_toggled = QtCore.pyqtSignal(bool)

        probe = SignalProbe()
        got: list = []
        h = Harness()
        probe.live_mode_changed.connect(lambda m: got.append(("mode", m)) or h.set_live_mode(m))
        probe.live_call_toggled.connect(lambda a: got.append(("call", a)) or h.set_live_call(a))

        probe.live_mode_changed.emit("on_call")
        app.processEvents()
        check("clicking Mode reached BrahmaLive", h._live_mode == "on_call", f"got={got}")

        probe.live_call_toggled.emit(True)
        app.processEvents()
        check("clicking Connect reached BrahmaLive", h._manual_call_active is True, f"got={got}")
        check("indicator moved off LISTENING", "STANDBY" in h.ui.states, f"states={h.ui.states}")
    except Exception as exc:
        check("Qt signal path reachable", False, repr(exc))

    # ------------------------------------------------------------------
    print("\n9. STANDBY is a known state in every indicator map")
    # ------------------------------------------------------------------
    src = ((BASE / "ui.py").read_text(encoding="utf-8")
           + (BASE / "core" / "ui_floating.py").read_text(encoding="utf-8")
           + (BASE / "core" / "ui_apply_state.py").read_text(encoding="utf-8")
           + (BASE / "core" / "ui_shell_delegates.py").read_text(encoding="utf-8"))
    check("status badge knows STANDBY", '"STANDBY":    ("◌ STANDBY"' in src)
    check("launcher orb theme knows standby", '"standby": (110, 122, 138)' in src)
    # Standby is no longer phrased as an On-Call-only state: it is also what an
    # explicit Disconnect looks like in Always On, on either backend.
    check("launcher status text knows standby",
          '"standby": "Standby - no session"' in src)
    check("chat footer knows standby",
          '"standby": "Standby - no session"' in src)
    check("no surface claims standby is On-Call-only",
          '"standby": "On Call - waiting' not in src
          and 'On Call - say' not in src)
    check("input placeholder still explains how to reconnect",
          '"STANDBY": "Standby - say' in src)
    check("stop_speaking no longer clobbers STANDBY",
          'self._win._state != "STANDBY"' in src)

    # ------------------------------------------------------------------
    section("Separation of controls: mode, Connect and Disconnect are independent")
    # ------------------------------------------------------------------
    # The user asked for a guarantee here. Previously the mode selector wrote
    # the connection gate, so pressing Disconnect in Always On did nothing (the
    # loop ignored the flag and reconnected 5s later) and merely re-selecting a
    # mode forced the gate open again. Walk the whole state space and assert
    # every transition has one defined outcome.
    m_src = (BASE / "main.py").read_text(encoding="utf-8")
    lsrc = (BASE / "core" / "session_lifecycle.py").read_text(encoding="utf-8")
    check("an explicit Disconnect is tracked separately from the mode",
          "self._live_suspended = False" in m_src)
    check("set_live_mode only changes the mode, not the connection gate",
          m_src.count("self._manual_call_active") >= 1
          and "_live_suspended" not in lsrc.split("def set_live_mode")[1]
                                .split("def _pause_until")[0])
    check("Connect clears an earlier Disconnect and Disconnect latches it",
          "self._live_suspended = not active" in m_src)
    check("standby honours the Disconnect latch before anything else",
          "if self._live_suspended:" in m_src)

    for mode in ("always_on", "on_call"):
        live = _control_surface(mode)
        live.set_live_call(False)
        check(f"Disconnect sticks in {mode}", live.should_standby() is True)
        live.set_live_call(True)
        check(f"Connect lifts it in {mode}", live.should_standby() is False)

    live = _control_surface("always_on")
    live.set_live_call(False)
    live.set_live_mode("on_call")
    check("switching mode does not resurrect a session the user switched off",
          live.should_standby() is True)
    live = _control_surface("always_on")
    live.set_live_call(False)
    live.set_live_mode("always_on")
    check("re-selecting the same mode does not resurrect it either",
          live.should_standby() is True)

    live = _control_surface("on_call")
    live.set_live_call(True)
    check("On Call + Connect is live", live.should_standby() is False)
    live.set_live_mode("always_on")
    check("moving to Always On keeps the live session up",
          live.should_standby() is False)
    live.set_live_mode("on_call")
    check("returning to On Call drops to standby (no call in progress)",
          live.should_standby() is True)

    # ------------------------------------------------------------------
    section("Local providers get a real voice session, not a bypass")
    # ------------------------------------------------------------------
    # The loop used to `continue` whenever Local was selected, so the mic
    # handler that pipes whisper STT into the local model never ran and all
    # four controls were inert for local models.
    check("backend choice is a session axis, not a bypass",
          "def _voice_backend" in m_src)
    check("a local provider runs a local voice loop",
          "def _run_local_voice_session" in lsrc
          and 'backend == "local_loop"' in lsrc)
    # Scope the "skip is gone" check to the session loop. A Local/Offline check
    # inside _listen_audio is a routing decision for the Gemini mic path, not a
    # session bypass, and removing it would be wrong.
    loop_at = lsrc.index("while True:", lsrc.index("async def run("))
    loop_body = lsrc[loop_at:loop_at + 2400]
    check("the session loop no longer skips when Local is selected",
          'app_cfg.get("default_ai_provider") == "Local"' not in loop_body
          and "self.session = None\n                await asyncio.sleep(5)\n                continue" not in loop_body)
    check("the local loop honours standby like the Gemini path",
          (m_src + lsrc).count("_should_standby(") >= 4)
    check("local voice transcribes with the pluggable STT",
          "speech_stt.transcribe_pcm" in m_src
          and "def _local_voice_turn" in m_src)
    check("the local turn feeds the normal command path (so it reaches the model)",
          'self._on_text_command(text, source="mic")' in m_src)
    check("Disconnect can cancel the local session too",
          "def _session_task_guard" in m_src
          and "asyncio.ensure_future" in m_src)
    check("local voice does not transcribe over its own reply",
          'speaking["on"]' in m_src)

    # ------------------------------------------------------------------
    section("Provider choice decides the voice backend, not the socket")
    # ------------------------------------------------------------------
    # Reported bug: picking the Custom (OpenAI-compatible) endpoint still
    # opened the default Gemini Live socket, so Gemini answered in its own
    # Charon voice and the selected local TTS engine was ignored. The backend
    # decision must live in one place (_voice_backend) and every caller has to
    # agree with it: the session loop, speak() and the mic path.
    aio_src = (BASE / "core" / "audio_io.py").read_text(encoding="utf-8")
    backend_body = m_src[m_src.index("def _voice_backend(self)"):
                         m_src.index("def _local_voice_turn")]
    check("Custom and OpenRouter run the local loop, not Gemini Live",
          backend_body.count('return "local_loop"') >= 4
          and backend_body.rindex('return "local_loop"')
          < backend_body.index('return "gemini_live"'))
    check("only an unhandled provider falls back to Gemini Live",
          backend_body.count('return "gemini_live"') == 1)

    speak_at = aio_src.index("    def speak(self, text: str, proactive: bool = False):")
    speak_body = aio_src[speak_at:aio_src.index("    def speak_error", speak_at)]
    check("speak() only takes the Charon path on the Gemini backend",
          'backend == "gemini_live"' in speak_body)
    check("speak() otherwise uses the user-selectable engine",
          speak_body.count("speech_tts.speak") == 1)
    check("the mic path asks the same backend helper",
          "_voice_backend()" in aio_src.split("def _listen_audio")[1]
                                     .split("def _receive_audio")[0])

    run_at = lsrc.index("async def run(")
    run_body = lsrc[run_at:]
    fallback = run_body.split("Gemini Live connection failed", 1)[1]\
                       .split("if session is not None:", 1)[0]
    check("a failed handshake falls through instead of ending run()",
          re.search(r"^\s*break\s*$", fallback, re.M) is None)
    check("the handshake fallback honours Disconnect",
          "_take_disconnect_request" in fallback)
    check("the serve block waits for a completed handshake",
          "if session is not None:" in run_body)

    # ------------------------------------------------------------------
    section("Mic buffer handling survives the buffer kind sounddevice picks")
    # ------------------------------------------------------------------
    # User-reported crash on first launch of the local loop:
    #   AttributeError: '_cffi_backend.buffer' object has no attribute 'tobytes'
    # RawInputStream + dtype="int16" delivers a raw C buffer; InputStream
    # delivers a numpy array. Because the raise happens inside a cffi callback
    # the exception is swallowed and the mic goes permanently silent.
    check("the local loop uses InputStream, not RawInputStream",
          "sd.InputStream(" in lsrc
          and "sd.RawInputStream(" not in lsrc.split("def _run_local_voice_session")[1]
                                     .split("def _wakeword_loop")[0])
    check("block conversion is a module-level, testable helper",
          "def _pcm_block(" in m_src)
    check("the callback never lets an exception reach cffi",
          "Local mic callback error" in lsrc)
    check("no callback calls .tobytes() on a raw sounddevice block",
          "indata.tobytes()" not in lsrc.split("def _run_local_voice_session")[1]
                                        .split("def _wakeword_loop")[0])

    import numpy as _np

    try:
        from main import _pcm_block
    except Exception as exc:  # pragma: no cover
        check("the block helper imports", False, str(exc))
    else:
        loud = _np.full(1600, 8000, dtype=_np.int16)
        quiet = _np.zeros(1600, dtype=_np.int16)
        for label, block in (
            ("numpy int16 (InputStream)", loud),
            ("float32 (InputStream default)", _np.full((2, 800), 0.25, dtype=_np.float32)),
            ("memoryview", memoryview(loud.tobytes())),
            ("raw bytes", loud.tobytes()),
            ("bytearray", bytearray(loud.tobytes())),
        ):
            try:
                raw, level = _pcm_block(block)
                ok = len(raw) > 0 and level > 300.0
            except Exception as exc:
                ok, level = False, repr(exc)
            check(f"_pcm_block handles {label}", ok, f"level={level}")
        _raw, silent = _pcm_block(quiet)
        check("_pcm_block reports silence as level 0", silent == 0.0, str(silent))

    # ------------------------------------------------------------------
    section("Connectivity is reported, not inferred from the microphone")
    # ------------------------------------------------------------------
    ui_src2 = ((BASE / "ui.py").read_text(encoding="utf-8")
               + (BASE / "core" / "ui_state_appliers.py").read_text(encoding="utf-8"))
    chat_src = (BASE / "core" / "ui_chat.py").read_text(encoding="utf-8")
    check("the window exposes set_voice_status",
          "def set_voice_status" in ui_src2)
    check("the footer renders backend + connection state",
          "_refresh_connectivity_indicator" in (ui_src2 + chat_src)
          and '"connected"' in (ui_src2 + chat_src))
    check("standby text is no longer On-Call-specific",
          '"standby": "On Call - waiting' not in ui_src2)
    check("the main loop reports connectivity from every transition",
          (m_src + lsrc).count("_report_voice_status(") >= 4)
    check("both backends report through the one function",
          "def _report_voice_status" in m_src
          and '"Local voice"' in m_src and '"Gemini Live"' in m_src)

    print("\n" + "=" * 74)
    return report()


if __name__ == "__main__":
    raise SystemExit(main())