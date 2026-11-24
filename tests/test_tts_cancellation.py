import os
import sys
from types import SimpleNamespace

import pytest

import actions.attention_monitor as attention_monitor


def test_stop_native_speech_purges_winsound_playback(monkeypatch):
    if os.name != "nt":
        pytest.skip("winsound is Windows-only")

    calls = []
    fake_winsound = SimpleNamespace(
        SND_PURGE=0x0040,
        PlaySound=lambda sound, flags: calls.append((sound, flags)),
    )
    monkeypatch.setitem(sys.modules, "winsound", fake_winsound)

    cleanup_calls = []
    monkeypatch.setattr(
        attention_monitor,
        "_cleanup_current_audio",
        lambda: cleanup_calls.append(True),
    )

    attention_monitor.stop_native_speech()

    assert calls == [(None, 0x0040)]
    assert cleanup_calls == [True]
