"""
core/voice_catalog.py
Browse and download local TTS voice models (Piper and Kokoro) from their
first-party sources, so Settings can offer the whole catalogue instead of one
hard-coded voice per engine.

Sources
-------
piper    rhasspy/piper-voices on Hugging Face publishes a single `voices.json`
         manifest describing EVERY published voice: language, quality, exact
         byte size, md5 digest and the `.onnx` + `.onnx.json` pair. One fetch
         yields the full catalogue (~100 voices, ~40 languages); each voice is
         two files verified by md5. Cached on disk so the list still renders
         offline and the manifest is not re-downloaded on every dialog open.
kokoro   the kokoro-onnx GitHub release `model-files-v1.1` is a fixed set of
         model builds (v1.0 English / v1.1-zh Chinese+English, each in
         fp32 / fp16 / int8) plus two voice packs. The assets carry sha256
         digests, so the catalogue is static data here and downloads are
         verified end to end. A model build must be paired with its matching
         voices pack (`v1.0` model -> `voices-v1.0.bin`).

Design rules
------------
- No Qt. Pure data + `urllib`; the UI layer owns threads and widgets.
- Nothing here raises: every entry point returns (result, error) or a list,
  because a catalog fetch failing must never break the Settings page.
- Downloads land in a `.part` file and are only renamed into place after the
  size / digest check passes, so a cancelled or corrupted download can never
  be mistaken for an installed voice.
- Files go under %LOCALAPPDATA%\\BrahmaAI\\voices, the same root
  core/speech_tts.py auto-discovers from, so a downloaded voice is picked up
  by the engine with no extra wiring.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_USER_AGENT = "Mozilla/5.0 (Brahma Evo voice catalog)"


def _voices_dir() -> str:
    """Same root core/speech_tts.py discovers from.

    Imported lazily rather than duplicated: if the app ever moves its data
    root, the downloader and the engine must move together.
    """
    try:
        from core import speech_tts

        return speech_tts._voices_dir()
    except Exception:
        base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
        return os.path.join(base, "BrahmaAI", "voices")


def _engine_dir(engine: str) -> str:
    return os.path.join(_voices_dir(), engine)


def _cache_dir() -> str:
    return os.path.join(_voices_dir(), "cache")


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------
PIPER_VOICES_URL = (
    "https://huggingface.co/rhasspy/piper-voices/raw/main/voices.json"
)
PIPER_FILE_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
# The manifest rarely changes; re-fetch weekly and otherwise trust the cache.
PIPER_CACHE_TTL_SECONDS = 7 * 24 * 3600

KOKORO_RELEASE_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"
)

# Fixed asset list of the kokoro-onnx `model-files-v1.1` release (verified
# against the GitHub API). Sizes and sha256 digests come from the release
# metadata itself, so a corrupted or truncated download is caught before the
# file is put where the engine will load it -- which matters here because a
# bad Kokoro build has historically hard-crashed onnxruntime.
KOKORO_ASSETS: List[Dict[str, Any]] = [
    {
        "filename": "kokoro-v1.0.onnx",
        "kind": "model",
        "family": "v1.0",
        "precision": "fp32",
        "label": "Kokoro v1.0 (English) - full precision",
        "detail": "326 MB. Best quality, slowest to load.",
        "size_bytes": 325505369,
        "sha256": "beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a",
        "requires": "voices-v1.0.bin",
        "voices": 54,
    },
    {
        "filename": "kokoro-v1.0.fp16.onnx",
        "kind": "model",
        "family": "v1.0",
        "precision": "fp16",
        "label": "Kokoro v1.0 (English) - half precision",
        "detail": "164 MB. Near-identical quality, half the size.",
        "size_bytes": 163527961,
        "sha256": "f3a290d384fbb27966d462905c71a46cef9e5fd00516b40df32a0b4afe77ac96",
        "requires": "voices-v1.0.bin",
        "voices": 54,
    },
    {
        "filename": "kokoro-v1.0.int8.onnx",
        "kind": "model",
        "family": "v1.0",
        "precision": "int8",
        "label": "Kokoro v1.0 (English) - quantised",
        "detail": "114 MB. Smallest; audible quality drop (correlation 0.916).",
        "size_bytes": 114119327,
        "sha256": "ae315a79b623f244700e4afb9246c46a26066782e049ba174bf3ba433970ee9c",
        "requires": "voices-v1.0.bin",
        "voices": 54,
        "warning": "Quantised builds are the ones most likely to crash "
                   "onnxruntime on some CPUs; Brahma load-tests every model in "
                   "a child process before offering it.",
    },
    {
        "filename": "kokoro-v1.1-zh.onnx",
        "kind": "model",
        "family": "v1.1-zh",
        "precision": "fp32",
        "label": "Kokoro v1.1 (Chinese + English) - full precision",
        "detail": "326 MB. Adds Mandarin; also speaks English.",
        "size_bytes": 325506167,
        "sha256": "859f9ded9f53be16c24857cdab3254a45da53c3afd5ba6ef134c7de3f822e326",
        "requires": "voices-v1.1-zh.bin",
        "voices": 103,
    },
    {
        "filename": "kokoro-v1.1-zh.fp16.onnx",
        "kind": "model",
        "family": "v1.1-zh",
        "precision": "fp16",
        "label": "Kokoro v1.1 (Chinese + English) - half precision",
        "detail": "164 MB. Near-identical quality, half the size.",
        "size_bytes": 163528759,
        "sha256": "a628ea5d6fbde96d1a85f691a6a00847829937f9e488021ba2c5359bc6ea08b5",
        "requires": "voices-v1.1-zh.bin",
        "voices": 103,
    },
    {
        "filename": "kokoro-v1.1-zh.int8.onnx",
        "kind": "model",
        "family": "v1.1-zh",
        "precision": "int8",
        "label": "Kokoro v1.1 (Chinese + English) - quantised",
        "detail": "114 MB. Smallest; audible quality drop (correlation 0.874).",
        "size_bytes": 114120125,
        "sha256": "11751c087b4bbeed031e2b687b11dda698bd27ba0509472f960b57f835a999f7",
        "requires": "voices-v1.1-zh.bin",
        "voices": 103,
        "warning": "Quantised builds are the ones most likely to crash "
                   "onnxruntime on some CPUs; Brahma load-tests every model in "
                   "a child process before offering it.",
    },
    {
        "filename": "voices-v1.0.bin",
        "kind": "voices",
        "family": "v1.0",
        "label": "Kokoro voice pack v1.0 (English)",
        "detail": "54 voices (af_heart, am_michael, bf_emma, ...).",
        "size_bytes": 28214398,
        "sha256": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
        "requires": "kokoro-v1.0.onnx",
    },
    {
        "filename": "voices-v1.1-zh.bin",
        "kind": "voices",
        "family": "v1.1-zh",
        "label": "Kokoro voice pack v1.1 (Chinese + English)",
        "detail": "103 voices; 100 Mandarin (zf_/zm_) plus 3 English.",
        "size_bytes": 53815880,
        # GitHub publishes no digest for this asset, so this pin was computed
        # from the release file itself and checked against the published size.
        "sha256": "14cb6186c99e4f6016871405f62046c5df863ae27465cbdc4ee08be7dd703acd",
        "requires": "kokoro-v1.1-zh.onnx",
    },
]

# Fallback if a voices pack cannot be parsed (numpy missing / odd format):
# the 26 English voices shipped in the original v1.0 pack, per the release notes.
KOKORO_FALLBACK_VOICES = (
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore",
    "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael",
    "am_onyx", "am_puck",
    "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
)

# Python packages each engine needs, so Settings can install the runtime too.
ENGINE_PACKAGES: Dict[str, Tuple[str, ...]] = {
    "piper": ("piper-tts", "onnxruntime"),
    "kokoro": ("kokoro-onnx", "onnxruntime"),
}


def size_label(size_bytes: int) -> str:
    """Human-readable size; the catalog shows this so users know the cost."""
    try:
        mb = float(size_bytes) / (1024 * 1024)
    except Exception:
        return "?"
    return f"{mb:.1f} MB" if mb >= 1 else f"{max(1, int(size_bytes) / 1024):.0f} KB"


# ---------------------------------------------------------------------------
# Engine runtime (pip) helpers
# ---------------------------------------------------------------------------
def engine_importable(engine: str) -> bool:
    """Whether the engine's Python package can be imported right now."""
    module = {"piper": "piper", "kokoro": "kokoro_onnx"}.get(engine)
    if not module:
        return False
    try:
        __import__(module)
        return True
    except Exception:
        return False


