"""
accept_wakeword.py
End-to-end check for the On Call standby wake word in main.py.

This is the last untested path from the On Call audit: it drives the REAL
_wakeword_loop, with a real microphone in section A and a scripted sounddevice
capture in sections B/C.

  A. hardware + network path -- the real mic opens and recognize_google is
     actually invoked on captured audio (the speech gate is forced open for
     this check so the result does not depend on the room's noise floor)
  B. detection  -- "Brahma Evo" (and only that) flips the session to connected
  C. no false positives -- "hi", "hello" and unrelated chatter do NOT connect
  D. shutdown -- the listener stops promptly and releases the microphone

Usage: py -3.11 accept_wakeword.py
"""
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from main import BrahmaLive, _standby_wakeword_detected, _wakeword_detected

from accept_util import check, report


class StubUI:
    def __init__(self) -> None:
        self.states: list[str] = []
        self.logs: list[str] = []

    def set_state(self, s: str) -> None:
        self.states.append(s)

    def write_log(self, t: str) -> None:
        self.logs.append(t)


class Harness(BrahmaLive):
    def __init__(self) -> None:
        self.ui = StubUI()
        self.session = None
        self._loop = None
        self._live_mode = "on_call"
        self._manual_call_active = False
        # Required by _should_standby() since the explicit-Disconnect latch.
        self._live_suspended = False
        self._disconnect_requested = False
        self._control_lock = threading.Lock()
        self._ui_loop = None          # no asyncio loop -> connect is a safe no-op
        self._connect_event = None
        self._session_task = None
        self._wake_stop = None
        self._wake_thread = None

    def set_speaking(self, _v: bool) -> None:
        pass


