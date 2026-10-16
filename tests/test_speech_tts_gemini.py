"""Tests for the optional Gemini TTS engine (review item 672).

The engine is additive: it appears in the catalogue and the Settings combo, is
gated on a configured API key, and is *not* part of the offline fallback chain
(offline mode must route it to a local voice). The request shape is locked to
the google-genai 2.19.0 surface via a fake SDK, so a change to the call would
fail here instead of at runtime.
"""
import sys
import types as pytypes

import numpy as np

from core import speech_tts


def test_gemini_engine_is_registered():
    assert "gemini" in speech_tts.ENGINES
    spec = speech_tts.ENGINES["gemini"]
    assert spec["local"] is False
    assert spec["probe"] is speech_tts._probe_gemini
    assert speech_tts._SPEAKERS["gemini"] == "_speak_gemini"
    assert callable(getattr(speech_tts, "_speak_gemini"))


def test_gemini_appears_in_engine_catalogue():
    ids = {e["id"] for e in speech_tts.available_engines()}
    assert {"edge", "piper", "kokoro", "sapi", "gemini"} <= ids


def test_probe_gemini_requires_a_key(monkeypatch):
    monkeypatch.setattr(speech_tts, "_gemini_api_key", lambda: "")
    assert speech_tts._probe_gemini() is False
    # google.genai is installed in this environment, so a key is the only gate.
    monkeypatch.setattr(speech_tts, "_gemini_api_key", lambda: "test-key")
    assert speech_tts._probe_gemini() is True


def _install_fake_genai(monkeypatch, captured):
    """Replace google.genai with a recorder so the SDK call shape is testable."""
    class _Rec:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    fake_types = pytypes.ModuleType("google.genai.types")
    for name in ("GenerateContentConfig", "SpeechConfig", "VoiceConfig", "PrebuiltVoiceConfig"):
        setattr(fake_types, name, _Rec)

    class _Models:
        def generate_content(self, *, model, contents, config):
            captured["model"] = model
            captured["contents"] = contents
            captured["config"] = config
            part = _Rec(inline_data=_Rec(data=np.zeros(4, dtype=np.int16).tobytes()))
            return _Rec(candidates=[_Rec(content=_Rec(parts=[part]))])

    class _Client:
        def __init__(self, *, api_key):
            captured["api_key"] = api_key
            self.models = _Models()

    fake_genai = pytypes.ModuleType("google.genai")
    fake_genai.Client = _Client
    fake_genai.types = fake_types

    import google

    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_types)
    monkeypatch.setattr(google, "genai", fake_genai, raising=False)


def test_speak_gemini_builds_an_audio_request(monkeypatch):
    captured = {}
    _install_fake_genai(monkeypatch, captured)
    monkeypatch.setattr(speech_tts, "_gemini_api_key", lambda: "test-key")
    monkeypatch.setattr(speech_tts, "get_voice", lambda: "Kore")

    written = {}
    monkeypatch.setattr(
        speech_tts, "write_wave",
        lambda path, samples, rate: written.update(rate=rate, n=len(samples)),
    )
    monkeypatch.setattr(speech_tts, "_play_wav", lambda path: written.update(played=True))

    assert speech_tts._speak_gemini("hello world") is True
    assert captured["api_key"] == "test-key"
    assert captured["contents"] == "hello world"
    assert captured["model"] == speech_tts._GEMINI_TTS_MODEL
    cfg = captured["config"]
    assert cfg.response_modalities == ["AUDIO"]
    assert cfg.speech_config.voice_config.prebuilt_voice_config.voice_name == "Kore"
    assert written["rate"] == 24000
    assert written["played"] is True


def test_gemini_ignores_a_path_like_voice(monkeypatch):
    captured = {}
    _install_fake_genai(monkeypatch, captured)
    monkeypatch.setattr(speech_tts, "_gemini_api_key", lambda: "test-key")
    # A leftover piper Voice-file path must not be sent as a Gemini voice name.
    monkeypatch.setattr(speech_tts, "get_voice", lambda: r"C:\voices\en_US.onnx")
    monkeypatch.setattr(speech_tts, "write_wave", lambda *a, **k: None)
    monkeypatch.setattr(speech_tts, "_play_wav", lambda *a, **k: None)

    assert speech_tts._speak_gemini("hi") is True
    voice = captured["config"].speech_config.voice_config.prebuilt_voice_config.voice_name
    assert voice == speech_tts._GEMINI_DEFAULT_VOICE


def test_speak_gemini_returns_false_without_a_key(monkeypatch):
    monkeypatch.setattr(speech_tts, "_gemini_api_key", lambda: "")
    # No SDK call is attempted when there is no key.
    assert speech_tts._speak_gemini("hi") is False


def test_offline_mode_routes_cloud_gemini_to_sapi(monkeypatch):
    calls = []
    monkeypatch.setattr(
        speech_tts, "_setting",
        lambda key, default=None: True if key == "offline_mode_enabled" else default,
    )
    for engine_id in ("edge", "sapi", "gemini"):
        monkeypatch.setitem(
            speech_tts.ENGINES, engine_id,
            {**speech_tts.ENGINES[engine_id], "probe": lambda: True},
        )
    monkeypatch.setattr(speech_tts, "_speak_sapi", lambda text: (calls.append("sapi"), True)[1])
    monkeypatch.setattr(speech_tts, "_speak_gemini", lambda text: (calls.append("gemini"), True)[1])

    assert speech_tts.speak("hi", engine_id="gemini", use_sink=False) is True
    assert calls == ["sapi"]
