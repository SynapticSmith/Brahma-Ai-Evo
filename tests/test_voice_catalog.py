"""Tests for core/voice_catalog.py (Piper + Kokoro voice downloads).

The catalog and the downloader are what stand between a user clicking
"Download" and a truncated or corrupt model landing where core/speech_tts.py
auto-discovers it -- and a bad Kokoro build has historically hard-crashed
onnxruntime, so the verification paths are pinned here.

Network is not touched: Piper manifest parsing runs from a fixture, Kokoro is
static release data, and downloads are served by a local HTTP server.
"""
import hashlib
import threading
from functools import partial
from http.server import (
    BaseHTTPRequestHandler,
    SimpleHTTPRequestHandler,
    ThreadingHTTPServer,
)

import pytest

from core import voice_catalog


PIPER_FIXTURE = {
    "en_US-lessac-medium": {
        "key": "en_US-lessac-medium",
        "name": "lessac",
        "language": {
            "code": "en_US",
            "name_english": "English",
            "name_native": "English",
            "country_english": "United States",
        },
        "quality": "medium",
        "num_speakers": 1,
        "files": {
            "en/en_US/lessac/medium/en_US-lessac-medium.onnx": {
                "size_bytes": 63201294,
                "md5_digest": "aa" * 16,
            },
            "en/en_US/lessac/medium/en_US-lessac-medium.onnx.json": {
                "size_bytes": 4882,
                "md5_digest": "bb" * 16,
            },
            "en/en_US/lessac/medium/MODEL_CARD": {"size_bytes": 284, "md5_digest": "cc" * 16},
        },
        "aliases": [],
    },
    "de_DE-thorsten-high": {
        "key": "de_DE-thorsten-high",
        "name": "thorsten",
        "language": {"code": "de_DE", "name_english": "German", "name_native": "Deutsch"},
        "quality": "high",
        "num_speakers": 1,
        "files": {
            "de/de_DE/thorsten/high/de_DE-thorsten-high.onnx": {
                "size_bytes": 113895201,
                "md5_digest": "dd" * 16,
            },
            "de/de_DE/thorsten/high/de_DE-thorsten-high.onnx.json": {
                "size_bytes": 4875,
                "md5_digest": "ee" * 16,
            },
        },
        "aliases": [],
    },
    # Deliberately broken: a voice whose config is missing cannot be used by
    # piper at all, so it must never reach the catalog.
    "xx-broken-low": {
        "key": "xx-broken-low",
        "name": "broken",
        "language": {"code": "xx", "name_english": "Broken"},
        "quality": "low",
        "num_speakers": 1,
        "files": {
            "x/xx/broken/low/xx-broken-low.onnx": {"size_bytes": 1, "md5_digest": "ff" * 16},
        },
        "aliases": [],
    },
}


