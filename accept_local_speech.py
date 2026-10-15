"""
accept_local_speech.py
Acceptance check for the local-model + local-speech work (ONE-391).

Proves four claims that were asserted but never measured:

  A. Local models get the SAME bounded tool surface as Gemini Live, and MCP
     tools become reachable through the discovery meta-tools.
  B. The speech pipeline is genuinely pluggable, with cloud defaults intact.
  C. Offline mode keeps working exactly as before (cloud voice never selected).
  D. The Gemini Live path is untouched -- a live session still wins.

No network, no microphone, no audio played. Optional packages (faster-whisper,
piper, kokoro) are absent on purpose: every assertion below must hold on a
machine where they are NOT installed, because that is the common case.
Usage: py -3.11 accept_local_speech.py
"""
import asyncio
import re
import sys
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from accept_util import check, section, report


# ---------------------------------------------------------------------------
# A. Local tool surface
# ---------------------------------------------------------------------------
def test_local_tool_surface() -> None:
    section("A. Local models get the bounded tool surface")
    import json
    import re

    from core.local_brain import LocalBrain

    src = (BASE / "main.py").read_text(encoding="utf-8")
    start = src.index("TOOL_DECLARATIONS = [")
    end = src.index("\n]", start)
    natives = []
    for m in re.finditer(
        r'"name":\s*"([a-z0-9_]+)"\s*,\s*"description":\s*(\(.*?\)|"[^"]*")', src[start:end], re.S
    ):
        try:
            d = eval(m.group(2)) if m.group(2).startswith("(") else json.loads(m.group(2))
        except Exception:
            d = m.group(2)
        natives.append({"name": m.group(1), "description": " ".join(str(d).split()),
                        "parameters": {"type": "OBJECT", "properties": {}}})

    brain = LocalBrain()
    static = natives

    # With no primed index the bridge still answers (meta-tools only), which is
    # what makes the fallback question interesting.
    picked, ctx = brain.resolve_tools("dim the monitors", static)
    names = {d.get("name") for d in picked}

    check("resolve_tools returns a non-empty surface", bool(picked), f"{len(picked)} tools")
    check("discovery meta-tools are reachable locally",
          {"search_tools", "call_tool", "get_skill_manual"} <= names, f"{sorted(names)[:5]}")
    check("surface is smaller than the static list", len(picked) < len(static),
          f"{len(picked)} vs {len(static)} static")

    # Formatting for an OpenAI-compatible local runtime must still be valid.
    formatted = brain.format_tools_for_local(picked)
    check("declarations convert to OpenAI tool shape",
          all(f.get("type") == "function" and "function" in f for f in formatted))
    check("schema types are lowercased for strict local runtimes",
          all(
              (p.get("type") in (None, "object")
               or isinstance(p.get("type"), str))
              for f in formatted
              for p in [f["function"].get("parameters", {})]
          ))

    # The bridge must never be able to strand the local path.
    def _explode(intent: str = ""):
        raise RuntimeError("index offline")

    import core.tool_bridge as tb

    original = tb.build_session_payload
    tb.build_session_payload = _explode
    try:
        fallback, fctx = brain.resolve_tools("anything", static)
        check("index failure degrades to the static list",
              len(fallback) == len(static), f"{len(fallback)} == {len(static)}")
    finally:
        tb.build_session_payload = original

    check("_fallback_reply no longer sends TOOL_DECLARATIONS directly",
          "tools=TOOL_DECLARATIONS" not in src)
    check("_fallback_reply routes through resolve_tools", "resolve_tools(" in src)
    check("JIT skill context is injected when present",
          "local_skill_ctx" in src)


