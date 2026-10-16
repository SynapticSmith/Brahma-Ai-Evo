"""Regression tests for the speech-sink re-entry that froze offline replies.

`main.speak` is installed as the process speech sink via
`set_speech_sink(self.speak)`, and `core.speech_tts.speak` honours an installed
sink by calling it. When there is no live Gemini session, `main.speak` routes to
`core.speech_tts.speak`, which called the sink (itself) again, spawning a fresh
thread each time: an unbounded recursion. The symptom was the app going
"not responding" with CPU/RAM pinned and speech engines flooding the audio
device the moment an offline/local reply arrived.

The fix is that the no-session branch calls `speech_tts.speak(..., use_sink=False)`
because it *is* the sink. These tests pin that contract.
"""

import re
from pathlib import Path

from actions import attention_monitor
from core import speech_tts

BASE = Path(__file__).resolve().parent.parent


def _speak_body() -> str:
    src = (BASE / "core" / "audio_io.py").read_text(encoding="utf-8")
    start = src.index("    def speak(self, text: str, proactive: bool = False):")
    end = src.index("    def speak_error(self", start)
    return src[start:end]


def test_offline_branch_skips_the_installed_sink():
    """Source guard: the sink must not be honored from inside the sink."""
    body = _speak_body()
    assert "speech_tts.speak(text, use_sink=False)" in body
    # The bare default (which would call the sink back into itself) must be gone.
    assert "speech_tts.speak(text)" not in body
    # Keep accept_local_speech.py's invariant: one and only one call site.
    assert len(re.findall(r"speech_tts\.speak\(", body)) == 1


def test_sink_contract_does_not_recurse(monkeypatch):
    """Mirror of the fix: a sink that re-enters with use_sink=False terminates.

    Install a sink shaped like `main.speak`'s no-session branch, then call
    `speech_tts.speak()` with the default `use_sink=True`. It must reach the sink
    exactly once and then synthesise via the engine, instead of recursing.
    """
    played = []
    sink_calls = []

    monkeypatch.setitem(speech_tts.ENGINES["sapi"], "probe", lambda: True)
    monkeypatch.setitem(speech_tts.ENGINES["edge"], "probe", lambda: False)
    monkeypatch.setattr(speech_tts, "_speak_sapi", lambda text: played.append(text) or True)
    # Force the offline branch so the chosen engine is SAPI (never the cloud voice).
    monkeypatch.setattr(
        speech_tts,
        "_setting",
        lambda key, default=None: True if key == "offline_mode_enabled" else default,
    )

    def app_sink(text):
        # Mirrors main.speak's no-session branch.
        sink_calls.append(text)
        speech_tts.speak(text, use_sink=False)

    monkeypatch.setattr(attention_monitor, "_speech_sink", app_sink)

    assert speech_tts.speak("hello") is True
    assert sink_calls == ["hello"]  # routed to the sink exactly once, no recursion
    assert played == ["hello"]  # and actually synthesised