@pytest.fixture
def voices_root(tmp_path, monkeypatch):
    """Point the catalog (and the engine) at a throwaway voices directory."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    return tmp_path / "BrahmaAI" / "voices"


@pytest.fixture
def http_root(tmp_path):
    """A local HTTP server serving files from a temp dir: (base_url, dir)."""
    served = tmp_path / "served"
    served.mkdir()

    class _Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):  # keep pytest output clean
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, directory=str(served)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/", served
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# Piper manifest -> catalog
# ---------------------------------------------------------------------------
def test_piper_manifest_is_normalised():
    entries = voice_catalog._sort_piper(
        [e for e in (
            voice_catalog._normalise_piper_entry(k, v) for k, v in PIPER_FIXTURE.items()
        ) if e]
    )
    ids = [e["id"] for e in entries]
    assert ids == ["de_DE-thorsten-high", "en_US-lessac-medium"], (
        "sorted by language code, then quality"
    )
    assert "xx-broken-low" not in ids, "a voice without its .onnx.json must be skipped"

    lessac = next(e for e in entries if e["id"] == "en_US-lessac-medium")
    assert lessac["engine"] == "piper"
    assert lessac["language"] == "English"
    assert lessac["quality"] == "medium"
    assert lessac["size_bytes"] == 63201294
    assert lessac["md5"] == "aa" * 16
    assert lessac["url"].endswith("en_US-lessac-medium.onnx")
    assert lessac["extra_url"].endswith("en_US-lessac-medium.onnx.json")
    assert lessac["filename"] == "en_US-lessac-medium.onnx"
    assert lessac["extra_filename"] == "en_US-lessac-medium.onnx.json"
    assert "MB" in lessac["size_label"]


def test_piper_catalog_uses_cache_when_the_network_is_down(monkeypatch, voices_root):
    cached = [
        e for e in (
            voice_catalog._normalise_piper_entry(k, v) for k, v in PIPER_FIXTURE.items()
        ) if e
    ]
    # Stale cache: the catalog will try to refresh, fail, and must fall back.
    monkeypatch.setattr(voice_catalog, "_load_piper_cache", lambda: (cached, 0.0))

    def _no_network(*args, **kwargs):
        raise OSError("no internet")

    monkeypatch.setattr(voice_catalog, "_fetch_json", _no_network)

    entries, err = voice_catalog.piper_catalog(force_refresh=True)
    assert {e["id"] for e in entries} == {"en_US-lessac-medium", "de_DE-thorsten-high"}
    assert "cached" in err.lower()


def test_piper_cache_round_trips_to_disk(voices_root):
    cached = [
        e for e in (
            voice_catalog._normalise_piper_entry(k, v) for k, v in PIPER_FIXTURE.items()
        ) if e
    ]
    voice_catalog._save_piper_cache(cached)
    loaded, fetched_at = voice_catalog._load_piper_cache()
    assert {e["id"] for e in loaded} == {e["id"] for e in cached}
    assert fetched_at > 0


def test_piper_catalog_fails_gracefully_with_no_cache_and_no_network(monkeypatch, voices_root):
    def _no_network(*args, **kwargs):
        raise OSError("no internet")

    monkeypatch.setattr(voice_catalog, "_fetch_json", _no_network)
    entries, err = voice_catalog.piper_catalog(force_refresh=True)
    assert entries == []
    assert err


# ---------------------------------------------------------------------------
# Kokoro catalog (static release data)
# ---------------------------------------------------------------------------
def test_kokoro_catalog_pairs_every_model_with_a_pack():
    entries, err = voice_catalog.kokoro_catalog()
    assert err == ""
    models = [e for e in entries if e["kind"] == "model"]
    packs = [e for e in entries if e["kind"] == "voices"]
    assert len(models) == 6 and len(packs) == 2
    pack_names = {e["filename"] for e in packs}
    for model in models:
        assert model["requires"] in pack_names, f"{model['filename']} has no matching pack"
        assert model["sha256"], "model assets carry digests and must be verified"
        assert model["url"].startswith(voice_catalog.KOKORO_RELEASE_URL)
        assert model["size_bytes"] > 0


def test_kokoro_pack_family_matches_the_models_that_need_it():
    entries, _ = voice_catalog.kokoro_catalog()
    by_name = {e["filename"]: e for e in entries}
    for model in entries:
        if model["kind"] != "model":
            continue
        pack = by_name[model["requires"]]
        assert pack["family"] == model["family"], (
            f"{model['filename']} must use the {model['family']} pack"
        )


def test_kokoro_model_family_fallback_for_hand_placed_models():
    assert voice_catalog.kokoro_model_family("kokoro-v1.0.onnx") == "v1.0"
    assert voice_catalog.kokoro_model_family("kokoro-v1.1-zh.int8.onnx") == "v1.1-zh"
    # A model the catalogue has never heard of still resolves sensibly.
    assert voice_catalog.kokoro_model_family("my-fine-tune.onnx") == "v1.0"
    assert voice_catalog.kokoro_model_family("zh-something.onnx") == "v1.1-zh"


def test_kokoro_bin_for_unknown_model_is_empty():
    assert voice_catalog.kokoro_bin_for_model("nope.onnx") == ""


# ---------------------------------------------------------------------------
# Downloading
# ---------------------------------------------------------------------------
def _serve(payload: bytes, name: str, http_root) -> str:
    base_url, served = http_root
    (served / name).write_bytes(payload)
    return base_url + name


def test_download_verifies_and_moves_into_place(voices_root, http_root):
    payload = b"x" * 5000
    url = _serve(payload, "voice.onnx", http_root)
    dest = str(voices_root / "piper" / "voice.onnx")

    seen = []
    ok, err = voice_catalog.download_file(
        url,
        dest,
        expected_size=len(payload),
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        on_progress=lambda done, total, name: seen.append((done, total, name)),
    )
    assert ok, err
    assert (voices_root / "piper" / "voice.onnx").read_bytes() == payload
    # No partial file is left behind under any name the engine might find.
    assert not list((voices_root / "piper").glob("*.part"))
    assert seen and seen[-1][0] == len(payload)
    assert seen[-1][2] == "voice.onnx", "progress reports the real name, not the .part"


def test_download_rejects_a_corrupt_file(voices_root, http_root):
    payload = b"y" * 2048
    url = _serve(payload, "bad.onnx", http_root)
    dest = str(voices_root / "kokoro" / "bad.onnx")

    ok, err = voice_catalog.download_file(
        url, dest, expected_sha256="0" * 64
    )
    assert not ok and "mismatch" in err.lower()
    assert not (voices_root / "kokoro" / "bad.onnx").exists()
    assert not list((voices_root / "kokoro").glob("*.part"))


def test_download_rejects_a_size_mismatch(voices_root, http_root):
    """A body shorter than the expected size must never be installed."""
    url = _serve(b"short", "trunc.onnx", http_root)
    ok, err = voice_catalog.download_file(
        url, str(voices_root / "trunc.onnx"), expected_size=99999, attempts=2
    )
    assert not ok and "99999" in err
    assert not (voices_root / "trunc.onnx").exists()
    assert not list((voices_root).glob("*.part"))


class _FlakyHandler(BaseHTTPRequestHandler):
    """Serves a payload, truncating the first response and honouring Range."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        state = self.server.state
        payload = state["payload"]
        start = 0
        header = self.headers.get("Range") or ""
        if header.startswith("bytes="):
            try:
                start = int(header[len("bytes="):].split("-")[0])
            except ValueError:
                start = 0
            state["ranges"].append(start)
        rest = payload[start:]
        self.send_response(206 if start else 200)
        self.send_header("Content-Length", str(len(rest)))
        if start:
            self.send_header(
                "Content-Range",
                f"bytes {start}-{len(payload) - 1}/{len(payload)}",
            )
        self.end_headers()
        if state["truncate"]:
            # Drop the connection halfway through: http.client reports this as
            # EOF (b"") rather than raising, which is exactly the silent
            # truncation that must not reach the engine.
            state["truncate"] = False
            self.wfile.write(rest[: len(rest) // 2])
            self.wfile.flush()
            self.close_connection = True
            return
        self.wfile.write(rest)


@pytest.fixture
def flaky_root(tmp_path):
    """A server that truncates the first download and then resumes."""
    state = {"payload": b"", "truncate": True, "ranges": []}

    class _Server(ThreadingHTTPServer):
        pass

    server = _Server(("127.0.0.1", 0), _FlakyHandler)
    server.state = state
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/file.bin", state, tmp_path
    finally:
        server.shutdown()
        server.server_close()


def test_download_resumes_after_a_truncated_response(flaky_root):
    """A dropped connection resumes with Range instead of failing outright."""
    url, state, tmp_path = flaky_root
    payload = bytes(range(256)) * 64  # 16 KB, distinct bytes
    state["payload"] = payload
    dest = str(tmp_path / "resumed.onnx")

    ok, err = voice_catalog.download_file(
        url, dest, expected_size=len(payload), timeout=10
    )
    assert ok, err
    assert (tmp_path / "resumed.onnx").read_bytes() == payload
    assert state["ranges"] == [len(payload) // 2], (
        "the retry must ask for the bytes it does not have yet"
    )
    assert not list(tmp_path.glob("*.part"))


def test_download_does_not_append_when_the_server_ignores_range(voices_root, http_root):
    """If a server answers a Range request with the whole file, restart cleanly."""
    payload = b"q" * 3000
    url = _serve(payload, "norange.onnx", http_root)
    dest = str(voices_root / "piper" / "norange.onnx")
    (voices_root / "piper").mkdir(parents=True, exist_ok=True)
    # Pretend an earlier attempt got partway through.
    (voices_root / "piper" / "norange.onnx.part").write_bytes(payload[:1000])

    ok, err = voice_catalog.download_file(url, dest, expected_size=len(payload))
    assert ok, err
    assert (voices_root / "piper" / "norange.onnx").read_bytes() == payload, (
        "the file must not end up longer than the source"
    )


def test_download_stops_retrying_when_no_progress_is_made(voices_root, http_root):
    """A permanently short file is reported, not retried forever."""
    url = _serve(b"abc", "tiny.onnx", http_root)
    calls = {"n": 0}

    original = voice_catalog._download_attempt

    def counting(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    voice_catalog._download_attempt = counting
    try:
        ok, err = voice_catalog.download_file(
            url, str(voices_root / "tiny.onnx"), expected_size=10_000, attempts=10
        )
    finally:
        voice_catalog._download_attempt = original
    assert not ok
    assert calls["n"] <= 3, f"gave up after {calls['n']} attempts instead of 10"


def test_download_honours_cancellation(voices_root, http_root):
    url = _serve(b"z" * 4096, "cancel.onnx", http_root)
    ok, err = voice_catalog.download_file(url, str(voices_root / "cancel.onnx"),
                                          cancel=lambda: True)
    assert not ok and "cancel" in err.lower()
    assert not (voices_root / "cancel.onnx").exists()


def test_download_entry_piper_fetches_both_files(voices_root, http_root):
    base_url, served = http_root
    onnx = b"m" * 3000
    config = b'{"audio": {}}'
    (served / "a.onnx").write_bytes(onnx)
    (served / "a.onnx.json").write_bytes(config)

    entry = {
        "engine": "piper",
        "id": "en_TEST-a-medium",
        "filename": "en_TEST-a-medium.onnx",
        "extra_filename": "en_TEST-a-medium.onnx.json",
        "url": base_url + "a.onnx",
        "extra_url": base_url + "a.onnx.json",
        "size_bytes": len(onnx),
        "md5": hashlib.md5(onnx).hexdigest(),
        "installed": False,
    }
    ok, err = voice_catalog.download_entry(entry)
    assert ok, err
    piper_dir = voices_root / "piper"
    assert (piper_dir / "en_TEST-a-medium.onnx").read_bytes() == onnx
    assert (piper_dir / "en_TEST-a-medium.onnx.json").read_bytes() == config

    # Both halves present means the pair shows up as installed, and deleting
    # the entry takes both files away again.
    installed = voice_catalog.installed_piper_voices()
    assert [v["id"] for v in installed] == ["en_TEST-a-medium"]
    ok, err = voice_catalog.delete_entry(entry)
    assert ok, err
    assert voice_catalog.installed_piper_voices() == []


def test_delete_entry_clears_the_kokoro_probe_cache(voices_root, monkeypatch):
    kokoro_dir = voices_root / "kokoro"
    kokoro_dir.mkdir(parents=True)
    (kokoro_dir / "kokoro-v1.0.onnx").write_bytes(b"model")

    from core import speech_tts

    speech_tts._probe_cache["x|y"] = True
    voice_catalog.delete_entry(
        {"engine": "kokoro", "filename": "kokoro-v1.0.onnx", "installed": True}
    )
    assert not (kokoro_dir / "kokoro-v1.0.onnx").exists()
    assert speech_tts._probe_cache == {}


def test_a_lone_onnx_is_not_reported_as_an_installed_piper_voice(voices_root):
    piper_dir = voices_root / "piper"
    piper_dir.mkdir(parents=True)
    (piper_dir / "en_US-amy-medium.onnx").write_bytes(b"model")
    assert voice_catalog.installed_piper_voices() == []
    (piper_dir / "en_US-amy-medium.onnx.json").write_bytes(b"{}")
    assert [v["id"] for v in voice_catalog.installed_piper_voices()] == ["en_US-amy-medium"]


# ---------------------------------------------------------------------------
# Voice packs
# ---------------------------------------------------------------------------
def test_kokoro_voice_names_read_the_pack(voices_root, tmp_path):
    np = pytest.importorskip("numpy")
    import os

    pack = voices_root / "kokoro" / "voices-v1.0.bin"
    pack.parent.mkdir(parents=True)
    np.savez(str(pack) + ".tmp.npz", af_heart=np.zeros(1), am_michael=np.zeros(1))
    os.replace(str(pack) + ".tmp.npz", pack)

    assert voice_catalog.kokoro_voice_names(str(pack)) == ["af_heart", "am_michael"]


def test_kokoro_voice_names_fall_back_when_the_pack_cannot_be_read(voices_root):
    missing = str(voices_root / "kokoro" / "absent.bin")
    names = voice_catalog.kokoro_voice_names(missing)
    assert "af_heart" in names, "a readable default is better than an empty picker"


def test_installed_kokoro_lists_separate_models_from_packs(voices_root):
    kokoro_dir = voices_root / "kokoro"
    kokoro_dir.mkdir(parents=True)
    (kokoro_dir / "kokoro-v1.0.onnx").write_bytes(b"m")
    (kokoro_dir / "voices-v1.0.bin").write_bytes(b"v")
    (kokoro_dir / "notes.txt").write_bytes(b"ignored")
    assert voice_catalog.installed_kokoro_models() == ["kokoro-v1.0.onnx"]
    assert voice_catalog.installed_kokoro_bins() == ["voices-v1.0.bin"]


def test_engine_package_map_covers_both_engines():
    assert set(voice_catalog.ENGINE_PACKAGES) == {"piper", "kokoro"}
    assert "onnxruntime" in voice_catalog.ENGINE_PACKAGES["kokoro"]
    assert voice_catalog.engine_importable("nope") is False