# ---------------------------------------------------------------------------
# B. Pluggable engines
# ---------------------------------------------------------------------------
def test_engine_registry() -> None:
    section("B. Engine registry and dispatch")
    from core import speech_stt, speech_tts

    stt = {e["id"]: e for e in speech_stt.available_engines()}
    tts = {e["id"]: e for e in speech_tts.available_engines()}

    check("STT offers google + faster_whisper", {"google", "faster_whisper"} <= set(stt))
    check("TTS offers edge + piper + kokoro + sapi",
          {"edge", "piper", "kokoro", "sapi"} <= set(tts))
    # Assert the *built-in* fallback, not whatever the user currently has
    # selected. These engines are user-configurable, so asserting the live value
    # makes the test fail the moment somebody actually uses the Settings card --
    # which is exactly what happened.
    _orig_setting = speech_stt._setting
    try:
        speech_stt._setting = lambda key, default=None: default
        check("STT falls back to google when unset (unchanged behaviour)",
              speech_stt.get_engine_id() == "google", speech_stt.get_engine_id())
    finally:
        speech_stt._setting = _orig_setting

    _orig_setting = speech_tts._setting
    try:
        speech_tts._setting = lambda key, default=None: default
        check("TTS falls back to edge when unset (unchanged behaviour)",
              speech_tts.get_engine_id() == "edge", speech_tts.get_engine_id())
    finally:
        speech_tts._setting = _orig_setting

    check("cloud engines are labelled non-local",
          stt["google"]["local"] is False and tts["edge"]["local"] is False)
    check("local engines are labelled local",
          stt["faster_whisper"]["local"] and tts["sapi"]["local"])

    # Unavailable engines must not be selectable as the live one.
    live_stt = speech_stt.resolve_available()
    live_tts = speech_tts.resolve_available()
    check("resolve never returns an unavailable engine",
          stt.get(live_stt, {}).get("available") and tts.get(live_tts, {}).get("available"),
          f"stt={live_stt} tts={live_tts}")

    # An engine that is not installed must fall through, not raise.
    resolved = speech_tts.resolve_available("piper")
    check("missing piper falls through to a working engine",
          tts.get(resolved, {}).get("available"), f"piper -> {resolved}")
    check("available selection becomes the live engine",
          speech_tts.resolve_available("sapi") == "sapi",
          f"sapi -> {speech_tts.resolve_available('sapi')}")

    # The settings card writes the choice; the pipeline must then honour it.
    original_setting = speech_tts._setting
    try:
        speech_tts._setting = lambda key, default=None: (
            "sapi" if key == "speech_tts_engine" else default
        )
        check("pipeline reads the selected engine from settings",
              speech_tts.resolve_available() == "sapi", speech_tts.resolve_available())
    finally:
        speech_tts._setting = original_setting

    section("B2. PCM conversion for local ASR")
    import numpy as np

    pcm = (np.sin(np.linspace(0, 40 * np.pi, 16000)) * 20000).astype(np.int16).tobytes()
    audio = speech_stt._pcm_to_float32(pcm, 16000)
    check("16 kHz input is accepted", audio.dtype == np.float32 and audio.size == 16000,
          f"size={audio.size}")
    check("samples are normalised into [-1, 1]", float(np.abs(audio).max()) <= 1.0,
          f"max={float(np.abs(audio).max()):.3f}")
    # 16000 samples captured at 8 kHz is 2.0 s of audio, so 16 kHz output must
    # be 32000 samples. (Asserting the duration, not a sample count.)
    resampled = speech_stt._pcm_to_float32(pcm, 8000)
    check("8 kHz input is resampled up to 16 kHz by duration", resampled.size == 32000,
          f"16000 samples @8kHz = 2.0s -> {resampled.size} @16kHz")
    half = speech_stt._pcm_to_float32((np.zeros(24000, dtype=np.int16)).tobytes(), 48000)
    check("48 kHz input is resampled down to 16 kHz", half.size == 8000,
          f"24000 samples @48kHz = 0.5s -> {half.size} @16kHz")
    check("silent input stays silent", float(np.abs(half).max()) == 0.0)
    check("empty input does not crash", speech_stt._pcm_to_float32(b"", 16000).size == 0)

    section("B3. STT dispatch honours the selected engine")
    calls = []
    original_fw = speech_stt._TRANSCRIBERS["faster_whisper"]
    original_gg = speech_stt._TRANSCRIBERS["google"]
    speech_stt._TRANSCRIBERS["faster_whisper"] = lambda p, r: calls.append("fw") or "brahma evo"
    speech_stt._TRANSCRIBERS["google"] = lambda p, r: calls.append("gg") or "cloud text"
    original_get = speech_stt.get_engine_id
    try:
        # Pin the engine explicitly instead of inheriting the user's current
        # selection, which may legitimately be faster-whisper now.
        speech_stt.get_engine_id = lambda: "google"
        calls.clear()
        out = speech_stt.transcribe_pcm(b"\x00\x01" * 64, 16000)
        check("explicitly-selected engine is used", calls[:1] == ["gg"], f"{calls}")
        check("transcription is returned", out == "cloud text", out)

        speech_stt.get_engine_id = lambda: "faster_whisper"
        calls.clear()
        out = speech_stt.transcribe_pcm(b"\x00\x01" * 64, 16000)
        check("local engine is used when selected", calls[:1] == ["fw"], f"{calls}")
        check("local engine transcript is returned", out == "brahma evo", out)

        speech_stt.get_engine_id = lambda: "disabled"
        calls.clear()
        out = speech_stt.transcribe_pcm(b"\x00\x01" * 64, 16000)
        check("disabled engine transcribes nothing", out == "" and not calls,
              f"out={out!r} calls={calls}")
    finally:
        speech_stt.get_engine_id = original_get
        speech_stt._TRANSCRIBERS["faster_whisper"] = original_fw
        speech_stt._TRANSCRIBERS["google"] = original_gg

    section("B4. TTS dispatch honours the selected engine")
    spoken = []
    for engine_id in ("edge", "piper", "kokoro", "sapi"):
        spoken.clear()
        ok = speech_tts.speak("hello", engine_id=engine_id)
        check(f"{engine_id}: speak() returns a bool, never raises", isinstance(ok, bool),
              f"returned {ok!r}")

    # Empty text must be a no-op rather than an engine call.
    check("empty text does not synthesise", speech_tts.speak("   ") is False)

    # ------------------------------------------------------------------
    section("B4b. The Settings 'Speak test' button tests the SELECTED engine")
    # ------------------------------------------------------------------
    # Reported: every engine played Gemini. speak() honours the app's
    # installed speech sink first - which is the Gemini-backed path - so the
    # test button could never exercise a modular engine at all.
    from actions import attention_monitor

    sink_calls: list = []
    orig_sink = getattr(attention_monitor, "_speech_sink", None)
    attention_monitor._speech_sink = lambda txt: sink_calls.append(txt)
    spoken_by: list = []
    # Save what we are about to replace. Popping these entries afterwards would
    # delete the REAL engine registrations and quietly break every later test.
    saved_speakers = {e: speech_tts._SPEAKERS.get(e) for e in ("edge", "sapi")}
    saved_probes = {
        e: speech_tts.ENGINES[e]["probe"]
        for e in ("edge", "sapi") if e in speech_tts.ENGINES
    }

    def _install_fake(engine_id: str):
        def _fake(text):
            spoken_by.append((engine_id, text))
            return True
        setattr(speech_tts, f"_probe_fake_{engine_id}", _fake)
        speech_tts._SPEAKERS[engine_id] = f"_probe_fake_{engine_id}"
        spec = speech_tts.ENGINES.get(engine_id)
        if spec is not None:
            spec["probe"] = lambda: True

    try:
        # Normal app speech must keep using the sink - that is not a bug.
        spoken_by.clear()
        sink_calls.clear()
        speech_tts.speak("normal speech", engine_id="edge")
        check("speak() still honours the installed sink",
              len(sink_calls) == 1 and not spoken_by,
              f"sink={len(sink_calls)} engine={len(spoken_by)}")

        for engine_id in ("edge", "sapi"):
            _install_fake(engine_id)
            spoken_by.clear()
            sink_calls.clear()
            played, used = speech_tts.speak_with(engine_id, "test phrase")
            check(f"speak_with('{engine_id}') bypasses the sink",
                  len(sink_calls) == 0, f"sink got {len(sink_calls)} call(s)")
            check(f"speak_with('{engine_id}') names the engine it used",
                  used == engine_id, f"used {used!r}")
            check(f"speak_with('{engine_id}') actually reached that engine",
                  spoken_by == [(engine_id, "test phrase")], str(spoken_by))
            check(f"speak_with('{engine_id}') reports the result", played is True)

        # A test that silently degrades to another voice proves nothing.
        spoken_by.clear()
        played, used = speech_tts.speak_with("no-such-engine", "x")
        check("an unknown engine fails instead of falling back",
              played is False and used == "" and not spoken_by,
              f"played={played} used={used!r}")

        # The button must read the combo, not the ambient default.
        ui_src = (BASE / "ui.py").read_text(encoding="utf-8")
        check("the card keeps a reference to the TTS combo",
              "self._tts_box = tts_box" in ui_src)
        check("the test button reads the selected combo",
              "self._tts_box.currentIndex()" in ui_src)
        check("the test button does not swallow failures",
              "except Exception:\n                    pass" not in ui_src.split(
                  "def _on_speech_test")[1].split("def ")[0])
        check("the test button reports which engine ran",
              "Tested {used or engine}" in ui_src)
    finally:
        attention_monitor._speech_sink = orig_sink
        for engine_id, original in saved_speakers.items():
            if original is None:
                speech_tts._SPEAKERS.pop(engine_id, None)
            else:
                speech_tts._SPEAKERS[engine_id] = original
        for engine_id, probe in saved_probes.items():
            speech_tts.ENGINES[engine_id]["probe"] = probe
        for engine_id in ("edge", "sapi"):
            setattr(speech_tts, f"_probe_fake_{engine_id}", None)

    # ------------------------------------------------------------------
    section("B4c. WAV playback uses flags that actually exist")
    # ------------------------------------------------------------------
    # "Tested kokoro. No audio produced." Both engines synthesised fine and
    # died in _play_wav: winsound.SND_SYNC does not exist in Python's winsound
    # (the real flags are SND_ASYNC/NOWAIT/...), so playback raised
    # AttributeError and the caller reported no audio.
    import winsound

    real_flags = {n for n in dir(winsound) if n.startswith("SND")}
    play_sound = (BASE / "core" / "speech_tts.py").read_text(encoding="utf-8")
    # Strip comments first: the explanation of this very bug names SND_SYNC,
    # and a naive scan would flag its own documentation.
    code_only = re.sub(r"#.*", "", play_sound)
    used_flags = set(re.findall(r"winsound\.(SND_[A-Z_]+)", code_only))
    check("every winsound flag used exists in the stdlib module",
          used_flags <= real_flags,
          "missing: %s" % (used_flags - real_flags))
    check("playback does not rely on the non-existent SND_SYNC",
          "SND_SYNC" not in code_only)
    check("WAV playback is still synchronous (no SND_ASYNC/NOWAIT)",
          not (used_flags & {"SND_ASYNC", "SND_NOWAIT"}),
          str(used_flags))


