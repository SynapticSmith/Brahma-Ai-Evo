"""Tests for per-engine voice selection in core/speech_tts.py.

With a single voice installed, "take the first file you find" works. The Voice
Library makes it normal to have a dozen Piper voices and several Kokoro model
builds on disk, so the engine must honour an explicit choice -- and must still
degrade to auto-discovery when that choice is stale (file deleted, pack
removed), because a picker that silently stops working is worse than none.

LOCALAPPDATA is redirected to a temp dir, so nothing here touches real user
data and no network or audio is involved.
"""
import pytest

from core import speech_tts
from core import voice_catalog


@pytest.fixture
def voices_root(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    return tmp_path / "BrahmaAI" / "voices"


@pytest.fixture
def settings(monkeypatch):
    """A dict-backed _setting: the tests pin their own values, not the user's."""
    values: dict = {}

    def _get(key, default=None):
        return values.get(key, default)

    monkeypatch.setattr(speech_tts, "_setting", _get)
    return values


def _install_piper_voice(root, key: str) -> None:
    piper = root / "piper"
    piper.mkdir(parents=True, exist_ok=True)
    (piper / f"{key}.onnx").write_bytes(b"model")
    (piper / f"{key}.onnx.json").write_bytes(b"{}")


def _install_kokoro(root, *filenames: str) -> None:
    kokoro = root / "kokoro"
    kokoro.mkdir(parents=True, exist_ok=True)
    for name in filenames:
        (kokoro / name).write_bytes(b"data")


# ---------------------------------------------------------------------------
# Piper
# ---------------------------------------------------------------------------
def test_piper_honours_the_selected_voice(voices_root, settings):
    _install_piper_voice(voices_root, "en_US-amy-medium")
    _install_piper_voice(voices_root, "en_US-lessac-medium")
    settings["speech_tts_piper_voice"] = "en_US-lessac-medium"

    chosen = speech_tts._piper_voice_path()
    assert chosen.endswith("en_US-lessac-medium.onnx")


def test_piper_falls_back_when_the_selected_voice_is_gone(voices_root, settings):
    _install_piper_voice(voices_root, "en_US-amy-medium")
    settings["speech_tts_piper_voice"] = "en_GB-deleted-medium"

    # The configured voice no longer exists: auto-discovery takes over rather
    # than the engine silently having nothing to say.
    assert speech_tts._piper_voice_path().endswith("en_US-amy-medium.onnx")


def test_piper_without_any_voice_is_still_unavailable(voices_root, settings):
    settings["speech_tts_piper_voice"] = "en_US-amy-medium"
    assert speech_tts._piper_voice_path() == ""
    assert speech_tts.ENGINES["piper"]["probe"]() is False


def test_piper_availability_tracks_the_selected_pair(voices_root, settings, monkeypatch):
    _install_piper_voice(voices_root, "en_US-amy-medium")
    _install_piper_voice(voices_root, "en_US-lessac-medium")
    settings["speech_tts_piper_voice"] = "en_US-lessac-medium"
    # Isolate the file half of the probe: whether piper-tts is importable on
    # this machine is a separate condition.
    monkeypatch.setattr(speech_tts, "_probe_piper", lambda: True)

    assert speech_tts.ENGINES["piper"]["probe"]() is True
    settings["speech_tts_piper_voice"] = "en_US-gone-medium"
    assert speech_tts._piper_voice_path().endswith("en_US-amy-medium.onnx")


# ---------------------------------------------------------------------------
# Kokoro model + pack pairing
# ---------------------------------------------------------------------------
def test_kokoro_model_selection_brings_its_matching_pack(voices_root, settings):
    _install_kokoro(
        voices_root,
        "kokoro-v1.0.onnx", "kokoro-v1.1-zh.onnx",
        "voices-v1.0.bin", "voices-v1.1-zh.bin",
    )
    settings["speech_tts_kokoro_model"] = "kokoro-v1.1-zh.onnx"

    model = speech_tts._kokoro_model_path()
    voices = speech_tts._kokoro_voices_path()
    assert model.endswith("kokoro-v1.1-zh.onnx")
    assert voices.endswith("voices-v1.1-zh.bin"), (
        "a v1.1-zh model must not be paired with the v1.0 pack"
    )


def test_kokoro_defaults_to_the_first_available_pair(voices_root, settings):
    _install_kokoro(voices_root, "kokoro-v1.0.onnx", "voices-v1.0.bin")
    assert speech_tts._kokoro_model_path().endswith("kokoro-v1.0.onnx")
    assert speech_tts._kokoro_voices_path().endswith("voices-v1.0.bin")


def test_kokoro_explicit_pack_is_ignored_when_it_does_not_match_the_model(
    voices_root, settings
):
    _install_kokoro(
        voices_root,
        "kokoro-v1.1-zh.onnx", "voices-v1.0.bin", "voices-v1.1-zh.bin",
    )
    settings["speech_tts_kokoro_model"] = "kokoro-v1.1-zh.onnx"
    settings["speech_tts_kokoro_voices"] = "voices-v1.0.bin"

    voices = speech_tts._kokoro_voices_path()
    assert voices.endswith("voices-v1.1-zh.bin"), (
        "a mismatched pack selection must not win over the model's requirement"
    )


def test_kokoro_selected_model_missing_falls_back(voices_root, settings):
    _install_kokoro(voices_root, "kokoro-v1.0.onnx", "voices-v1.0.bin")
    settings["speech_tts_kokoro_model"] = "kokoro-gone.onnx"
    assert speech_tts._kokoro_model_path().endswith("kokoro-v1.0.onnx")


def test_kokoro_unavailable_without_both_halves(voices_root, settings):
    _install_kokoro(voices_root, "kokoro-v1.0.onnx")  # no voices pack
    assert speech_tts._kokoro_voices_path() == ""
    assert speech_tts.ENGINES["kokoro"]["probe"]() is False


# ---------------------------------------------------------------------------
# Voice names inside the pack
# ---------------------------------------------------------------------------
def test_kokoro_voice_name_honours_a_valid_choice(voices_root, settings, monkeypatch):
    _install_kokoro(voices_root, "kokoro-v1.0.onnx", "voices-v1.0.bin")
    settings["speech_tts_kokoro_voice"] = "am_michael"
    monkeypatch.setattr(
        voice_catalog, "kokoro_voice_names",
        lambda _path: ["af_heart", "am_michael"],
    )

    voice, lang = speech_tts._kokoro_voice_name(speech_tts._kokoro_voices_path())
    assert voice == "am_michael"
    assert lang == "en-us"


def test_kokoro_voice_name_falls_back_when_the_pack_lacks_the_choice(
    voices_root, settings, monkeypatch
):
    _install_kokoro(voices_root, "kokoro-v1.0.onnx", "voices-v1.0.bin")
    settings["speech_tts_kokoro_voice"] = "not_in_this_pack"
    monkeypatch.setattr(
        voice_catalog, "kokoro_voice_names",
        lambda _path: ["af_heart", "bf_emma"],
    )

    voice, _lang = speech_tts._kokoro_voice_name(speech_tts._kokoro_voices_path())
    assert voice == "af_heart", "an unavailable voice must not reach create()"


def test_kokoro_chinese_voices_select_the_chinese_language_tag(
    voices_root, settings, monkeypatch
):
    """Mandarin must go to espeak as ``cmn``; ``zh``/``zh-cn`` are rejected."""
    _install_kokoro(voices_root, "kokoro-v1.1-zh.onnx", "voices-v1.1-zh.bin")
    settings["speech_tts_kokoro_model"] = "kokoro-v1.1-zh.onnx"
    settings["speech_tts_kokoro_voice"] = "zf_xiaobei"
    monkeypatch.setattr(
        voice_catalog, "kokoro_voice_names", lambda _path: ["zf_xiaobei", "af_heart"]
    )

    voice, lang = speech_tts._kokoro_voice_name(speech_tts._kokoro_voices_path())
    assert voice == "zf_xiaobei" and lang == "cmn"


def test_kokoro_language_tag_matches_what_espeak_accepts():
    """The bundled espeak-ng backend only knows ``cmn`` for Mandarin."""
    assert speech_tts.kokoro_lang_for_voice("zf_001") == "cmn"
    assert speech_tts.kokoro_lang_for_voice("zm_009") == "cmn"
    assert speech_tts.kokoro_lang_for_voice("af_heart") == "en-us"
    assert speech_tts.kokoro_lang_for_voice("bm_george") == "en-us"


def test_kokoro_probe_candidates_are_valid_for_a_chinese_pack(
    voices_root, monkeypatch
):
    """A zh pack has no af_heart, so the probe must not rely on it alone."""
    _install_kokoro(voices_root, "kokoro-v1.1-zh.onnx", "voices-v1.1-zh.bin")
    monkeypatch.setattr(
        voice_catalog, "kokoro_voice_names", lambda _path: ["zf_001", "zm_002"]
    )

    candidates = speech_tts._kokoro_probe_candidates(
        speech_tts._kokoro_voices_path(), ""
    )
    assert candidates, "the probe needs at least one candidate"
    assert candidates[0] in ("zf_001", "zm_002"), candidates


def test_kokoro_probe_candidates_prefer_the_selected_voice(voices_root, monkeypatch):
    _install_kokoro(voices_root, "kokoro-v1.1-zh.onnx", "voices-v1.1-zh.bin")
    monkeypatch.setattr(
        voice_catalog, "kokoro_voice_names", lambda _path: ["zf_001", "zm_002"]
    )

    candidates = speech_tts._kokoro_probe_candidates(
        speech_tts._kokoro_voices_path(), "zm_002"
    )
    assert candidates[0] == "zm_002"
    assert len(candidates) == len(set(candidates)), "no duplicate attempts"


def test_kokoro_speaker_uses_the_resolved_voice_and_lang(monkeypatch):
    """_speak_kokoro must pass the resolved voice, not a hardcoded name."""
    import re

    source = open(speech_tts.__file__, encoding="utf-8").read()
    body = source.split("def _speak_kokoro", 1)[1].split("\ndef ", 1)[0]
    assert "af_heart" not in body, "the speaker must not hardcode a voice"
    assert re.search(r"kokoro\.create\(.*voice=voice", body, re.S), body
    assert "lang=lang" in body


# ---------------------------------------------------------------------------
# The legacy single "Voice file" field
# ---------------------------------------------------------------------------
def test_a_legacy_voice_path_still_wins(voices_root, settings):
    """Users who pointed Voice file at a file keep that behaviour."""
    _install_piper_voice(voices_root, "en_US-amy-medium")
    legacy = voices_root / "piper" / "en_US-amy-medium.onnx"
    settings["speech_tts_voice"] = str(legacy)
    settings["speech_tts_piper_voice"] = "en_US-lessac-medium"

    assert speech_tts._piper_voice_path() == str(legacy)


def test_setters_round_trip_through_settings(settings, monkeypatch):
    # The setters write through config_manager (a real file); point them at the
    # dict so the test checks the key names, not the user's config store.
    monkeypatch.setattr(speech_tts, "_set_setting", settings.__setitem__)

    speech_tts.set_piper_voice(" en_US-amy-medium ")
    speech_tts.set_kokoro_model("kokoro-v1.0.onnx")
    speech_tts.set_kokoro_voices("voices-v1.0.bin")
    speech_tts.set_kokoro_voice("af_heart")

    assert speech_tts.get_piper_voice() == "en_US-amy-medium"
    assert speech_tts.get_kokoro_model() == "kokoro-v1.0.onnx"
    assert speech_tts.get_kokoro_voices() == "voices-v1.0.bin"
    assert speech_tts.get_kokoro_voice() == "af_heart"