def install_engine(engine: str, on_line: Optional[Callable[[str], None]] = None) -> Tuple[bool, str]:
    """Blocking pip install of an engine's packages. Run it on a worker thread.

    Returns (ok, tail_of_output). Never raises: a failed install is reported as
    text so the dialog can show why (commonly: pip cannot write to a
    user-owned site-packages, or there is no network).
    """
    packages = ENGINE_PACKAGES.get(engine)
    if not packages:
        return False, f"Unknown engine: {engine}"
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", *packages]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:
        return False, f"Could not start pip: {exc}"

    tail: List[str] = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            tail.append(line)
            if len(tail) > 40:
                tail.pop(0)
            if on_line is not None:
                try:
                    on_line(line)
                except Exception:
                    pass
        proc.wait(timeout=900)
    except Exception as exc:
        try:
            proc.kill()
        except Exception:
            pass
        return False, f"pip failed: {exc}"

    ok = proc.returncode == 0
    return ok, "\n".join(tail[-12:]) if not ok else ""


# ---------------------------------------------------------------------------
# Piper catalog (Hugging Face manifest)
# ---------------------------------------------------------------------------
def _piper_cache_path() -> str:
    return os.path.join(_cache_dir(), "piper_voices.json")


def _load_piper_cache() -> Tuple[list, float]:
    path = _piper_cache_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            blob = json.load(fh)
        return list(blob.get("voices", [])), float(blob.get("fetched_at", 0.0))
    except Exception:
        return [], 0.0