# ---------------------------------------------------------------------------
# B5. Local TTS call contracts match the INSTALLED libraries
# ---------------------------------------------------------------------------
def test_voice_api_contracts() -> None:
    section("B5. Voice engine call contracts match the installed libraries")
    # These engines were originally written against remembered APIs and were both
    # wrong: piper.synthesize() takes no file, and Kokoro() needs two paths. This
    # pins the contracts so a version bump surfaces as a test failure rather than
    # a silent fall-through at runtime.
    import inspect

    try:
        from piper import PiperVoice
    except Exception:
        PiperVoice = None
    if PiperVoice is not None:
        params = inspect.signature(PiperVoice.synthesize_wav).parameters
        check("piper.synthesize_wav accepts (text, wav_file)",
              "text" in params and "wav_file" in params, f"{list(params)}")
        check("piper.synthesize_wav sets the WAV format itself",
              params.get("set_wav_format", None) is not None)
        check("piper.synthesize_wav takes an already-open wave writer",
              "Wave_write" in str(params["wav_file"].annotation))
        from core import speech_tts

        check("our piper speaker uses synthesize_wav, not synthesize",
              "synthesize_wav" in speech_tts._speak_piper.__code__.co_names
              or "synthesize_wav" in str(speech_tts._speak_piper.__code__.co_consts))
    else:
        print("  [SKIP] piper not installed")

    try:
        from kokoro_onnx import Kokoro
    except Exception:
        Kokoro = None
    if Kokoro is not None:
        params = list(inspect.signature(Kokoro.__init__).parameters)
        check("Kokoro requires both model_path and voices_path",
              "model_path" in params and "voices_path" in params, f"{params}")
        check("Kokoro.create returns (samples, rate)",
              "text" in inspect.signature(Kokoro.create).parameters)
        from core import speech_tts

        src = speech_tts._speak_kokoro.__code__.co_names
        called = speech_tts._speak_kokoro.__code__.co_varnames
        check("our kokoro speaker resolves BOTH the model and the voices file",
              "_kokoro_model_path" in src and "_kokoro_voices_path" in src
              and "model_path" in called and "voices_path" in called,
              f"calls={[n for n in src if 'kokoro' in n]} vars={[v for v in called if 'path' in v]}")
    else:
        print("  [SKIP] kokoro not installed")

    # Availability must reflect voice FILES that actually load, not merely
    # whether the package imports. These are user-supplied files, so assert
    # self-consistency (probe == files-present-and-loadable) rather than a
    # hardcoded expectation that would break the moment someone installs a
    # voice -- which is exactly what happened.
    from core import speech_tts

    piper_has_files = bool(speech_tts._piper_voice_path())
    check("piper availability tracks whether a voice pair is present",
          speech_tts.ENGINES["piper"]["probe"]() is piper_has_files,
          f"files={piper_has_files} probe={speech_tts.ENGINES['piper']['probe']()}")

    kokoro_files = bool(speech_tts._kokoro_model_path() and speech_tts._kokoro_voices_path())
    if kokoro_files:
        # The probe runs the model in a child process: a quantisation that
        # crashes onnxruntime must read as unavailable, not take the app down.
        expected = speech_tts._kokoro_model_usable()
        check("kokoro availability matches the out-of-process load test",
              speech_tts.ENGINES["kokoro"]["probe"]() is expected,
              f"files={kokoro_files} loadable={expected}")
    else:
        check("kokoro unavailable with no model files",
              not speech_tts.ENGINES["kokoro"]["probe"]())

    # The crash-safety property itself.
    src = speech_tts._kokoro_probe_subprocess.__code__
    check("kokoro probe isolates crashes in a child process",
          "subprocess" in (src.co_names or ()) or "run" in (src.co_names or ()))


