"""Voice library acceptance: real downloads from the real sources.

This is the live counterpart to tests/test_voice_catalog.py. It is NOT part of
the pytest suite because it needs the network and pulls ~250 MB (one small
Piper voice, the Chinese Kokoro voice pack, and the kokoro-v1.1-zh int8 build),
so run it deliberately:

    py -3.11 accept_voice_downloads.py

Everything lands in a throwaway %LOCALAPPDATA%, so it cannot disturb the
voices or settings a real user already has. It answers the question the unit
tests cannot: do these URLs, digests and file formats still work together?
"""
import os
import sys
import tempfile

ROOT = tempfile.mkdtemp(prefix="voice_accept_")
os.environ["LOCALAPPDATA"] = ROOT

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import speech_tts
from core import voice_catalog as vc

fails = []


def check(label, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        fails.append(label)


print("=" * 70)
print("VOICE LIBRARY ACCEPTANCE: live downloads")
print("data root:", vc._voices_dir())
print("=" * 70)

# --- Piper catalogue -------------------------------------------------------
print("\n1. Piper catalogue from the live manifest")
entries, err = vc.piper_catalog(force_refresh=True)
check("manifest fetched and parsed", bool(entries), f"{len(entries)} voices, err={err!r}")
langs = {e["language_code"] for e in entries}
check("many languages are represented", len(langs) > 30, f"{len(langs)} language codes")
check("every entry has an onnx + config url", all(
    e["url"].endswith(".onnx") and e["extra_url"].endswith(".onnx.json") for e in entries
))
check("every entry has a name/quality/size",
      all(e["name"] and e["quality"] and e["size_bytes"] > 0 for e in entries))
check("no entry is marked installed yet", not any(e["installed"] for e in entries))
print("   sample:", ", ".join(e["id"] for e in entries[:3]))

# smallest voice keeps the smoke test quick
smallest = min(entries, key=lambda e: e["size_bytes"])
print(f"   downloading smallest: {smallest['id']} ({smallest['size_label']})")

progress = []
ok, err = vc.download_entry(smallest, on_progress=lambda d, t, n: progress.append((d, t)))
check("piper voice downloaded", ok, err)
check("progress was reported", len(progress) > 0, f"{len(progress)} callbacks")
check("file is on disk at the expected name",
      os.path.isfile(os.path.join(vc._engine_dir("piper"), smallest["filename"])))
check("no .part file was left behind",
      not any(n.endswith(".part") for n in os.listdir(vc._engine_dir("piper"))))
check("installed detection picks it up",
      any(v["id"] == smallest["id"] for v in vc.installed_piper_voices()))

# the real piper library must be able to load and speak with it
try:
    import wave

    from piper import PiperVoice

    wav_path = os.path.join(ROOT, "piper.wav")
    voice = PiperVoice.load(os.path.join(vc._engine_dir("piper"), smallest["filename"]))
    with wave.open(wav_path, "wb") as wav_file:
        voice.synthesize_wav("Testing the voice library.", wav_file)
    frames = os.path.getsize(wav_path)
    check("piper synthesises with the downloaded voice", frames > 20000, f"{frames} bytes of wav")
except Exception as exc:
    check("piper synthesises with the downloaded voice", False, f"{type(exc).__name__}: {exc}")

# checksum gate must reject a bad digest
forged = dict(smallest, id="forged", filename="forged.onnx", md5="0" * 32,
              size_bytes=smallest["size_bytes"])
ok, err = vc.download_entry(forged)
check("wrong checksum is rejected", not ok, err[:70])
check("rejected download leaves no file",
      not os.path.exists(os.path.join(vc._engine_dir("piper"), "forged.onnx")))

# --- Kokoro catalogue ------------------------------------------------------
print("\n2. Kokoro catalogue (pinned release)")
kok, err = vc.kokoro_catalog()
check("catalogue is populated", len(kok) == 8, f"{len(kok)} assets, err={err!r}")
check("all six model builds are offered",
      len([e for e in kok if e["kind"] == "model"]) == 6)
check("both voice packs are offered", len([e for e in kok if e["kind"] == "voices"]) == 2)
check("model -> pack pairing is present",
      all(vc.kokoro_bin_for_model(e["filename"]) for e in kok if e["kind"] == "model"))

print("\n3. Download a real Kokoro pack and the previously-mistyped int8 build")
pack = next(e for e in kok if e["filename"] == "voices-v1.1-zh.bin")
model = next(e for e in kok if e["filename"] == "kokoro-v1.1-zh.int8.onnx")
ok, err = vc.download_entry(pack)
check("voice pack downloaded and size-verified", ok, err)

import hashlib

pack_path = os.path.join(vc._engine_dir("kokoro"), pack["filename"])
if os.path.isfile(pack_path):
    h = hashlib.sha256()
    with open(pack_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    print("   observed size  :", os.path.getsize(pack_path))
    print("   observed sha256:", h.hexdigest())

ok, err = vc.download_entry(model)
check("int8 model downloads (digest pin is correct)", ok, err)

names = vc.kokoro_voice_names(os.path.join(vc._engine_dir("kokoro"), pack["filename"]))
check("voice pack enumerates its voices", len(names) > 50, f"{len(names)} voices")
check("Chinese voices are present", any(n.startswith("zf_") or n.startswith("zm_") for n in names),
      ", ".join(sorted(n for n in names if n.startswith("z")))[:70])

# zh model + zh pack: the library must accept the pairing
try:
    from kokoro_onnx import Kokoro

    k = Kokoro(
        os.path.join(vc._engine_dir("kokoro"), model["filename"]),
        os.path.join(vc._engine_dir("kokoro"), pack["filename"]),
    )
    zh_voice = next(
        n for n in vc.kokoro_voice_names(
            os.path.join(vc._engine_dir("kokoro"), pack["filename"])
        ) if n.startswith("z")
    )
    samples, rate = k.create(
        "你好世界", voice=zh_voice, speed=1.0,
        lang=speech_tts.kokoro_lang_for_voice(zh_voice),
    )
    check("kokoro speaks Chinese with the downloaded pair", len(samples) > 1000,
          f"{len(samples)} samples @ {rate} Hz, voice={zh_voice}")
except Exception as exc:
    check("kokoro speaks Chinese with the downloaded pair", False, f"{type(exc).__name__}: {exc}")

# The app's own resolution path must agree with a direct library call.
try:
    speech_tts.set_kokoro_model(model["filename"])
    speech_tts.set_kokoro_voices(pack["filename"])
    speech_tts.set_kokoro_voice(zh_voice)
    resolved_voice, resolved_lang = speech_tts._kokoro_voice_name(
        speech_tts._kokoro_voices_path()
    )
    check("engine resolution matches the direct call",
          resolved_voice == zh_voice and resolved_lang == "cmn",
          f"{resolved_voice} / {resolved_lang}")
    check("zh model passes the crash probe", speech_tts._kokoro_model_usable())
except Exception as exc:
    check("engine resolution matches the direct call", False, f"{type(exc).__name__}: {exc}")

# --- engine selection integration -----------------------------------------
print("\n4. Engine picks the downloads up without extra wiring")
check("kokoro reports installed model files", bool(vc.installed_kokoro_models()))
check("kokoro reports installed voice packs", bool(vc.installed_kokoro_bins()))
check("piper reports the downloaded voice", bool(vc.installed_piper_voices()))
check("engine availability follows the probe",
      speech_tts.ENGINES["kokoro"]["probe"]() is speech_tts._kokoro_model_usable())

print("\n" + "=" * 70)
if fails:
    print("RESULT: FAILURES ->", fails)
    sys.exit(1)
print("RESULT: ALL LIVE CHECKS PASS")