def _save_piper_cache(voices: list) -> None:
    try:
        os.makedirs(_cache_dir(), exist_ok=True)
        path = _piper_cache_path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"fetched_at": time.time(), "voices": voices}, fh)
        os.replace(tmp, path)
    except Exception:
        # A read-only or full disk only costs us the cache, never the feature.
        pass


def _fetch_json(url: str, timeout: float) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _normalise_piper_entry(key: str, raw: dict) -> Optional[Dict[str, Any]]:
    """One manifest voice -> the normalized shape the UI and downloader share."""
    try:
        files = raw.get("files") or {}
        onnx_rel = json_rel = ""
        size = 0
        md5 = ""
        for rel, meta in files.items():
            if rel.endswith(".onnx"):
                onnx_rel = rel
                size = int((meta or {}).get("size_bytes", 0))
                md5 = str((meta or {}).get("md5_digest", "") or "")
            elif rel.endswith(".onnx.json"):
                json_rel = rel
        if not (onnx_rel and json_rel):
            return None  # an entry without both halves is unusable for piper
        lang = raw.get("language") or {}
        return {
            "engine": "piper",
            "id": key,
            "kind": "model",
            "label": key,
            "name": str(raw.get("name") or key),
            "language_code": str(lang.get("code") or ""),
            "language": str(lang.get("name_english") or lang.get("code") or ""),
            "language_native": str(lang.get("name_native") or ""),
            "quality": str(raw.get("quality") or ""),
            "num_speakers": int(raw.get("num_speakers") or 1),
            "size_bytes": size,
            "size_label": size_label(size),
            "md5": md5,
            "sha256": "",
            "url": PIPER_FILE_BASE + onnx_rel,
            "extra_url": PIPER_FILE_BASE + json_rel,
            "filename": os.path.basename(onnx_rel),
            "extra_filename": os.path.basename(json_rel),
            "requires": "",
            "warning": "",
        }
    except Exception:
        return None