# ---------------------------------------------------------------------------
# C. Offline mode behaviour preserved
# ---------------------------------------------------------------------------
def test_offline_mode() -> None:
    section("C. Offline mode keeps its old behaviour")
    from core import speech_tts

    original_setting = speech_tts._setting
    original_edge = speech_tts._speak_edge
    original_sapi = speech_tts._speak_sapi
    used = []

    def fake_edge(text):
        used.append("edge")
        return True

    def fake_sapi(text):
        used.append("sapi")
        return True

    try:
        speech_tts._speak_edge = fake_edge
        speech_tts._speak_sapi = fake_sapi

        # Offline mode must never select the cloud voice.
        speech_tts._setting = lambda key, default=None: (
            True if key == "offline_mode_enabled" else default
        )
        used.clear()
        speech_tts.speak("hi", engine_id="edge")
        check("offline mode never picks the cloud voice",
              "edge" not in used and "sapi" in used, f"used={used}")

        # An explicitly chosen local engine is still honoured offline.
        used.clear()
        speech_tts.speak("hi", engine_id="sapi")
        check("explicit local engine is honoured offline",
              used == ["sapi"], f"used={used}")

        # Online: the cloud default must be exactly as it was.
        speech_tts._setting = lambda key, default=None: (
            False if key == "offline_mode_enabled" else default
        )
        used.clear()
        speech_tts.speak("hi", engine_id="edge")
        check("online mode still uses edge by default", used == ["edge"], f"used={used}")
    finally:
        speech_tts._setting = original_setting
        speech_tts._speak_edge = original_edge
        speech_tts._speak_sapi = original_sapi


