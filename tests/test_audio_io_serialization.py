import sys
import threading
import time
from types import SimpleNamespace

from core.audio_io import AudioIO


class _Host:
    def __init__(self):
        self.session = None
        self._loop = None
        self._is_speaking = False

    def _voice_backend(self):
        return "local_loop"

    def set_speaking(self, value):
        self._is_speaking = value


def test_modular_tts_calls_are_serialized(monkeypatch):
    active = 0
    max_active = 0
    completed = []
    state_lock = threading.Lock()

    def fake_speak(text, use_sink=True):
        nonlocal active, max_active
        with state_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with state_lock:
            active -= 1
            completed.append(text)

    monkeypatch.setitem(
        sys.modules,
        "core.speech_tts",
        SimpleNamespace(speak=fake_speak),
    )

    host = _Host()
    audio = AudioIO.__new__(AudioIO)
    audio._host = host
    audio._tts_lock = threading.Lock()

    audio.speak("first")
    audio.speak("second")

    deadline = time.monotonic() + 3
    while len(completed) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)

    assert sorted(completed) == ["first", "second"]
    assert max_active == 1
    assert host._is_speaking is False