def wait_for(pred, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


# ---------------------------------------------------------------------------
# A. The real microphone and the real recognition pipeline.
# ---------------------------------------------------------------------------
def test_real_microphone() -> None:
    print("\nA. Real microphone, real capture, real STT call")
    import speech_recognition as sr

    h = Harness()
    calls: list[int] = []
    original = sr.Recognizer.recognize_google

    def spy(self_recognizer, audio, **kw):
        calls.append(1)
        # Pass through to the real service so the genuine failure mode
        # (UnknownSpeechError on room noise) is exercised, not stubbed away.
        return original(self_recognizer, audio, **kw)

    # The transcription engine is a user setting and may legitimately be
    # faster-whisper now, in which case recognize_google is never called and this
    # spy proves nothing. Pin the cloud engine for this specific assertion and
    # restore it; the local path is covered by the offline STT verification.
    from core import speech_stt
    from core import session_lifecycle

    original_engine = speech_stt.get_engine_id
    speech_stt.get_engine_id = lambda: "google"
    # A quiet room never crosses the speech gate on its own, so the assertion
    # would measure ambient noise rather than the code. Force the gate open for
    # this check: REAL mic audio is still sealed into a phrase and handed to the
    # REAL recognize_google. Production keeps the 300.0 default.
    original_gate = session_lifecycle._SPEECH_ENERGY_THRESHOLD
    session_lifecycle._SPEECH_ENERGY_THRESHOLD = -1.0

    sr.Recognizer.recognize_google = spy
    try:
        h._start_wakeword_listener()
        thread = h._wake_thread
        check("listener thread started", thread is not None)
        ok = wait_for(lambda: len(calls) > 0, timeout=25.0)
        check("STT invoked on real captured audio", ok, f"{len(calls)} call(s)")
        if ok:
            print(f"      audio frames sent: {len(calls)}")
        t0 = time.monotonic()
        h._stop_wakeword_listener()
        thread.join(timeout=8.0)
        stopped = not thread.is_alive()
        check("listener stopped promptly", stopped,
              f"{(time.monotonic() - t0):.1f}s")
    finally:
        sr.Recognizer.recognize_google = original
        speech_stt.get_engine_id = original_engine
        session_lifecycle._SPEECH_ENERGY_THRESHOLD = original_gate


# ---------------------------------------------------------------------------
# B/C. Detection and false positives, with scripted sounddevice capture.
# ---------------------------------------------------------------------------
class FakeInputStream:
    """Stand-in for sounddevice.InputStream that replays scripted audio.

    Feeds the real _wakeword_loop a repeating loud/silent pattern so its energy
    gate seals a phrase and calls the scripted transcriber -- no hardware, and no
    dependence on a live microphone's noise floor.
    """

    def __init__(self, samplerate=None, blocksize=None, device=None,
                 dtype=None, channels=None, callback=None):
        self.samplerate = samplerate or 16000
        self.blocksize = blocksize or int(self.samplerate * 0.1)
        self.callback = callback
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        return False

    def _run(self):
        import numpy as np

        loud = np.full((self.blocksize, 1), 8000, dtype=np.int16)
        quiet = np.zeros((self.blocksize, 1), dtype=np.int16)
        while not self._stop.is_set():
            for _ in range(5):
                if self._stop.is_set():
                    return
                self.callback(loud, len(loud), None, None)
                time.sleep(0.01)
            for _ in range(4):
                if self._stop.is_set():
                    return
                self.callback(quiet, len(quiet), None, None)
                time.sleep(0.01)


def run_with_transcript(transcript: str, expect_connect: bool) -> Harness:
    """Drive the real _wakeword_loop with a scripted transcript.

    On a wake word the loop must exit (it hands off to the session). On
    ordinary speech it must KEEP listening -- that is what standby means.
    """
    import sounddevice as sd

    h = Harness()
    real_stream = sd.InputStream
    heard = {"n": 0}

    def _fake_transcribe(recognizer, audio):
        heard["n"] += 1
        return transcript

    sd.InputStream = FakeInputStream
    h._transcribe_wakeword = _fake_transcribe
    try:
        h._start_wakeword_listener()
        # _stop_wakeword_listener() clears the attribute, so hold our own ref.
        thread = h._wake_thread
        thread.join(timeout=6.0)
        if expect_connect:
            check(f"listener exited after wake word {transcript!r}",
                  not thread.is_alive())
        else:
            check(f"listener stayed armed on {transcript!r}", thread.is_alive(),
                  f"phrases heard={heard['n']}")
            check(f"listener is still polling {transcript!r}", heard["n"] >= 2,
                  f"phrases heard={heard['n']}")
    finally:
        sd.InputStream = real_stream
        h._stop_wakeword_listener()
        if h._wake_thread is not None:
            h._wake_thread.join(timeout=5.0)
    return h


def main() -> int:
    print("=" * 74)
    print("On Call standby wake word acceptance")
    print("=" * 74)

    try:
        import speech_recognition  # noqa: F401
        print(f"speech_recognition: {speech_recognition.__version__}")
    except Exception as exc:
        print(f"SKIP: SpeechRecognition unavailable ({exc})")
        return 1

    # ------------------------------------------------------------------
    print("\nB. The wake word connects the session")
    # ------------------------------------------------------------------
    for phrase in ("Brahma Evo", "hey brahma evo please", "Brahma"):
        h = run_with_transcript(phrase, expect_connect=True)
        check(f"{phrase!r} triggers connect", h._manual_call_active is True)
        check(f"{phrase!r} leaves standby", not h._should_standby())

    # ------------------------------------------------------------------
    print("\nC. Ordinary speech must NOT connect")
    # ------------------------------------------------------------------
    for chatter in ("hi", "hello there", "hey how are you", "what time is it",
                    "hello world this is a test"):
        h = run_with_transcript(chatter, expect_connect=False)
        check(f"{chatter!r} does not connect", h._manual_call_active is False,
              f"_manual_call_active={h._manual_call_active}")
        check(f"{chatter!r} stays in standby", h._should_standby())

    # ------------------------------------------------------------------
    print("\nC2. The old loose matcher would have fired on these")
    # ------------------------------------------------------------------
    loose = [t for t in ("hi", "hello there", "hey how are you")
             if _wakeword_detected(t)]
    strict = [t for t in ("hi", "hello there", "hey how are you")
              if _standby_wakeword_detected(t)]
    check("legacy matcher still fires on bare greetings (unchanged in-session "
          "behaviour)", len(loose) == 3, f"legacy={loose}")
    check("standby matcher rejects all of them", not strict, f"strict={strict}")

    # ------------------------------------------------------------------
    print("\nD. Real microphone round trip")
    # ------------------------------------------------------------------
    test_real_microphone()

    print("\n" + "=" * 74)
    return report()


if __name__ == "__main__":
    raise SystemExit(main())