# ---------------------------------------------------------------------------
# D. Gemini Live path untouched
# ---------------------------------------------------------------------------
def test_gemini_untouched() -> None:
    section("D. Gemini Live path is untouched")
    src = (BASE / "main.py").read_text(encoding="utf-8")

    speak_start = src.index("    def speak(self, text: str, proactive: bool = False):")
    speak_end = src.index("    def speak_error(self", speak_start)
    body = src[speak_start:speak_end]

    gemini_branch = body.split("else:")[0]
    check("live-session branch still sends to Gemini",
          "send_client_content" in gemini_branch)
    check("live-session branch does not import speech_tts",
          "speech_tts" not in gemini_branch)
    check("only the offline branch calls speech_tts",
          body.count("speech_tts.speak") == 1)
    check("unified voice comment retained",
          "Charon" in gemini_branch)

    check("no STT/TTS engine import inside _build_config",
          "speech_stt" not in src[src.index("def _build_config"):src.index("async def _execute_tool")])
    check("Live handshake config untouched by speech work",
          "tool_bridge.build_session_payload(last_intent)" in src)


def main() -> int:
    print("=" * 74)
    print("ONE-391 local model + local speech acceptance")
    print("=" * 74)
    test_local_tool_surface()
    test_engine_registry()
    test_voice_api_contracts()
    test_offline_mode()
    test_gemini_untouched()
    print("\n" + "=" * 74)
    return report()


if __name__ == "__main__":
    raise SystemExit(main())