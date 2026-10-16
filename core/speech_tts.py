"""
core/speech_tts.py
Pluggable text-to-speech for Brahma Evo's non-Gemini path.

Brahma already routes every utterance one of two ways:

  * a live Gemini session is connected -> Gemini Live synthesises the audio
    (unified native voice). That path is untouched by this module.
  * otherwise -> the "offline" sink, historically `edge_tts`, which is a
    Microsoft **cloud** service. This module owns that second path and makes it
    selectable.

Engines
-------
edge        default, unchanged. Microsoft Edge neural voices (en-US-GuyNeural).
            Best quality, needs internet.
piper       local neural, ~60 MB voice. Offline, fast, natural cadence.
kokoro      local neural ONNX. Offline, higher quality, larger download.
sapi        Windows SAPI via pyttsx3 / the existing native male voice.
            Zero download, instant, robotic. Always present on Windows.

Design rules
------------
- `edge` stays the default so nothing changes for existing users, including the
  existing `offline_mode_enabled` -> SAPI routing, which is preserved below that
  the user's explicit engine choice wins.
- No hard dependency. piper / kokoro are probed and degrade to the next engine.
- `speak()` is safe to call from any thread and returns whether audio was
  actually produced, so callers can log honestly instead of assuming success.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import uuid
from typing import Any, Dict, List, Optional, Tuple

_speak_lock = threading.Lock()

# Where local voices live. %LOCALAPPDATA%\BrahmaAI\voices is the app's own data
# root, so voices survive upgrades and do not pollute the repo.
def _voices_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return os.path.join(base, "BrahmaAI", "voices")


def _setting(key: str, default: Any = None) -> Any:
    try:
        from memory import config_manager

        return config_manager.get_setting(key, default)
    except Exception:
        return default


def get_engine_id() -> str:
    return str(_setting("speech_tts_engine", "edge") or "edge").strip().lower()


def set_engine_id(engine_id: str) -> None:
    try:
        from memory import config_manager

        config_manager.set_setting("speech_tts_engine", engine_id)
    except Exception:
        pass


def get_voice() -> str:
    return str(_setting("speech_tts_voice", "") or "").strip()


def set_voice(voice: str) -> None:
    try:
        from memory import config_manager

        config_manager.set_setting("speech_tts_voice", voice)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Availability probes. Cheap, never raise.
# ---------------------------------------------------------------------------
def _probe_edge() -> bool:
    try:
        import edge_tts  # noqa: F401

        return True
    except Exception:
        return False


def _probe_piper() -> bool:
    try:
        import piper  # noqa: F401

        return True
    except Exception:
        return False


def _probe_kokoro() -> bool:
    try:
        import kokoro_onnx  # noqa: F401

        return True
    except Exception:
        return False


def _probe_sapi() -> bool:
    if os.name != "nt":
        return False
    try:
        import pyttsx3  # noqa: F401

        return True
    except Exception:
        # The native male voice does not need pyttsx3; it drives SAPI directly.
        try:
            from actions import attention_monitor

            return callable(getattr(attention_monitor, "_speak_sapi_male", None))
        except Exception:
            return False


def _first_with_suffix(directory: str, suffixes: tuple) -> str:
    """First file in `directory` ending with any of `suffixes`, or "".

    Deliberately NOT preferring a quantised filename: on this machine the
    `q8f16` Kokoro build hard-crashes the ONNX runtime's CPU provider while the
    full-precision build works fine, so "prefer the small one" picked the file
    that takes the process down. Order is left deterministic and alphabetical;
    the explicit Voice-file setting in Settings is the escape hatch when a pack
    offers several models.
    """
    if not os.path.isdir(directory):
        return ""
    for name in sorted(os.listdir(directory)):
        if name.endswith(suffixes):
            return os.path.join(directory, name)
    return ""


def _piper_voice_path() -> str:
    """Piper needs BOTH the .onnx weights and its sibling config.

    Piper's release layout is ``<name>.onnx`` + ``<name>.onnx.json`` (the config
    keeps the full original extension), so the sibling is derived from the full
    filename rather than from a stem that has already been stripped.
    """
    configured = get_voice()
    if configured and os.path.isfile(configured) and os.path.isfile(configured + ".json"):
        return configured
    directory = os.path.join(_voices_dir(), "piper")
    onnx = _first_with_suffix(directory, (".onnx",))
    if onnx and os.path.isfile(onnx + ".json"):
        return onnx
    # Some older packs ship the config as <stem>.json instead.
    if onnx and os.path.isfile(os.path.splitext(onnx)[0] + ".json"):
        return onnx
    return ""


def _kokoro_model_path() -> str:
    configured = get_voice()
    if configured and os.path.isfile(configured):
        return configured
    return _first_with_suffix(os.path.join(_voices_dir(), "kokoro"), (".onnx",))


def _kokoro_voices_path() -> str:
    """Kokoro ships a separate voices.bin; the constructor requires both paths."""
    directory = os.path.join(_voices_dir(), "kokoro")
    found = _first_with_suffix(directory, (".bin",))
    if found:
        return found
    return _first_with_suffix(directory, (".voices",))


def _kokoro_probe_subprocess(model_path: str, voices_path: str, timeout: float = 90.0) -> bool:
    """Load the Kokoro model in a child process to see whether it survives.

    A mismatched quantisation can make onnxruntime's CPU provider die with an
    access violation, which is a hard process crash that no in-process try/except
    can catch -- selecting that model would take Brahma down every time it tried
    to speak. Probing out-of-process turns a crash into a False.
    """
    import subprocess
    import sys as _sys

    script = (
        "import sys\n"
        "from kokoro_onnx import Kokoro\n"
        "k = Kokoro(sys.argv[1], sys.argv[2])\n"
        "s, r = k.create('ok', voice='af_heart', speed=1.0, lang='en-us')\n"
        "sys.exit(0 if len(s) > 0 else 3)\n"
    )
    try:
        result = subprocess.run(
            [_sys.executable, "-c", script, model_path, voices_path],
            timeout=timeout,
            capture_output=True,
        )
        return result.returncode == 0
    except Exception:
        return False


_probe_cache: Dict[str, bool] = {}
# Small ceiling: the key is a model|voices path pair, so this only ever holds a
# handful of entries, but a bounded cache cannot grow without limit if a user
# keeps pointing at new voice packs.
_PROBE_CACHE_LIMIT = 16


def _kokoro_model_usable() -> bool:
    """True when the discovered model actually loads (survives the crash probe)."""
    model, voices = _kokoro_model_path(), _kokoro_voices_path()
    if not (model and voices):
        return False
    key = f"{model}|{voices}"
    cached = _probe_cache.get(key)
    if cached is None:
        cached = _kokoro_probe_subprocess(model, voices)
        if len(_probe_cache) >= _PROBE_CACHE_LIMIT:
            _probe_cache.clear()
        _probe_cache[key] = cached
    return cached


ENGINES: Dict[str, Dict[str, Any]] = {
    "edge": {
        "label": "Microsoft Edge (cloud)",
        "probe": _probe_edge,
        "detail": "Default, unchanged. en-US-GuyNeural. Best quality, needs internet.",
        "local": False,
    },
    "piper": {
        "label": "Piper (local neural)",
        "probe": lambda: _probe_piper() and bool(_piper_voice_path()),
        "detail": "Offline. Needs BOTH a voice .onnx and its .json in %LOCALAPPDATA%\\BrahmaAI\\voices\\piper.",
        "local": True,
    },
    "kokoro": {
        "label": "Kokoro (local neural ONNX)",
        "probe": lambda: _probe_kokoro() and _kokoro_model_usable(),
        "detail": "Offline. Needs a model .onnx AND voices.bin in %LOCALAPPDATA%\\BrahmaAI\\voices\\kokoro. "
                  "The model is load-tested in a child process; a quantisation that crashes onnxruntime is reported as unavailable.",
        "local": True,
    },
    "sapi": {
        "label": "Windows SAPI (system voice)",
        "probe": _probe_sapi,
        "detail": "Offline, zero download, instant. Traditional/robotic cadence.",
        "local": True,
    },
}

_FALLBACK_CHAIN = ("edge", "sapi")


def available_engines() -> List[Dict[str, Any]]:
    """Engine catalogue for the settings UI."""
    out: List[Dict[str, Any]] = []
    for engine_id, spec in ENGINES.items():
        try:
            ok = bool(spec["probe"]())
        except Exception:
            ok = False
        detail = spec["detail"]
        if engine_id == "piper" and not ok and _probe_piper():
            detail += " Voice file missing - run install_speech_deps.ps1."
        if engine_id == "kokoro" and not ok and _probe_kokoro():
            detail += " Voice file missing - run install_speech_deps.ps1."
        out.append({
            "id": engine_id,
            "label": spec["label"],
            "detail": detail,
            "local": spec["local"],
            "available": ok,
        })
    return out


def resolve_available(engine_id: Optional[str] = None) -> str:
    engine_id = (engine_id or get_engine_id()).strip().lower()
    order: List[str] = []
    if engine_id in ENGINES:
        order.append(engine_id)
    order.extend(e for e in _FALLBACK_CHAIN if e != engine_id)
    for candidate in order:
        spec = ENGINES.get(candidate)
        if not spec:
            continue
        try:
            if spec["probe"]():
                return candidate
        except Exception:
            continue
    return "none"


# ---------------------------------------------------------------------------
# Playback. Piper and Kokoro both emit WAV, which winsound plays natively.
# ---------------------------------------------------------------------------
def _play_wav(path: str) -> None:
    if os.name == "nt":
        import winsound

        # No SND_SYNC: Python's winsound has no such flag (the real set is
        # SND_ASYNC/NOWAIT/...). PlaySound plays synchronously unless SND_ASYNC
        # or SND_NOWAIT is given, so SND_FILENAME alone already blocks until the
        # audio finishes. Passing SND_SYNC raised AttributeError, which killed
        # Piper and Kokoro at playback even though they synthesised correctly.
        winsound.PlaySound(path, winsound.SND_FILENAME)
        return
    # POSIX fallback: try ffplay, then aplay, then give up quietly.
    for cmd in (["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path],
                ["aplay", "-q", path]):
        try:
            subprocess.run(cmd, check=True, timeout=120)
            return
        except Exception:
            continue


def _speak_edge(text: str) -> bool:
    """The pre-existing edge_tts path, unchanged, wrapped for bool returns."""
    from actions.attention_monitor import _speak_edge_native

    _speak_edge_native(text, force_edge=True)
    return True


def _speak_sapi(text: str) -> bool:
    try:
        import pyttsx3

        engine = pyttsx3.init()
        engine.setProperty("rate", 175)
        engine.say(text)
        engine.runAndWait()
        return True
    except Exception:
        pass
    try:
        from actions.attention_monitor import _speak_sapi_male

        _speak_sapi_male(text)
        return True
    except Exception:
        return False


_piper_lock = threading.Lock()
_piper_cache: Dict[str, Any] = {}


def _speak_piper(text: str) -> bool:
    """Piper 1.8: synthesize_wav(text, wave.Wave_write) sets the WAV format itself."""
    from piper import PiperVoice

    path = _piper_voice_path()
    if not path:
        return False
    with _piper_lock:
        voice = _piper_cache.get(path)
        if voice is None:
            voice = PiperVoice.load(path)
            _piper_cache.clear()
            _piper_cache[path] = voice
        wav_path = os.path.join(tempfile.gettempdir(), f"brahma_piper_{uuid.uuid4().hex}.wav")
        try:
            import wave

            # set_wav_format=True is the default, so piper writes nchannels /
            # sampwidth / framerate itself. Pre-setting them would be overwritten.
            with wave.open(wav_path, "wb") as wav:
                voice.synthesize_wav(text, wav)
            _play_wav(wav_path)
            return True
        finally:
            try:
                os.remove(wav_path)
            except Exception:
                pass


_kokoro_lock = threading.Lock()
_kokoro_cache: Dict[str, Any] = {}


def _speak_kokoro(text: str) -> bool:
    """Kokoro 0.6: Kokoro(model_path, voices_path) needs BOTH, create() -> (samples, rate)."""
    from kokoro_onnx import Kokoro

    model_path = _kokoro_model_path()
    voices_path = _kokoro_voices_path()
    if not (model_path and voices_path):
        return False
    with _kokoro_lock:
        key = f"{model_path}|{voices_path}"
        kokoro = _kokoro_cache.get(key)
        if kokoro is None:
            kokoro = Kokoro(model_path, voices_path)
            _kokoro_cache.clear()
            _kokoro_cache[key] = kokoro
        samples, rate = kokoro.create(text, voice="af_heart", speed=1.0, lang="en-us")
        wav_path = os.path.join(tempfile.gettempdir(), f"brahma_kokoro_{uuid.uuid4().hex}.wav")
        try:
            write_wave(wav_path, samples, rate)
            _play_wav(wav_path)
            return True
        finally:
            try:
                os.remove(wav_path)
            except Exception:
                pass


def write_wave(path: str, samples, rate: int) -> None:
    """Write float32 samples to a 16-bit mono WAV."""
    import wave

    import numpy as np

    data = np.asarray(samples, dtype=np.float32)
    data = np.clip(data, -1.0, 1.0)
    pcm = (data * 32767.0).astype(np.int16)
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(int(rate))
        wav.writeframes(pcm.tobytes())


# Engine id -> module-level function name, resolved at call time. Holding the
# names (not the objects) keeps this dispatch testable and lets an engine be
# swapped or stubbed without reimporting the module.
_SPEAKERS: Dict[str, str] = {
    "edge": "_speak_edge",
    "piper": "_speak_piper",
    "kokoro": "_speak_kokoro",
    "sapi": "_speak_sapi",
}


def speak_with(engine_id: str, text: str) -> Tuple[bool, str]:
    """Speak through exactly `engine_id`, bypassing the app's speech sink.

    Returns (played, engine_actually_used). Used by the Settings test button so
    the user hears the engine they selected, and so the UI can name it instead
    of claiming success for an engine that never ran. Unlike speak(), this does
    not fall back to another engine: a test that silently degrades to a
    different voice proves nothing about the selection.
    """
    text = (text or "").strip()
    if not text:
        return False, ""
    wanted = (engine_id or "").strip().lower()
    spec = ENGINES.get(wanted)
    if spec is None:
        return False, ""
    fn = globals().get(_SPEAKERS.get(wanted) or "")
    if fn is None:
        return False, ""
    try:
        if not spec["probe"]():
            return False, ""
    except Exception:
        return False, ""
    try:
        return (bool(fn(text)), wanted)
    except Exception:
        return False, wanted


def speak(text: str, engine_id: Optional[str] = None, use_sink: bool = True) -> bool:
    """Synthesise and play `text`. Returns True when audio was produced.

    Honours the existing speech sink and the pre-existing offline-mode routing:
    offline_mode_enabled still selects SAPI unless the user explicitly picked a
    different engine. Never raises.

    `use_sink=False` deliberately skips the installed sink. The Settings "Speak
    test" button passes False: the sink is the app's own Gemini-backed speech
    path, so honouring it here meant the test always played through Gemini no
    matter which engine was selected - the button tested nothing.
    """
    text = (text or "").strip()
    if not text:
        return False

    # Respect an installed sink first, exactly like speak_native() does. The sink
    # lookup and the offline-mode read are independent: an import failure here
    # must not silently clear the offline flag and send us to the cloud.
    if use_sink:
        try:
            from actions import attention_monitor

            if attention_monitor._speech_sink is not None:
                attention_monitor._speech_sink(text)
                return True
        except Exception:
            pass

    offline = bool(_setting("offline_mode_enabled", False))

    chosen = (engine_id or get_engine_id() or "").strip().lower()

    # Offline mode cannot use a cloud voice: edge-tts is a network service.
    # The pre-existing behaviour routed offline mode to SAPI, so preserve that,
    # but make it depend on reachability rather than on whether the setting key
    # happens to be present — otherwise simply saving this card would silently
    # turn offline mode back into a failing cloud call.
    spec = ENGINES.get(chosen)
    if offline and spec is not None and not spec["local"]:
        chosen = "sapi"

    order: List[str] = []
    if chosen in _SPEAKERS:
        order.append(chosen)
    order.extend(e for e in _FALLBACK_CHAIN if e != chosen)

    for candidate in order:
        fn_name = _SPEAKERS.get(candidate)
        fn = globals().get(fn_name) if fn_name else None
        if fn is None:
            continue
        try:
            if not ENGINES[candidate]["probe"]():
                continue
        except Exception:
            continue
        try:
            if fn(text):
                return True
        except Exception:
            continue
    return False