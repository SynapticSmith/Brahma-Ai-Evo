"""
core/speech_stt.py
Pluggable speech-to-text for Brahma Evo.

Replaces the hard-coded `speech_recognition.Recognizer().recognize_google()`
calls with a selectable engine so the local / offline path does not depend on a
cloud endpoint.

Engines
-------
google          default. The previous behaviour, byte for byte: SpeechRecognition
                posting to Google's public endpoint. Needs internet.
faster_whisper  fully local. faster-whisper (CTranslate2, int8) on CPU.
                No network once the model is on disk.
disabled        no transcription; callers see an empty string.

Design rules
------------
- No hard dependency. Every engine is probed lazily and reports availability, so
  a machine with nothing installed still boots.
- Fail-soft and fail-down: if the selected engine is unavailable or throws, we
  fall back down the preference chain rather than losing audio outright.
- The model is loaded on first use and cached. Loading faster-whisper costs
  hundreds of milliseconds, so `warm()` exists for a background thread to call
  during startup; nothing here ever blocks the request path for longer than the
  transcription itself.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional

# Preference order used when the selected engine cannot serve a request.
_FALLBACK_CHAIN = ("faster_whisper", "google")

_model_lock = threading.Lock()
_model_cache: Dict[str, Any] = {}

# faster-whisper's expected input rate. We resample ourselves rather than rely on
# a library version accepting a sampling-rate argument for raw arrays.
_TARGET_RATE = 16000


def _setting(key: str, default: Any = None) -> Any:
    try:
        from memory import config_manager

        return config_manager.get_setting(key, default)
    except Exception:
        return default


def get_engine_id() -> str:
    return str(_setting("speech_stt_engine", "google") or "google").strip().lower()


def set_engine_id(engine_id: str) -> None:
    try:
        from memory import config_manager

        config_manager.set_setting("speech_stt_engine", engine_id)
    except Exception:
        pass


def get_model_name() -> str:
    return str(_setting("speech_whisper_model", "base.en") or "base.en").strip()


def set_model_name(name: str) -> None:
    try:
        from memory import config_manager

        config_manager.set_setting("speech_whisper_model", name)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Availability probes. Each is cheap and must never raise.
# ---------------------------------------------------------------------------
def _probe_google() -> bool:
    try:
        import speech_recognition  # noqa: F401

        return True
    except Exception:
        return False


def _probe_faster_whisper() -> bool:
    try:
        import faster_whisper  # noqa: F401

        return True
    except Exception:
        return False


ENGINES: Dict[str, Dict[str, Any]] = {
    "google": {
        "label": "Google (cloud)",
        "probe": _probe_google,
        "detail": "Default. SpeechRecognition -> Google endpoint. Requires internet.",
        "local": False,
    },
    "faster_whisper": {
        "label": "faster-whisper (local)",
        "probe": _probe_faster_whisper,
        "detail": "Offline. CTranslate2 int8 on CPU. pip install faster-whisper",
        "local": True,
    },
    "disabled": {
        "label": "Disabled",
        "probe": lambda: True,
        "detail": "Never transcribes. Microphone still runs for wake level.",
        "local": True,
    },
}


def available_engines() -> List[Dict[str, Any]]:
    """Engine catalogue for the settings UI."""
    out: List[Dict[str, Any]] = []
    for engine_id, spec in ENGINES.items():
        try:
            ok = bool(spec["probe"]())
        except Exception:
            ok = False
        out.append({
            "id": engine_id,
            "label": spec["label"],
            "detail": spec["detail"],
            "local": spec["local"],
            "available": ok,
        })
    return out


def resolve_available(engine_id: Optional[str] = None) -> str:
    """The engine actually usable right now, given preference and fallbacks."""
    engine_id = (engine_id or get_engine_id()).strip().lower()
    order: List[str] = []
    if engine_id in ENGINES:
        order.append(engine_id)
    order.extend(e for e in _FALLBACK_CHAIN if e != engine_id)
    if "google" not in order:
        order.append("google")
    for candidate in order:
        spec = ENGINES.get(candidate)
        if not spec:
            continue
        try:
            if spec["probe"]():
                return candidate
        except Exception:
            continue
    return "disabled"


# ---------------------------------------------------------------------------
# faster-whisper model handling
# ---------------------------------------------------------------------------
def _get_faster_whisper_model():
    """Load and cache the model. Thread-safe; blocking by design."""
    name = get_model_name()
    with _model_lock:
        cached = _model_cache.get(name)
        if cached is not None:
            return cached
        try:
            from faster_whisper import WhisperModel
        except Exception:
            return None
        # cpu_threads=0 lets CTranslate2 use every core; int8 keeps it small.
        model = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=0)
        _model_cache.clear()
        _model_cache[name] = model
        return model


def warm() -> None:
    """Kick off a background load so the first real utterance is not the slow one."""
    if resolve_available() != "faster_whisper":
        return

    def _bg():
        try:
            _get_faster_whisper_model()
        except Exception:
            pass

    threading.Thread(target=_bg, daemon=True, name="stt-warm").start()


def _pcm_to_float32(pcm_bytes: bytes, src_rate: int):
    """int16 mono PCM -> float32 in [-1, 1], resampled to 16 kHz."""
    import numpy as np

    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    if src_rate and src_rate != _TARGET_RATE and audio.size:
        duration = audio.size / float(src_rate)
        target_n = max(1, int(round(duration * _TARGET_RATE)))
        # np.interp maps target times onto source times. Source coordinates are
        # in seconds, not sample indices -- mixing the two silently truncates.
        source_times = np.arange(audio.size, dtype=np.float64) / float(src_rate)
        target_times = np.linspace(0.0, duration, target_n, endpoint=False)
        audio = np.interp(target_times, source_times, audio).astype(np.float32)
    return audio


def _transcribe_faster_whisper(pcm_bytes: bytes, sample_rate: int) -> str:
    model = _get_faster_whisper_model()
    if model is None:
        raise RuntimeError("faster-whisper model unavailable")
    audio = _pcm_to_float32(pcm_bytes, sample_rate)
    segments, _info = model.transcribe(audio, beam_size=1, vad_filter=True)
    return "".join(segment.text for segment in segments).strip()


def _transcribe_google(pcm_bytes: bytes, sample_rate: int) -> str:
    import speech_recognition as sr

    recognizer = sr.Recognizer()
    audio_data = sr.AudioData(pcm_bytes, sample_rate, 2)
    return recognizer.recognize_google(audio_data) or ""


_TRANSCRIBERS: Dict[str, Callable[[bytes, int], str]] = {
    "faster_whisper": _transcribe_faster_whisper,
    "google": _transcribe_google,
}


def transcribe_pcm(pcm_bytes: bytes, sample_rate: int = _TARGET_RATE) -> str:
    """Transcribe mono int16 PCM. Returns "" when nothing usable was heard.

    Never raises: an unusable engine or a failed decode degrades to the next
    candidate and finally to an empty string, because losing one utterance is
    always better than taking down the caller.
    """
    if not pcm_bytes:
        return ""
    primary = get_engine_id()
    if primary == "disabled":
        return ""
    for candidate in (primary, *_FALLBACK_CHAIN):
        fn = _TRANSCRIBERS.get(candidate)
        if fn is None:
            continue
        try:
            if not ENGINES[candidate]["probe"]():
                continue
        except Exception:
            continue
        try:
            return fn(pcm_bytes, sample_rate) or ""
        except Exception:
            continue
    return ""