def _sort_piper(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    quality_order = {"high": 0, "medium": 1, "low": 2, "x_low": 3}
    return sorted(
        entries,
        key=lambda e: (
            str(e.get("language_code") or ""),
            quality_order.get(str(e.get("quality")), 9),
            str(e.get("id")),
        ),
    )


def piper_catalog(force_refresh: bool = False, timeout: float = 25.0) -> Tuple[List[Dict[str, Any]], str]:
    """Every published Piper voice. Returns (entries, error).

    The manifest is cached on disk for a week, so opening the dialog repeatedly
    costs nothing and a machine that is offline right now still gets a usable
    (if possibly stale) list.
    """
    cached, fetched_at = _load_piper_cache()
    fresh = (time.time() - fetched_at) < PIPER_CACHE_TTL_SECONDS
    if cached and (fresh or not force_refresh):
        return _sort_piper(_mark_installed("piper", cached)), ""

    try:
        raw = _fetch_json(PIPER_VOICES_URL, timeout)
    except Exception as exc:
        if cached:
            # Stale-but-real beats empty: the user can still pick a voice, and
            # the download URLs are stable paths in the same repo.
            return (_sort_piper(_mark_installed("piper", cached)),
                    f"Using the cached voice list ({exc}).")
        return [], f"Could not download the Piper voice list: {exc}"

    entries: List[Dict[str, Any]] = []
    if isinstance(raw, dict):
        for key, value in raw.items():
            entry = _normalise_piper_entry(str(key), value or {})
            if entry is not None:
                entries.append(entry)
    if not entries:
        return [], "The Piper voice list downloaded but contained no usable voices."

    _save_piper_cache(entries)
    return _sort_piper(_mark_installed("piper", entries)), ""


def _mark_installed(engine: str, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flag entries whose files are already on disk (both halves, for piper)."""
    try:
        directory = _engine_dir(engine)
        present = set(os.listdir(directory)) if os.path.isdir(directory) else set()
    except Exception:
        present = set()
    for entry in entries:
        if engine == "piper":
            entry["installed"] = (
                entry.get("filename", "") in present
                and entry.get("extra_filename", "") in present
            )
        else:
            entry["installed"] = entry.get("filename", "") in present
    return entries


def kokoro_catalog() -> Tuple[List[Dict[str, Any]], str]:
    """Kokoro model builds + voice packs from the pinned release.

    Static data by design: the release is immutable, the digests come from its
    metadata, and there is nothing to fetch. (entries, error)
    """
    entries: List[Dict[str, Any]] = []
    for asset in KOKORO_ASSETS:
        entry = dict(asset)
        entry["engine"] = "kokoro"
        entry["id"] = asset["filename"]
        entry["size_label"] = size_label(int(asset.get("size_bytes", 0)))
        entry["url"] = KOKORO_RELEASE_URL + asset["filename"]
        entry["extra_url"] = ""
        entry["extra_filename"] = ""
        entry["installed"] = False
        entries.append(entry)
    return _mark_installed("kokoro", entries), ""


# ---------------------------------------------------------------------------
# What is already installed (including voices the user dropped in by hand)
# ---------------------------------------------------------------------------
def installed_piper_voices() -> List[Dict[str, str]]:
    """Voice pairs actually present: [{"id": ..., "path": ....onnx}, ...]."""
    directory = _engine_dir("piper")
    out: List[Dict[str, str]] = []
    try:
        names = sorted(os.listdir(directory))
    except Exception:
        return out
    for name in names:
        if not name.endswith(".onnx"):
            continue
        onnx = os.path.join(directory, name)
        if not (os.path.isfile(onnx + ".json") or os.path.isfile(os.path.splitext(onnx)[0] + ".json")):
            continue  # piper needs the config too; a lone .onnx cannot speak
        out.append({"id": name[: -len(".onnx")], "path": onnx})
    return out


def installed_kokoro_models() -> List[str]:
    directory = _engine_dir("kokoro")
    try:
        return sorted(n for n in os.listdir(directory) if n.endswith(".onnx"))
    except Exception:
        return []


def installed_kokoro_bins() -> List[str]:
    directory = _engine_dir("kokoro")
    try:
        return sorted(
            n for n in os.listdir(directory) if n.endswith((".bin", ".voices"))
        )
    except Exception:
        return []


def kokoro_voice_names(bin_path: str) -> List[str]:
    """Voice names inside a Kokoro voices pack.

    The pack is a numpy `.npz` archive of style vectors keyed by voice name, so
    numpy alone can enumerate them -- no kokoro_onnx import needed, which keeps
    this usable before the engine is installed. Falls back to the known English
    set rather than returning nothing.
    """
    if not (bin_path and os.path.isfile(bin_path)):
        return list(KOKORO_FALLBACK_VOICES)
    try:
        import numpy as np

        with np.load(bin_path, allow_pickle=False) as data:
            names = sorted(str(k) for k in data.files)
        if names:
            return names
    except Exception:
        pass
    return list(KOKORO_FALLBACK_VOICES)


def kokoro_bin_for_model(model_filename: str) -> str:
    """The voices pack a model build requires ("" when unknown).

    Mixing them is the classic Kokoro misconfiguration: a v1.1-zh model asked
    for a voice that only exists in the v1.0 pack just fails at create().
    """
    for asset in KOKORO_ASSETS:
        if asset["filename"] == model_filename:
            return str(asset.get("requires") or "")
    return ""


def kokoro_model_family(model_filename: str) -> str:
    for asset in KOKORO_ASSETS:
        if asset["filename"] == model_filename:
            return str(asset.get("family") or "v1.0")
    # A hand-placed model: assume the English pack, which is the common case.
    return "v1.0" if "zh" not in model_filename else "v1.1-zh"


# ---------------------------------------------------------------------------
# Downloading
# ---------------------------------------------------------------------------
def _part_size(part: str) -> int:
    try:
        return os.path.getsize(part)
    except Exception:
        return 0


def _download_attempt(
    url: str,
    part: str,
    label: str,
    *,
    total: int,
    on_progress: Optional[Callable[[int, int, str], None]],
    cancel: Optional[Callable[[], bool]],
    timeout: float,
) -> Tuple[int, int, str]:
    """One transfer attempt, resuming from whatever is already in `part`.

    Returns (bytes_on_disk, total_bytes, error). `error` is "" when the attempt
    ended cleanly, which does NOT mean it is complete -- the caller compares
    bytes against total, because a dropped connection makes `read()` return an
    empty chunk instead of raising, i.e. a truncated body looks like EOF.
    """
    done = _part_size(part)
    if total and done > total:
        _discard(part)  # nonsense length: start over rather than trust it
        done = 0

    headers = {"User-Agent": _USER_AGENT}
    if done:
        headers["Range"] = f"bytes={done}-"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            if done and status != 206:
                # The server ignored the Range header, so the body starts at
                # byte 0; appending it to the partial file would corrupt it.
                done = 0
            if not total:
                if status == 206:
                    # Content-Range: "bytes 100-999/1000" -> take the /1000.
                    tail = (response.headers.get("Content-Range") or "").rpartition("/")[2]
                    total = int(tail) if tail.isdigit() else 0
                else:
                    try:
                        total = int(response.headers.get("Content-Length") or 0)
                    except Exception:
                        total = 0
            with open(part, "ab" if done else "wb") as out:
                while True:
                    if cancel is not None and cancel():
                        return done, total, "Cancelled."
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if on_progress is not None and total:
                        try:
                            on_progress(done, total, label)
                        except Exception:
                            pass
    except Exception as exc:
        return _part_size(part), total, f"{type(exc).__name__}: {exc}"
    return _part_size(part), total, ""


def download_file(
    url: str,
    dest: str,
    *,
    expected_size: int = 0,
    expected_md5: str = "",
    expected_sha256: str = "",
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    cancel: Optional[Callable[[], bool]] = None,
    timeout: float = 60.0,
    attempts: int = 5,
) -> Tuple[bool, str]:
    """Stream `url` to `dest`, verify it, then atomically move it into place.

    Returns (ok, error). The `.part` suffix is what makes this safe: a partial
    file is never left under the final name, so core/speech_tts.py cannot
    auto-discover a truncated model and crash on it.

    Large models (Kokoro fp32 is 326 MB) routinely lose a connection partway
    through, and the short read is silent, so each attempt resumes with a
    Range request from the bytes already on disk instead of starting over.
    """
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    part = dest + ".part"
    label = os.path.basename(dest)
    total = int(expected_size or 0)
    last_err = ""
    stalled = 0

    for attempt in range(1, max(1, attempts) + 1):
        if cancel is not None and cancel():
            _discard(part)
            return False, "Cancelled."
        previous = _part_size(part)
        done, total, err = _download_attempt(
            url,
            part,
            label,
            total=total,
            on_progress=on_progress,
            cancel=cancel,
            timeout=timeout,
        )
        if err == "Cancelled.":
            _discard(part)
            return False, "Cancelled."
        if total and done >= total:
            break
        if not total and not err:
            total = done  # unknown length, clean EOF: trust what arrived
            break
        last_err = err or f"connection closed after {done} of {total} bytes"
        # Only keep retrying while attempts actually gain ground: a server that
        # keeps returning the same short body will never finish, and a Kokoro
        # model is far too large to re-request on a loop.
        stalled = stalled + 1 if done <= previous else 0
        if stalled >= 2:
            break
        if attempt < attempts:
            time.sleep(min(2.0, 0.5 * attempt))

    done = _part_size(part)
    if total and done != total:
        _discard(part)
        return False, (
            f"Download failed ({label}): {last_err or 'incomplete'}"
            + (f" (got {done} of {total} bytes)" if total else "")
        )

    if expected_size and done != int(expected_size):
        _discard(part)
        return False, (
            f"Size mismatch for {label}: {done} bytes, expected {expected_size}."
        )
    if expected_sha256 or expected_md5:
        expected = (expected_sha256 or expected_md5).lower()
        algorithm = hashlib.sha256 if expected_sha256 else hashlib.md5
        try:
            digest = algorithm()
            with open(part, "rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    digest.update(chunk)
            actual = digest.hexdigest()
        except Exception as exc:
            _discard(part)
            return False, f"Could not verify {label}: {exc}"
        if actual != expected:
            _discard(part)
            return False, (
                f"Checksum mismatch for {label} "
                f"(downloaded file is corrupt or was tampered with)."
            )

    try:
        os.replace(part, dest)
    except Exception as exc:
        _discard(part)
        return False, f"Could not finalise {label}: {exc}"
    return True, ""


def _discard(path: str) -> None:
    try:
        os.remove(path)
    except Exception:
        pass


def download_entry(
    entry: Dict[str, Any],
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    cancel: Optional[Callable[[], bool]] = None,
) -> Tuple[bool, str]:
    """Download one catalog entry (both files, for piper). Returns (ok, error)."""
    engine = str(entry.get("engine") or "")
    if engine not in ("piper", "kokoro"):
        return False, f"Unknown engine for this voice: {engine!r}"
    directory = _engine_dir(engine)
    os.makedirs(directory, exist_ok=True)

    if engine == "piper":
        # The config is a few KB: fetch it first so a failure costs nothing.
        ok, err = download_file(
            entry["extra_url"],
            os.path.join(directory, entry["extra_filename"]),
            expected_size=0,
            cancel=cancel,
        )
        if not ok:
            return ok, err
        return download_file(
            entry["url"],
            os.path.join(directory, entry["filename"]),
            expected_size=int(entry.get("size_bytes") or 0),
            expected_md5=str(entry.get("md5") or ""),
            on_progress=on_progress,
            cancel=cancel,
        )

    return download_file(
        entry["url"],
        os.path.join(directory, entry["filename"]),
        expected_size=int(entry.get("size_bytes") or 0),
        expected_sha256=str(entry.get("sha256") or ""),
        on_progress=on_progress,
        cancel=cancel,
    )


def delete_entry(entry: Dict[str, Any]) -> Tuple[bool, str]:
    """Remove an installed voice's files. (ok, error)"""
    engine = str(entry.get("engine") or "")
    directory = _engine_dir(engine)
    removed = False
    errors: List[str] = []
    for name in (entry.get("filename"), entry.get("extra_filename")):
        if not name:
            continue
        try:
            os.remove(os.path.join(directory, name))
            removed = True
        except FileNotFoundError:
            pass
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    if removed:
        _invalidate_probe_cache(engine)
    return (True, "") if removed and not errors else (False, "; ".join(errors) or "Nothing to remove.")


def _invalidate_probe_cache(engine: str) -> None:
    """Adding or removing a Kokoro model must re-run the crash probe."""
    if engine != "kokoro":
        return
    try:
        from core import speech_tts

        speech_tts._probe_cache.clear()
    except Exception:
        pass
